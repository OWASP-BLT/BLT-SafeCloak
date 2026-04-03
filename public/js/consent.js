/**
 * BLT-SafeCloak — consent.js
 * Consent protection: record, timestamp, hash, store, and export consent events
 */

const ConsentManager = (() => {
  const STORAGE_KEY = "safecloak_consent_log_v1";
  let log = [];

  /* ── ID generation ──
   * Previously: Date.now().toString() + Math.random().toString(36).slice(2, 7)
   *
   * Bug: Date.now() has millisecond resolution. Two events recorded within the
   * same millisecond (e.g. rapid consent + session-start during page load) could
   * produce the same timestamp prefix.  Math.random() adds only ~5 base-36 chars
   * (~25 bits of entropy), giving a roughly 1-in-33M collision chance per pair —
   * low but non-zero, and collision would cause verifyEntry() to silently verify
   * the wrong entry because Array.find() returns the first match.
   *
   * Fix: use crypto.getRandomValues() for a 128-bit hex UUID-style token.
   * This makes collisions astronomically unlikely (1 in 2^128) regardless of
   * timing, and keeps the id fully opaque and unguessable.
   */
  function generateId() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes)
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  }

  /* ── Load / Save ── */
  function load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      log = raw ? JSON.parse(raw) : [];
      // Guard: ensure log is always an array even if storage is corrupted.
      if (!Array.isArray(log)) log = [];
    } catch {
      log = [];
    }
  }

  function save() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(log));
    } catch (err) {
      console.error("Failed to save consent log:", err);
    }
  }

  /* ── Record a consent event ── */
  async function record(event) {
    const ts = Date.now();
    const entry = {
      // Use crypto-random id instead of timestamp + Math.random() to eliminate
      // collision risk when multiple events are recorded in the same millisecond.
      id: generateId(),
      type: event.type || "recorded", // 'given' | 'withdrawn' | 'recorded'
      name: event.name || "Unnamed event",
      details: event.details || "",
      purpose: event.purpose || "",
      participants: event.participants || [],
      timestamp: ts,
      isoTime: new Date(ts).toISOString(),
      userAgent: navigator.userAgent.slice(0, 100),
    };
    // Create a tamper-evident hash of the entry data
    try {
      const data = `${entry.type}|${entry.name}|${entry.isoTime}|${entry.details}`;
      entry.hash = await Crypto.sha256(data);
    } catch {
      entry.hash = "hash-unavailable";
    }

    log.unshift(entry);
    save();
    return entry;
  }

  /* ── Rendering ── */
  function renderLog(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!log.length) {
      container.innerHTML = `<div class="text-muted text-small" style="padding:1rem;text-align:center">No consent events recorded yet.</div>`;
      return;
    }
    container.innerHTML = log
      .map(
        (entry) => `
      <div class="consent-entry" data-id="${entry.id}">
        <div class="consent-entry-header">
          <span class="consent-entry-type ${entry.type}">${capitalise(entry.type)}</span>
          <span class="consent-entry-time">${formatDateTime(entry.timestamp)}</span>
        </div>
        <div class="consent-entry-name">${escHtml(entry.name)}</div>
        ${entry.details ? `<div class="consent-entry-details">${escHtml(entry.details)}</div>` : ""}
        ${entry.purpose ? `<div class="consent-entry-details text-secondary" style="margin-top:0.2rem">Purpose: ${escHtml(entry.purpose)}</div>` : ""}
        <div class="consent-entry-hash" title="Integrity hash">🔏 ${entry.hash}</div>
        <div style="margin-top:0.5rem;display:flex;gap:0.5rem">
          <button class="btn btn-sm btn-secondary" data-action="verify" data-entry-id="${entry.id}">Verify</button>
          <button class="btn btn-sm btn-danger" data-action="delete" data-entry-id="${entry.id}">Delete</button>
        </div>
      </div>
    `
      )
      .join("");
  }

  /* ── Verify a specific entry ── */
  async function verifyEntry(id) {
    // Previously used log.find() which returns the first match.
    // With the old timestamp-based ids a collision would cause this to verify
    // a different entry's hash against the wrong data, silently returning
    // "untampered" for a record that was never actually checked.
    // Now ids are 128-bit random hex strings so collisions cannot occur, but
    // we still validate that exactly one entry matches to surface any corruption.
    const matches = log.filter((e) => e.id === id);
    if (matches.length === 0) return showToast("Entry not found", "error");
    if (matches.length > 1) {
      // Should never happen with crypto-random ids, but handle defensively.
      return showToast("⚠️ Duplicate entry ids detected — log may be corrupted", "error");
    }
    const entry = matches[0];
    try {
      const data = `${entry.type}|${entry.name}|${entry.isoTime}|${entry.details}`;
      const hash = await Crypto.sha256(data);
      if (hash === entry.hash) {
        showToast("✅ Entry integrity verified — untampered", "success");
      } else {
        showToast("⚠️ Hash mismatch — entry may have been tampered with!", "error");
      }
    } catch {
      showToast("Verification failed", "error");
    }
  }

  /* ── Delete entry ── */
  function deleteEntry(id) {
    if (!confirm("Delete this consent record? This cannot be undone.")) return;
    const before = log.length;
    log = log.filter((e) => e.id !== id);
    if (log.length === before) {
      // Nothing was removed — id didn't match any entry.
      showToast("Entry not found", "error");
      return;
    }
    save();
    renderLog("consent-log");
    showToast("Consent record deleted", "info");
    updateStats();
  }

  /* ── Export ── */
  function exportLog(format = "json") {
    const exportData = {
      exportedAt: new Date().toISOString(),
      exportedBy: "BLT-SafeCloak",
      version: "1.0",
      totalEntries: log.length,
      entries: log,
    };

    let content, mime, ext;
    if (format === "csv") {
      const headers = "ID,Type,Name,Details,Purpose,Timestamp,ISO Time,Hash";
      const rows = log.map((e) =>
        [
          e.id,
          e.type,
          `"${e.name.replace(/"/g, '""')}"`,
          `"${e.details.replace(/"/g, '""')}"`,
          `"${e.purpose.replace(/"/g, '""')}"`,
          e.timestamp,
          e.isoTime,
          e.hash,
        ].join(",")
      );
      content = [headers, ...rows].join("\n");
      mime = "text/csv";
      ext = "csv";
    } else {
      content = JSON.stringify(exportData, null, 2);
      mime = "application/json";
      ext = "json";
    }

    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `safecloak_consent_${Date.now()}.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`Consent log exported as .${ext}`, "success");
  }

  /* ── Stats ── */
  function updateStats() {
    const totalEl = document.getElementById("stat-total");
    const givenEl = document.getElementById("stat-given");
    const withdrawnEl = document.getElementById("stat-withdrawn");
    const recordedEl = document.getElementById("stat-recorded");
    if (totalEl) totalEl.textContent = log.length;
    if (givenEl) givenEl.textContent = log.filter((e) => e.type === "given").length;
    if (withdrawnEl) withdrawnEl.textContent = log.filter((e) => e.type === "withdrawn").length;
    if (recordedEl) recordedEl.textContent = log.filter((e) => e.type === "recorded").length;
  }

  /* ── Utils ── */
  function capitalise(s) {
    return s ? s[0].toUpperCase() + s.slice(1) : "";
  }
  function escHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ── Init ── */
  async function init() {
    load();
    renderLog("consent-log");
    updateStats();

    const logContainer = document.getElementById("consent-log");
    if (logContainer) {
      logContainer.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-action][data-entry-id]");
        if (!button) return;
        const action = button.getAttribute("data-action");
        const entryId = button.getAttribute("data-entry-id");
        if (!entryId) return;
        if (action === "verify") {
          verifyEntry(entryId);
        } else if (action === "delete") {
          deleteEntry(entryId);
        }
      });
    }

    // Record form
    const form = document.getElementById("consent-form");
    if (form) {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const entry = await record({
          type: fd.get("consent-type"),
          name: fd.get("participant-name"),
          details: fd.get("details"),
          purpose: fd.get("purpose"),
        });
        renderLog("consent-log");
        updateStats();
        form.reset();
        showToast("Consent recorded & hashed", "success");
      });
    }

    // Export buttons
    document.getElementById("btn-export-json") &&
      document.getElementById("btn-export-json").addEventListener("click", () => exportLog("json"));
    document.getElementById("btn-export-csv") &&
      document.getElementById("btn-export-csv").addEventListener("click", () => exportLog("csv"));

    // Clear all
    document.getElementById("btn-clear-log") &&
      document.getElementById("btn-clear-log").addEventListener("click", () => {
        if (!confirm("Clear ALL consent records? This cannot be undone.")) return;
        log = [];
        save();
        renderLog("consent-log");
        updateStats();
        showToast("Consent log cleared", "info");
      });
  }

  return {
    init,
    record,
    verifyEntry,
    deleteEntry,
    exportLog,
    getLog: () => log,
    renderLog,
    updateStats,
  };
})();

document.addEventListener("DOMContentLoaded", () => ConsentManager.init());
