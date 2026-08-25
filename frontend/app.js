/* PocketAI dashboard — vanilla JS, no frameworks, no external requests. */
"use strict";

const $ = (id) => document.getElementById(id);

const MODE_STORAGE_KEY = "pocketai.mode";
const WELCOME_STORAGE_KEY = "pocketai.welcomed";
const DEVMODE_STORAGE_KEY = "pocketai.devmode";
const THEME_STORAGE_KEY = "pocketai.theme";

/* Thinking styles: the only user-facing orchestration control. Skills and
   pipelines stay internal and are never shown outside developer mode. */
const STYLE_OPTIONS = [
  { value: "auto", label: "Auto", desc: "Pick the right style automatically" },
  { value: "fast", label: "Fast", desc: "Quick answers" },
  { value: "balanced", label: "Balanced", desc: "Normal tasks" },
  { value: "deep", label: "Deep Think", desc: "Complex reasoning" },
  { value: "research", label: "Research", desc: "Analysis and comparison" },
  { value: "build", label: "Build", desc: "Coding and creation" },
];

const STYLE_LABELS = {};
for (const opt of STYLE_OPTIONS) STYLE_LABELS[opt.value] = opt.label;

function devModeEnabled() {
  return localStorage.getItem(DEVMODE_STORAGE_KEY) === "1";
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light") {
    root.setAttribute("data-theme", "light");
  } else {
    root.removeAttribute("data-theme");
  }
  localStorage.setItem(THEME_STORAGE_KEY, theme);
}

function initTheme() {
  const saved = localStorage.getItem(THEME_STORAGE_KEY);
  if (saved === "light" || saved === "dark") {
    applyTheme(saved);
  } else {
    // Default to dark, but respect system preference if no saved preference
    let prefersLight = false;
    if (window.matchMedia) {
      prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
    }
    applyTheme(prefersLight ? "light" : "dark");
  }
}

const state = {
  conversationId: null,
  busy: false,
  style: "auto",
  abortController: null,
  // Pending image attachment (clipboard paste or file upload) before send.
  pendingImage: null,
  // Last image-related user-facing error (shown in the composer preview box).
  attachmentError: null,
};

// Sequence token guarding async FileReader results. Each new image action
// (select / remove / send) bumps it so a late reader callback can never
// resurrect an image the user already removed or replaced (see F1).
let imageLoadSeq = 0;

// Sequence token guarding async conversation loads. Rapid clicks on the
// sidebar each bump it so only the LATEST selection renders — out-of-order
// responses from earlier clicks are discarded instead of painting the wrong
// conversation (see conversation-loading race).
let convLoadSeq = 0;

/* Image-input limits (best-effort client guard; the server enforces the real
   caps from config/image). Kept in sync with backend defaults. */
const IMAGE_LIMITS = {
  maxBytes: 10 * 1024 * 1024,
  types: ["image/png", "image/jpeg", "image/webp", "image/bmp", "image/x-ms-bmp"],
};

/* ---------------- API helpers ---------------- */

async function api(path, options = {}) {
  const resp = await fetch(path, options);
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!resp.ok) {
    const detail = data && data.error ? data.error : resp.statusText;
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return data;
}

/* ---------------- error messages ----------------
 * Map technical backend errors to student-friendly text. */

function friendlyError(msg) {
  if (!msg) return "Something went wrong. Please try again.";
  const m = msg.toLowerCase();
  if (m.includes("memory") || m.includes("ram") || m.includes("503"))
    return "PocketAI is low on available memory. Close some applications and try again.";
  if (m.includes("model server") || m.includes("502") || m.includes("unreachable"))
    return "PocketAI's AI engine isn't ready yet. Please wait a moment and try again.";
  if (m.includes("timeout") || m.includes("timed out"))
    return "The request took too long. Try a shorter question or a simpler task.";
  if (m.includes("ocr") && m.includes("not available"))
    return "Image reading isn't available on this build. Try typing your question instead.";
  return `Something went wrong: ${msg}`;
}

/* ---------------- status bar ----------------
 * Status pills (model state, hardware profile, free RAM) are technical
 * detail: hidden for normal users, shown only in developer mode. */

function setPill(el, text, cls) {
  el.textContent = text;
  el.className = "status-pill" + (cls ? ` ${cls}` : "");
}

function applyDevModeVisibility() {
  const el = $("status-cluster");
  if (el) el.style.display = devModeEnabled() ? "" : "none";
}

async function refreshStatus() {
  const dev = devModeEnabled();
  try {
    const health = await api("/health");
    const backend = health.backend || {};
    if (backend.version) $("brand-version").textContent = `v${backend.version}`;
    applyModelDisplay(health);
    if (dev) {
      const model = health.model || {};
      const runtime = health.runtime || {};
      const pill = $("status-model");
      if (model.status === "ready") setPill(pill, "model: ready", "ok");
      else if (runtime.status === "stopped") setPill(pill, "model: stopped", "err");
      else setPill(pill, `model: ${model.status}`, "warn");
    }
  } catch {
    applyModelDisplay(null);
    if (dev) setPill($("status-model"), "backend: unreachable", "err");
  }
  if (!dev) return;
  try {
    const sys = await api("/system");
    const p = sys.profile || {};
    setPill($("status-profile"), `profile: ${p.name || "?"}`, "ok");
    const ram = sys.ram || {};
    const freeGb = (ram.available_mb / 1024).toFixed(1);
    setPill($("status-ram"), `free: ${freeGb} GB`, ram.available_mb < 1200 ? "warn" : "");
  } catch { /* status pills stay as-is */ }
}

/* Model indicator (top-right): real values from /health, never
   hardcoded. Normal users see name + runtime state only — no RAM,
   profiles or other hardware data. */
function applyModelDisplay(health) {
  const title = $("model-menu-title");
  const runtime = $("model-menu-runtime");
  const indicator = $("model-indicator");
  if (!health) {
    if (title) title.textContent = "Model";
    if (runtime) {
      runtime.textContent = "Runtime: Unreachable";
      runtime.className = "model-menu-runtime err";
    }
    if (indicator) indicator.textContent = "Model: Unreachable";
    return;
  }
  const model = health.model || {};
  const rt = health.runtime || {};
  const name = model.name || model.alias || "Model";
  if (title) title.textContent = name;
  if (indicator) indicator.textContent = name;
  let label;
  let cls = "";
  if (model.status === "ready") {
    label = "Ready";
    cls = "ok";
  } else if (rt.status === "stopped") {
    label = "Stopped";
    cls = "err";
  } else {
    label = model.status || "Unknown";
    cls = "warn";
  }
  if (runtime) {
    runtime.textContent = `Runtime: ${label}`;
    runtime.className = `model-menu-runtime ${cls}`;
  }
}

/* ---------------- chat ---------------- */

function addMessage(role, text, meta = {}) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble" + (meta.clarification ? " clarification" : "");
  if (role === "assistant") {
    // Assistant replies are rendered markdown; every character of model
    // output is escaped by renderMarkdown before tags are inserted.
    bubble.classList.add("md");
    bubble.innerHTML = renderMarkdown(text);
  } else {
    // User messages stay plain text — never interpreted as markdown.
    bubble.textContent = text;
  }

  // Image attachment: a session thumbnail (data URL) when just sent, or a
  // lightweight chip when reloaded from history (no image bytes stored).
  if (meta.image) {
    const img = document.createElement("img");
    img.className = "attached-img";
    img.src = meta.image;
    img.alt = "attached image";
    // Real browsers can refuse to paint an oversized/invalid data URL; never
    // leave a broken-image glyph. Degrade to a chip on error.
    img.onerror = () => {
      img.remove();
      const chip = document.createElement("div");
      chip.className = "img-chip";
      chip.textContent = "\u{1F4F7} image \u00b7 preview unavailable";
      wrap.appendChild(chip);
    };
    wrap.appendChild(img);
  } else if (meta.attachment && meta.attachment.type === "image") {
    const chip = document.createElement("div");
    chip.className = "img-chip";
    const conf = meta.attachment.ocr_confidence;
    let ocrText = " \u00b7 no text read";
    if (meta.attachment.ocr_available) {
      if (conf != null) {
        if (conf >= 80) ocrText = " \u00b7 OCR detected text \u2014 high confidence";
        else if (conf >= 50) ocrText = " \u00b7 OCR detected text \u2014 medium confidence";
        else ocrText = " \u00b7 OCR detected text \u2014 low confidence";
      } else {
        ocrText = " \u00b7 OCR detected text";
      }
    }
    chip.textContent = `\u{1F4F7} image${ocrText}`;
    wrap.appendChild(chip);
  }
  wrap.appendChild(bubble);

  if (meta.warning) {
    const warn = document.createElement("div");
    warn.className = "msg-warning";
    warn.textContent = `\u26A0 ${meta.warning}`;
    wrap.appendChild(warn);
  }

  if (meta.reasoning) {
    const details = document.createElement("details");
    details.className = "reasoning";
    const summary = document.createElement("summary");
    summary.textContent = "show reasoning";
    const pre = document.createElement("pre");
    pre.textContent = meta.reasoning;
    details.append(summary, pre);
    wrap.appendChild(details);
  }

  if (meta.metaLine) {
    const m = document.createElement("div");
    m.className = "msg-meta";
    m.textContent = meta.metaLine;
    wrap.appendChild(m);
  }

  $("messages").appendChild(wrap);
  scrollDown(true);
  return wrap;
}

/* Auto-scroll: new messages always follow; streaming deltas only scroll
   when the user is already near the bottom, so reading back up is never
   yanked away. */
function nearBottom() {
  const s = $("chat-scroll");
  return s.scrollHeight - s.scrollTop - s.clientHeight < 80;
}

function scrollDown(force = false) {
  const scroller = $("chat-scroll");
  if (force || nearBottom()) scroller.scrollTop = scroller.scrollHeight;
}

/* ---------------- markdown rendering ----------------
 * Minimal, dependency-free markdown renderer for assistant replies.
 * Security model: every character of model output is HTML-escaped BEFORE
 * any markup is inserted, so no input can inject tags, attributes or
 * scripts. Only http(s) links are rendered as anchors. */

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* Inline formatting on escaped text: `code`, **bold**, *italic*,
   [label](https://...). Inline code is stashed behind placeholders first
   so its contents are never treated as formatting. */
function renderInline(raw) {
  let s = escapeHtml(raw);
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, code) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*\s][^*]*)\*/g, "<em>$1</em>");
  s = s.replace(/(^|[^\w])_([^_\s][^_]*?)_(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) =>
    /^https?:\/\//i.test(url)
      ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`
      : label,
  );
  return s.replace(
    /\u0000(\d+)\u0000/g,
    (_, i) => `<code>${codes[Number(i)]}</code>`,
  );
}

function splitTableRow(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function codeBlockHtml(lang, code) {
  const label = lang ? escapeHtml(lang) : "code";
  return (
    `<div class="code-block">` +
    `<div class="code-bar"><span class="code-lang">${label}</span>` +
    `<button type="button" class="copy-btn" title="Copy code">Copy</button></div>` +
    `<pre><code>${escapeHtml(code)}</code></pre>` +
    `</div>`
  );
}

function tableHtml(header, rows) {
  const thead =
    "<thead><tr>" +
    header.map((c) => `<th>${renderInline(c)}</th>`).join("") +
    "</tr></thead>";
  const tbody = rows
    .map(
      (r) =>
        "<tr>" +
        header.map((_, ci) => `<td>${renderInline(r[ci] || "")}</td>`).join("") +
        "</tr>",
    )
    .join("");
  return `<div class="md-table-wrap"><table class="md-table">${thead}<tbody>${tbody}</tbody></table></div>`;
}

const TABLE_SEPARATOR_RE = /^\s*\|?\s*:?-{2,}[:\s|-]*$/;
const HR_RE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const BULLET_RE = /^\s*[-*+]\s+/;
const ORDERED_RE = /^\s*\d+[.)]\s+/;
const QUOTE_RE = /^\s*>\s?/;
const HEADING_RE = /^\s{0,3}(#{1,6})\s+(.*)$/;
const FENCE_RE = /^\s*```/;

function renderMarkdown(raw) {
  const lines = String(raw).replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    // Fenced code block (```lang ... ```).
    if (FENCE_RE.test(line)) {
      const lang = line.replace(FENCE_RE, "").trim();
      const buf = [];
      i += 1;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1; // skip the closing fence (or run to EOF when unclosed)
      out.push(codeBlockHtml(lang, buf.join("\n")));
      continue;
    }

    // Heading (# .. ######), shifted two levels down for chat sizing.
    const heading = line.match(HEADING_RE);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 6);
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    // Table: a | header row followed by a |---| separator row.
    if (
      line.trim().startsWith("|") &&
      i + 1 < lines.length &&
      TABLE_SEPARATOR_RE.test(lines[i + 1]) &&
      lines[i + 1].includes("-")
    ) {
      const header = splitTableRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      out.push(tableHtml(header, rows));
      continue;
    }

    // Bullet list.
    if (BULLET_RE.test(line)) {
      const items = [];
      while (i < lines.length && BULLET_RE.test(lines[i])) {
        items.push(lines[i].replace(BULLET_RE, ""));
        i += 1;
      }
      out.push(
        `<ul>${items.map((t) => `<li>${renderInline(t)}</li>`).join("")}</ul>`,
      );
      continue;
    }

    // Numbered list.
    if (ORDERED_RE.test(line)) {
      const items = [];
      while (i < lines.length && ORDERED_RE.test(lines[i])) {
        items.push(lines[i].replace(ORDERED_RE, ""));
        i += 1;
      }
      out.push(
        `<ol>${items.map((t) => `<li>${renderInline(t)}</li>`).join("")}</ol>`,
      );
      continue;
    }

    // Blockquote.
    if (QUOTE_RE.test(line)) {
      const buf = [];
      while (i < lines.length && QUOTE_RE.test(lines[i])) {
        buf.push(lines[i].replace(QUOTE_RE, ""));
        i += 1;
      }
      out.push(`<blockquote>${buf.map(renderInline).join("<br>")}</blockquote>`);
      continue;
    }

    // Horizontal rule.
    if (HR_RE.test(line)) {
      out.push("<hr>");
      i += 1;
      continue;
    }

    // Paragraph: consecutive plain lines, soft breaks kept.
    const buf = [line];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !FENCE_RE.test(lines[i]) &&
      !HEADING_RE.test(lines[i]) &&
      !BULLET_RE.test(lines[i]) &&
      !ORDERED_RE.test(lines[i]) &&
      !QUOTE_RE.test(lines[i]) &&
      !HR_RE.test(lines[i]) &&
      !lines[i].trim().startsWith("|")
    ) {
      buf.push(lines[i]);
      i += 1;
    }
    out.push(`<p>${buf.map(renderInline).join("<br>")}</p>`);
  }
  return out.join("");
}

/* Copy button on code blocks (event-delegated; clipboard API with a
   legacy fallback for non-secure contexts). */
function copyCodeBlock(btn) {
  const block = btn.closest(".code-block");
  const code = block && block.querySelector("pre code");
  if (!code) return;
  const text = code.innerText;
  const done = () => {
    btn.textContent = "Copied";
    window.setTimeout(() => {
      btn.textContent = "Copy";
    }, 1500);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => legacyCopy(text, done));
  } else {
    legacyCopy(text, done);
  }
}

function legacyCopy(text, done) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand("copy");
    done();
  } catch {
    /* clipboard unavailable */
  }
  ta.remove();
}

/* Developer-mode-only meta line: style, tokens/time, internal stages. */
function metaLine(data) {
  const t = data.timings || {};
  const label = STYLE_LABELS[data.mode] || data.mode;
  const bits = [`PocketAI \u2022 ${label}`];
  if (t.completion_tokens) {
    const secs = t.predicted_ms ? (t.predicted_ms / 1000).toFixed(1) : null;
    bits.push(`${t.completion_tokens} tok${secs ? ` in ${secs}s` : ""}`);
  }
  if (Array.isArray(data.workflow) && data.workflow.length) {
    bits.push(`stages: ${data.workflow.join(" \u2192 ")}`);
  }
  return bits.join(" \u00B7 ");
}

/* ---------------- image input (clipboard paste + upload) ----------------
 * An image is an input modality, not a document. The browser turns it into
 * base64 and the backend OCRs it offline; no image bytes are ever uploaded
 * to a server or the cloud. Clipboard handling stops the browser from
 * inserting a dead blob/string into the text box when a screenshot is pasted. */

function dataUrlToBase64(dataUrl) {
  const idx = dataUrl.indexOf(",");
  return idx >= 0 ? dataUrl.slice(idx + 1) : dataUrl;
}

function showImageError(msg) {
  state.attachmentError = msg || null;
  renderAttachmentPreview();
}

function renderAttachmentPreview() {
  const preview = $("attachment-preview");
  const thumb = $("attachment-thumb");
  const name = $("attachment-name");
  const err = $("attachment-error");
  if (!preview) return;
  // No image and no error -> fully hidden.
  if (!state.pendingImage && !state.attachmentError) {
    preview.hidden = true;
    if (thumb) {
      thumb.removeAttribute("src");
      thumb.classList.remove("placeholder");
    }
    if (name) name.textContent = "";
    if (err) err.textContent = "";
    return;
  }
  preview.hidden = false;
  if (state.pendingImage) {
    if (thumb) {
      thumb.hidden = false;
      thumb.classList.remove("placeholder");
      thumb.onerror = () => { thumb.hidden = true; };
      thumb.src = state.pendingImage.dataUrl;
    }
    if (name) name.textContent = state.pendingImage.name || "pasted image";
    if (err) err.textContent = "";
  } else if (state.attachmentError) {
    // Error box: show a clean SVG placeholder instead of a broken <img>.
    if (thumb) {
      thumb.hidden = false;
      thumb.classList.add("placeholder");
      thumb.onerror = null;
      thumb.src = "/static/assets/image-placeholder.svg";
    }
    if (name) name.textContent = "";
    if (err) err.textContent = state.attachmentError;
  }
}

function updateComposerPlaceholder() {
  const input = $("input");
  if (!input) return;
  input.placeholder = state.pendingImage
    ? "Ask something about this image\u2026"
    : "Ask anything\u2026";
}

function removeAttachment() {
  imageLoadSeq++; // invalidate any in-flight FileReader
  state.pendingImage = null;
  state.attachmentError = null;
  renderAttachmentPreview();
  updateComposerPlaceholder();
}

function handleImageFile(file) {
  if (!file) return;
  const type = (file.type || "").toLowerCase();
  if (!type.startsWith("image/")) {
    showImageError("That file is not an image.");
    return;
  }
  if (IMAGE_LIMITS.types.indexOf(type) === -1) {
    showImageError("Unsupported image type. Use PNG, JPG, WEBP or BMP.");
    return;
  }
  if (file.size > IMAGE_LIMITS.maxBytes) {
    showImageError("Image is too large (over 10 MB).");
    return;
  }
  const seq = ++imageLoadSeq; // claim this load
  const reader = new FileReader();
  reader.onload = () => {
    if (seq !== imageLoadSeq) return; // superseded by a newer action
    state.attachmentError = null;
    state.pendingImage = {
      dataUrl: String(reader.result),
      base64: dataUrlToBase64(String(reader.result)),
      name: file.name || "pasted image",
      type,
    };
    renderAttachmentPreview();
    updateComposerPlaceholder();
  };
  reader.onerror = () => {
    if (seq === imageLoadSeq) showImageError("Could not read that image.");
  };
  reader.readAsDataURL(file);
}

function onComposerPaste(e) {
  const dt = e.clipboardData || (window.clipboardData && window.clipboardData);
  if (!dt || !dt.items) return;
  for (const item of dt.items) {
    if (item.kind === "file" && item.type && item.type.startsWith("image/")) {
      // A screenshot/image is on the clipboard: take it, don't let the
      // browser drop a useless blob placeholder into the text box.
      e.preventDefault();
      const file = item.getAsFile();
      if (file) handleImageFile(file);
      return;
    }
  }
  // Otherwise it's text (or mixed text): allow the default paste.
}

function initImageInput() {
  const attachBtn = $("attach-btn");
  const fileInput = $("image-input");
  const removeBtn = $("attachment-remove");
  const input = $("input");
  if (attachBtn && fileInput) {
    attachBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      for (const file of fileInput.files) handleImageFile(file);
      fileInput.value = ""; // allow re-selecting the same file
    });
  }
  if (removeBtn) removeBtn.addEventListener("click", removeAttachment);
  if (input) input.addEventListener("paste", onComposerPaste);
}

/* ---------------- streaming chat (Tasks 1 + 2) ----------------
 * POST /chat/stream returns Server-Sent Events:
 *   meta  -> conversation id, resolved style
 *   delta -> visible answer text (thinking tokens never arrive)
 *   done  -> warning + timings
 *   error -> model server failure mid-stream
 * The answer renders token by token as plain text, then gets the full
 * markdown treatment once complete. */

function parseSSEBuffer(buffer) {
  const events = [];
  let idx;
  while ((idx = buffer.indexOf("\n\n")) >= 0) {
    const frame = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    let type = "message";
    const dataLines = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) type = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) continue;
    let data = null;
    try { data = JSON.parse(dataLines.join("\n")); } catch { continue; }
    events.push({ type, data });
  }
  return [events, buffer];
}

function setSendButtonBusy(busy) {
  const btn = $("send-btn");
  if (busy) {
    btn.classList.add("stop");
    btn.innerHTML = "&#9632;";
    btn.title = "Stop generating";
  } else {
    btn.classList.remove("stop");
    btn.innerHTML = "&#8593;";
    btn.title = "Send";
  }
  btn.disabled = false;
}

function stopGeneration() {
  if (state.abortController) state.abortController.abort();
}

async function sendMessage(event) {
  if (event) event.preventDefault();
  if (state.busy) {
    stopGeneration();
    return;
  }
  const input = $("input");
  const message = input.value.trim();
  // An image alone is a valid send; the server treats it as the input.
  if (!message && !state.pendingImage) return;
  input.value = "";
  // Capture the attachment before runChat clears it, so the user bubble can
  // show the thumbnail. runChat resets pendingImage when it builds the body.
  const sentImage = state.pendingImage;
  await runChat({ message, regenerate: false, image: sentImage });
}

async function runChat({ message, regenerate = false, image = null }) {
  const input = $("input");
  $("welcome").style.display = "none";
  if (!regenerate) addMessage("user", message, image ? { image: image.dataUrl } : {});

  state.busy = true;
  setSendButtonBusy(true);
  // Reconcile composer preview/error state for this send: a prior image
  // error must not linger after a successful (even text-only) send.
  state.attachmentError = null;
  renderAttachmentPreview();

  // Placeholder bubble with animated dots until the first token arrives.
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  const bubble = document.createElement("div");
  bubble.className = "bubble streaming";
  const loadingLabel = image ? "Reading image" : "Thinking";
  bubble.innerHTML =
    `${loadingLabel}<span class="dots"><span>.</span><span>.</span><span>.</span></span>`;
  wrap.appendChild(bubble);
  $("messages").appendChild(wrap);
  scrollDown(true);

  const controller = new AbortController();
  state.abortController = controller;

  const useDocs = $("use-docs");
  const body = {
    message,
    conversation_id: state.conversationId,
    mode: state.style,
  };
  if (regenerate) body.regenerate = true;
  if (useDocs && useDocs.checked) body.use_documents = true;
  if (image) {
    body.image = image.base64;
    body.image_name = image.name;
    body.image_type = image.type;
    // Reset the composer attachment now that it is part of the request.
    state.pendingImage = null;
    renderAttachmentPreview();
    updateComposerPlaceholder();
  }

  let acc = "";
  let meta = null;
  let done = null;

  try {
    const resp = await fetch("/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const j = await resp.json();
        if (j && j.error) detail = j.error;
      } catch { /* non-JSON error body */ }
      throw new Error(detail);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done: chunkDone } = await reader.read();
      if (chunkDone) break;
      buffer += decoder.decode(value, { stream: true });
      let events;
      [events, buffer] = parseSSEBuffer(buffer);
      for (const ev of events) {
        if (ev.type === "meta") {
          meta = ev.data;
          if (meta.conversation_id) state.conversationId = meta.conversation_id;
        } else if (ev.type === "delta") {
          acc += ev.data.text;
          // Plain text while streaming; markdown runs once at the end.
          bubble.textContent = acc;
          scrollDown(false);
        } else if (ev.type === "done") {
          done = ev.data;
        } else if (ev.type === "error") {
          throw new Error((ev.data && ev.data.error) || "stream error");
        }
      }
    }
    finalizeAssistant(wrap, bubble, acc, meta, done, false);
  } catch (err) {
    if (err.name === "AbortError") {
      // Stop button: keep whatever was generated so far (the backend
      // persists the partial answer too).
      finalizeAssistant(wrap, bubble, acc, meta, done, true);
    } else {
      wrap.remove();
      const errWrap = addMessage("assistant", friendlyError(err.message));
      // Failed turns must be retryable (e.g. a memory-error response can be
      // retried once the user closes apps / frees RAM). Attach a Retry that
      // re-sends the latest user turn.
      addRegenerateButton(errWrap);
    }
  } finally {
    state.busy = false;
    state.abortController = null;
    setSendButtonBusy(false);
    input.focus();
    loadConversations();
  }
}

function finalizeAssistant(wrap, bubble, text, meta, done, stopped) {
  bubble.classList.remove("streaming");
  const isClarification = !!(meta && meta.clarification);
  if (isClarification) bubble.classList.add("clarification");
  if (text) {
    bubble.classList.add("md");
    bubble.innerHTML = renderMarkdown(text);
  } else {
    bubble.textContent = stopped ? "(stopped)" : "(empty response)";
  }

  const warning = (done && done.warning) || (stopped ? "Generation stopped." : null);
  if (warning) {
    const warn = document.createElement("div");
    warn.className = "msg-warning";
    warn.textContent = `\u26A0 ${warning}`;
    wrap.appendChild(warn);
  }

  if (devModeEnabled() && (done || meta)) {
    const data = done || meta;
    const m = document.createElement("div");
    m.className = "msg-meta";
    m.textContent = metaLine({
      mode: data.mode,
      workflow: data.workflow,
      timings: (done && done.timings) || null,
    });
    wrap.appendChild(m);
  }

  // Regenerate only makes sense for real (persisted) answers.
  if (!isClarification && text) addRegenerateButton(wrap);
  scrollDown(false);
}

/* Regenerate (Task 2): replaces the last assistant answer. The user
   message is reused from history — the backend does not duplicate it. */
function addRegenerateButton(wrap) {
  for (const old of document.querySelectorAll("#messages .msg-actions")) {
    old.remove();
  }
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "msg-action";
  btn.title = "Generate this answer again";
  btn.innerHTML = "&#8635; Regenerate";
  btn.addEventListener("click", regenerateLast);
  actions.appendChild(btn);
  wrap.appendChild(actions);
}

async function regenerateLast() {
  if (state.busy || !state.conversationId) return;
  const wraps = Array.from($("messages").children);

  // Find the LAST user message in the conversation
  let userWrap = null;
  for (let i = wraps.length - 1; i >= 0; i -= 1) {
    if (wraps[i].classList.contains("user")) {
      userWrap = wraps[i];
      break;
    }
  }
  if (!userWrap) return;

  const b = userWrap.querySelector(".bubble");
  const userText = b ? b.textContent : null;
  if (!userText) return;

  // Preserve an attached image on the last user turn so Retry re-sends it
  // (e.g. a memory-error response to an image question can be retried with
  // the same image rather than losing it).
  let image = null;
  const imgEl = userWrap.querySelector("img.attached-img");
  if (imgEl && imgEl.src && imgEl.src.startsWith("data:")) {
    const colon = imgEl.src.indexOf(":");
    const semi = imgEl.src.indexOf(";");
    const mime = colon >= 0 && semi > colon
      ? imgEl.src.slice(colon + 1, semi)
      : "image/png";
    image = {
      dataUrl: imgEl.src,
      base64: dataUrlToBase64(imgEl.src),
      name: imgEl.alt || "attached image",
      type: mime,
    };
  }

  // Find and remove the assistant message after this user message (if any)
  let assistantWrap = null;
  let next = userWrap.nextElementSibling;
  while (next) {
    if (next.classList.contains("assistant")) {
      assistantWrap = next;
      break;
    }
    // If we hit another user message, stop looking
    if (next.classList.contains("user")) break;
    next = next.nextElementSibling;
  }
  if (assistantWrap) assistantWrap.remove();

  // Also remove any error messages after the user message
  next = userWrap.nextElementSibling;
  while (next && next.classList.contains("error")) {
    const toRemove = next;
    next = next.nextElementSibling;
    toRemove.remove();
  }

  await runChat({ message: userText, regenerate: true, image });
}

/* ---------------- thinking style + model popovers ---------------- */

function closePopovers() {
  for (const pop of document.querySelectorAll(".popover.open")) {
    pop.classList.remove("open");
  }
}

function setStyle(value, persist = true) {
  const known = STYLE_OPTIONS.some((o) => o.value === value);
  state.style = known ? value : "auto";
  if (persist) localStorage.setItem(MODE_STORAGE_KEY, state.style);
  $("style-chip").textContent = `${STYLE_LABELS[state.style]} \u25BE`;
  for (const item of $("style-menu").querySelectorAll(".popover-item")) {
    item.classList.toggle("active", item.dataset.value === state.style);
  }
}

function initWithStylePopover() {
  const menu = $("style-menu");
  for (const opt of STYLE_OPTIONS) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "popover-item";
    item.dataset.value = opt.value;
    const name = document.createElement("span");
    name.className = "popover-item-name";
    name.textContent = opt.label;
    const desc = document.createElement("span");
    desc.className = "popover-item-desc";
    desc.textContent = opt.desc;
    const check = document.createElement("span");
    check.className = "popover-item-check";
    check.textContent = "\u2713";
    item.append(name, desc, check);
    item.addEventListener("click", () => {
      setStyle(opt.value);
      closePopovers();
    });
    menu.appendChild(item);
  }
  setStyle(localStorage.getItem(MODE_STORAGE_KEY) || "auto", false);
  $("style-chip").addEventListener("click", (e) => {
    e.stopPropagation();
    const pop = $("style-pop");
    const wasOpen = pop.classList.contains("open");
    closePopovers();
    if (!wasOpen) pop.classList.add("open");
  });
}

function initModelPopover() {
  const chip = $("model-chip");
  if (!chip) return;
  chip.addEventListener("click", (e) => {
    e.stopPropagation();
    const pop = $("model-pop");
    const wasOpen = pop.classList.contains("open");
    closePopovers();
    if (!wasOpen) pop.classList.add("open");
  });
}

function initDevModeToggle() {
  const box = $("devmode-toggle");
  if (!box) return;
  box.checked = devModeEnabled();
  box.addEventListener("change", () => {
    localStorage.setItem(DEVMODE_STORAGE_KEY, box.checked ? "1" : "0");
    applyDevModeVisibility();
    refreshStatus();
    if ($("view-about").classList.contains("active")) loadAbout();
  });
}

function initThemeToggle() {
  const btn = $("theme-toggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const current = document.documentElement.hasAttribute("data-theme") ? "light" : "dark";
    const next = current === "light" ? "dark" : "light";
    applyTheme(next);
  });
}

/* ---------------- conversation history sidebar (Task 3) ----------------
 * SQLite (via /conversations) is the source of truth; the sidebar is a
 * plain list grouped into Today / Older. */

function convItem(conv) {
  const item = document.createElement("div");
  item.className = "conv-item";
  item.dataset.id = String(conv.id);
  item.setAttribute("role", "button");
  item.title = conv.title;

  const title = document.createElement("span");
  title.className = "conv-title";
  title.textContent = conv.title || "Untitled";

  const del = document.createElement("button");
  del.type = "button";
  del.className = "conv-del";
  del.title = "Delete conversation";
  del.textContent = "\u00D7";
  del.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      await api(`/conversations/${conv.id}`, { method: "DELETE" });
    } catch { /* list refresh below shows the truth */ }
    if (state.conversationId === conv.id) newChat();
    else loadConversations();
  });

  item.append(title, del);
  item.addEventListener("click", () => openConversation(conv.id));
  return item;
}

function convGroup(list, label, convs) {
  const head = document.createElement("div");
  head.className = "conv-group-label";
  head.textContent = label;
  list.appendChild(head);
  for (const conv of convs) list.appendChild(convItem(conv));
}

async function loadConversations() {
  const list = $("conv-list");
  if (!list) return;
  let convs = [];
  try {
    convs = await api("/conversations");
  } catch {
    return;
  }
  list.textContent = "";
  if (!convs.length) {
    const empty = document.createElement("div");
    empty.className = "conv-empty";
    empty.textContent = "No conversations yet";
    list.appendChild(empty);
    return;
  }
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const today = [];
  const older = [];
  for (const conv of convs) {
    const updated = new Date(conv.updated_at);
    (Number.isNaN(updated.getTime()) || updated < todayStart ? older : today)
      .push(conv);
  }
  if (today.length) convGroup(list, "Today", today);
  if (older.length) convGroup(list, "Older", older);
  markActiveConversation();
}

function markActiveConversation() {
  for (const item of document.querySelectorAll("#conv-list .conv-item")) {
    item.classList.toggle(
      "active",
      state.conversationId !== null
        && item.dataset.id === String(state.conversationId),
    );
  }
}

async function openConversation(id) {
  // Block only while a message is actively streaming (abortController set),
  // not during conversation-to-conversation switching — otherwise a quick
  // second click is dropped and the latest selection never loads.
  if (state.busy && state.abortController) return;
  const seq = ++convLoadSeq;
  state.busy = true;
  let data;
  try {
    data = await api(`/conversations/${id}`);
  } catch (err) {
    state.busy = false;
    if (seq === convLoadSeq) {
      addMessage("assistant", `Could not load conversation: ${err.message}`);
    }
    return;
  }
  // A newer click arrived while we were fetching: discard this stale result
  // so the latest selection always wins and the wrong conversation can never
  // be painted.
  if (seq !== convLoadSeq) {
    state.busy = false;
    return;
  }
  state.conversationId = id;
  $("welcome").style.display = "none";
  const box = $("messages");
  box.textContent = "";
  let lastAssistant = null;
  for (const m of data.messages || []) {
    if (m.role === "user") {
      let meta = {};
      if (m.attachment) {
        try {
          const att =
            typeof m.attachment === "string"
              ? JSON.parse(m.attachment)
              : m.attachment;
          if (att && att.type === "image") meta = { attachment: att };
        } catch { /* ignore malformed attachment */ }
      }
      addMessage("user", m.content, meta);
      lastAssistant = null;
    } else if (m.role === "assistant") {
      lastAssistant = addMessage("assistant", m.content, {
        reasoning: m.reasoning,
      });
    }
  }
  if (lastAssistant) addRegenerateButton(lastAssistant);
  markActiveConversation();
  scrollDown(true);
  $("input").focus();
  state.busy = false;
}

function newChat() {
  if (state.busy) return;
  state.conversationId = null;
  $("messages").textContent = "";
  $("welcome").style.display = "";
  markActiveConversation();
  $("input").focus();
}

/* ---------------- documents (RAG) ---------------- */

function fmtSize(bytes) {
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

async function loadDocuments() {
  const tbody = $("doc-tbody");
  tbody.textContent = "";
  let docs;
  try {
    docs = await api("/documents");
  } catch {
    return;
  }
  for (const d of docs) {
    const tr = document.createElement("tr");
    const tdName = document.createElement("td");
    tdName.textContent = d.filename;
    const tdChunks = document.createElement("td");
    tdChunks.textContent = String(d.chunk_count);
    const tdSize = document.createElement("td");
    tdSize.textContent = fmtSize(d.size_bytes || 0);
    const tdRm = document.createElement("td");
    const btn = document.createElement("button");
    btn.className = "rm";
    btn.textContent = "remove";
    btn.addEventListener("click", () => removeDocument(d.id));
    tdRm.appendChild(btn);
    tr.append(tdName, tdChunks, tdSize, tdRm);
    tbody.appendChild(tr);
  }
}

async function uploadDocuments(event) {
  event.preventDefault();
  const files = $("file-input").files;
  if (!files.length) return;
  const status = $("upload-status");
  const btn = $("upload-btn");
  status.className = "upload-status";
  btn.disabled = true;
  btn.textContent = "Processing\u2026";
  let ok = 0;
  let lastError = null;
  for (const file of files) {
    status.textContent = `Processing ${file.name}\u2026`;
    const form = new FormData();
    form.append("file", file);
    try {
      await api("/documents/upload", { method: "POST", body: form });
      ok += 1;
      status.textContent = `Processed ${file.name}`;
    } catch (err) {
      lastError = `${file.name}: ${err.message}`;
      status.textContent = `Failed: ${file.name}`;
      status.classList.add("err");
    }
  }
  btn.disabled = false;
  btn.textContent = "Upload";
  $("file-input").value = "";
  status.textContent = lastError
    ? `${ok} indexed, failed \u2014 ${lastError}`
    : `${ok} document${ok === 1 ? "" : "s"} indexed.`;
  if (lastError) status.classList.add("err");
  loadDocuments();
}

async function removeDocument(id) {
  try {
    await api(`/documents/${id}`, { method: "DELETE" });
  } catch (err) {
    $("upload-status").textContent = err.message;
  }
  loadDocuments();
}

async function runSearch(event) {
  event.preventDefault();
  const query = $("search-input").value.trim();
  if (!query) return;
  const box = $("search-results");
  box.textContent = "Searching\u2026";
  try {
    const data = await api("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, top_k: 6 }),
    });
    box.textContent = "";
    if (!data.results.length) {
      box.textContent = "No matches found.";
      return;
    }
    for (const hit of data.results) {
      const div = document.createElement("div");
      div.className = "search-hit";
      const meta = document.createElement("div");
      meta.className = "hit-meta";
      meta.textContent =
        `${hit.filename} \u00B7 chunk ${hit.chunk_index} \u00B7 score ${hit.score.toFixed(2)}`;
      const text = document.createElement("div");
      text.className = "hit-text";
      text.textContent = hit.text;
      div.append(meta, text);
      box.appendChild(div);
    }
  } catch (err) {
    box.textContent = `Search failed: ${err.message}`;
  }
}

/* ---------------- demo tab ---------------- */

const SAMPLE_DOCS = [
  {
    name: "sample-study-notes.md",
    type: "text/markdown",
    content: [
      "# Study Notes: Photosynthesis",
      "",
      "## Overview",
      "Photosynthesis is the process by which green plants convert light",
      "energy into chemical energy. It happens mainly in the leaves, inside",
      "organelles called chloroplasts.",
      "",
      "## The two stages",
      "1. Light-dependent reactions: capture sunlight and split water,",
      "   releasing oxygen as a by-product.",
      "2. Calvin cycle: uses the captured energy to fix carbon dioxide",
      "   into glucose.",
      "",
      "## Key equation",
      "6 CO2 + 6 H2O + light -> C6H12O6 + 6 O2",
      "",
      "## Exam reminders",
      "- Chlorophyll absorbs red and blue light, reflects green.",
      "- Stomata control gas exchange and water loss.",
      "- Limiting factors: light intensity, CO2 concentration, temperature.",
    ].join("\n"),
  },
  {
    name: "sample-project-brief.txt",
    type: "text/plain",
    content: [
      "PROJECT BRIEF: PocketAI",
      "",
      "PocketAI is a portable, offline AI assistant designed to run from a",
      "USB stick on ordinary laptops. It targets machines with 8 GB of RAM",
      "and no dedicated GPU.",
      "",
      "Key facts:",
      "- Backend: FastAPI on 127.0.0.1:8090, loopback only.",
      "- Model server: llama.cpp on 127.0.0.1:8091 with a 4B Qwen model,",
      "  quantized to Q4_K_M (about 2.5 GB).",
      "- Hardware profiles (SAFE, NORMAL, PERFORMANCE) adapt token budgets",
      "  to available RAM.",
      "- Documents: PDF, TXT and Markdown files are chunked and searched",
      "  locally (BM25), never uploaded anywhere.",
      "- Thinking styles: Fast, Balanced, Deep Think, Research and Build",
      "  adapt the answer to the question; Auto picks between them.",
      "",
      "Design goals: offline, portable, honest about limitations, and",
      "simple enough to audit end to end.",
    ].join("\n"),
  },
];

async function loadSampleDocs() {
  const status = $("demo-docs-status");
  status.className = "upload-status";
  const btn = $("demo-docs-btn");
  btn.disabled = true;
  let ok = 0;
  let lastError = null;
  for (const doc of SAMPLE_DOCS) {
    status.textContent = `Indexing ${doc.name}\u2026`;
    const form = new FormData();
    form.append("file", new File([doc.content], doc.name, { type: doc.type }));
    try {
      await api("/documents/upload", { method: "POST", body: form });
      ok += 1;
    } catch (err) {
      lastError = `${doc.name}: ${err.message}`;
    }
  }
  btn.disabled = false;
  status.textContent = lastError
    ? `${ok} indexed, failed \u2014 ${lastError}`
    : `${ok} sample documents indexed. Try the chat with \u201Cdocs\u201D checked.`;
  if (lastError) status.classList.add("err");
}

/* One showcase prompt per thinking style; each matches the classifier. */
const DEMO_PROMPTS = [
  { label: "Auto", prompt: "compare React and Angular", style: "auto" },
  { label: "Fast", prompt: "Tell me a fun fact", style: "fast" },
  { label: "Balanced", prompt: "explain quantum physics", style: "balanced" },
  { label: "Deep Think", prompt: "design a distributed banking system", style: "deep" },
  { label: "Research", prompt: "best processor for gaming", style: "research" },
  { label: "Build", prompt: "build a React dashboard", style: "build" },
];

function renderDemoPrompts() {
  const box = $("demo-prompts");
  for (const item of DEMO_PROMPTS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "demo-chip";
    chip.textContent = item.label;
    chip.title = item.prompt;
    chip.addEventListener("click", () => {
      setStyle(item.style);
      switchTab("chat");
      const input = $("input");
      input.value = item.prompt;
      input.focus();
    });
    box.appendChild(chip);
  }
}

/* The sample conversation shows only what a user sees: clean answers,
   no meta lines, no internal stages. */
const DEMO_CONVERSATION = [
  {
    role: "user",
    text: "hello",
  },
  {
    role: "assistant",
    text: "Hey! What can I help you with today?",
  },
  {
    role: "user",
    text: "compare React and Angular",
  },
  {
    role: "assistant",
    text: "React is the better default for most new web apps; Angular pays off "
      + "mainly for large teams that want a batteries-included framework.\n\n"
      + "React\n"
      + "- A library: bring your own router, state management and tooling.\n"
      + "- The largest ecosystem and the easiest hiring pool.\n"
      + "- Gentle learning curve for the basics.\n\n"
      + "Angular\n"
      + "- A full framework: router, forms and dependency injection included.\n"
      + "- Opinionated structure that scales across big teams.\n"
      + "- Steeper learning curve (TypeScript, RxJS, DI).\n\n"
      + "Recommendation: start with React unless your team already knows "
      + "Angular or you want every architectural decision made for you.",
  },
];

function renderDemoConversation() {
  const box = $("demo-conversation");
  for (const item of DEMO_CONVERSATION) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${item.role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (item.role === "assistant") {
      bubble.classList.add("md");
      bubble.innerHTML = renderMarkdown(item.text);
    } else {
      bubble.textContent = item.text;
    }
    wrap.appendChild(bubble);
    box.appendChild(wrap);
  }
}

/* ---------------- about tab ---------------- */

function aboutRow(label, value, cls) {
  const row = document.createElement("div");
  row.className = "about-row";
  const k = document.createElement("span");
  k.className = "about-key";
  k.textContent = label;
  const v = document.createElement("span");
  v.className = "about-val" + (cls ? ` ${cls}` : "");
  v.textContent = value;
  row.append(k, v);
  return row;
}

async function loadAbout() {
  const grid = $("about-system");
  grid.textContent = "";
  if (!devModeEnabled()) {
    // Normal user view: hardware and runtime detail stays hidden.
    grid.appendChild(aboutRow(
      "System details",
      "hidden \u2014 enable developer mode below to see them",
    ));
    return;
  }
  try {
    const health = await api("/health");
    const backend = health.backend || {};
    const model = health.model || {};
    grid.appendChild(aboutRow("Backend", `v${backend.version || "?"} \u00B7 ${backend.status || "?"}`, "ok"));
    grid.appendChild(aboutRow(
      "Model server",
      `${model.alias || "model"}: ${model.status || "?"}`,
      model.status === "ready" ? "ok" : "warn",
    ));
  } catch {
    grid.appendChild(aboutRow("Backend", "unreachable", "err"));
  }
  try {
    const sys = await api("/system");
    const p = sys.profile || {};
    const ram = sys.ram || {};
    const server = sys.model_server || {};
    const cpu = sys.cpu || {};
    grid.appendChild(aboutRow("Hardware profile", p.name || "?", "ok"));
    if (server.context) {
      grid.appendChild(aboutRow("Server context", `${server.context} tokens \u00B7 ${server.parallel_slots} slot`, "ok"));
    }
    if (ram.available_mb != null) {
      grid.appendChild(aboutRow("RAM", `${(ram.available_mb / 1024).toFixed(1)} GB free of ${(ram.total_mb / 1024).toFixed(1)} GB`));
    }
    if (cpu.logical_cores) {
      grid.appendChild(aboutRow("CPU", `${cpu.logical_cores} logical cores \u00B7 ${cpu.arch || "?"}`));
    }
    if (sys.python) grid.appendChild(aboutRow("Python", sys.python));
  } catch { /* system info is best-effort */ }
}

/* ---------------- save conversation ---------------- */

function openSaveDialog() {
  if (!state.conversationId) {
    addMessage("assistant", "No conversation to save. Start a chat first.");
    return;
  }
  $("save-dialog").classList.add("open");
  $("save-status").textContent = "";
}

async function saveConversation() {
  if (!state.conversationId) return;
  const status = $("save-status");
  status.textContent = "Saving...";
  try {
    const result = await api(`/conversations/${state.conversationId}/save`, {
      method: "POST",
    });
    status.textContent = `Saved to ${result.directory}`;
    status.className = "save-status ok";
  } catch (err) {
    status.textContent = `Failed to save: ${err.message}`;
    status.className = "save-status err";
  }
}

function closeSaveDialog() {
  $("save-dialog").classList.remove("open");
}

/* ---------------- keyboard shortcuts help ---------------- */

function openShortcutsDialog() {
  $("shortcuts-dialog").classList.add("open");
}

function closeShortcutsDialog() {
  $("shortcuts-dialog").classList.remove("open");
}

/* ---------------- first run ----------------
 * Welcome only — no hardware or runtime detail for normal users. */

function initFirstRun() {
  if (localStorage.getItem(WELCOME_STORAGE_KEY)) return;
  $("firstrun").classList.add("open");
}

/* ---------------- tabs ---------------- */

const TABS = ["chat", "docs", "demo", "about"];

function switchTab(which) {
  for (const name of TABS) {
    const active = name === which;
    $(`tab-${name}`).classList.toggle("active", active);
    $(`tab-${name}`).setAttribute("aria-selected", String(active));
    $(`view-${name}`).classList.toggle("active", active);
  }
  if (which === "docs") loadDocuments();
  if (which === "about") loadAbout();
}

/* ---------------- init ---------------- */

document.addEventListener("DOMContentLoaded", () => {
  $("composer").addEventListener("submit", sendMessage);
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(e);
      return;
    }
    // Escape removes a pending attachment without requiring the mouse.
    if (e.key === "Escape" && state.pendingImage) {
      e.preventDefault();
      removeAttachment();
    }
  });
  $("new-chat-btn").addEventListener("click", newChat);
  for (const name of TABS) {
    $(`tab-${name}`).addEventListener("click", () => switchTab(name));
  }
  $("upload-form").addEventListener("submit", uploadDocuments);
  $("search-form").addEventListener("submit", runSearch);
  $("demo-docs-btn").addEventListener("click", loadSampleDocs);
  $("firstrun-close").addEventListener("click", () => {
    localStorage.setItem(WELCOME_STORAGE_KEY, "1");
    $("firstrun").classList.remove("open");
  });
  $("save-chat-btn").addEventListener("click", openSaveDialog);
  $("save-btn").addEventListener("click", () => {
    saveConversation();
  });
  $("save-cancel-btn").addEventListener("click", closeSaveDialog);
  $("shortcuts-btn").addEventListener("click", openShortcutsDialog);
  $("shortcuts-close-btn").addEventListener("click", closeShortcutsDialog);
  document.addEventListener("click", () => closePopovers());
  document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest ? e.target.closest(".copy-btn") : null;
    if (btn) copyCodeBlock(btn);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closePopovers();
      closeSaveDialog();
      closeShortcutsDialog();
    }
  });

  initWithStylePopover();
  initModelPopover();
  initDevModeToggle();
  initTheme();
  initThemeToggle();
  initImageInput();
  renderDemoPrompts();
  renderDemoConversation();
  applyDevModeVisibility();
  loadConversations();
  refreshStatus();
  initFirstRun();
  setInterval(refreshStatus, 15000);
});
