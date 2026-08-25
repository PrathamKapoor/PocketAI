// Real DOM reproduction of conversation-loading + image-preview flows using jsdom.
// Goal: empirically reproduce (or rule out) the reported real-browser bugs.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { createRequire } from "node:module";

function loadJsdom() {
  const require = createRequire(import.meta.url);
  const candidates = [
    process.env.JSDOM_DIR,
    "jsdom",
    "C:/Users/LENOVO/AppData/Local/Temp/pai_testing/node_modules/jsdom",
    "C:/tmp/pai_testing/node_modules/jsdom",
  ].filter(Boolean);
  for (const c of candidates) {
    try { return require(c); } catch { /* try next */ }
  }
  return null;
}
const jsdomMod = loadJsdom();
if (!jsdomMod) { console.log("SKIP: jsdom not installed"); process.exit(77); }
const { JSDOM } = jsdomMod;

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..", "..");
const html = readFileSync(resolve(root, "frontend/index.html"), "utf8");
const appJs = readFileSync(resolve(root, "frontend/app.js"), "utf8");

const PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

let failures = 0;
function check(name, cond) {
  if (cond) console.log(`  ok  - ${name}`);
  else { failures += 1; console.log(`FAIL  - ${name}`); }
}

// ---- In-memory conversation store behind a realistic fetch mock ----
const conversations = {
  1: { id: 1, title: "Alpha", updated_at: "2026-08-25T10:00:00", messages: [
        { role: "user", content: "Hello A", attachment: null },
        { role: "assistant", content: "Hi from A", reasoning: null },
        { role: "user", content: "Second A", attachment: null },
        { role: "user", content: "Here is a pic", attachment: JSON.stringify({ type: "image", ocr_available: true, ocr_confidence: 88 }) },
      ] },
  2: { id: 2, title: "Beta", updated_at: "2026-08-25T11:00:00", messages: [
        { role: "user", content: "Hello B", attachment: null },
        { role: "assistant", content: "Hi from B", reasoning: null },
      ] },
};
function convList() {
  return Object.values(conversations).map(({ id, title, updated_at }) => ({ id, title, updated_at }));
}

const dom = new JSDOM(html, {
  url: "http://127.0.0.1:8090/",
  runScripts: "dangerously",
  pretendToBeVisual: true,
  resources: undefined,
});
const { window } = dom;
const { document } = window;

// Track stream body + capture conversation_id the app reports via meta events.
let streamBody = null;
let forceError = false;
function makeStream(metaObj) {
  const enc = new TextEncoder();
  return {
    ok: true, status: 200,
    headers: { get: () => "text/event-stream" },
    body: new ReadableStream({
      start(c) {
        c.enqueue(enc.encode(`event: meta\ndata: ${JSON.stringify(metaObj)}\n\n`));
        c.enqueue(enc.encode("event: done\ndata: {}\n\n"));
        c.close();
      },
    }),
    json: async () => ({}),
  };
}
function res(body, { ok = true, status = 200, type = "json" } = {}) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok,
    status,
    headers: { get: () => (type === "json" ? "application/json" : "text/event-stream") },
    text: async () => text,
    json: async () => (typeof body === "string" ? JSON.parse(body) : body),
  };
}
window.fetch = async (url, opts = {}) => {
  const u = String(url);
  if (u.endsWith("/chat/stream")) {
    streamBody = JSON.parse(opts.body);
    if (forceError) {
      return res({ error: "MemoryError: out of RAM" }, { ok: false, status: 503 });
    }
    const cid = streamBody.conversation_id || 1;
    return makeStream({ conversation_id: cid, mode: "auto" });
  }
  if (u === "/conversations") {
    return res(convList());
  }
  if (u.startsWith("/conversations/")) {
    const id = Number(u.split("/").pop());
    const conv = conversations[id];
    if (!conv) return res({ error: "nf" }, { ok: false, status: 404 });
    return res({ conversation: { id: conv.id, title: conv.title }, messages: conv.messages });
  }
  if (u === "/health") {
    return res({ backend: { version: "t" }, model: { status: "ready", name: "x" }, runtime: { status: "running" } });
  }
  return res({});
};
window.AbortController = AbortController;
window.TextDecoder = TextDecoder;

const script = document.createElement("script");
script.textContent = appJs;
document.body.appendChild(script);
window.document.dispatchEvent(new window.Event("DOMContentLoaded"));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function userMsgs() {
  return [...document.querySelectorAll("#messages .msg.user")].map((m) => m.querySelector(".bubble")?.textContent);
}

await (async () => {
  // Let initial loadConversations() (fired on DOMContentLoaded) populate the sidebar.
  await sleep(60);
  const sidebar = document.getElementById("conv-list");

  check("sidebar lists Alpha", !!sidebar.querySelector('[data-id="1"]'));
  check("sidebar lists Beta", !!sidebar.querySelector('[data-id="2"]'));

  // ---- Click conversation 1 (Alpha) ----
  sidebar.querySelector('[data-id="1"]').dispatchEvent(new window.Event("click"));
  await sleep(80);
  console.log("DEBUG first A:", JSON.stringify(userMsgs()));
  check("Alpha loaded: 3 user msgs", userMsgs().length === 3);
  check("Alpha first msg text", userMsgs()[0] === "Hello A");
  check("Alpha active highlight", sidebar.querySelector('[data-id="1"]').classList.contains("active"));

  // ---- Click conversation 2 (Beta) ----
  sidebar.querySelector('[data-id="2"]').dispatchEvent(new window.Event("click"));
  await sleep(80);
  check("Beta loaded: 1 user msg", userMsgs().length === 1);
  check("Beta first msg text", userMsgs()[0] === "Hello B");
  check("Beta active highlight", sidebar.querySelector('[data-id="2"]').classList.contains("active"));
  check("Alpha no longer highlighted", !sidebar.querySelector('[data-id="1"]').classList.contains("active"));

  // ---- Click back to Alpha ----
  sidebar.querySelector('[data-id="1"]').dispatchEvent(new window.Event("click"));
  await sleep(80);
  check("Alpha re-loaded: 3 user msgs", userMsgs().length === 3);
  check("Alpha first msg text after switch", userMsgs()[0] === "Hello A");

  // ---- Send a message into Alpha, then verify it stays in Alpha ----
  document.getElementById("input").value = "New A msg";
  const composer = document.getElementById("composer");
  composer.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await sleep(120);
  check("Alpha has 4 user msgs after send", userMsgs().length === 4);
  check("Alpha latest msg is the new one", userMsgs()[3] === "New A msg");
  // The sidebar reload (loadConversations) must NOT wipe the message display.
  check("messages still present after sidebar reload", userMsgs().length === 4);

  // ---- IMAGE: paste, verify preview is a real data URL (not broken/alt) ----
  const bytes = Uint8Array.from(atob(PNG_B64), (c) => c.charCodeAt(0));
  const file = new window.File([bytes], "shot.png", { type: "image/png" });
  const pasteEv = new window.Event("paste");
  pasteEv.clipboardData = { items: [{ kind: "file", type: "image/png", getAsFile: () => file }] };
  document.getElementById("input").dispatchEvent(pasteEv);
  await sleep(60);
  const thumb = document.getElementById("attachment-thumb");
  const src = thumb.getAttribute("src") || "";
  check("image preview src is valid data URL", src.startsWith("data:image/png;base64,"));
  check("image preview NOT empty/broken", src.length > 40);

  // ---- Send the image; verify user bubble gets a real <img> with valid src ----
  streamBody = null;
  composer.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await sleep(120);
  const lastUserBubble = [...document.querySelectorAll("#messages .msg.user")].pop();
  const imgEl = lastUserBubble.querySelector("img.attached-img");
  check("sent image bubble has <img>", !!imgEl);
  check("sent image <img> src valid", !!imgEl && (imgEl.getAttribute("src") || "").startsWith("data:image/"));
  check("send payload included image base64", !!streamBody && typeof streamBody.image === "string" && streamBody.image.length > 0);

  // ---- Reload conversation from backend (simulate page reload): image must be a chip, text preserved ----
  sidebar.querySelector('[data-id="1"]').dispatchEvent(new window.Event("click"));
  await sleep(80);
  const imgs = [...document.querySelectorAll("#messages .msg.user img.attached-img")];
  // The new message had an image but we only stored metadata; reloaded conv shows a chip, not a broken <img>.
  check("reloaded conv has no broken <img> attachments", imgs.length === 0);
  check("reloaded conv shows image chip", !!document.querySelector("#messages .img-chip"));

  // ---- RACE: rapid clicks on A then B must show the LATEST (B), not A ----
  sidebar.querySelector('[data-id="1"]').dispatchEvent(new window.Event("click"));
  sidebar.querySelector('[data-id="2"]').dispatchEvent(new window.Event("click"));
  await sleep(120); // let both fetches resolve; seq token should make B win
  check("rapid click race: latest (Beta) wins", userMsgs().length === 1 && userMsgs()[0] === "Hello B");
  check("rapid click race: Beta highlighted", sidebar.querySelector('[data-id="2"]').classList.contains("active"));
  check("rapid click race: Alpha not highlighted", !sidebar.querySelector('[data-id="1"]').classList.contains("active"));

  // ---- ERROR PATH: unsupported file must NOT render a broken <img> (thumb hidden) ----
  const badInput = document.getElementById("image-input");
  Object.defineProperty(badInput, "files", {
    value: [{ name: "notes.txt", type: "text/plain", size: 12 }],
    configurable: true,
  });
  badInput.dispatchEvent(new window.Event("change"));
  check(
    "error path shows error text",
    (document.getElementById("attachment-error").textContent || "").length > 0
  );
  check(
    "error path shows SVG placeholder (no broken image)",
    !document.getElementById("attachment-thumb").hidden &&
    document.getElementById("attachment-thumb").getAttribute("src")?.includes("image-placeholder.svg")
  );

  // ---- RETRY: a memory error must be retryable, and must target the LATEST user turn ----
  document.getElementById("attachment-remove").dispatchEvent(new window.Event("click"));
  sidebar.querySelector('[data-id="1"]').dispatchEvent(new window.Event("click"));
  await sleep(80);

  const composer2 = document.getElementById("composer");
  // First turn succeeds.
  document.getElementById("input").value = "first question";
  composer2.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await sleep(100);
  // Second turn fails with a memory error.
  forceError = true;
  document.getElementById("input").value = "second question";
  composer2.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await sleep(120);
  const errBtn = [...document.querySelectorAll("#messages .msg.assistant")]
    .pop()?.querySelector(".msg-action");
  check("memory error shows a Retry button", !!errBtn);
  // Retry must re-send the LATEST user turn ("second question"), not "first question".
  forceError = false;
  streamBody = null;
  errBtn.dispatchEvent(new window.Event("click"));
  await sleep(120);
  check("retry targets latest user turn", !!streamBody && streamBody.message === "second question");

  // ---- RETRY preserves an attached image ----
  document.getElementById("attachment-remove").dispatchEvent(new window.Event("click"));
  sidebar.querySelector('[data-id="1"]').dispatchEvent(new window.Event("click"));
  await sleep(80);
  const pngBytes = Uint8Array.from(atob(PNG_B64), (c) => c.charCodeAt(0));
  const imgFile = new window.File([pngBytes], "retry.png", { type: "image/png" });
  const pEv = new window.Event("paste");
  pEv.clipboardData = { items: [{ kind: "file", type: "image/png", getAsFile: () => imgFile }] };
  document.getElementById("input").dispatchEvent(pEv);
  await sleep(50);
  forceError = true;
  document.getElementById("input").value = "what is this";
  composer2.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await sleep(120);
  const errBtn2 = [...document.querySelectorAll("#messages .msg.assistant")]
    .pop()?.querySelector(".msg-action");
  forceError = false;
  streamBody = null;
  errBtn2.dispatchEvent(new window.Event("click"));
  await sleep(120);
  check("retry preserves image", !!streamBody && typeof streamBody.image === "string" && streamBody.image.length > 0);
})();

console.log(failures === 0 ? "\nREPRO TESTS PASSED" : `\nREPRO TESTS FAILED: ${failures}`);
process.exit(failures === 0 ? 0 : 1);
