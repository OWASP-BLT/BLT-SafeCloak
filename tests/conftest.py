import os
import re
import socket
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import pytest

# JavaScript shim to mock media hardware in headless CI to ensure E2E stability.
_MOCK_GET_USER_MEDIA = """
(function () {
  var _orig = navigator.mediaDevices && navigator.mediaDevices.getUserMedia ? navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices) : null;
  async function _fakeStream(constraints) {
    var stream = new MediaStream();
    if (!constraints || constraints.video !== false) {
      try {
        var canvas = document.createElement("canvas");
        canvas.width = 320; canvas.height = 240;
        canvas.getContext("2d").fillRect(0, 0, 320, 240);
        canvas.captureStream(10).getVideoTracks().forEach(t => stream.addTrack(t));
      } catch (e) {}
    }
    if (!constraints || constraints.audio !== false) {
      try {
        var ac = new AudioContext(), osc = ac.createOscillator(), dest = ac.createMediaStreamDestination();
        osc.connect(dest); osc.start();
        dest.stream.getAudioTracks().forEach(t => stream.addTrack(t));
      } catch (e) {}
    }
    return stream;
  }
  if (navigator.mediaDevices) {
    navigator.mediaDevices.getUserMedia = async function (constraints) {
      if (_orig) { try { return await _orig(constraints); } catch (e) {} }
      return _fakeStream(constraints);
    };
  }
})();
"""


def _get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: int = 10):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection(("localhost", port), timeout=0.5):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.1)
    return False


@pytest.fixture(scope="session")
def peerjs_server():
    """Start a local PeerJS signaling server with robust lifecycle management."""
    port = _get_free_port()
    # Resolve npx executable explicitly to avoid shell=True inconsistencies (Issue 8)
    npx_exe = shutil.which("npx") or "npx"
    if os.name == "nt" and not npx_exe.lower().endswith((".cmd", ".exe", ".bat")):
        npx_exe += ".cmd"

    cmd = [npx_exe, "--yes", "-p", "peer", "peerjs", "--port", str(port)]
    process = subprocess.Popen(cmd,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               shell=(os.name == "nt"))

    if not _wait_for_port(port, timeout=15):
        process.terminate()
        try:
            process.wait(timeout=5)
        except:
            pass
        raise RuntimeError(f"PeerJS signaling server failed to start on port {port}.")

    yield port

    # Robust teardown
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
        else:
            process.kill()


@pytest.fixture(scope="session")
def patched_app_dir(peerjs_server):
    """Create a temporary directory with patched HTML files for zero-network testing."""
    repo_root = Path(__file__).resolve().parents[1]
    src_pages = repo_root / "src" / "pages"

    tmp_dir = tempfile.mkdtemp(prefix="safecloak_tests_")
    tmp_path = Path(tmp_dir)

    try:
        # Copy all files from src/pages to tmp_dir
        for item in src_pages.iterdir():
            if item.is_file():
                shutil.copy2(item, tmp_path / item.name)

        # Patch HTML files
        for html_name in ["index.html", "video-room.html"]:
            html_path = tmp_path / html_name
            if not html_path.exists():
                continue

            content = html_path.read_text(encoding="utf-8")

            # 1. Inject local PeerJS config (Issue 6)
            cfg_stub = f"<script>window.__PEERJS_CONFIG__ = {{host:'localhost', port:{peerjs_server}, secure:false}};</script>"
            content = content.replace("</head>", f"{cfg_stub}\n  </head>")

            # 2. Patch PeerJS CDN to local vendor path
            content = re.sub(r"https://unpkg\.com/peerjs@[^/]+/dist/peerjs\.min\.js",
                             "vendor/peerjs.min.js", content)

            # 3. Strip external CDN assets for offline stability (Issue 7)
            content = re.sub(
                r'<script\b[^>]*\bsrc=["\']https://cdn\.tailwindcss\.com["\'][^>]*>\s*</script>\s*',
                "",
                content,
                flags=re.I)
            content = re.sub(r'<script\b[^>]*>\s*tailwind\.config\s*=\s*\{.*?\}\s*</script>\s*',
                             "",
                             content,
                             flags=re.I | re.S)
            content = re.sub(
                r'<link\b[^>]*\bhref=["\']https://fonts\.(googleapis|gstatic)\.com/[^"\']*["\'][^>]*>\s*',
                "",
                content,
                flags=re.I)
            content = re.sub(
                r'<link\b[^>]*\bhref=["\']https://cdnjs\.cloudflare\.com/ajax/libs/font-awesome/[^"\']*["\'][^>]*>\s*',
                "",
                content,
                flags=re.I)

            html_path.write_text(content, encoding="utf-8")

        yield tmp_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class _AppHandler(SimpleHTTPRequestHandler):
    """Secure app handler that serves from the virtual filesystem and public assets."""
    base_dir: Path = None
    patched_dir: Path = None

    def translate_path(self, path):
        """Map URL paths to filesystem paths, prioritizing the virtual filesystem."""
        clean = path.split("?")[0].lstrip("/")

        # Security: Prevent path traversal
        if ".." in clean.replace("\\", "/").split("/"):
            return str(self.patched_dir / "index.html")

        # Map routes to patched HTML files
        if clean == "" or clean == "index":
            return str(self.patched_dir / "index.html")
        if clean == "video-room":
            return str(self.patched_dir / "video-room.html")

        # Map vendored PeerJS
        if clean == "vendor/peerjs.min.js":
            return str(self.base_dir / "tests" / "vendor" / "peerjs.min.js")

        # Prioritize public assets (css/js/img)
        pub = self.base_dir / "public" / clean
        if pub.exists():
            return str(pub)

        # Fallback to general HTML mapping in patched dir
        cand = self.patched_dir / f"{clean}.html"
        if cand.exists(): return str(cand)
        if clean.endswith(".html"):
            cand = self.patched_dir / clean
            if cand.exists(): return str(cand)

        return super().translate_path(path)


@pytest.fixture(scope="session")
def base_url(peerjs_server, patched_app_dir):
    """Initialize the app server using the virtual filesystem."""
    port = _get_free_port()
    _AppHandler.base_dir = Path(__file__).resolve().parents[1]
    _AppHandler.patched_dir = patched_app_dir

    server = ThreadingHTTPServer(("localhost", port), _AppHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    yield f"http://localhost:{port}"
    server.shutdown()


@pytest.fixture
def new_context(browser):
    """Fixture to provide a Playwright browser context with hardware mocks."""

    def _factory():
        ctx = browser.new_context(permissions=["camera", "microphone"])
        ctx.add_init_script(_MOCK_GET_USER_MEDIA)
        return ctx

    return _factory
