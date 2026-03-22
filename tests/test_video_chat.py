"""
E2E test: three browser clients join the same video-chat room and each
client must see the other two participants' camera streams.

How it works
------------
1. A lightweight Python HTTP server is started locally to serve the app
   pages (src/pages/) and static assets (public/).
2. Three separate Chromium browser instances are launched with fake
   media flags so no real camera or microphone is required.
3. Client 2 connects to Client 1.  Both consent dialogs are accepted.
4. Client 3 connects to Client 1.  Client 3's consent dialog is accepted.
   The full-mesh data channel then automatically bridges Client 3 to
   Client 2 (no extra consent needed as both already consented).
5. The test asserts that every client ends up with exactly 3 video
   wrappers (1 local + 2 remote) and that each remote <video> element
   has a live srcObject attached.

The test depends on the public PeerJS cloud server (0.peerjs.com) for
WebRTC signalling, which is available in GitHub Actions runners.
"""

import http.server
import socketserver
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent

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

# Generous timeout for WebRTC operations that go through an external server
TIMEOUT_MS = 60_000

# Chromium flags that enable fake camera/microphone without real hardware
_BROWSER_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-input",
    "--no-sandbox",
    "--disable-setuid-sandbox",
]


class _AppHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler that serves app pages and public assets."""

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]

        # Serve HTML pages
        if path in _PAGES:
            data = (ROOT / _PAGES[path]).read_bytes()
            self._respond(200, "text/html; charset=utf-8", data)
            return

        # Serve static files from public/
        public_root = (ROOT / "public").resolve()
        candidate = (public_root / path.lstrip("/")).resolve()
        # Ensure the resolved path is within the public/ directory to avoid traversal
        if candidate.is_relative_to(public_root) and candidate.is_file():
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


@pytest.fixture(scope="module")
def base_url():
    """Start a local HTTP server and return its base URL."""
    server = socketserver.TCPServer(("127.0.0.1", 0), _AppHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


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
        b1 = pw.chromium.launch(args=_BROWSER_ARGS)
        b2 = pw.chromium.launch(args=_BROWSER_ARGS)
        b3 = pw.chromium.launch(args=_BROWSER_ARGS)
        try:
            ctx1 = b1.new_context(permissions=["camera", "microphone"])
            ctx2 = b2.new_context(permissions=["camera", "microphone"])
            ctx3 = b3.new_context(permissions=["camera", "microphone"])

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

            # ── Step 4: Verify live camera streams ───────────────────────────
            assert p1.evaluate(_STREAM_CHECK_JS), (
                "Client 1 should see live streams from both other participants"
            )
            assert p2.evaluate(_STREAM_CHECK_JS), (
                "Client 2 should see live streams from both other participants"
            )
            assert p3.evaluate(_STREAM_CHECK_JS), (
                "Client 3 should see live streams from both other participants"
            )
        finally:
            b1.close()
            b2.close()
            b3.close()
