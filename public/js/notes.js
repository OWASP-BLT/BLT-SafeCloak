/**
 * BLT-SafeCloak — notes.js
 * Encrypted AI notes with Web Crypto API (AES-GCM) + client-side AI features
 */

const NotesApp = (() => {
  const STORAGE_KEY = "safecloak_notes_v1";
  const PASS_KEY = "safecloak_notes_pass";
  const PREVIEW_LENGTH = 60;
  const STOPWORDS = new Set([
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "is",
    "was",
    "are",
    "were",
    "be",
    "been",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
    "i",
    "you",
    "we",
    "they",
    "he",
    "she",
    "his",
    "her",
    "our",
    "your",
    "their",
  ]);
  let notes = [];
  let activeNoteId = null;
  let passphrase = null;
  let saveTimer = null;

  function cloneNotesSnapshot(sourceNotes) {
    if (typeof structuredClone === "function") return structuredClone(sourceNotes);
    return JSON.parse(JSON.stringify(sourceNotes));
  }

  /* ── Persistence ── */
  function getPassphrase() {
    if (passphrase) return passphrase;
    // Derive a device-session passphrase from a stored random key
    let stored = localStorage.getItem(PASS_KEY);
    if (!stored) {
      stored = Crypto.randomId(24);
      localStorage.setItem(PASS_KEY, stored);
    }
    passphrase = stored;
    return passphrase;
  }

  async function saveNotes(nextNotes = notes) {
    await Crypto.saveEncrypted(STORAGE_KEY, nextNotes, getPassphrase());
  }

  async function loadNotes() {
    try {
      const loaded = await Crypto.loadEncrypted(STORAGE_KEY, getPassphrase());
      notes = loaded || [];
    } catch {
      notes = [];
    }
  }

  /* ── Note CRUD ── */
  function createNote() {
    const note = {
      id: Date.now().toString(),
      title: "Untitled Note",
      content: "",
      tags: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    notes.unshift(note);
    scheduleSave();
    renderNotesList();
    setActiveNote(note.id);
    document.getElementById("note-title") && document.getElementById("note-title").focus();
    showToast("New note created", "success");
    return note;
  }

  function deleteNote(id) {
    if (!confirm("Delete this note? This cannot be undone.")) return;
    notes = notes.filter((n) => n.id !== id);
    scheduleSave();
    if (activeNoteId === id) {
      activeNoteId = notes[0] ? notes[0].id : null;
    }
    renderNotesList();
    renderEditor();
    showToast("Note deleted", "info");
  }

  function updateActiveNote() {
    if (!activeNoteId) return;
    const note = notes.find((n) => n.id === activeNoteId);
    if (!note) return;
    const title = document.getElementById("note-title");
    const body = document.getElementById("note-body");
    if (title) note.title = title.value || "Untitled Note";
    if (body) note.content = body.value;
    note.updatedAt = Date.now();
    // Update list without full re-render
    const item = Array.from(document.querySelectorAll(".note-item")).find(
      (el) => el.getAttribute("data-id") === String(activeNoteId)
    );
    if (item) {
      const titleText = item.querySelector(".note-item-title-text");
      if (titleText) titleText.textContent = note.title;
      item.querySelector(".note-item-preview").textContent = note.content.slice(0, PREVIEW_LENGTH);
      item.querySelector(".note-item-date").textContent = formatDateShort(note.updatedAt);
    }
    scheduleSave();
  }

  function scheduleSave() {
    cancelAutosave();
    const notesSnapshot = cloneNotesSnapshot(notes);
    saveTimer = setTimeout(() => {
      saveNotes(notesSnapshot).catch((err) => {
        console.error("Failed to save notes:", err);
        showToast("Failed to save notes. Please try again.", "error");
      }).finally(() => {
        saveTimer = null;
      });
    }, 800);
  }

  function cancelAutosave() {
    if (saveTimer !== null) {
      clearTimeout(saveTimer);
      saveTimer = null;
    }
  }

  /* ── Rendering ── */
  function renderNotesList() {
    const container = document.getElementById("notes-list");
    if (!container) return;
    if (!notes.length) {
      container.innerHTML = `<div class="text-muted text-small" style="padding:1rem;text-align:center">No notes yet.<br>Click <strong>+ New</strong> to create one.</div>`;
      return;
    }
    container.innerHTML = notes
      .map(
        (n) => `
      <div class="note-item${n.id === activeNoteId ? " active" : ""}" data-id="${n.id}" tabindex="0" role="button">
        <div class="note-item-title">
          <span class="note-item-title-text">${escHtml(n.title)}</span>
          ${n.imported ? '<span class="note-imported-badge">Imported</span>' : ""}
        </div>
        <div class="note-item-preview">${escHtml(n.content.slice(0, PREVIEW_LENGTH))}</div>
        <div class="note-item-date">${formatDateShort(n.updatedAt)}</div>
      </div>
    `
      )
      .join("");

    container.querySelectorAll(".note-item").forEach((el) => {
      el.addEventListener("click", () => setActiveNote(el.dataset.id));
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter") setActiveNote(el.dataset.id);
      });
    });
  }

  function setActiveNote(id) {
    activeNoteId = id;
    renderNotesList();
    renderEditor();
  }

  function renderEditor() {
    const note = notes.find((n) => n.id === activeNoteId);
    const title = document.getElementById("note-title");
    const body = document.getElementById("note-body");
    const empty = document.getElementById("editor-empty");
    const editorWrapper = document.getElementById("editor-wrapper");
    const aiOutput = document.getElementById("ai-output");

    if (!note) {
      if (title) title.value = "";
      if (body) body.value = "";
      if (empty) empty.style.display = "flex";
      if (editorWrapper) editorWrapper.style.display = "none";
      return;
    }

    if (empty) empty.style.display = "none";
    if (editorWrapper) editorWrapper.style.display = "flex";
    if (title) title.value = note.title;
    if (body) body.value = note.content;
    if (aiOutput) aiOutput.textContent = "";

    // Update word count
    updateWordCount(note.content);
  }

  function updateWordCount(text) {
    const wc = document.getElementById("word-count");
    if (!wc) return;
    const words = text.trim().split(/\s+/).filter(Boolean).length;
    const chars = text.length;
    wc.textContent = `${words} words · ${chars} chars`;
  }

  /* ── AI Features (client-side text processing) ── */
  function summarize(text) {
    if (!text.trim()) return "(no content to summarize)";
    const sentences = text.match(/[^.!?]+[.!?]+/g) || [text];
    // Score sentences by keyword frequency
    const words = text
      .toLowerCase()
      .split(/\s+/)
      .filter((w) => w.length > 4);
    const freq = {};
    words.forEach((w) => {
      freq[w] = (freq[w] || 0) + 1;
    });
    const scored = sentences
      .map((s) => ({
        s,
        score: s
          .toLowerCase()
          .split(/\s+/)
          .reduce((sum, w) => sum + (freq[w] || 0), 0),
      }))
      .sort((a, b) => b.score - a.score);
    // Top 3 sentences in original order
    const top = scored.slice(0, Math.min(3, scored.length)).map((x) => x.s.trim());
    const indices = top.map((t) => sentences.indexOf(t)).sort((a, b) => a - b);
    return "📝 Summary:\n" + indices.map((i) => sentences[i].trim()).join(" ");
  }

  function extractKeyPoints(text) {
    if (!text.trim()) return "(no content)";
    const lines = text.split("\n").filter((l) => l.trim().length > 10);
    // Find lines with action words or emphasis
    const keywords =
      /\b(must|need|should|important|key|note|action|todo|decision|agree|consent|record|encrypt|secure|protect)\b/i;
    const keyLines = lines.filter((l) => keywords.test(l));
    const result = keyLines.length > 0 ? keyLines : lines.slice(0, 5);
    return (
      "🔑 Key Points:\n" +
      result
        .slice(0, 7)
        .map((l) => `• ${l.trim()}`)
        .join("\n")
    );
  }

  function extractActionItems(text) {
    if (!text.trim()) return "(no content)";
    const actionWords =
      /\b(todo|action|follow.?up|remind|schedule|send|review|check|complete|assign|deadline)\b/i;
    const lines = text.split("\n").filter((l) => actionWords.test(l) && l.trim().length > 5);
    if (!lines.length)
      return '✅ No explicit action items found.\n\nTip: include words like "todo", "action", "follow up", or "deadline" to auto-detect action items.';
    return (
      "✅ Action Items:\n" +
      lines
        .slice(0, 10)
        .map((l) => `• ${l.trim()}`)
        .join("\n")
    );
  }

  function wordFrequency(text) {
    if (!text.trim()) return "(no content)";
    const words = text.toLowerCase().match(/\b[a-z]{3,}\b/g) || [];
    const freq = {};
    words
      .filter((w) => !STOPWORDS.has(w))
      .forEach((w) => {
        freq[w] = (freq[w] || 0) + 1;
      });
    const top = Object.entries(freq)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);
    if (!top.length) return "(no significant words)";
    return "📊 Top Keywords:\n" + top.map(([w, c]) => `• ${w} (${c}x)`).join("\n");
  }

  /* ── Export ── */
  function exportNote(format = "txt") {
    const note = notes.find((n) => n.id === activeNoteId);
    if (!note) return;
    let content, mime, ext;
    if (format === "json") {
      content = JSON.stringify({ ...note, exported: new Date().toISOString() }, null, 2);
      mime = "application/json";
      ext = "json";
    } else if (format === "md") {
      content = `# ${note.title}\n\n*Created: ${new Date(note.createdAt).toISOString()}*\n*Updated: ${new Date(note.updatedAt).toISOString()}*\n\n---\n\n${note.content}`;
      mime = "text/markdown";
      ext = "md";
    } else {
      content = `${note.title}\n${"=".repeat(note.title.length)}\nCreated: ${new Date(note.createdAt).toLocaleString()}\nUpdated: ${new Date(note.updatedAt).toLocaleString()}\n\n${note.content}`;
      mime = "text/plain";
      ext = "txt";
    }
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${note.title.replace(/[^a-zA-Z0-9]/g, "_")}.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`Note exported as .${ext}`, "success");
  }

  function exportAllNotes() {
    const content = JSON.stringify({ notes, exported: new Date().toISOString() }, null, 2);
    const blob = new Blob([content], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `safecloak_notes_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`${notes.length} notes exported`, "success");
  }

  function isValidImportedNote(note) {
    return note && typeof note === "object" && typeof note.title === "string";
  }

  function normalizeImportedNotes(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.notes)) return payload.notes;
    if (isValidImportedNote(payload)) return [payload];
    return [];
  }

  function normalizeComparableId(value) {
    if (value === null || value === undefined) return "";
    return String(value).trim().toLowerCase().slice(0, 128);
  }

  function sanitizeIdToken(value) {
    return String(value)
      .trim()
      .replace(/[^a-zA-Z0-9_-]/g, "-")
      .replace(/-+/g, "-")
      .replace(/^[-_]+|[-_]+$/g, "")
      .slice(0, 64);
  }

  function createSafeImportedId(seed) {
    const token = sanitizeIdToken(seed);
    if (token) return token;
    return `imp-${Crypto.randomId(10).toLowerCase()}`;
  }

  function sanitizeImportedNote(note, fallbackId, importSource) {
    const now = Date.now();
    const createdAt = Number.isFinite(note.createdAt) ? Number(note.createdAt) : now;
    const updatedAt = Number.isFinite(note.updatedAt) ? Number(note.updatedAt) : createdAt;
    const tags = Array.isArray(note.tags)
      ? note.tags.filter((tag) => typeof tag === "string" && tag.length <= 100).slice(0, 20)
      : [];

    const normalizedId = createSafeImportedId(note.id);
    return {
      id: normalizedId || createSafeImportedId(fallbackId),
      title:
        typeof note.title === "string" && note.title.trim()
          ? note.title.trim()
          : "Untitled Note",
      content: typeof note.content === "string" ? note.content : "",
      tags,
      createdAt,
      updatedAt,
      imported: true,
      importedAt: now,
      importSource: importSource || "manual-import",
    };
  }

  function chooseImportMode(rawImportCount, overlapCount) {
    return new Promise((resolve) => {
      const noteWord = rawImportCount === 1 ? "note" : "notes";
      const conflictVerb = overlapCount === 1 ? "is" : "are";
      const conflictSummary =
        overlapCount === rawImportCount
          ? `Found <strong>${rawImportCount}</strong> ${noteWord} in the selected import file, out of which all ${conflictVerb} conflicting.`
          : `Found <strong>${rawImportCount}</strong> ${noteWord} in the selected import file, out of which <strong>${overlapCount}</strong> ${conflictVerb} conflicting.`;
      const overlay = document.createElement("div");
      overlay.className = "modal-overlay";
      overlay.style.display = "flex";
      overlay.innerHTML = `
        <div class="modal notes-import-modal" role="dialog" aria-modal="true" aria-labelledby="notes-import-title">
          <button
            class="notes-import-close"
            id="notes-import-close"
            type="button"
            aria-label="Close conflict dialog"
          >
            &times;
          </button>
          <h3 id="notes-import-title">Resolve Note Conflicts</h3>
          <p class="notes-import-summary">${conflictSummary}</p>
          <p class="notes-import-footnote">
            <strong>*</strong> Comparison is based on note IDs, not note content.
          </p>
          <div class="notes-import-actions">
            <button
              class="inline-flex items-center justify-center gap-2 rounded-md border border-neutral-border bg-white px-3 py-1.5 text-xs font-bold text-gray-700 transition hover:border-primary hover:text-primary hover:bg-red-50"
              id="notes-import-skip-conflicts"
              type="button"
            >
              Import New Only
            </button>
            <button
              class="inline-flex items-center justify-center gap-2 rounded-md border border-transparent bg-primary px-3 py-1.5 text-xs font-bold text-white transition hover:bg-primary-hover"
              id="notes-import-replace-conflicts"
              type="button"
            >
              Import All (Replace Conflicts)
            </button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      const previouslyFocusedElement = document.activeElement;
      const getFocusableElements = () =>
        Array.from(
          overlay.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
          )
        );
      let isClosed = false;
      const onKeyDown = (event) => {
        if (event.key === "Escape") {
          cleanup("cancel");
          return;
        }
          if (event.key !== "Tab") return;
        const focusable = getFocusableElements();
        if (!focusable.length) {
          event.preventDefault();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const current = document.activeElement;
        if (event.shiftKey) {
          if (current === first || !overlay.contains(current)) {
            event.preventDefault();
            last.focus();
          }
        } else if (current === last || !overlay.contains(current)) {
          event.preventDefault();
          first.focus();
        }
      };
      document.addEventListener("keydown", onKeyDown);
      const initialFocusTarget =
        overlay.querySelector("#notes-import-close") || overlay.querySelector("#notes-import-skip-conflicts");
      if (initialFocusTarget && typeof initialFocusTarget.focus === "function") {
        initialFocusTarget.focus();
      }

      const cleanup = (decision) => {
        if (isClosed) return;
        isClosed = true;
        document.removeEventListener("keydown", onKeyDown);
        overlay.remove();
        if (
          previouslyFocusedElement &&
          typeof previouslyFocusedElement.focus === "function" &&
          document.contains(previouslyFocusedElement)
        ) {
          previouslyFocusedElement.focus();
        }
        resolve(decision);
      };

      overlay.querySelector("#notes-import-close").addEventListener("click", () => cleanup("cancel"));
      overlay
        .querySelector("#notes-import-skip-conflicts")
        .addEventListener("click", () => cleanup("skip_conflicts"));
      overlay
        .querySelector("#notes-import-replace-conflicts")
        .addEventListener("click", () => cleanup("replace_conflicts"));
      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) cleanup("cancel");
      });
    });
  }

  function readJsonFileFromPicker() {
    return new Promise((resolve, reject) => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = "application/json,.json";
      input.style.display = "none";
      document.body.appendChild(input);
      let settled = false;

      const cleanup = () => {
        window.removeEventListener("focus", onWindowFocus);
        input.remove();
      };

      const settleResolve = (value) => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(value);
      };

      const settleReject = (error) => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(error);
      };

      const onWindowFocus = () => {
        // File picker closes before focus returns. Give change handlers a small window first.
        setTimeout(() => {
          if (settled) return;
          const file = input.files && input.files[0];
          if (!file) settleResolve(null);
        }, 200);
      };

      window.addEventListener("focus", onWindowFocus);

      input.addEventListener("change", async () => {
        try {
          const file = input.files && input.files[0];
          if (!file) {
            settleResolve(null);
            return;
          }
          const text = await file.text();
          settleResolve({ text, fileName: file.name || "notes.json" });
        } catch (err) {
          settleReject(err);
        }
      });

      input.click();
    });
  }

  async function importNotesFromFile() {
    try {
      const fileData = await readJsonFileFromPicker();
      if (fileData === null) return;

      let parsed;
      try {
        parsed = JSON.parse(fileData.text);
      } catch {
        showToast("Import failed: file is not valid JSON", "error");
        return;
      }

      const rawImportedEntries = normalizeImportedNotes(parsed);
      const rawImportCount = rawImportedEntries.length;
      const imported = rawImportedEntries.filter(isValidImportedNote);
      if (!imported.length) {
        showToast("Import failed: no valid notes found in file", "error");
        return;
      }

      const normalized = imported.map((note, index) =>
        sanitizeImportedNote(note, `imported-${Date.now()}-${index}`, fileData.fileName)
      );
      const seenIds = new Set();
      const uniqueImported = normalized.filter((note) => {
        const key = normalizeComparableId(note.id);
        if (!key || seenIds.has(key)) return false;
        seenIds.add(key);
        return true;
      });

      const existingIds = new Set(notes.map((n) => normalizeComparableId(n.id)).filter(Boolean));
      const overlapCount = uniqueImported.filter((n) => existingIds.has(normalizeComparableId(n.id))).length;
      let mode = "skip_conflicts";
      if (overlapCount > 0) {
        mode = await chooseImportMode(rawImportCount, overlapCount);
        if (mode === "cancel") return;
      }

      let addedCount = 0;
      let skippedCount = 0;
      let replacedCount = 0;
      let nextNotes;

      if (mode === "replace_conflicts") {
        const byId = new Map(
          notes
            .map((n) => [normalizeComparableId(n.id), n])
            .filter(([key]) => Boolean(key))
        );
        uniqueImported.forEach((importedNote) => {
          const key = normalizeComparableId(importedNote.id);
          if (byId.has(key)) {
            replacedCount += 1;
          } else {
            addedCount += 1;
          }
          byId.set(key, importedNote);
        });
        nextNotes = Array.from(byId.values()).sort((a, b) => b.updatedAt - a.updatedAt);
      } else {
        const additions = uniqueImported.filter((n) => !existingIds.has(normalizeComparableId(n.id)));
        skippedCount = uniqueImported.length - additions.length;
        addedCount = additions.length;
        if (addedCount === 0) {
          const entryWord = rawImportCount === 1 ? "entry" : "entries";
          showToast(
            `No new notes to import from ${rawImportCount} parsed ${entryWord}. All selected notes already exist by ID.`,
            "info"
          );
          return;
        }
        nextNotes = [...additions, ...notes].sort((a, b) => b.updatedAt - a.updatedAt);
      }

      const nextActiveNoteId =
        !nextNotes.length
          ? null
          : nextNotes.some((n) => n.id === activeNoteId)
            ? activeNoteId
            : nextNotes[0].id;

      cancelAutosave();
      await saveNotes(nextNotes);
      notes = nextNotes;
      activeNoteId = nextActiveNoteId;
      renderNotesList();
      renderEditor();

      if (mode === "replace_conflicts") {
        const entryWord = rawImportCount === 1 ? "entry" : "entries";
        showToast(
          `Processed ${rawImportCount} parsed ${entryWord}: ${addedCount + replacedCount} notes imported (${addedCount} added, ${replacedCount} replaced)`,
          "success"
        );
      } else if (skippedCount > 0) {
        const entryWord = rawImportCount === 1 ? "entry" : "entries";
        const conflictWord = skippedCount === 1 ? "conflict" : "conflicts";
        showToast(
          `Processed ${rawImportCount} parsed ${entryWord}: imported ${addedCount} new notes and skipped ${skippedCount} ${conflictWord}`,
          "success"
        );
      } else {
        const entryWord = rawImportCount === 1 ? "entry" : "entries";
        const noteWordImported = addedCount === 1 ? "note" : "notes";
        showToast(
          `Processed ${rawImportCount} parsed ${entryWord}: imported ${addedCount} ${noteWordImported}`,
          "success"
        );
      }
    } catch (err) {
      console.error("Import notes failed:", err);
      showToast("Import failed. Please try again.", "error");
    }
  }

  /* ── Utils ── */
  function escHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ── Init ── */
  async function init() {
    await loadNotes();
    renderNotesList();
    if (notes.length > 0) setActiveNote(notes[0].id);

    // Wire up editor inputs
    const titleEl = document.getElementById("note-title");
    const bodyEl = document.getElementById("note-body");
    if (titleEl)
      titleEl.addEventListener("input", () => {
        updateActiveNote();
      });
    if (bodyEl)
      bodyEl.addEventListener("input", () => {
        updateActiveNote();
        updateWordCount(bodyEl.value);
      });

    // Wire up toolbar buttons
    document.getElementById("btn-new-note") &&
      document.getElementById("btn-new-note").addEventListener("click", createNote);
    document.getElementById("btn-delete-note") &&
      document
        .getElementById("btn-delete-note")
        .addEventListener("click", () => deleteNote(activeNoteId));
    document.getElementById("btn-export-txt") &&
      document.getElementById("btn-export-txt").addEventListener("click", () => exportNote("txt"));
    document.getElementById("btn-export-md") &&
      document.getElementById("btn-export-md").addEventListener("click", () => exportNote("md"));
    document.getElementById("btn-export-json") &&
      document
        .getElementById("btn-export-json")
        .addEventListener("click", () => exportNote("json"));
    document.getElementById("btn-export-all") &&
      document.getElementById("btn-export-all").addEventListener("click", exportAllNotes);
    document.getElementById("btn-import-notes") &&
      document.getElementById("btn-import-notes").addEventListener("click", importNotesFromFile);

    // AI buttons
    document.getElementById("btn-summarize") &&
      document.getElementById("btn-summarize").addEventListener("click", () => {
        const note = notes.find((n) => n.id === activeNoteId);
        if (!note) return showToast("No note selected", "warning");
        const out = document.getElementById("ai-output");
        if (out) out.textContent = summarize(note.content);
      });

    document.getElementById("btn-keypoints") &&
      document.getElementById("btn-keypoints").addEventListener("click", () => {
        const note = notes.find((n) => n.id === activeNoteId);
        if (!note) return showToast("No note selected", "warning");
        const out = document.getElementById("ai-output");
        if (out) out.textContent = extractKeyPoints(note.content);
      });

    document.getElementById("btn-actions") &&
      document.getElementById("btn-actions").addEventListener("click", () => {
        const note = notes.find((n) => n.id === activeNoteId);
        if (!note) return showToast("No note selected", "warning");
        const out = document.getElementById("ai-output");
        if (out) out.textContent = extractActionItems(note.content);
      });

    document.getElementById("btn-keywords") &&
      document.getElementById("btn-keywords").addEventListener("click", () => {
        const note = notes.find((n) => n.id === activeNoteId);
        if (!note) return showToast("No note selected", "warning");
        const out = document.getElementById("ai-output");
        if (out) out.textContent = wordFrequency(note.content);
      });
  }

  return {
    init,
    createNote,
    deleteNote,
    exportNote,
    exportAllNotes,
    importNotesFromFile,
    notes: () => notes,
  };
})();

document.addEventListener("DOMContentLoaded", () => NotesApp.init());
