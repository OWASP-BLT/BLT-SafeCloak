"""
E2E test: three browser clients join the same video-chat room and each
client must see the other two participants' camera streams.

How it works
------------
1. A lightweight Python HTTP server is started locally to serve the app
   pages (src/pages/) and static assets (public/).
2. A local PeerJS signaling server (``peerjs`` npm CLI) is started so
   the test does not rely on the external 0.peerjs.com cloud service.
3. The test server patches the served video-chat HTML to:
   a. Replace the unpkg.com CDN URL for peerjs.min.js with a locally
      cached copy (downloaded once to /tmp at module import time).
   b. Inject a thin override that redirects ``new Peer(...)`` to the
      local signaling server.
4. A single Chromium browser is launched with fake media flags; three
   isolated browser contexts are created from it so no real camera or
   microphone is required.
5. Client 2 connects to Client 1.  Both consent dialogs are accepted.
6. Client 3 connects to Client 1.  Client 3's consent dialog is accepted.
   The full-mesh data channel then automatically bridges Client 3 to
   Client 2 (no extra consent needed as both already consented).
7. The test waits for each remote <video> to have a live srcObject and
   then asserts the condition, avoiding any race between wrapper creation
   and stream attachment.
"""

import http.server
import re
import socket
import socketserver
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent

_PEERJS_CDN_URL = "https://unpkg.com/peerjs@1.5.2/dist/peerjs.min.js"
_PEERJS_CACHE = Path("/tmp/peerjs_test_vendor.min.js")
_PEERJS_MIN_SIZE = 50_000  # peerjs.min.js is ~250 KB; reject obviously bad downloads

# Seconds to wait for the local PeerJS server to become ready
_PEERJS_STARTUP_TIMEOUT = 6


def _get_peerjs_js() -> bytes:
    """Return peerjs.min.js bytes, downloading from the CDN if not cached.

    A basic size sanity-check guards against caching an empty or truncated
    download from the CDN.
    """
    if _PEERJS_CACHE.exists():
        cached = _PEERJS_CACHE.read_bytes()
        if len(cached) >= _PEERJS_MIN_SIZE:
            return cached
        # Cached copy is suspiciously small – delete and re-download
        _PEERJS_CACHE.unlink()
    with urllib.request.urlopen(_PEERJS_CDN_URL, timeout=30) as resp:
        data = resp.read()
    if len(data) < _PEERJS_MIN_SIZE:
        raise RuntimeError(
            f"Downloaded peerjs.min.js is too small ({len(data)} bytes); "
            "the CDN may have returned an error page."
        )
    _PEERJS_CACHE.write_bytes(data)
    return data


def _free_port() -> int:
    """Return an unused TCP port on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

# Map clean URL paths to HTML source files
_PAGES = {
    "/": "src/pages/index.html",
    "/video-chat": "src/pages/video-chat.html",
    "/notes": "src/pages/notes.html",
    "/consent": "src/pages/consent.html",
}

# MIME types for static asset extensions
_MIME = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

# Generous timeout for WebRTC operations (all local after patching)
TIMEOUT_MS = 60_000

# Chromium flags that enable fake camera/microphone without real hardware
_BROWSER_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-input",
    "--no-sandbox",
    "--disable-setuid-sandbox",
]

# Regex that matches the peerjs CDN <script> tag (spans multiple lines)
_PEERJS_SCRIPT_RE = re.compile(
    r'<script\s[^>]*src="https://unpkg\.com/peerjs[^"]*"[^>]*>.*?</script>',
    re.DOTALL,
)


class _AppHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler that serves app pages and public assets.

    Two class-level attributes are populated by the ``base_url`` fixture
    before the server starts:

    * ``peerjs_port`` – port of the local PeerJS signaling server
    * ``peerjs_js``   – bytes of peerjs.min.js (served at /peerjs.min.js)
    """

    peerjs_port: int = 0
    peerjs_js: bytes = b""

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]

        # Serve the locally cached peerjs.min.js
        if path == "/peerjs.min.js":
            self._respond(200, "application/javascript", self.__class__.peerjs_js)
            return

        # Serve HTML pages (video-chat is patched to use local signaling)
        if path in _PAGES:
            data = (ROOT / _PAGES[path]).read_bytes()
            if path == "/video-chat":
                data = _patch_video_chat_html(data, self.__class__.peerjs_port)
            self._respond(200, "text/html; charset=utf-8", data)
            return

        # Serve static files from public/
        public_root = (ROOT / "public").resolve()
        # Resolve the candidate to eliminate any ".." segments and follow
        # symlinks, then confirm it still lives under public_root.
        candidate = (public_root / path.lstrip("/")).resolve()
        # Guard against path traversal
        try:
            candidate.relative_to(public_root)
        except ValueError:
            self._respond(404, "text/plain", b"Not found")
            return
        if candidate.is_file():
            data = candidate.read_bytes()
            ct = _MIME.get(candidate.suffix, "application/octet-stream")
            self._respond(200, ct, data)
            return

        self._respond(404, "text/plain", b"Not found")

    def _respond(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # silence request logs
        pass


def _patch_video_chat_html(data: bytes, peerjs_port: int) -> bytes:
    """Rewrite video-chat HTML to use a local PeerJS server.

    Replaces the unpkg.com CDN script tag with a local ``/peerjs.min.js``
    reference, then injects a thin wrapper that overrides the ``Peer``
    constructor to point at the local signaling server instead of
    ``0.peerjs.com``.
    """
    override = (
        '<script src="/peerjs.min.js"></script>\n'
        "    <script>\n"
        "    (function () {\n"
        "      var _P = window.Peer;\n"
        "      window.Peer = class extends _P {\n"
        "        constructor(id, opts) {\n"
        "          super(id, Object.assign({}, opts, {\n"
        f"            host: '127.0.0.1', port: {peerjs_port},\n"
        "            path: '/', secure: false, key: 'peerjs'\n"
        "          }));\n"
        "        }\n"
        "      };\n"
        "    })();\n"
        "    </script>"
    )
    html = data.decode("utf-8")
    html = _PEERJS_SCRIPT_RE.sub(override, html)
    return html.encode("utf-8")


@pytest.fixture(scope="module")
def peerjs_server():
    """Start a local PeerJS signaling server and yield its port number."""
    port = _free_port()
    proc = subprocess.Popen(
        ["peerjs", "--port", str(port), "--path", "/"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait until the server is ready (max ~_PEERJS_STARTUP_TIMEOUT s)
    deadline = time.monotonic() + _PEERJS_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("Local PeerJS server did not start in time")
    try:
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def base_url(peerjs_server):
    """Start a local HTTP server and return its base URL."""
    _AppHandler.peerjs_js = _get_peerjs_js()
    _AppHandler.peerjs_port = peerjs_server
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _AppHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _peer_id(page) -> str:
    """Block until the peer ID has been assigned and return it."""
    page.wait_for_function(
        "document.getElementById('my-peer-id').textContent.trim() !== 'Connecting...'",
        timeout=TIMEOUT_MS,
    )
    return page.evaluate("document.getElementById('my-peer-id').textContent.trim()")


def _accept_consent(page, timeout: int = TIMEOUT_MS):
    """Wait for the consent dialog to appear and click 'I Consent'."""
    page.wait_for_selector("#consent-allow", timeout=timeout)
    page.click("#consent-allow")


_STREAM_CHECK_JS = """
() => {
    const wrappers = Array.from(document.querySelectorAll('.video-wrapper'));
    const remotes = wrappers.slice(1);   // skip the local tile
    return (
        remotes.length === 2 &&
        remotes.every(w => {
            const v = w.querySelector('video');
            return v !== null && v.srcObject !== null;
        })
    );
}
"""


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_three_clients_connect_and_see_cameras(base_url):
    """
    Three clients join the same room.  Assert each client can see the
    other two participants' camera streams (srcObject != null).
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=_BROWSER_ARGS)
        try:
            ctx1 = browser.new_context(permissions=["camera", "microphone"])
            ctx2 = browser.new_context(permissions=["camera", "microphone"])
            ctx3 = browser.new_context(permissions=["camera", "microphone"])

            p1 = ctx1.new_page()
            p2 = ctx2.new_page()
            p3 = ctx3.new_page()

            video_url = f"{base_url}/video-chat"
            for page in (p1, p2, p3):
                page.goto(video_url)

            # ── Collect peer IDs ─────────────────────────────────────────────
            id1 = _peer_id(p1)
            id2 = _peer_id(p2)
            id3 = _peer_id(p3)
            assert id1 and id2 and id3, "All clients must receive a peer ID"
            assert len({id1, id2, id3}) == 3, "All peer IDs must be unique"

            # ── Step 1: Client 2 calls Client 1 ─────────────────────────────
            # callPeer shows a consent dialog on the *caller* before dialling.
            p2.fill("#remote-id", id1)
            p2.click("#btn-call")
            _accept_consent(p2)  # p2 consents (caller side)
            _accept_consent(p1)  # p1 consents (callee side)

            # Wait for the p1–p2 connection to be fully established
            p1.wait_for_function(
                "document.querySelectorAll('.video-wrapper').length >= 2",
                timeout=TIMEOUT_MS,
            )
            p2.wait_for_function(
                "document.querySelectorAll('.video-wrapper').length >= 2",
                timeout=TIMEOUT_MS,
            )

            # ── Step 2: Client 3 calls Client 1 ─────────────────────────────
            # After this call is answered, Client 1 sends Client 3 the
            # existing peer list [id2] via the data channel, and Client 3
            # automatically calls Client 2 to complete the full mesh.
            # (Both p1 and p2 already have consentGiven=true at this point.)
            p3.fill("#remote-id", id1)
            p3.click("#btn-call")
            _accept_consent(p3)  # p3 consents (caller side)
            # p1 already has consentGiven=true → no dialog

            # ── Step 3: Wait for full mesh ───────────────────────────────────
            # Every client should have 3 video wrappers: 1 local + 2 remote.
            for page in (p1, p2, p3):
                page.wait_for_function(
                    "document.querySelectorAll('.video-wrapper').length >= 3",
                    timeout=TIMEOUT_MS,
                )

            # ── Step 4: Wait for and verify live camera streams ──────────────
            # `handleCallStream` creates the wrapper before stream arrives, so
            # we must wait (not just assert) to avoid a race with srcObject
            # assignment.
            for page, name in ((p1, "Client 1"), (p2, "Client 2"), (p3, "Client 3")):
                page.wait_for_function(_STREAM_CHECK_JS, timeout=TIMEOUT_MS)
                assert page.evaluate(_STREAM_CHECK_JS), (
                    f"{name} should see live streams from both other participants"
                )
        finally:
            browser.close()
