/**
 * BLT-SafeCloak — transcription.js
 * Real-time speech transcription using the Web Speech API (primary)
 * with a Cloudflare AI Whisper fallback for unsupported browsers.
 */

const Transcription = (() => {
  let recognition = null;
  let mediaRecorder = null;
  let isRunning = false;
  let useCloudflare = false;

  /** Check if the Web Speech API is available in this browser */
  function isWebSpeechSupported() {
    return "SpeechRecognition" in window || "webkitSpeechRecognition" in window;
  }

  /**
   * Start real-time transcription.
   * Prefers the Web Speech API; falls back to Cloudflare AI Whisper when a
   * microphone stream is provided and the browser lacks native support.
   *
   * @param {function} onTranscript  Called with { text: string, isFinal: boolean }
   * @param {{ stream?: MediaStream }} options
   * @returns {boolean} true if transcription started successfully
   */
  function start(onTranscript, options = {}) {
    if (isWebSpeechSupported()) {
      return startWebSpeech(onTranscript);
    }
    if (options.stream) {
      return startCloudflare(onTranscript, options.stream);
    }
    return false;
  }

  /* ── Web Speech API ── */
  function startWebSpeech(onTranscript) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event) => {
      let interimText = "";
      let finalText = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const text = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          finalText += text;
        } else {
          interimText += text;
        }
      }
      if (finalText) {
        onTranscript({ text: finalText.trim(), isFinal: true });
      } else if (interimText) {
        onTranscript({ text: interimText.trim(), isFinal: false });
      }
    };

    recognition.onerror = (event) => {
      if (event.error !== "no-speech" && event.error !== "aborted") {
        console.warn("Speech recognition error:", event.error);
      }
    };

    /* Auto-restart to maintain a continuous session */
    recognition.onend = () => {
      if (isRunning) {
        try {
          recognition.start();
        } catch (e) {
          /* ignore race conditions on stop */
        }
      }
    };

    try {
      recognition.start();
      isRunning = true;
      useCloudflare = false;
      return true;
    } catch (e) {
      isRunning = false;
      return false;
    }
  }

  /* ── Cloudflare AI Whisper fallback ── */
  function startCloudflare(onTranscript, stream) {
    const audioOnlyStream = new MediaStream(stream.getAudioTracks());

    /* Pick a supported MIME type */
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"].find((t) =>
      MediaRecorder.isTypeSupported(t)
    );
    if (!mimeType) return false;

    try {
      mediaRecorder = new MediaRecorder(audioOnlyStream, { mimeType });
    } catch (e) {
      return false;
    }

    mediaRecorder.ondataavailable = async (event) => {
      if (!isRunning || event.data.size < 1000) return; /* skip near-empty chunks */
      try {
        const buffer = await event.data.arrayBuffer();
        const response = await fetch("/api/transcribe", {
          method: "POST",
          body: buffer,
          headers: { "Content-Type": mimeType },
        });
        if (!response.ok) return;
        const { text } = await response.json();
        if (text && text.trim()) {
          onTranscript({ text: text.trim(), isFinal: true });
        }
      } catch (e) {
        /* network or parse error — silently skip this chunk */
      }
    };

    mediaRecorder.start(4000); /* send a chunk every 4 seconds */
    isRunning = true;
    useCloudflare = true;
    return true;
  }

  /** Stop all transcription */
  function stop() {
    isRunning = false;
    if (recognition) {
      try {
        recognition.stop();
      } catch (e) {
        /* ignore */
      }
      recognition = null;
    }
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      try {
        mediaRecorder.stop();
      } catch (e) {
        /* ignore */
      }
    }
    mediaRecorder = null;
    useCloudflare = false;
  }

  return { start, stop, isWebSpeechSupported };
})();
