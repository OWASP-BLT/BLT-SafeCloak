/**
 * BLT-SafeCloak - video-preview.js
 * Shared local preview renderer (UI-only transforms, no stream mutation).
 */

(() => {
  function setStream(videoEl, stream) {
    if (!videoEl) return;
    const next = stream || null;
    if (videoEl.srcObject !== next) {
      videoEl.srcObject = next;
    }
  }

  function setMirror(videoEl, mirrorEnabled) {
    if (!videoEl) return;
    videoEl.classList.toggle("video-local-mirrored", Boolean(mirrorEnabled));
  }

  function render({ videoEl, stream, visible = true, muted, isLocal = false, mirror = false } = {}) {
    if (!videoEl) return;
    if (typeof muted === "boolean") {
      videoEl.muted = muted;
    }
    if (isLocal) {
      setMirror(videoEl, mirror);
    }
    if (visible) {
      videoEl.style.display = "block";
      setStream(videoEl, stream);
      return;
    }
    videoEl.style.display = "none";
    setStream(videoEl, null);
  }

  window.VideoPreview = {
    setStream,
    setMirror,
    render,
  };
})();
