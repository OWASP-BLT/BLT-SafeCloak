"""
test_notes.py — Full test suite for the BLT-SafeCloak Notes module.

The Notes module (public/js/notes.js) is the most security-critical part of the
application: it handles AES-GCM encryption, passphrase management, note CRUD,
AI text-processing features, and multi-format export.  Despite this, it had
*zero* automated test coverage.

This suite fixes that gap by running the JavaScript under Playwright so every
test exercises the real Web Crypto API, the real localStorage / sessionStorage
APIs, and the real DOM — exactly as a user's browser would.

Test groups
-----------
1.  Smoke / initialisation  – page loads, DOM wired up, no JS errors
2.  Passphrase security     – key is in sessionStorage, NOT localStorage
3.  Note CRUD               – create / read / update / delete
4.  Persistence             – notes survive a reload (encrypted round-trip)
5.  AI: summarise           – output format and edge cases
6.  AI: key points          – keyword extraction
7.  AI: action items        – action-word detection
8.  AI: word frequency      – top-keyword ranking, stopword filtering
9.  Export                  – txt / md / json download triggers
10. Multi-note management   – ordering, active selection, preview truncation
11. Word-count widget       – live character / word counter
12. Encryption isolation    – different sessions cannot read each other's data
13. Concurrent edits        – rapid typing debounce does not corrupt data
14. Empty-state handling    – graceful behaviour with no notes present

Local setup
-----------
    npm install
    pip install -r requirements-dev.txt
    playwright install chromium --with-deps
    pytest tests/test_notes.py -v
"""

import http.server
import socket
import socketserver
import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect


ROOT = Path(__file__).parent.parent

_MIME: dict[str, str] = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
_PAGES: dict[str, str] = {
    "/": "src/pages/index.html",
    "/video-chat": "src/pages/video-chat.html",
    "/notes": "src/pages/notes.html",
    "/consent": "src/pages/consent.html",
}
_PUBLIC_FILES: dict[str, Path] = {
    "/" + f.relative_to(ROOT / "public").as_posix(): f
    for f in (ROOT / "public").rglob("*")
    if f.is_file()
}

TIMEOUT_MS = 30_000
STORAGE_KEY = "safecloak_notes_v1"
PASS_KEY = "safecloak_notes_pass"


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    """TCPServer with SO_REUSEADDR set before bind."""

    allow_reuse_address = True


class _AppHandler(http.server.BaseHTTPRequestHandler):
    """Serve HTML pages and public assets for the test suite."""

    def do_GET(self):  
        path = self.path.split("?")[0]

        if path in _PAGES:
            data = (ROOT / _PAGES[path]).read_bytes()
            self._respond(200, "text/html; charset=utf-8", data)
            return

        file_path = _PUBLIC_FILES.get(path)
        if file_path is not None:
            data = file_path.read_bytes()
            ct = _MIME.get(file_path.suffix, "application/octet-stream")
            self._respond(200, ct, data)
            return

        self._respond(404, "text/plain", b"Not found")

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:  
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    """Start the local HTTP server and yield its base URL for the module."""
    server = _ThreadingTCPServer(("127.0.0.1", 0), _AppHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def browser_instance():
    """Launch a single Chromium browser for the whole module."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
        yield browser
        browser.close()


@pytest.fixture()
def page(browser_instance, base_url):
    """
    Fresh browser context + page for every test.

    Each test gets its own isolated context so localStorage / sessionStorage
    never leak between tests.
    """
    ctx = browser_instance.new_context()
    pg = ctx.new_page()

    pg.errors = []
    pg.on("pageerror", lambda exc: pg.errors.append(str(exc)))

    pg.goto(f"{base_url}/notes", wait_until="domcontentloaded")

    pg.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)

    yield pg

    ctx.close()

def _create_note(page, title: str = "Test Note", content: str = "Hello, world!") -> str:
    """
    Click '+ New', fill in title + body, and return the generated note ID.
    Waits for the note to appear in the list before returning.
    """
    page.click("#btn-new-note")
    page.wait_for_selector("#note-title", timeout=TIMEOUT_MS)
 
    page.fill("#note-title", title)
    page.fill("#note-body", content)
 
    page.dispatch_event("#note-body", "input")

    note_id = page.evaluate(
        "document.querySelector('.note-item.active') &&"
        " document.querySelector('.note-item.active').dataset.id"
    )
    return note_id or ""


def _wait_for_save(page) -> None:
    """Wait longer than the 800 ms debounce so saveNotes() has run."""
    time.sleep(1.0)


def _reload_notes_page(page, base_url: str) -> None:
    """Navigate away and back to /notes to simulate a browser reload."""
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.goto(f"{base_url}/notes", wait_until="domcontentloaded")
    page.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)

class TestSmoke:
    """The page loads and the core UI elements are present."""

    def test_page_loads_without_js_errors(self, page):
        assert page.errors == [], f"JS errors on load: {page.errors}"

    def test_notes_app_is_defined(self, page):
        defined = page.evaluate("typeof NotesApp !== 'undefined'")
        assert defined, "NotesApp should be defined on the /notes page"

    def test_new_note_button_present(self, page):
        page.wait_for_selector("#btn-new-note", timeout=TIMEOUT_MS)

    def test_empty_state_shown_when_no_notes(self, page):

        container = page.locator("#notes-list")
        expect(container).to_contain_text("No notes yet", timeout=TIMEOUT_MS)

    def test_editor_hidden_when_no_active_note(self, page):
        editor = page.locator("#editor-wrapper")
      
        display = page.evaluate(
            "getComputedStyle(document.getElementById('editor-wrapper')).display"
        )
        assert display == "none", "Editor should be hidden when no note is active"

    def test_ai_toolbar_buttons_present(self, page):
        for btn_id in ("btn-summarize", "btn-keypoints", "btn-actions", "btn-keywords"):
            page.wait_for_selector(f"#{btn_id}", timeout=TIMEOUT_MS)


class TestPassphraseSecurity:
    """
    The encryption key must live in sessionStorage, NOT in localStorage.

    This is the core security invariant: storing the key next to the
    ciphertext in the same storage bucket defeats AES-GCM entirely.
    """

    def test_passphrase_not_in_localstorage_before_any_note(self, page):
        val = page.evaluate(f"localStorage.getItem('{PASS_KEY}')")
        assert val is None, (
            "Passphrase must NOT be written to localStorage — "
            "it would be co-located with the ciphertext."
        )

    def test_passphrase_stored_in_sessionstorage_after_first_note(self, page):
        _create_note(page)
        _wait_for_save(page)
        val = page.evaluate(f"sessionStorage.getItem('{PASS_KEY}')")
        assert val is not None, "Passphrase should be in sessionStorage after first save"
        assert len(val) >= 16, "Passphrase should be a non-trivial random string"

    def test_passphrase_still_absent_from_localstorage_after_save(self, page):
        _create_note(page)
        _wait_for_save(page)
        val = page.evaluate(f"localStorage.getItem('{PASS_KEY}')")
        assert val is None, (
            "Passphrase must NEVER appear in localStorage, even after a save."
        )

    def test_ciphertext_stored_in_localstorage(self, page):
        _create_note(page)
        _wait_for_save(page)
        raw = page.evaluate(f"localStorage.getItem('{STORAGE_KEY}')")
        assert raw is not None, "Encrypted notes should be written to localStorage"

        import json
        payload = json.loads(raw)
        assert "iv" in payload and "ciphertext" in payload, (
            "Stored payload should have 'iv' and 'ciphertext' fields"
        )

    def test_passphrase_is_random_between_sessions(self, browser_instance, base_url):
        """Two independent browser contexts must generate different passphrases."""
        passphrases = []
        for _ in range(2):
            ctx = browser_instance.new_context()
            pg = ctx.new_page()
            pg.goto(f"{base_url}/notes", wait_until="domcontentloaded")
            pg.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)
            _create_note(pg)
            _wait_for_save(pg)
            pp = pg.evaluate(f"sessionStorage.getItem('{PASS_KEY}')")
            passphrases.append(pp)
            ctx.close()
        assert passphrases[0] != passphrases[1], (
            "Each session must derive a fresh random passphrase"
        )

class TestNoteCRUD:
    """Create, read, update, and delete notes through the UI."""

    def test_create_note_appears_in_list(self, page):
        _create_note(page, title="My First Note")
        page.wait_for_selector(".note-item", timeout=TIMEOUT_MS)
        titles = page.locator(".note-item-title").all_text_contents()
        assert "My First Note" in titles

    def test_create_note_opens_editor(self, page):
        _create_note(page, title="Editor Test")
        display = page.evaluate(
            "getComputedStyle(document.getElementById('editor-wrapper')).display"
        )
        assert display != "none", "Editor should be visible after creating a note"

    def test_create_note_title_shows_in_editor(self, page):
        _create_note(page, title="Unique Title XYZ")
        val = page.input_value("#note-title")
        assert val == "Unique Title XYZ"

    def test_create_note_body_shows_in_editor(self, page):
        _create_note(page, content="Secret body content")
        val = page.input_value("#note-body")
        assert val == "Secret body content"

    def test_update_note_title_reflected_in_list(self, page):
        _create_note(page, title="Old Title")
        page.fill("#note-title", "Updated Title")
        page.dispatch_event("#note-title", "input")
        page.wait_for_function(
            "document.querySelector('.note-item.active .note-item-title').textContent"
            " === 'Updated Title'",
            timeout=TIMEOUT_MS,
        )

    def test_delete_note_removes_from_list(self, page):
        _create_note(page, title="To Be Deleted")
        page.wait_for_selector(".note-item", timeout=TIMEOUT_MS)

        page.once("dialog", lambda d: d.accept())
        page.click("#btn-delete-note")

        expect(page.locator("#notes-list")).to_contain_text("No notes yet", timeout=TIMEOUT_MS)

    def test_delete_note_hides_editor(self, page):
        _create_note(page)
        page.once("dialog", lambda d: d.accept())
        page.click("#btn-delete-note")
        page.wait_for_function(
            "getComputedStyle(document.getElementById('editor-wrapper')).display === 'none'",
            timeout=TIMEOUT_MS,
        )

    def test_multiple_notes_all_appear_in_list(self, page):
        for i in range(3):
            _create_note(page, title=f"Note {i}", content=f"Content {i}")
        items = page.locator(".note-item").count()
        assert items == 3, f"Expected 3 note items, got {items}"

    def test_selecting_note_loads_correct_content(self, page):
        _create_note(page, title="Alpha Note", content="Alpha body")
        _create_note(page, title="Beta Note", content="Beta body")

        page.locator(".note-item").first.click()
        title_val = page.input_value("#note-title")
        body_val = page.input_value("#note-body")
        assert title_val in ("Alpha Note", "Beta Note"), "Editor should load the clicked note"
        assert body_val in ("Alpha body", "Beta body")

class TestPersistence:
    """Notes survive a page reload via AES-GCM encryption."""

    def test_note_persists_across_reload(self, page, base_url):
        _create_note(page, title="Persistent Note", content="Remember me")
        _wait_for_save(page)

        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)

        titles = page.locator(".note-item-title").all_text_contents()
        assert "Persistent Note" in titles, "Note should survive a page reload"

    def test_note_content_persists_across_reload(self, page, base_url):
        _create_note(page, title="Content Test", content="This is my persisted body")
        _wait_for_save(page)

        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)

        page.locator(".note-item").filter(has_text="Content Test").click()
        body = page.input_value("#note-body")
        assert body == "This is my persisted body"

    def test_multiple_notes_all_persist(self, page, base_url):
        for i in range(4):
            _create_note(page, title=f"Persist {i}", content=f"Body {i}")
        _wait_for_save(page)

        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)

        count = page.locator(".note-item").count()
        assert count == 4, f"All 4 notes should persist; got {count}"

    def test_new_session_cannot_decrypt_old_ciphertext(self, browser_instance, base_url):
        """
        A new browser context generates a new passphrase, so it should NOT be
        able to decrypt notes saved by a previous context.  The app must
        silently fall back to an empty notes list rather than crash or expose
        garbled data.
        """
        ctx_a = browser_instance.new_context()
        pg_a = ctx_a.new_page()
        pg_a.goto(f"{base_url}/notes", wait_until="domcontentloaded")
        pg_a.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)
        _create_note(pg_a, title="Secret from A", content="Top secret")
        _wait_for_save(pg_a)

        ciphertext = pg_a.evaluate(f"localStorage.getItem('{STORAGE_KEY}')")
        ctx_a.close()

        ctx_b = browser_instance.new_context()
        pg_b = ctx_b.new_page()
       
        pg_b.goto(f"{base_url}/notes", wait_until="domcontentloaded")
        pg_b.evaluate(f"localStorage.setItem('{STORAGE_KEY}', {repr(ciphertext)})")
  
        pg_b.reload(wait_until="domcontentloaded")
        pg_b.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)

        count = pg_b.locator(".note-item").count()
        assert count == 0, (
            "A new session with a different passphrase must not decrypt "
            "another session's notes — expected 0 notes, got " + str(count)
        )

        assert pg_b.errors == [], f"Unexpected JS errors in Session B: {pg_b.errors}"
        ctx_b.close()


class TestAISummarise:
    """summarize() extracts the most relevant sentences from a note."""

    def _run_summarise(self, page, content: str) -> str:
        _create_note(page, title="Summarise Test", content=content)
        page.click("#btn-summarize")
        page.wait_for_function(
            "document.getElementById('ai-output').textContent.startsWith('📝')",
            timeout=TIMEOUT_MS,
        )
        return page.locator("#ai-output").text_content() or ""

    def test_summarise_output_starts_with_emoji(self, page):
        result = self._run_summarise(
            page,
            "This is the first sentence. Here is another one. And a third. A fourth follows.",
        )
        assert result.startswith("📝 Summary:")

    def test_summarise_returns_at_most_three_sentences(self, page):
        text = ". ".join([f"Sentence number {i}" for i in range(10)]) + "."
        result = self._run_summarise(page, text)
     
        body = result.replace("📝 Summary:\n", "")
        sentence_count = len([s for s in body.split(".") if s.strip()])
        assert sentence_count <= 3, f"Expected ≤ 3 sentences in summary, got {sentence_count}"

    def test_summarise_empty_note_shows_placeholder(self, page):
        _create_note(page, title="Empty", content="")
        page.click("#btn-summarize")
        page.wait_for_function(
            "document.getElementById('ai-output').textContent !== ''",
            timeout=TIMEOUT_MS,
        )
        result = page.locator("#ai-output").text_content()
        assert "no content" in (result or "").lower()

    def test_summarise_single_sentence(self, page):
        result = self._run_summarise(page, "Only one sentence here.")
        assert "Only one sentence here" in result

class TestAIKeyPoints:
    """extractKeyPoints() surfaces lines containing action/importance keywords."""

    def _run_keypoints(self, page, content: str) -> str:
        _create_note(page, title="Key Points Test", content=content)
        page.click("#btn-keypoints")
        page.wait_for_function(
            "document.getElementById('ai-output').textContent.startsWith('🔑')",
            timeout=TIMEOUT_MS,
        )
        return page.locator("#ai-output").text_content() or ""

    def test_keypoints_output_starts_with_emoji(self, page):
        result = self._run_keypoints(page, "We must encrypt all data.\nThis is important.\nEnd.")
        assert result.startswith("🔑 Key Points:")

    def test_keypoints_detects_must_keyword(self, page):
        result = self._run_keypoints(page, "We must ship this feature by Friday.")
        assert "must" in result.lower()

    def test_keypoints_detects_encrypt_keyword(self, page):
        result = self._run_keypoints(page, "All files must be encrypted before upload.")
        assert "encrypt" in result.lower()

    def test_keypoints_falls_back_to_first_lines_when_no_keywords(self, page):
        content = "\n".join([f"Ordinary line {i} with no special words." for i in range(6)])
        result = self._run_keypoints(page, content)
       
        assert "🔑 Key Points:" in result
        assert "Ordinary line" in result

    def test_keypoints_limits_output_to_seven_items(self, page):
        lines = "\n".join([f"We must do item {i}." for i in range(15)])
        result = self._run_keypoints(page, lines)
        bullet_count = result.count("•")
        assert bullet_count <= 7, f"Key points should be capped at 7; got {bullet_count}"

    def test_keypoints_empty_content_shows_placeholder(self, page):
        _create_note(page, title="Empty KP", content="")
        page.click("#btn-keypoints")
        page.wait_for_function(
            "document.getElementById('ai-output').textContent !== ''", timeout=TIMEOUT_MS
        )
        result = page.locator("#ai-output").text_content()
        assert "no content" in (result or "").lower()


class TestAIActionItems:
    """extractActionItems() identifies lines with task / action language."""

    def _run_actions(self, page, content: str) -> str:
        _create_note(page, title="Action Items Test", content=content)
        page.click("#btn-actions")
        page.wait_for_function(
            "document.getElementById('ai-output').textContent.startsWith('✅')",
            timeout=TIMEOUT_MS,
        )
        return page.locator("#ai-output").text_content() or ""

    def test_action_items_output_starts_with_emoji(self, page):
        result = self._run_actions(page, "Todo: buy groceries.\nReview the quarterly report.")
        assert result.startswith("✅ Action Items:")

    def test_action_items_detects_todo_keyword(self, page):
        result = self._run_actions(page, "Todo: finish the report by Monday.")
        assert "Todo" in result or "todo" in result.lower()

    def test_action_items_detects_deadline_keyword(self, page):
        result = self._run_actions(page, "Deadline for submission is next Friday.")
        assert "Deadline" in result or "deadline" in result.lower()

    def test_action_items_detects_follow_up_keyword(self, page):
        result = self._run_actions(page, "Follow up with the client after the demo.")
        assert "Follow up" in result or "follow" in result.lower()

    def test_no_action_items_shows_helpful_tip(self, page):
        result = self._run_actions(page, "The weather is nice today. Nothing to do here.")
        assert "No explicit action items" in result
        assert "todo" in result.lower() or "tip" in result.lower()

    def test_action_items_capped_at_ten(self, page):
        lines = "\n".join([f"Todo: action item number {i}" for i in range(15)])
        result = self._run_actions(page, lines)
        bullet_count = result.count("•")
        assert bullet_count <= 10, f"Action items should be capped at 10; got {bullet_count}"


class TestAIWordFrequency:
    """wordFrequency() returns the top-10 non-stopword keywords."""

    def _run_freq(self, page, content: str) -> str:
        _create_note(page, title="Frequency Test", content=content)
        page.click("#btn-keywords")
        page.wait_for_function(
            "document.getElementById('ai-output').textContent.startsWith('📊')",
            timeout=TIMEOUT_MS,
        )
        return page.locator("#ai-output").text_content() or ""

    def test_frequency_output_starts_with_emoji(self, page):
        result = self._run_freq(page, "security security encryption encryption encryption")
        assert result.startswith("📊 Top Keywords:")

    def test_most_frequent_word_appears_first(self, page):
        content = " ".join(["encryption"] * 10 + ["privacy"] * 3 + ["security"] * 1)
        result = self._run_freq(page, content)
        lines = [l.strip() for l in result.split("\n") if l.strip().startswith("•")]
        assert lines, "Should have at least one bullet point"
        assert "encryption" in lines[0], f"Most frequent word should be first; got: {lines[0]}"

    def test_stopwords_are_excluded(self, page):

        content = "the the the and and and or or is is was was"
        result = self._run_freq(page, content)
       
        for sw in ("the", "and", "or", "is", "was"):
            assert f"• {sw} " not in result, f"Stopword '{sw}' should not appear in freq output"

    def test_capped_at_ten_keywords(self, page):
   
        words = " ".join([f"uniqueword{i}word{i}" * 2 for i in range(15)])
        result = self._run_freq(page, words)
        bullet_count = result.count("•")
        assert bullet_count <= 10, f"Word frequency should show at most 10 keywords; got {bullet_count}"

    def test_empty_content_shows_placeholder(self, page):
        _create_note(page, title="Empty Freq", content="")
        page.click("#btn-keywords")
        page.wait_for_function(
            "document.getElementById('ai-output').textContent !== ''", timeout=TIMEOUT_MS
        )
        result = page.locator("#ai-output").text_content()
        assert "no content" in (result or "").lower() or "no significant" in (result or "").lower()

    def test_minimum_word_length_three_chars(self, page):
     
        content = "aa bb cc encryption encryption encryption"
        result = self._run_freq(page, content)
        assert "• aa" not in result
        assert "• bb" not in result
        assert "• cc" not in result

class TestExport:
    """Export buttons trigger a file download with the correct MIME type."""

    def _trigger_export_and_capture(self, page, btn_id: str):
        """Click an export button and return the Download object."""
        with page.expect_download(timeout=TIMEOUT_MS) as dl_info:
            page.click(btn_id)
        return dl_info.value

    def test_export_txt_triggers_download(self, page):
        _create_note(page, title="Export TXT", content="Some content")
        dl = self._trigger_export_and_capture(page, "#btn-export-txt")
        assert dl.suggested_filename.endswith(".txt"), (
            f"Expected .txt download, got {dl.suggested_filename}"
        )

    def test_export_md_triggers_download(self, page):
        _create_note(page, title="Export MD", content="Markdown content")
        dl = self._trigger_export_and_capture(page, "#btn-export-md")
        assert dl.suggested_filename.endswith(".md"), (
            f"Expected .md download, got {dl.suggested_filename}"
        )

    def test_export_json_triggers_download(self, page):
        _create_note(page, title="Export JSON", content="JSON content")
        dl = self._trigger_export_and_capture(page, "#btn-export-json")
        assert dl.suggested_filename.endswith(".json"), (
            f"Expected .json download, got {dl.suggested_filename}"
        )

    def test_export_md_contains_title(self, page):
        _create_note(page, title="My MD Title", content="Body text")
        with page.expect_download(timeout=TIMEOUT_MS) as dl_info:
            page.click("#btn-export-md")
        dl = dl_info.value
        content = dl.path().read_text()
        assert "My MD Title" in content, "Markdown export should contain the note title"

    def test_export_md_contains_body(self, page):
        _create_note(page, title="MD Body Test", content="Exported body line")
        with page.expect_download(timeout=TIMEOUT_MS) as dl_info:
            page.click("#btn-export-md")
        dl = dl_info.value
        content = dl.path().read_text()
        assert "Exported body line" in content

    def test_export_json_is_valid_json(self, page):
        import json
        _create_note(page, title="JSON Valid", content="Check structure")
        with page.expect_download(timeout=TIMEOUT_MS) as dl_info:
            page.click("#btn-export-json")
        dl = dl_info.value
        content = dl.path().read_text()
        data = json.loads(content)  
        assert "title" in data or "notes" in data, "JSON export should have expected structure"

    def test_export_all_notes_triggers_download(self, page):
        _create_note(page, title="Note A")
        _create_note(page, title="Note B")
        dl = self._trigger_export_and_capture(page, "#btn-export-all")
        assert dl.suggested_filename.endswith(".json")

    def test_export_all_contains_all_notes(self, page):
        import json
        for t in ("Alpha", "Beta", "Gamma"):
            _create_note(page, title=t)
        with page.expect_download(timeout=TIMEOUT_MS) as dl_info:
            page.click("#btn-export-all")
        dl = dl_info.value
        data = json.loads(dl.path().read_text())
        assert "notes" in data
        titles = [n["title"] for n in data["notes"]]
        for t in ("Alpha", "Beta", "Gamma"):
            assert t in titles, f"Export-all missing note '{t}'"


class TestMultiNoteManagement:
    """Ordering, active-note selection, and list preview truncation."""

    def test_newest_note_appears_at_top_of_list(self, page):
        _create_note(page, title="First Note")
        _create_note(page, title="Second Note")
        _create_note(page, title="Third Note")

        first_title = page.locator(".note-item-title").first.text_content()
        assert first_title == "Third Note", (
            f"Most recently created note should be first; got '{first_title}'"
        )

    def test_note_preview_is_truncated_to_60_chars(self, page):
        long_content = "A" * 120
        _create_note(page, title="Truncation Test", content=long_content)
        preview = page.locator(".note-item.active .note-item-preview").text_content() or ""
        assert len(preview) <= 60, (
            f"Preview should be ≤ 60 chars; got {len(preview)}"
        )

    def test_active_note_has_active_class(self, page):
        _create_note(page, title="Active Note")
        active_count = page.locator(".note-item.active").count()
        assert active_count == 1, "Exactly one note should be active at a time"

    def test_clicking_different_note_switches_active(self, page):
        _create_note(page, title="Note One", content="Body One")
        _create_note(page, title="Note Two", content="Body Two")

        page.locator(".note-item").nth(1).click()
        active_title = page.input_value("#note-title")
        assert active_title == "Note One"

    def test_deleting_one_note_keeps_others(self, page):
        _create_note(page, title="Keep Me")
        _create_note(page, title="Delete Me") 

        page.once("dialog", lambda d: d.accept())
        page.click("#btn-delete-note")

        titles = page.locator(".note-item-title").all_text_contents()
        assert "Keep Me" in titles
        assert "Delete Me" not in titles


class TestWordCountWidget:
    """The live word / char counter updates as the user types."""

    def test_word_count_shows_zero_for_empty_note(self, page):
        _create_note(page, title="WC Test", content="")
        wc = page.locator("#word-count").text_content() or ""
        assert "0 words" in wc

    def test_word_count_reflects_content(self, page):
        _create_note(page, title="WC Content", content="one two three four five")
        page.dispatch_event("#note-body", "input")
        wc = page.locator("#word-count").text_content() or ""
        assert "5 words" in wc

    def test_char_count_reflects_content(self, page):
        content = "hello"
        _create_note(page, title="Char Test", content=content)
        page.dispatch_event("#note-body", "input")
        wc = page.locator("#word-count").text_content() or ""
        assert f"{len(content)} chars" in wc

    def test_word_count_updates_on_typing(self, page):
        _create_note(page, title="Typing Test", content="one")
        page.dispatch_event("#note-body", "input")
        before = page.locator("#word-count").text_content() or ""

        page.fill("#note-body", "one two")
        page.dispatch_event("#note-body", "input")
        after = page.locator("#word-count").text_content() or ""

        assert before != after, "Word count should change after adding words"
        assert "2 words" in after

class TestEncryptionIsolation:
    """Different sessions cannot read each other's encrypted notes."""

    def test_corrupt_ciphertext_in_localstorage_causes_graceful_fallback(
        self, browser_instance, base_url
    ):
        """Tampered ciphertext must never crash the app — fall back to empty list."""
        ctx = browser_instance.new_context()
        pg = ctx.new_page()
        pg.goto(f"{base_url}/notes", wait_until="domcontentloaded")
        pg.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)

        pg.evaluate(
            f"localStorage.setItem('{STORAGE_KEY}', "
            "JSON.stringify({iv:'aaaa', ciphertext:'notvalidbase64!!!'}))"
        )
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)

        count = pg.locator(".note-item").count()
        assert count == 0, "Corrupted ciphertext should result in empty notes list"
        assert pg.errors == [], f"Unexpected JS errors with corrupted ciphertext: {pg.errors}"
        ctx.close()

    def test_empty_localstorage_shows_empty_state(self, page):
        page.evaluate(f"localStorage.removeItem('{STORAGE_KEY}')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)
        expect(page.locator("#notes-list")).to_contain_text("No notes yet", timeout=TIMEOUT_MS)


class TestDebounce:
    """Rapid typing should not result in data loss or corruption."""

    def test_rapid_typing_saves_final_content(self, page, base_url):
        _create_note(page, title="Debounce Test", content="")

        body = page.locator("#note-body")
        final_text = "rapid typing test final value"
        body.fill(final_text)
        page.dispatch_event("#note-body", "input")

        _wait_for_save(page)

        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("typeof NotesApp !== 'undefined'", timeout=TIMEOUT_MS)

        page.locator(".note-item").filter(has_text="Debounce Test").click()
        saved_body = page.input_value("#note-body")
        assert saved_body == final_text, (
            f"Expected '{final_text}' after reload; got '{saved_body}'"
        )
        class TestEmptyState:
    """Graceful behaviour when there are no notes."""
    def test_ai_buttons_do_nothing_when_no_note_selected(self, page):
        """Clicking AI buttons without an active note should show a warning toast."""

        for btn_id in ("#btn-summarize", "#btn-keypoints", "#btn-actions", "#btn-keywords"):
            page.click(btn_id)
   
            output = page.locator("#ai-output").text_content() or ""
            assert output == "", (
                f"{btn_id} should not populate ai-output when no note is selected"
            )

    def test_delete_button_does_nothing_when_no_note(self, page):
        """Clicking Delete with no active note must not throw a JS error."""
        page.click("#btn-delete-note")
        assert page.errors == [], f"Delete with no note caused JS errors: {page.errors}"
