/**
 * BLT-SafeCloak — video.js
 * Secure video chat using WebRTC (via PeerJS) with consent recording
 */

const VideoChat = (() => {
  let peer = null;
  let localStream = null;
  const activeCalls = new Map(); // peerId -> MediaConnection
  const activeDataConns = new Map(); // peerId -> DataConnection
  let audioContext = null;
  let analyser = null;
  let voiceAnimFrame = null;
  let micMuted = false;
  let camOff = false;
  let consentGiven = false;
  let screenSharing = false;
  let statsTimer = null;
  const statsSnapshot = new Map(); // peerId -> { ts, inBytes, outBytes }
  const healthHistory = {
    score: [],
    rtt: [],
    loss: [],
    maxPoints: 40,
  };

  const state = {
    peerId: null,
    connected: false,
    sessionId: null,
    sessionKey: null,
  };

  /* ── DOM helpers ── */
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) =>
    String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  function updateStatus(text, type = "muted") {
    const el = $("connection-status");
    if (!el) return;
    el.textContent = text;
    el.className = `text-${type}`;
  }

  function setDotStatus(status) {
    const dot = $("status-dot");
    if (dot) dot.className = `status-dot ${status}`;
  }

  /* ── Call health panel ── */
  function setSummaryChip(label, tone) {
    const chip = $("call-health-summary");
    if (!chip) return;
    chip.textContent = label;
    chip.className = `health-chip ${tone}`;
  }

  function setCallHealthView(summary, latency, stability, tip) {
    setSummaryChip(summary.label, summary.tone);
    if ($("call-health-latency")) $("call-health-latency").textContent = latency;
    if ($("call-health-stability")) $("call-health-stability").textContent = stability;
    if ($("call-health-tip")) $("call-health-tip").textContent = tip;
  }

  function renderTechRows(rows) {
    const body = $("tech-details-body");
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="7">No active peers yet.</td></tr>`;
      return;
    }
    body.innerHTML = rows
      .map(
        (r) => `
      <tr>
        <td class="font-mono">${escapeHtml(r.peer)}</td>
        <td>${escapeHtml(r.state)}</td>
        <td>${escapeHtml(r.rtt)}</td>
        <td>${escapeHtml(r.jitter)}</td>
        <td>${escapeHtml(r.loss)}</td>
        <td>${escapeHtml(r.down)}</td>
        <td>${escapeHtml(r.up)}</td>
      </tr>
    `
      )
      .join("");
  }

  function resetCallHealthPanel() {
    setCallHealthView(
      { label: "Idle", tone: "quality-idle" },
      "--",
      "--",
      "Connect to a participant to start live quality checks."
    );
    renderTechRows([]);
    statsSnapshot.clear();
    healthHistory.score = [];
    healthHistory.rtt = [];
    healthHistory.loss = [];
    renderHealthCharts();
  }

  function classifyQuality(score) {
    if (score >= 85) return { label: "Excellent", tone: "quality-excellent" };
    if (score >= 70) return { label: "Good", tone: "quality-good" };
    if (score >= 50) return { label: "Fair", tone: "quality-fair" };
    return { label: "Poor", tone: "quality-poor" };
  }

  function latencyLabel(rttMs) {
    if (rttMs == null) return "Unknown";
    if (rttMs < 120) return "Low";
    if (rttMs < 250) return "Medium";
    return "High";
  }

  function stabilityLabel(lossPct, jitterMs) {
    if (lossPct == null && jitterMs == null) return "Unknown";
    if ((lossPct || 0) < 1.5 && (jitterMs || 0) < 12) return "Stable";
    if ((lossPct || 0) < 4.5 && (jitterMs || 0) < 25) return "Mostly stable";
    return "Unstable";
  }

  function healthTip(score) {
    if (score >= 85) return "Connection is healthy. Audio/video should be smooth.";
    if (score >= 70) return "Minor fluctuation detected. Keep screen share off if quality drops.";
    if (score >= 50) return "Some instability detected. Try disabling camera for better audio.";
    return "Network quality is low. Move closer to Wi-Fi or pause screen sharing.";
  }

  function pushHistory(series, value) {
    if (typeof value !== "number" || !isFinite(value)) return;
    // For the quality chart, anchor the first point at baseline so trend starts from bottom.
    if (series === healthHistory.score && series.length === 0) {
      series.push(0);
    }
    series.push(value);
    if (series.length > healthHistory.maxPoints) series.shift();
  }

  function drawLine(ctx, values, maxValue, color, options = {}) {
    if (!values.length) return;
    const w = ctx.canvas.width;
    const h = ctx.canvas.height;
    const stepX = values.length > 1 ? w / (values.length - 1) : w;
    const topPx = typeof options.topPx === "number" ? options.topPx : 4;
    const bottomPx = typeof options.bottomPx === "number" ? options.bottomPx : h - 4;
    const range = Math.max(1, bottomPx - topPx);
    ctx.beginPath();
    values.forEach((value, i) => {
      const x = i * stepX;
      const normalized = Math.max(0, Math.min(1, value / maxValue));
      const y = bottomPx - normalized * range;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.stroke();
  }

  function drawGrid(ctx) {
    const w = ctx.canvas.width;
    const h = ctx.canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "#e5e7eb";
    ctx.lineWidth = 1;
    for (let i = 1; i <= 3; i += 1) {
      const y = (h / 4) * i;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }
  }

  function renderHealthCharts() {
    const qualityCanvas = $("quality-trend-canvas");
    const networkCanvas = $("network-trend-canvas");
    if (!qualityCanvas || !networkCanvas) return;

    const qCtx = qualityCanvas.getContext("2d");
    const nCtx = networkCanvas.getContext("2d");
    if (!qCtx || !nCtx) return;

    drawGrid(qCtx);
    drawLine(qCtx, healthHistory.score, 100, "#2563eb");

    drawGrid(nCtx);
    const h = nCtx.canvas.height;
    const dividerY = Math.round(h * 0.75);
    nCtx.beginPath();
    nCtx.moveTo(0, dividerY);
    nCtx.lineTo(nCtx.canvas.width, dividerY);
    nCtx.strokeStyle = "#cbd5e1";
    nCtx.lineWidth = 1;
    nCtx.setLineDash([4, 3]);
    nCtx.stroke();
    nCtx.setLineDash([]);

    // RTT in upper band
    drawLine(nCtx, healthHistory.rtt, 400, "#7c3aed", {
      topPx: 4,
      bottomPx: Math.round(h * 0.7),
    });
    // Loss in lower band for clearer separation
    drawLine(nCtx, healthHistory.loss, 5, "#dc2626", {
      topPx: Math.round(h * 0.8),
      bottomPx: h - 4,
    });
  }

  async function collectPeerStats(peerId, call) {
    const pc = call && call.peerConnection;
    if (!pc || typeof pc.getStats !== "function") return null;

    let rttMs = null;
    let jitterMs = null;
    let packetsLost = 0;
    let packetsRecv = 0;
    let bytesIn = 0;
    let bytesOut = 0;

    try {
      const report = await pc.getStats();
      report.forEach((stat) => {
        if (
          stat.type === "candidate-pair" &&
          (stat.nominated || stat.selected || stat.state === "succeeded")
        ) {
          if (typeof stat.currentRoundTripTime === "number") {
            rttMs = stat.currentRoundTripTime * 1000;
          }
          if (typeof stat.bytesReceived === "number") bytesIn = Math.max(bytesIn, stat.bytesReceived);
          if (typeof stat.bytesSent === "number") bytesOut = Math.max(bytesOut, stat.bytesSent);
        }

        if (stat.type === "inbound-rtp" && !stat.isRemote) {
          if (typeof stat.packetsLost === "number") packetsLost += stat.packetsLost;
          if (typeof stat.packetsReceived === "number") packetsRecv += stat.packetsReceived;
          if (typeof stat.jitter === "number") jitterMs = Math.max(jitterMs || 0, stat.jitter * 1000);
          if (typeof stat.bytesReceived === "number") bytesIn = Math.max(bytesIn, stat.bytesReceived);
        }

        if (stat.type === "outbound-rtp" && !stat.isRemote) {
          if (typeof stat.bytesSent === "number") bytesOut = Math.max(bytesOut, stat.bytesSent);
        }
      });
    } catch {
      return null;
    }

    const now = Date.now();
    const prev = statsSnapshot.get(peerId);
    let downKbps = null;
    let upKbps = null;
    if (prev && now > prev.ts) {
      const dt = (now - prev.ts) / 1000;
      if (bytesIn >= prev.inBytes) downKbps = ((bytesIn - prev.inBytes) * 8) / 1000 / dt;
      if (bytesOut >= prev.outBytes) upKbps = ((bytesOut - prev.outBytes) * 8) / 1000 / dt;
    }
    statsSnapshot.set(peerId, { ts: now, inBytes: bytesIn, outBytes: bytesOut });

    const lossPct =
      packetsRecv + packetsLost > 0 ? (packetsLost / (packetsRecv + packetsLost)) * 100 : null;

    return {
      peer: peerId,
      state: pc.connectionState || "unknown",
      rttMs,
      jitterMs,
      lossPct,
      downKbps,
      upKbps,
    };
  }

  function formatMs(v) {
    return typeof v === "number" && isFinite(v) ? `${Math.round(v)} ms` : "--";
  }

  function formatLoss(v) {
    return typeof v === "number" && isFinite(v) ? `${v.toFixed(1)}%` : "--";
  }

  function formatKbps(v) {
    return typeof v === "number" && isFinite(v) ? `${Math.round(v)} kbps` : "--";
  }

  async function updateCallHealthPanel() {
    if (activeCalls.size === 0) {
      resetCallHealthPanel();
      return;
    }

    const rows = [];
    for (const [peerId, call] of activeCalls.entries()) {
      const s = await collectPeerStats(peerId, call);
      if (!s) continue;
      rows.push({
        peer: s.peer,
        state: s.state,
        rtt: formatMs(s.rttMs),
        jitter: formatMs(s.jitterMs),
        loss: formatLoss(s.lossPct),
        down: formatKbps(s.downKbps),
        up: formatKbps(s.upKbps),
        raw: s,
      });
    }

    renderTechRows(rows);
    if (!rows.length) {
      setCallHealthView(
        { label: "Connecting", tone: "quality-fair" },
        "Unknown",
        "Unknown",
        "Collecting stats from peers..."
      );
      return;
    }

    const avg = rows.reduce(
      (acc, r) => {
        const s = r.raw;
        if (s.rttMs != null) {
          acc.rtt += s.rttMs;
          acc.rttCount += 1;
        }
        if (s.jitterMs != null) {
          acc.jitter += s.jitterMs;
          acc.jitterCount += 1;
        }
        if (s.lossPct != null) {
          acc.loss += s.lossPct;
          acc.lossCount += 1;
        }
        return acc;
      },
      { rtt: 0, jitter: 0, loss: 0, rttCount: 0, jitterCount: 0, lossCount: 0 }
    );

    const avgRtt = avg.rttCount ? avg.rtt / avg.rttCount : null;
    const avgJitter = avg.jitterCount ? avg.jitter / avg.jitterCount : null;
    const avgLoss = avg.lossCount ? avg.loss / avg.lossCount : null;

    let score = 100;
    if (avgRtt != null) {
      if (avgRtt >= 300) score -= 40;
      else if (avgRtt >= 180) score -= 25;
      else if (avgRtt >= 120) score -= 12;
    }
    if (avgJitter != null) {
      if (avgJitter >= 30) score -= 28;
      else if (avgJitter >= 18) score -= 16;
      else if (avgJitter >= 10) score -= 8;
    }
    if (avgLoss != null) {
      if (avgLoss >= 8) score -= 35;
      else if (avgLoss >= 4) score -= 22;
      else if (avgLoss >= 1.5) score -= 10;
    }
    score = Math.max(0, Math.min(100, score));

    const quality = classifyQuality(score);
    pushHistory(healthHistory.score, score);
    if (avgRtt != null) pushHistory(healthHistory.rtt, Math.min(avgRtt, 400));
    if (avgLoss != null) pushHistory(healthHistory.loss, Math.min(avgLoss, 5));
    renderHealthCharts();
    setCallHealthView(
      quality,
      latencyLabel(avgRtt),
      stabilityLabel(avgLoss, avgJitter),
      healthTip(score)
    );
  }

  function startStatsMonitoring() {
    if (statsTimer) clearTimeout(statsTimer);
    resetCallHealthPanel();
    let cancelled = false;
    const run = async () => {
      if (cancelled) return;
      await updateCallHealthPanel();
      if (cancelled) return;
      statsTimer = setTimeout(run, 3000);
    };
    statsTimer = setTimeout(run, 0);
    startStatsMonitoring._cancel = () => {
      cancelled = true;
    };
  }

  function stopStatsMonitoring() {
    if (statsTimer) {
      clearTimeout(statsTimer);
      statsTimer = null;
    }
    if (typeof startStatsMonitoring._cancel === "function") {
      startStatsMonitoring._cancel();
      startStatsMonitoring._cancel = null;
    }
    resetCallHealthPanel();
  }

  /* ── Browser detection ── */
  function detectBrowser() {
    const ua = navigator.userAgent;
    if (/Edg\//.test(ua)) return "edge";
    if (/OPR\/|Opera/.test(ua)) return "opera";
    if (/Chrome\//.test(ua)) return "chrome";
    if (/Firefox\//.test(ua)) return "firefox";
    if (/Safari\//.test(ua) && !/Chrome\/|Chromium\//.test(ua)) return "safari";
    return "other";
  }

  function getCameraInstructions(browser) {
    const steps = {
      chrome: `<strong>Google Chrome:</strong><ol style="margin:0.5rem 0 0 1.25rem;display:flex;flex-direction:column;gap:0.3rem">
        <li>Click the <strong>camera blocked</strong> icon (🔒 or 📷) in the address bar.</li>
        <li>Select <strong>Always allow</strong> for the camera and microphone, then click <strong>Done</strong>.</li>
        <li>Or go to <strong>Settings → Privacy and security → Site settings → Camera</strong> and allow this site.</li>
        <li>Reload the page and click <em>Try Again</em>.</li></ol>`,
      edge: `<strong>Microsoft Edge:</strong><ol style="margin:0.5rem 0 0 1.25rem;display:flex;flex-direction:column;gap:0.3rem">
        <li>Click the <strong>camera blocked</strong> icon (🔒 or 📷) in the address bar.</li>
        <li>Set Camera and Microphone permissions to <strong>Allow</strong>, then click <strong>Save</strong>.</li>
        <li>Or go to <strong>Settings → Cookies and site permissions → Camera</strong> and add this site to the allow list.</li>
        <li>Reload the page and click <em>Try Again</em>.</li></ol>`,
      firefox: `<strong>Mozilla Firefox:</strong><ol style="margin:0.5rem 0 0 1.25rem;display:flex;flex-direction:column;gap:0.3rem">
        <li>Click the <strong>camera blocked</strong> icon (🎥 with a slash) in the address bar.</li>
        <li>Click <strong>Blocked Temporarily</strong> or <strong>Blocked</strong> next to Camera and Microphone and choose <strong>Allow</strong>.</li>
        <li>Or go to <strong>about:preferences#privacy</strong> → Permissions → Camera → Settings, and allow this site.</li>
        <li>Reload the page and click <em>Try Again</em>.</li></ol>`,
      safari: `<strong>Safari:</strong><ol style="margin:0.5rem 0 0 1.25rem;display:flex;flex-direction:column;gap:0.3rem">
        <li>In the menu bar, go to <strong>Safari → Settings for This Website</strong> (or <strong>Preferences → Websites → Camera</strong>).</li>
        <li>Set Camera and Microphone to <strong>Allow</strong>.</li>
        <li>Reload the page and click <em>Try Again</em>.</li></ol>`,
      opera: `<strong>Opera:</strong><ol style="margin:0.5rem 0 0 1.25rem;display:flex;flex-direction:column;gap:0.3rem">
        <li>Click the <strong>camera blocked</strong> icon (🔒 or 📷) in the address bar.</li>
        <li>Select <strong>Always allow</strong> for the camera and microphone, then click <strong>Done</strong>.</li>
        <li>Or go to <strong>Settings → Privacy &amp; security → Site settings → Camera</strong> and allow this site.</li>
        <li>Reload the page and click <em>Try Again</em>.</li></ol>`,
      other: `<strong>Your browser:</strong><ol style="margin:0.5rem 0 0 1.25rem;display:flex;flex-direction:column;gap:0.3rem">
        <li>Look for a <strong>camera or lock icon</strong> in the address bar and click it.</li>
        <li>Set Camera and Microphone permissions to <strong>Allow</strong>.</li>
        <li>Check your browser's <strong>site settings / permissions</strong> page and ensure this site is not blocked.</li>
        <li>Reload the page and click <em>Try Again</em>.</li></ol>`,
    };
    return steps[browser] || steps.other;
  }

  function showCameraDenied() {
    const denied = $("camera-denied");
    const instructions = $("camera-denied-instructions");
    const mainGrid = $("main-grid");
    const permAlert = $("perm-alert");
    if (instructions) instructions.innerHTML = getCameraInstructions(detectBrowser());
    if (denied) denied.style.display = "flex";
    if (mainGrid) mainGrid.style.display = "none";
    if (permAlert) permAlert.style.display = "none";
    const retryBtn = $("btn-camera-retry");
    if (retryBtn) retryBtn.addEventListener("click", () => location.reload());
  }

  /* ── Media ── */
  async function attachStream(stream) {
    const localVideo = $("local-video");
    if (localVideo) {
      localVideo.srcObject = stream;
      localVideo.muted = true;
    }
    startVoiceMeter(stream);
  }

  async function startLocalMedia() {
    // 1. Try full video + audio
    try {
      localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      await attachStream(localStream);
      return true;
    } catch (err) {
      if (
        err.name === "NotAllowedError" ||
        err.name === "PermissionDeniedError" ||
        err.name === "SecurityError"
      ) {
        showCameraDenied();
        return false;
      }

      if (err.name === "NotReadableError" || err.name === "TrackStartError") {
        showToast(
          "Camera or microphone is already in use by another application. Please close it and reload.",
          "error"
        );
        return false;
      }

      // For NotFoundError / DevicesNotFoundError try partial fallbacks below.
      // For any other unexpected error fall through to the generic handler at the end.
      if (err.name !== "NotFoundError" && err.name !== "DevicesNotFoundError") {
        showToast("Could not access camera/microphone: " + err.message, "error");
        return false;
      }
    }

    // 2. No combined device found — try audio-only
    try {
      localStream = await navigator.mediaDevices.getUserMedia({ video: false, audio: true });
      await attachStream(localStream);
      showToast("No camera found — joining with audio only", "warning");
      return true;
    } catch (audioErr) {
      if (
        audioErr.name === "NotAllowedError" ||
        audioErr.name === "PermissionDeniedError" ||
        audioErr.name === "SecurityError"
      ) {
        showCameraDenied();
        return false;
      }
      if (audioErr.name !== "NotFoundError" && audioErr.name !== "DevicesNotFoundError") {
        showToast("Could not access microphone: " + audioErr.message, "error");
        return false;
      }
    }

    // 3. No microphone either — try video-only
    try {
      localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      await attachStream(localStream);
      showToast("No microphone found — joining with video only", "warning");
      return true;
    } catch (videoErr) {
      if (
        videoErr.name === "NotAllowedError" ||
        videoErr.name === "PermissionDeniedError" ||
        videoErr.name === "SecurityError"
      ) {
        showCameraDenied();
        return false;
      }
      if (videoErr.name === "NotReadableError" || videoErr.name === "TrackStartError") {
        showToast(
          "Camera is already in use by another application. Please close it and reload.",
          "error"
        );
        return false;
      }
      if (videoErr.name !== "NotFoundError" && videoErr.name !== "DevicesNotFoundError") {
        showToast("Could not access camera: " + videoErr.message, "error");
        return false;
      }
    }

    // 4. No devices at all
    showToast(
      "No camera or microphone found. Please connect a device and try reloading the page.",
      "error"
    );
    return false;
  }

  function startVoiceMeter(stream) {
    try {
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      const src = audioContext.createMediaStreamSource(stream);
      src.connect(analyser);
      animateVoiceMeter();
    } catch {
      /* audio context not available */
    }
  }

  function animateVoiceMeter() {
    const bars = document.querySelectorAll(".voice-bar");
    if (!bars.length || !analyser) return;
    const data = new Uint8Array(analyser.frequencyBinCount);
    function frame() {
      analyser.getByteFrequencyData(data);
      const slice = Math.floor(data.length / bars.length);
      bars.forEach((bar, i) => {
        const avg = data.slice(i * slice, (i + 1) * slice).reduce((a, b) => a + b, 0) / slice;
        bar.style.height = `${Math.max(4, (avg / 255) * 24)}px`;
      });
      voiceAnimFrame = requestAnimationFrame(frame);
    }
    frame();
  }

  /* ── PeerJS setup ── */
  async function initPeer() {
    if (typeof Peer === "undefined") {
      showToast("PeerJS not loaded", "error");
      return;
    }
    state.peerId = Crypto.randomId(6);
    state.sessionKey = await Crypto.generateKey();
    state.sessionId = state.peerId;

    peer = new Peer(state.peerId, Object.assign({
      host: "0.peerjs.com",
      port: 443,
      secure: true,
      path: "/",
      debug: 0,
    }, window.__PEERJS_CONFIG__ || {}));

    peer.on("open", (id) => {
      $("my-peer-id") && ($("my-peer-id").textContent = id);
      updateStatus("Ready — share your Room ID", "secondary");
      setDotStatus("online");
      showToast("Connected to signaling server", "success");
      // Auto-connect if a room ID was passed in the URL
      const params = new URLSearchParams(window.location.search);
      const joinId = params.get("room");
      if (joinId && joinId !== state.peerId) {
        const remoteInput = $("remote-id");
        if (remoteInput) {
          remoteInput.value = joinId;
        }
        callPeer(joinId);
      }
    });

    peer.on("call", async (incomingCall) => {
      if (activeCalls.has(incomingCall.peer)) {
        incomingCall.close();
        return;
      }
      if (!consentGiven) {
        const ok = await askConsent(incomingCall.peer);
        if (!ok) {
          incomingCall.close();
          return;
        }
      }
      activeCalls.set(incomingCall.peer, incomingCall);
      updateParticipantsList();
      incomingCall.answer(localStream);
      handleCallStream(incomingCall);
      sendPeerListTo(incomingCall.peer);
    });

    peer.on("connection", (conn) => {
      setupDataConn(conn);
    });

    peer.on("error", (err) => {
      updateStatus("Error: " + err.message, "danger");
      setDotStatus("offline");
      showToast("Connection error: " + err.type, "error");
    });

    peer.on("disconnected", () => {
      updateStatus("Disconnected", "warning");
      setDotStatus("offline");
    });
  }

  function updateParticipantsList() {
    const listEl = $("participants-list");
    const countEl = $("participant-count");
    if (countEl) {
      countEl.textContent = `${activeCalls.size} connected`;
    }
    if (!listEl) return;
    listEl.innerHTML = "";
    if (activeCalls.size === 0) {
      const empty = document.createElement("p");
      empty.className = "text-sm text-gray-500 text-center py-2";
      empty.textContent = "No participants connected";
      listEl.appendChild(empty);
      updateCallHealthPanel();
      return;
    }
    activeCalls.forEach((_call, peerId) => {
      const item = document.createElement("div");
      item.className = "flex items-center justify-between py-1 text-sm";

      const nameSpan = document.createElement("span");
      nameSpan.className = "flex items-center gap-2";

      const dot = document.createElement("span");
      dot.className = "status-dot online";
      dot.setAttribute("aria-hidden", "true");

      const idLabel = document.createElement("span");
      idLabel.className = "font-mono font-bold truncate max-w-[120px]";
      idLabel.title = peerId;
      idLabel.textContent = peerId;

      nameSpan.appendChild(dot);
      nameSpan.appendChild(idLabel);

      const disconnectBtn = document.createElement("button");
      disconnectBtn.className = "control-btn";
      disconnectBtn.style.cssText = "width:32px;height:32px;font-size:0.75rem";
      disconnectBtn.title = `Disconnect ${peerId}`;
      disconnectBtn.setAttribute("aria-label", `Disconnect ${peerId}`);
      disconnectBtn.innerHTML = '<i class="fa-solid fa-phone-slash" aria-hidden="true"></i>';
      disconnectBtn.addEventListener("click", () => VideoChat.disconnectPeer(peerId));

      item.appendChild(nameSpan);
      item.appendChild(disconnectBtn);
      listEl.appendChild(item);
    });
    updateCallHealthPanel();
  }

  function handleCallStream(call) {
    const remotePeerId = call.peer;

    const videoWrapper = document.createElement("div");
    videoWrapper.className = "video-wrapper bg-gray-900";
    videoWrapper.id = `wrapper-${remotePeerId}`;

    const videoEl = document.createElement("video");
    videoEl.autoplay = true;
    videoEl.playsInline = true;
    videoEl.setAttribute("aria-label", `Participant ${remotePeerId} video`);

    const label = document.createElement("div");
    label.className = "video-label";
    label.id = `label-${remotePeerId}`;

    const labelDot = document.createElement("span");
    labelDot.className = "status-dot connecting";
    labelDot.setAttribute("aria-hidden", "true");
    labelDot.id = `dot-${remotePeerId}`;

    const labelText = document.createElement("span");
    labelText.className = "font-mono font-bold";
    labelText.title = remotePeerId;
    labelText.textContent = remotePeerId;

    label.appendChild(labelDot);
    label.appendChild(labelText);

    videoWrapper.appendChild(videoEl);
    videoWrapper.appendChild(label);

    const videoGrid = $("video-grid");
    if (videoGrid) videoGrid.appendChild(videoWrapper);

    call.on("stream", (remoteStream) => {
      videoEl.srcObject = remoteStream;
      const dot = $(`dot-${remotePeerId}`);
      if (dot) dot.className = "status-dot online";
      state.connected = true;
      const count = activeCalls.size;
      updateStatus(
        `🔒 Encrypted call active (${count} participant${count !== 1 ? "s" : ""})`,
        "success"
      );
      setDotStatus("online");
      $("call-controls") && $("call-controls").classList.remove("hidden");
      updateParticipantsList();
    });

    call.on("close", () => {
      activeCalls.delete(remotePeerId);
      const wrapper = $(`wrapper-${remotePeerId}`);
      if (wrapper) wrapper.remove();
      if (activeCalls.size === 0) {
        state.connected = false;
        updateStatus("Call ended", "muted");
        setDotStatus("offline");
        $("call-controls") && $("call-controls").classList.add("hidden");
      } else {
        const count = activeCalls.size;
        updateStatus(
          `🔒 Encrypted call active (${count} participant${count !== 1 ? "s" : ""})`,
          "success"
        );
      }
      updateParticipantsList();
    });

    call.on("error", (err) => {
      showToast("Call error: " + err.message, "error");
    });
  }

  /* ── Full-mesh helpers ── */
  function setupDataConn(conn) {
    activeDataConns.set(conn.peer, conn);
    conn.on("data", (data) => {
      if (data && data.type === "peers" && Array.isArray(data.ids)) {
        data.ids.forEach((id) => {
          if (id !== state.peerId && !activeCalls.has(id)) {
            callPeer(id);
          }
        });
      }
    });
    conn.on("close", () => activeDataConns.delete(conn.peer));
    conn.on("error", () => activeDataConns.delete(conn.peer));
  }

  function sendPeerListTo(remotePeerId) {
    const peerList = Array.from(activeCalls.keys()).filter((id) => id !== remotePeerId);
    if (peerList.length === 0) return;
    if (activeDataConns.has(remotePeerId)) {
      const existing = activeDataConns.get(remotePeerId);
      if (existing.open) {
        existing.send({ type: "peers", ids: peerList });
        return;
      }
    }
    const conn = peer.connect(remotePeerId);
    setupDataConn(conn);
    conn.on("open", () => conn.send({ type: "peers", ids: peerList }));
  }

  /* ── Input validation ── */
  function isValidRoomId(roomId) {
    if (!roomId || typeof roomId !== "string") return false;
    // Match the same character set used by Crypto.randomId(): uppercase A-Z (except I,O) + digits 2-9
    return /^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}$/.test(roomId.trim());
  }

  async function callPeer(remotePeerId) {
    if (!peer) {
      showToast("Not connected to server", "error");
      return;
    }
    if (!localStream) {
      showToast("No local stream — allow camera/mic first", "error");
      return;
    }
    if (!remotePeerId) {
      showToast("Enter a Room ID to call", "warning");
      return;
    }

    // Ensure remotePeerId is a string before trimming (defensive against network data)
    if (typeof remotePeerId !== "string") {
      showToast("Invalid Room ID format", "error");
      return;
    }

    // Normalize the peer ID by trimming whitespace
    remotePeerId = remotePeerId.trim();

    if (!remotePeerId) {
      showToast("Enter a Room ID to call", "warning");
      return;
    }

    if (!isValidRoomId(remotePeerId)) {
      showToast("Room ID must be exactly 6 characters using only uppercase letters (A-Z except I,O) and digits (2-9)", "error");
      return;
    }
    if (remotePeerId === state.peerId) {
      showToast("You cannot call yourself", "warning");
      return;
    }
    if (activeCalls.has(remotePeerId)) {
      showToast("Already connected to this participant", "warning");
      return;
    }

    if (!consentGiven) {
      const ok = await askConsent("the remote participant");
      if (!ok) return;
    }

    updateStatus("Calling…", "warning");
    setDotStatus("connecting");
    const call = peer.call(remotePeerId, localStream);
    activeCalls.set(remotePeerId, call);
    updateParticipantsList();
    handleCallStream(call);
  }

  /* ── Controls ── */
  function toggleMic() {
    if (!localStream) return;
    micMuted = !micMuted;
    localStream.getAudioTracks().forEach((t) => (t.enabled = !micMuted));
    const btn = $("btn-mic");
    if (btn) {
      btn.textContent = micMuted ? "🔇" : "🎙️";
      btn.title = micMuted ? "Unmute mic" : "Mute mic";
      btn.classList.toggle("active", micMuted);
    }
    showToast(micMuted ? "Microphone muted" : "Microphone unmuted", "info");
  }

  function toggleCamera() {
    if (!localStream) return;
    camOff = !camOff;
    localStream.getVideoTracks().forEach((t) => (t.enabled = !camOff));
    const btn = $("btn-cam");
    if (btn) {
      btn.textContent = camOff ? "📷" : "🎥";
      btn.title = camOff ? "Enable camera" : "Disable camera";
      btn.classList.toggle("active", camOff);
    }
    showToast(camOff ? "Camera disabled" : "Camera enabled", "info");
  }

  function disconnectPeer(peerId) {
    const call = activeCalls.get(peerId);
    if (call) {
      call.close();
    }
  }

  function endCall() {
    activeCalls.forEach((call) => call.close());
    activeCalls.clear();
    activeDataConns.forEach((conn) => conn.close());
    activeDataConns.clear();
    state.connected = false;
    updateStatus("Call ended", "muted");
    setDotStatus("offline");
    const videoGrid = $("video-grid");
    if (videoGrid) {
      videoGrid.querySelectorAll(".video-wrapper:not(:first-child)").forEach((w) => w.remove());
    }
    $("call-controls") && $("call-controls").classList.add("hidden");
    updateParticipantsList();
    updateCallHealthPanel();
    showToast("Call ended", "info");
    // Record consent end
    ConsentManager &&
      ConsentManager.record({
        type: "recorded",
        name: "Call session ended",
        details: `Session ID: ${state.sessionId} — ended at ${new Date().toISOString()}`,
      });
  }

  function hangup() {
    endCall();
    if (peer) {
      peer.disconnect();
      peer.destroy();
      peer = null;
    }
    if (localStream) {
      localStream.getTracks().forEach((t) => t.stop());
      localStream = null;
    }
    if (voiceAnimFrame) cancelAnimationFrame(voiceAnimFrame);
    if (audioContext) audioContext.close();
    stopStatsMonitoring();
    setDotStatus("offline");
    updateStatus("Disconnected", "muted");
    showToast("Session ended and media released", "success");
  }

  /* ── Noise suppression hint ── */
  async function toggleNoiseSuppression() {
    if (!localStream) return;
    const audioTrack = localStream.getAudioTracks()[0];
    if (!audioTrack) return;
    try {
      const settings = audioTrack.getSettings();
      const current = settings.noiseSuppression;
      await audioTrack.applyConstraints({
        noiseSuppression: !current,
        echoCancellation: true,
        autoGainControl: true,
      });
      showToast(`Noise suppression ${!current ? "enabled" : "disabled"}`, "success");
      const btn = $("btn-noise");
      if (btn) btn.classList.toggle("active", !current);
    } catch {
      showToast("Noise suppression not supported on this device", "warning");
    }
  }

  /* ── Consent gate ── */
  function askConsent(callerName) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "modal-overlay";
      overlay.style.display = "flex";
      overlay.innerHTML = `
        <div class="modal" style="max-width:440px">
          <h3>🔒 Recording Consent Required</h3>
          <p>This call may be recorded for AI notes and security purposes. Do you consent to participate in this secure call with <strong style="color:#fff">${callerName}</strong>?</p>
          <div class="alert alert-info" style="margin-bottom:1rem">
            <span>ℹ️</span>
            <span>Consent is cryptographically timestamped and stored locally. You can withdraw at any time.</span>
          </div>
          <div style="display:flex;gap:0.75rem;justify-content:flex-end">
            <button class="btn btn-secondary" id="consent-deny">Decline</button>
            <button class="btn btn-primary" id="consent-allow">I Consent</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);

      overlay.querySelector("#consent-allow").onclick = () => {
        consentGiven = true;
        overlay.remove();
        ConsentManager &&
          ConsentManager.record({
            type: "given",
            name: `Consent given for call with ${callerName}`,
            details: `Session ID: ${state.sessionId}`,
          });
        resolve(true);
      };
      overlay.querySelector("#consent-deny").onclick = () => {
        overlay.remove();
        resolve(false);
      };
    });
  }

  /* ── Share link ── */
  function copyRoomLink() {
    if (!state.peerId) {
      showToast("Room not ready yet — please wait", "warning");
      return;
    }
    const url = `${window.location.origin}${window.location.pathname}?room=${encodeURIComponent(state.peerId)}`;
    copyToClipboard(url, "Room link");
  }

  /* ── Screen share ── */
  async function shareScreen() {
    try {
      const screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true });
      const screenTrack = screenStream.getVideoTracks()[0];
      for (const call of activeCalls.values()) {
        if (call.peerConnection) {
          const sender = call.peerConnection
            .getSenders()
            .find((s) => s.track && s.track.kind === "video");
          if (sender) await sender.replaceTrack(screenTrack);
        }
      }
      const localVideo = $("local-video");
      if (localVideo) localVideo.srcObject = screenStream;
      showToast("Screen sharing started", "success");
      screenSharing = true;
      $("btn-screen") && $("btn-screen").classList.add("active");
      screenTrack.onended = () => {
        if (screenSharing) stopScreenShare();
      };
    } catch (err) {
      if (err.name !== "NotAllowedError") showToast("Screen share error: " + err.message, "error");
    }
  }

  function stopScreenShare() {
    if (!localStream || activeCalls.size === 0) return;
    const videoTrack = localStream.getVideoTracks()[0];
    if (!videoTrack) return;
    for (const call of activeCalls.values()) {
      const sender =
        call.peerConnection &&
        call.peerConnection.getSenders().find((s) => s.track && s.track.kind === "video");
      if (sender) sender.replaceTrack(videoTrack);
    }
    const localVideo = $("local-video");
    if (localVideo) {
      localVideo.srcObject = localStream;
    }
    $("btn-screen") && $("btn-screen").classList.remove("active");
    screenSharing = false;
    showToast("Screen sharing stopped", "info");
  }

  /* ── Init ── */
  async function init() {
    const graphToggle = $("toggle-health-graphs");
    const graphsPanel = $("health-charts");
    if (graphToggle && graphsPanel) {
      graphToggle.addEventListener("click", () => {
        const isHidden = graphsPanel.classList.toggle("hidden");
        graphToggle.setAttribute("aria-expanded", isHidden ? "false" : "true");
        graphToggle.innerHTML = isHidden
          ? '<i class="fa-solid fa-chart-line mr-1" aria-hidden="true"></i>Show Graphs'
          : '<i class="fa-solid fa-chart-line mr-1" aria-hidden="true"></i>Hide Graphs';
      });
    }

    const techToggle = $("toggle-tech-details");
    const techPanel = $("tech-details-panel");
    if (techToggle && techPanel) {
      techToggle.addEventListener("click", () => {
        const isHidden = techPanel.classList.toggle("hidden");
        techToggle.setAttribute("aria-expanded", isHidden ? "false" : "true");
        techToggle.innerHTML = isHidden
          ? '<i class="fa-solid fa-wrench mr-1" aria-hidden="true"></i>Show Technical Details'
          : '<i class="fa-solid fa-wrench mr-1" aria-hidden="true"></i>Hide Technical Details';
      });
    }

    startStatsMonitoring();
    const ok = await startLocalMedia();
    if (ok) await initPeer();
  }

  return {
    init,
    callPeer,
    disconnectPeer,
    toggleMic,
    toggleCamera,
    endCall,
    hangup,
    toggleNoiseSuppression,
    shareScreen,
    stopScreenShare,
    copyRoomLink,
    state,
  };
})();
