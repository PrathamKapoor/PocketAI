// Real DOM test of the image-input UI using jsdom (no browser required).
// Exercises clipboard paste -> preview, remove, file upload, and that the
// send payload includes the image base64. Run with:
//   NODE_PATH=/tmp/pai_testing/node_modules node frontend/tests/image_input.test.mjs
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
    try {
      return require(c);
    } catch { /* try next */ }
  }
  return null;
}

const jsdomMod = loadJsdom();
if (!jsdomMod) {
  console.log("SKIP: jsdom not installed (frontend DOM test requires `npm i jsdom`).");
  process.exit(77);
}
const { JSDOM } = jsdomMod;

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..", "..");
const html = readFileSync(resolve(root, "frontend/index.html"), "utf8");
const appJs = readFileSync(resolve(root, "frontend/app.js"), "utf8");

const PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

let failures = 0;
function check(name, cond) {
  if (cond) {
    console.log(`  ok  - ${name}`);
  } else {
    failures += 1;
    console.log(`FAIL  - ${name}`);
  }
}

const dom = new JSDOM(html, {
  url: "http://127.0.0.1:8090/",
  runScripts: "dangerously",
  pretendToBeVisual: true,
});
const { window } = dom;
const { document } = window;

// Capture chat/stream requests.
let streamBody = null;
window.fetch = async (url, opts = {}) => {
  if (String(url).endsWith("/chat/stream")) {
    streamBody = JSON.parse(opts.body);
    const enc = new TextEncoder();
    const body = new ReadableStream({
      start(c) {
        c.enqueue(enc.encode('event: meta\ndata: {"conversation_id":1,"mode":"auto","input_type":"image","attachment":{"type":"image"}}\n\n'));
        c.enqueue(enc.encode("event: done\ndata: {}\n\n"));
        c.close();
      },
    });
    return { ok: true, status: 200, headers: { get: () => "text/event-stream" }, body, json: async () => ({}) };
  }
  return {
    ok: true,
    status: 200,
    json: async () => ({ backend: { version: "t" }, model: { status: "ready", name: "x" }, runtime: { status: "running" } }),
  };
};
window.AbortController = AbortController;
window.TextDecoder = TextDecoder;

// Run app.js in the window context, then fire DOMContentLoaded.
const script = document.createElement("script");
script.textContent = appJs;
document.body.appendChild(script);
window.document.dispatchEvent(new window.Event("DOMContentLoaded"));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function makeImageFile() {
  const bytes = Uint8Array.from(atob(PNG_B64), (c) => c.charCodeAt(0));
  return new window.File([bytes], "shot.png", { type: "image/png" });
}

await (async () => {
  // --- DOM contract ---
  check("composer has attachment preview", !!document.getElementById("attachment-preview"));
  check("composer has attach button", !!document.getElementById("attach-btn"));
  check("composer has hidden image input", !!document.getElementById("image-input"));

  const preview = document.getElementById("attachment-preview");
  const thumb = document.getElementById("attachment-thumb");
  const nameEl = document.getElementById("attachment-name");

  // --- clipboard paste of an image ---
  const file = makeImageFile();
  const pasteEv = new window.Event("paste");
  pasteEv.clipboardData = {
    items: [{ kind: "file", type: "image/png", getAsFile: () => file }],
  };
  const input = document.getElementById("input");
  input.dispatchEvent(pasteEv);
  await sleep(50);

  check("paste shows preview", preview.hidden === false);
  check("paste sets thumbnail", (thumb.getAttribute("src") || "").startsWith("data:image/"));
  check("paste sets filename", nameEl.textContent === "shot.png");

  // --- remove image ---
  document.getElementById("attachment-remove").dispatchEvent(new window.Event("click"));
  check("remove hides preview", preview.hidden === true);

  // --- F1: remove during the async FileReader read must win (no resurrection) ---
  const raceFile = makeImageFile();
  const raceEv = new window.Event("paste");
  raceEv.clipboardData = {
    items: [{ kind: "file", type: "image/png", getAsFile: () => raceFile }],
  };
  input.dispatchEvent(raceEv); // starts an async FileReader (claims a seq)
  document.getElementById("attachment-remove").dispatchEvent(new window.Event("click")); // bumps seq, hides
  await sleep(80); // let the late onload fire
  check("F1 remove wins race (preview stays hidden)", preview.hidden === true);
  check("F1 remove wins race (no thumbnail resurrected)", !thumb.getAttribute("src"));

  // --- file upload via input ---
  const file2 = makeImageFile();
  const fileInput = document.getElementById("image-input");
  Object.defineProperty(fileInput, "files", { value: [file2], configurable: true });
  fileInput.dispatchEvent(new window.Event("change"));
  await sleep(50);
  check("upload shows preview", preview.hidden === false);

  // --- F2/F3: a non-image file shows an error, a later valid image clears it ---
  document.getElementById("attachment-remove").dispatchEvent(new window.Event("click"));
  const badInput = document.getElementById("image-input");
  Object.defineProperty(badInput, "files", {
    value: [{ name: "notes.txt", type: "text/plain", size: 12 }],
    configurable: true,
  });
  badInput.dispatchEvent(new window.Event("change"));
  check(
    "F2 non-image shows error",
    preview.hidden === false &&
      (document.getElementById("attachment-error").textContent || "").length > 0
  );
  const goodFile = makeImageFile();
  const goodEv = new window.Event("paste");
  goodEv.clipboardData = {
    items: [{ kind: "file", type: "image/png", getAsFile: () => goodFile }],
  };
  input.dispatchEvent(goodEv);
  await sleep(50);
  check(
    "F3 valid image clears the error",
    preview.hidden === false &&
      (document.getElementById("attachment-error").textContent || "") === "" &&
      (thumb.getAttribute("src") || "").startsWith("data:image/")
  );

  // --- send includes image in payload ---
  const composer = document.getElementById("composer");
  composer.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await sleep(80);
  check("send posts image base64", !!streamBody && typeof streamBody.image === "string" && streamBody.image.length > 0);
  check("send posts image type", !!streamBody && streamBody.image_type === "image/png");
  check("send posts image name", !!streamBody && streamBody.image_name === "shot.png");

  // --- image-only send (no typed text) is allowed ---
  streamBody = null;
  const file3 = makeImageFile();
  const pe2 = new window.Event("paste");
  pe2.clipboardData = { items: [{ kind: "file", type: "image/png", getAsFile: () => file3 }] };
  input.dispatchEvent(pe2);
  await sleep(50);
  document.getElementById("input").value = "";
  composer.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await sleep(80);
  check("image-only send allowed", !!streamBody && !!streamBody.image);
})();

console.log(failures === 0 ? "\nFRONTEND TESTS PASSED" : `\nFRONTEND TESTS FAILED: ${failures}`);
process.exit(failures === 0 ? 0 : 1);
