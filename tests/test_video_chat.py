"""
E2E test: three browser clients join the same video-chat room and each
client must see the other two participants' camera streams.

This test relies on the shared fixtures in tests/conftest.py which start
a local PeerJS signaling server and a patched Python app server.
"""

from pathlib import Path
import re
import pytest

# ROOT path for file-based checks
ROOT = Path(__file__).parent.parent

# Generous timeout for WebRTC operations (all local after patching).
TIMEOUT_MS = 120_000


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
def _peer_id(page) -> str:
    """Block until the peer ID has been assigned and return it."""
    page.wait_for_function(
        "document.getElementById('my-peer-id') && "
        "document.getElementById('my-peer-id').textContent.trim() !== '' && "
        "document.getElementById('my-peer-id').textContent.trim() !== 'Connecting...'",
        timeout=TIMEOUT_MS,
    )
    return page.evaluate("document.getElementById('my-peer-id').textContent.trim()")


def _accept_consent(page, timeout: int = TIMEOUT_MS, required: bool = False):
    """Wait for consent dialog. If required is True, fail on timeout."""
    try:
        page.wait_for_selector("#consent-allow", timeout=timeout)
        page.click("#consent-allow")
    except Exception:
        if required: raise


_STREAM_CHECK_JS = """
() => {
    const wrappers = Array.from(document.querySelectorAll('.video-wrapper'));
    const remotes = wrappers.slice(1);
    return remotes.length === 2 && remotes.every(w => {
        const v = w.querySelector('video');
        return v !== null && v.srcObject !== null;
    });
}
"""


# ---------------------------------------------------------------------------
# Test - Three Clients Mesh
# ---------------------------------------------------------------------------
def test_three_clients_connect_and_see_cameras(base_url, new_context):
    """Three clients join the same room and verify camera streams."""
    ctx1, ctx2, ctx3 = new_context(), new_context(), new_context()
    try:
        p1, p2, p3 = ctx1.new_page(), ctx2.new_page(), ctx3.new_page()
        video_url = f"{base_url}/video-room"
        for page in (p1, p2, p3):
            page.goto(video_url)

        id1, id2, id3 = _peer_id(p1), _peer_id(p2), _peer_id(p3)
        assert id1 and id2 and id3, "All clients must receive a peer ID"
        assert len({id1, id2, id3}) == 3, "All peer IDs must be unique"

        p2.fill("#remote-id", id1)
        p2.click("#btn-call")
        _accept_consent(p2)
        _accept_consent(p1)

        p3.fill("#remote-id", id1)
        p3.click("#btn-call")
        _accept_consent(p3)
        _accept_consent(p1)

        p2.fill("#remote-id", id3)
        p2.click("#btn-call")
        _accept_consent(p2)
        _accept_consent(p3)

        # ── Step 4: Wait for full mesh ───────────────────────────────────
        for page, name in ((p1, "Client 1"), (p2, "Client 2"), (p3, "Client 3")):
            page.wait_for_function(
                "document.querySelectorAll('.video-wrapper').length >= 3",
                timeout=TIMEOUT_MS,
            )
            assert page.evaluate("document.querySelectorAll('.video-wrapper').length") >= 3, \
                f"{name} did not reach 3 participants"

        # ── Step 5: Wait for and verify live camera streams ──────────────
        for page, name in ((p1, "Client 1"), (p2, "Client 2"), (p3, "Client 3")):
            page.wait_for_function(_STREAM_CHECK_JS, timeout=TIMEOUT_MS)
            assert page.evaluate(_STREAM_CHECK_JS), (
                f"{name} should see live streams from both participants")
    finally:
        ctx1.close()
        ctx2.close()
        ctx3.close()


# ---------------------------------------------------------------------------
# VoiceChanger unit tests (run in a headless browser page)
# ---------------------------------------------------------------------------

# JavaScript that exercises VoiceChanger in the browser context.
_VOICE_CHANGER_MODES_JS = """
() => {
    /* VoiceChanger must be defined */
    if (typeof VoiceChanger === 'undefined') return {ok: false, error: 'VoiceChanger not defined'};

    const modes = VoiceChanger.getModes();
    const expected = ['normal', 'deep', 'chipmunk', 'robot', 'echo', 'voice1', 'voice2', 'voice3'];
    for (const m of expected) {
        if (!modes[m]) return {ok: false, error: 'Missing mode: ' + m};
        if (!modes[m].label) return {ok: false, error: 'Missing label for: ' + m};
        if (!modes[m].icon) return {ok: false, error: 'Missing icon for: ' + m};
    }
    return {ok: true};
}
"""

_VOICE_CHANGER_INIT_JS = """
async () => {
    if (typeof VoiceChanger === 'undefined') return {ok: false, error: 'VoiceChanger not defined'};

    /* Build a minimal fake audio stream via AudioContext */
    let stream;
    try {
        const ac = new AudioContext();
        const osc = ac.createOscillator();
        const dest = ac.createMediaStreamDestination();
        osc.connect(dest);
        osc.start();
        stream = dest.stream;
    } catch (e) {
        return {ok: false, error: 'AudioContext unavailable: ' + e.message};
    }

    const processed = VoiceChanger.init(stream);
    if (!processed) return {ok: false, error: 'init() returned falsy'};

    /* Processed stream should have at least one audio track */
    const audioTracks = processed.getAudioTracks ? processed.getAudioTracks() : [];
    if (audioTracks.length === 0) return {ok: false, error: 'No audio tracks in processed stream'};

    /* Default mode should still be normal */
    if (VoiceChanger.getMode() !== 'normal') return {ok: false, error: 'Default mode is not normal'};

    VoiceChanger.destroy();
    return {ok: true};
}
"""

_VOICE_CHANGER_SET_MODE_JS = """
async () => {
    if (typeof VoiceChanger === 'undefined') return {ok: false, error: 'VoiceChanger not defined'};

    /* Build a fake stream */
    let stream;
    try {
        const ac = new AudioContext();
        const osc = ac.createOscillator();
        const dest = ac.createMediaStreamDestination();
        osc.connect(dest);
        osc.start();
        stream = dest.stream;
    } catch (e) {
        return {ok: false, error: 'AudioContext unavailable: ' + e.message};
    }

    VoiceChanger.init(stream);

    const modes = ['normal', 'deep', 'chipmunk', 'robot', 'echo', 'voice1', 'voice2', 'voice3'];
    for (const mode of modes) {
        VoiceChanger.setMode(mode);
        if (VoiceChanger.getMode() !== mode) {
            VoiceChanger.destroy();
            return {ok: false, error: 'setMode(' + mode + ') did not update getMode()'};
        }
        /* getProcessedStream() must remain valid after a mode switch */
        const ps = VoiceChanger.getProcessedStream();
        if (!ps) {
            VoiceChanger.destroy();
            return {ok: false, error: 'getProcessedStream() returned null after setMode(' + mode + ')'};
        }
    }

    VoiceChanger.destroy();
    /* After destroy, getMode resets to normal */
    if (VoiceChanger.getMode() !== 'normal') return {ok: false, error: 'getMode() after destroy() is not normal'};
    return {ok: true};
}
"""

_VOICE_CHANGER_IGNORE_UNKNOWN_MODE_JS = """
() => {
    if (typeof VoiceChanger === 'undefined') return {ok: false, error: 'VoiceChanger not defined'};
    /* setMode with an unknown key must be a no-op */
    const before = VoiceChanger.getMode();
    VoiceChanger.setMode('__unknown__');
    const after = VoiceChanger.getMode();
    if (before !== after) return {ok: false, error: 'setMode(unknown) changed mode to: ' + after};
    return {ok: true};
}
"""


@pytest.fixture
def voice_changer_page(base_url, new_context):
    """Open a single in-call page for VoiceChanger unit tests."""
    ctx = new_context()
    try:
        page = ctx.new_page()
        page.goto(f"{base_url}/video-room")
        page.wait_for_function("typeof VoiceChanger !== 'undefined'", timeout=TIMEOUT_MS)
        yield page
    finally:
        ctx.close()


def test_voice_changer_modes_defined(voice_changer_page):
    """VoiceChanger.getModes() must expose all five required effect keys."""
    res = voice_changer_page.evaluate(_VOICE_CHANGER_MODES_JS)
    assert res["ok"], res.get("error")


def test_voice_changer_init_returns_processed_stream(voice_changer_page):
    """VoiceChanger.init() must return a MediaStream with at least one audio track."""
    res = voice_changer_page.evaluate(_VOICE_CHANGER_INIT_JS)
    assert res["ok"], res.get("error")


def test_voice_changer_set_mode_cycles_all_effects(voice_changer_page):
    """setMode() must switch the active mode and keep getProcessedStream() valid."""
    res = voice_changer_page.evaluate(_VOICE_CHANGER_SET_MODE_JS)
    assert res["ok"], res.get("error")


def test_voice_changer_ignores_unknown_mode(voice_changer_page):
    """setMode() with an unrecognised key must not change the current mode."""
    res = voice_changer_page.evaluate(_VOICE_CHANGER_IGNORE_UNKNOWN_MODE_JS)
    assert res["ok"], res.get("error")


# ---------------------------------------------------------------------------
# VoiceChanger monitor / mic-gain tests
# ---------------------------------------------------------------------------

_VOICE_CHANGER_MONITOR_JS = """
async () => {
    if (typeof VoiceChanger === 'undefined') return {ok: false, error: 'VoiceChanger not defined'};
    let stream;
    try {
        const ac = new AudioContext(), osc = ac.createOscillator(), dest = ac.createMediaStreamDestination();
        osc.connect(dest); osc.start(); stream = dest.stream;
    } catch (e) { return {ok: false, error: 'AudioContext unavailable: ' + e.message}; }
    VoiceChanger.init(stream);
    if (VoiceChanger.getMonitorEnabled()) { VoiceChanger.destroy(); return {ok: false, error: 'monitor should be disabled after init'}; }
    if (!VoiceChanger.toggleMonitor()) { VoiceChanger.destroy(); return {ok: false, error: 'toggleMonitor() should return true'}; }
    if (!VoiceChanger.getMonitorEnabled()) { VoiceChanger.destroy(); return {ok: false, error: 'getMonitorEnabled() should be true'}; }
    VoiceChanger.toggleMonitor();
    if (VoiceChanger.getMonitorEnabled()) { VoiceChanger.destroy(); return {ok: false, error: 'getMonitorEnabled() should be false after off'}; }
    VoiceChanger.destroy(); return {ok: true};
}
"""

_VOICE_CHANGER_VOLUME_GAIN_JS = """
async () => {
    if (typeof VoiceChanger === 'undefined') return {ok: false, error: 'VoiceChanger not defined'};
    let stream;
    try {
        const ac = new AudioContext(), osc = ac.createOscillator(), dest = ac.createMediaStreamDestination();
        osc.connect(dest); osc.start(); stream = dest.stream;
    } catch (e) { return {ok: false, error: 'AudioContext unavailable: ' + e.message}; }
    VoiceChanger.init(stream);
    VoiceChanger.setMonitorVolume(0.75);
    if (Math.abs(VoiceChanger.getMonitorVolume() - 0.75) > 0.001) { VoiceChanger.destroy(); return {ok: false, error: 'volume mismatch'}; }
    VoiceChanger.setMonitorVolume(5); if (VoiceChanger.getMonitorVolume() !== 1) { VoiceChanger.destroy(); return {ok: false, error: 'clamp max volume'}; }
    VoiceChanger.setMonitorVolume(-1); if (VoiceChanger.getMonitorVolume() !== 0) { VoiceChanger.destroy(); return {ok: false, error: 'clamp min volume'}; }
    VoiceChanger.setMicGain(1.5); if (Math.abs(VoiceChanger.getMicGain() - 1.5) > 0.001) { VoiceChanger.destroy(); return {ok: false, error: 'gain mismatch'}; }
    VoiceChanger.setMicGain(10); if (VoiceChanger.getMicGain() !== 2) { VoiceChanger.destroy(); return {ok: false, error: 'clamp max gain'}; }
    VoiceChanger.setMicGain(-1); if (VoiceChanger.getMicGain() !== 0) { VoiceChanger.destroy(); return {ok: false, error: 'clamp min gain'}; }
    VoiceChanger.destroy(); return {ok: true};
}
"""

_VOICE_CHANGER_INIT_IDEMPOTENT_JS = """
async () => {
    if (typeof VoiceChanger === 'undefined') return {ok: false, error: 'VoiceChanger not defined'};
    const make = () => { const ac = new AudioContext(), osc = ac.createOscillator(), dest = ac.createMediaStreamDestination(); osc.connect(dest); osc.start(); return dest.stream; };
    try {
        if (!VoiceChanger.init(make())) return {ok: false, error: 'First init failed'};
        const s2 = VoiceChanger.init(make());
        if (!s2 || (s2.getAudioTracks && s2.getAudioTracks().length === 0)) return {ok: false, error: 'Second init/stream invalid'};
    } catch (e) { VoiceChanger.destroy(); return {ok: false, error: 'Error on re-init: ' + e.message}; }
    VoiceChanger.destroy(); return {ok: true};
}
"""

_VOICE_CHANGER_INTENSITY_JS = """
async () => {
    if (typeof VoiceChanger === 'undefined') return {ok: false, error: 'VoiceChanger not defined'};
    let stream;
    try {
        const ac = new AudioContext(), osc = ac.createOscillator(), dest = ac.createMediaStreamDestination();
        osc.connect(dest); osc.start(); stream = dest.stream;
    } catch (e) { return {ok: false, error: 'AudioContext unavailable'}; }
    VoiceChanger.init(stream);
    VoiceChanger.setEffectIntensity(0.75);
    if (Math.abs(VoiceChanger.getEffectIntensity() - 0.75) > 0.001) { VoiceChanger.destroy(); return {ok: false, error: 'intensity mismatch'}; }
    VoiceChanger.setEffectIntensity(5); if (VoiceChanger.getEffectIntensity() !== 1) { VoiceChanger.destroy(); return {ok: false, error: 'clamp intensity 1'}; }
    VoiceChanger.setEffectIntensity(-1); if (VoiceChanger.getEffectIntensity() !== 0) { VoiceChanger.destroy(); return {ok: false, error: 'clamp intensity 0'}; }
    for (const m of ['voice1', 'voice2', 'voice3']) { try { VoiceChanger.setMode(m); VoiceChanger.setEffectIntensity(0.8); } catch (e) { VoiceChanger.destroy(); return {ok: false, error: 'Intensity error: ' + m}; } }
    VoiceChanger.destroy(); return {ok: true};
}
"""


def test_voice_changer_monitor_toggle(voice_changer_page):
    """toggleMonitor() must enable/disable the monitor correctly."""
    res = voice_changer_page.evaluate(_VOICE_CHANGER_MONITOR_JS)
    assert res["ok"], res.get("error")


def test_voice_changer_volume_and_mic_gain(voice_changer_page):
    """setMonitorVolume() and setMicGain() must clamp and persist values."""
    res = voice_changer_page.evaluate(_VOICE_CHANGER_VOLUME_GAIN_JS)
    assert res["ok"], res.get("error")


def test_voice_changer_init_idempotent(voice_changer_page):
    """Calling init() twice must return valid streams."""
    res = voice_changer_page.evaluate(_VOICE_CHANGER_INIT_IDEMPOTENT_JS)
    assert res["ok"], res.get("error")


def test_voice_changer_effect_intensity(voice_changer_page):
    """setEffectIntensity() must clamp, persist, and work on persona modes."""
    res = voice_changer_page.evaluate(_VOICE_CHANGER_INTENSITY_JS)
    assert res["ok"], res.get("error")


# ---------------------------------------------------------------------------
# Combined-effects API tests
# ---------------------------------------------------------------------------

_VOICE_CHANGER_COMBINED_EFFECTS_JS = """
async () => {
    if (typeof VoiceChanger === 'undefined') return {ok: false, error: 'VoiceChanger not defined'};
    let stream;
    try {
        const ac = new AudioContext(), osc = ac.createOscillator(), dest = ac.createMediaStreamDestination();
        osc.connect(dest); osc.start(); stream = dest.stream;
    } catch (e) { return {ok: false, error: 'AudioContext unavailable'}; }
    VoiceChanger.init(stream);
    const initial = VoiceChanger.getEffectLevels();
    if (Object.values(initial).some(v => v !== 0)) { VoiceChanger.destroy(); return {ok: false, error: 'non-zero initial levels'}; }
    VoiceChanger.setEffectLevel('deep', 0.6);
    if (Math.abs(VoiceChanger.getEffectLevels()['deep'] - 0.6) > 0.001) { VoiceChanger.destroy(); return {ok: false, error: 'level deep mismatch'}; }
    VoiceChanger.toggleEffect('robot');
    if (VoiceChanger.getEffectLevels()['robot'] <= 0) { VoiceChanger.destroy(); return {ok: false, error: 'toggleEffect failed'}; }
    VoiceChanger.destroy(); return {ok: true};
}
"""

_VOICE_CHANGER_ALL_EFFECTS_COMBINED_JS = """
async () => {
    if (typeof VoiceChanger === 'undefined') return {ok: false, error: 'VoiceChanger not defined'};
    let stream;
    try {
        const ac = new AudioContext(), osc = ac.createOscillator(), dest = ac.createMediaStreamDestination();
        osc.connect(dest); osc.start(); stream = dest.stream;
    } catch (e) { return {ok: false, error: 'AudioContext unavailable'}; }
    VoiceChanger.init(stream);
    const effectModes = ['deep', 'chipmunk', 'robot', 'echo', 'voice1', 'voice2', 'voice3'];
    for (const m of effectModes) { VoiceChanger.setEffectLevel(m, 0.5); }
    const ps = VoiceChanger.getProcessedStream();
    if (!ps || (ps.getAudioTracks && ps.getAudioTracks().length === 0)) { VoiceChanger.destroy(); return {ok: false, error: 'All combined invalid stream'}; }
    VoiceChanger.destroy(); return {ok: true};
}
"""


def test_voice_changer_combined_effects_api(voice_changer_page):
    """setEffectLevel/getEffectLevels/toggleEffect must support independent levels."""
    res = voice_changer_page.evaluate(_VOICE_CHANGER_COMBINED_EFFECTS_JS)
    assert res["ok"], res.get("error")


def test_voice_changer_all_effects_combined(voice_changer_page):
    """All 7 effects active simultaneously must not throw."""
    res = voice_changer_page.evaluate(_VOICE_CHANGER_ALL_EFFECTS_COMBINED_JS)
    assert res["ok"], res.get("error")


def test_video_room_includes_voice_controller_ui():
    """Video room page should include voice UI and script wiring."""
    html = (ROOT / "src/pages/video-room.html").read_text(encoding="utf-8")
    for s in ['id="btn-voice-changer"', 'id="voice-effects-panel"', 'src="js/voice-changer.js"']:
        assert s in html, f"Missing in video-room.html: {s}"


def test_video_chat_includes_prejoin_voice_controller_ui():
    """Video chat lobby should include pre-join voice UI."""
    html = (ROOT / "src/pages/video-chat.html").read_text(encoding="utf-8")
    for s in ['id="prejoin-voice-panel"', 'src="js/voice-changer.js"']:
        assert s in html, f"Missing in video-chat.html: {s}"


def test_video_room_peerjs_script_has_no_sri_integrity():
    """PeerJS script should not use SRI integrity."""
    html = (ROOT / "src/pages/video-room.html").read_text(encoding="utf-8")
    # Search for PeerJS script tag, allowing for multi-line formatting common in the project.
    pattern = (r'<script\b[^>]*'
               r'src="https://unpkg\.com/peerjs@[^/]+/dist/peerjs\.min\.js"'
               r'[^>]*>')
    m = re.search(pattern, html)
    assert m and "integrity=" not in m.group(0), "PeerJS script should not have SRI integrity"
