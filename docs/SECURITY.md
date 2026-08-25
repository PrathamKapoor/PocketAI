# PocketAI Security

Phase 10 security review. Honest about the trade-offs: PocketAI is a
single-user, offline, loopback-only tool, and its security model is built
for exactly that — nothing more.

---

## 1. Threat model

**In scope** (defended against):

- Other local processes / other users of the same PC using the model API.
- Malformed or malicious input: uploads, skill files, chat messages.
- Accidental exposure to the network.
- Drive-letter / path manipulation bugs (portable-drive specific).

**Out of scope** (documented, not defended):

- An attacker with code execution on the host PC — they can read the USB
  contents and process memory regardless.
- Targeted browser attacks (see §4.2 for the residual risk and rationale).
- Physical theft of the drive (data is unencrypted, see §8).

## 2. Network surface

Two services, both bound to **127.0.0.1 only**:

| Port | Service | Auth |
|---|---|---|
| 8090 | FastAPI backend + UI | none (see §4) |
| 8091 | llama-server model API | Bearer API key |

- The backend **refuses to start** on a non-loopback host: `main()` checks
  the configured host against a loopback allowlist and exits otherwise
  (`security.require_loopback_bind` in `config\runtime.json`).
- No component makes outbound connections at runtime. Verified by audit:
  every URL in runtime code paths is loopback; the only external URLs in the
  repository are in `launcher\build_runtime.py` (build-time downloads) and
  XML namespace constants. A frontend test asserts the UI contains no
  external URLs.
- No telemetry, no update checks, no analytics. Offline by construction.

## 3. Model API key (8091)

- Generated locally, stored in `config\model.json`, sent as
  `Authorization: Bearer …` on every request from the backend.
- Purpose: stop other local processes and drive-by web requests from using
  the model server. It is **defense in depth, not a secret** — anyone who
  can read the config file can read the key.
- The key is never returned by any backend endpoint (`/health`, `/system`,
  …) and never logged.
- Residual note: the key is visible on the llama-server command line
  (process list). Acceptable: the threat is casual local use, not host
  compromise.
- If you rotate it: edit `config\model.json`, restart the stack.

## 4. Backend port 8090 — deliberately unauthenticated

### 4.1 The decision

The backend has no auth. Any local process can call `/chat`, upload or
delete documents. This is a conscious trade-off:

- Adding auth would require key distribution to the browser on locked-down
  PCs (no installable extensions, no storage guarantees) — degrading the
  one-click experience for weak protection.
- The service is loopback-only, single-user, and its capabilities are
  bounded: it can run inference on the local model and manage the local
  document store. It cannot execute commands, touch other files, or reach
  the network.

### 4.2 Residual risk: browser-based attacks

A malicious web page opened *while PocketAI runs* could try to reach
`127.0.0.1:8090`:

- **CSRF** — `POST /chat` sends JSON; cross-origin JSON POSTs are blocked
  by CORS preflight in modern browsers. Multipart upload from a foreign
  origin is likewise preflighted. Risk: low.
- **DNS rebinding** — in theory a rebinding attack could make the browser
  treat the attacker's origin as local and read responses (leaking uploaded
  document content via `/search`). Mitigations in modern browsers (Host
  header checks, private network access prompts) reduce but do not eliminate
  this. Risk: low on a college PC browsing normal sites; **documented, not
  mitigated server-side**. If this matters to you, stop PocketAI
  (`STOP_AI.bat`) while browsing untrusted sites.

### 4.3 What the backend enforces regardless

- Pydantic validation on every request (message length caps, types).
- Skill ids validated against `^[a-z0-9][a-z0-9_]{0,63}$`.
- Error responses never echo config values, keys, or stack internals.

## 5. Uploads (RAG)

Defense in depth at `rag/pipeline.py`:

1. **Extension allowlist**: `.pdf .txt .md .markdown .pptx` only → 415.
2. **Size cap**: 25 MB, enforced by reading cap+1 bytes (no full-read of
   oversized bodies) → 413.
3. **Filename sanitization**: path components stripped (`Path(name).name`),
   character whitelist, leading dots removed, 120-char cap — uploads cannot
   write outside their directory.
4. **Random ids**: documents live at `rag/uploads/<16-hex-doc_id>/<file>`;
   the original name is metadata only, never a path.
5. **Delete validation**: `doc_id` must match `[a-f0-9]{16}` before any
   `rmtree` — no traversal via crafted ids.
6. **Parser limits**: PDFs capped at 1000 pages, PPTX at 500 slides;
   extraction failures clean up partial files and return 400.

## 6. Skills

- Loaded only from `skills/<id>/skill.md` under the PocketAI root.
- Directory ids must match the skill-id regex; anything else is ignored.
- **Symlink escape guard**: a `skill.md` that resolves outside `skills/`
  is rejected — a malicious shortcut/junction cannot smuggle in a prompt
  from elsewhere on the PC.
- Malformed skills are skipped, never fatal (one bad file can't DoS the
  loader).
- Skills are system prompts only: they cannot execute code; the backend
  contains **no subprocess calls at all**.

## 7. Storage

- SQLite only (`storage\pocket_ai.db`, `rag\vector_store\documents.db`),
  WAL mode, foreign keys on.
- **All queries parameterized** — no string-built SQL anywhere.
- No blobs beyond chunk text; no credentials stored.

## 8. Data at rest

- Chat history and uploaded documents are stored **unencrypted** on the
  drive. Anyone with physical access to the USB stick can read them.
  Treat the drive like any file containing your notes: don't leave it
  behind.
- Deleting data: remove the files listed in `docs/USER_GUIDE.md` §6 while
  the stack is stopped.

## 9. Frontend

- Served by the backend itself (same origin) — no CDN, no external
  scripts/styles/fonts (asserted by a test).
- All dynamic content rendered via `textContent`/`createElement`; model
  output is never interpreted as HTML → no XSS path from model responses
  or document content.

## 10. Build-time hygiene

- `launcher\build_runtime.py` pins the Python embeddable version and
  requirement versions; run it with `PYTHONNOUSERSITE=1` so host user-site
  packages cannot leak into the bundle.
- llama.cpp is taken from official release assets; the verified build id is
  recorded in `docs/runtime_setup.md` — re-verify before upgrading.

## 11. Summary table

| Surface | Posture |
|---|---|
| Network egress | None at runtime (audited) |
| Bind address | Loopback enforced at startup |
| 8091 auth | Bearer key (local-only, defense in depth) |
| 8090 auth | None by design; browser-attack trade-off documented (§4) |
| Uploads | Allowlist + caps + sanitized paths + validated deletes |
| Skills | Regex ids + symlink escape guard + skip-on-error |
| SQL | Parameterized only |
| Frontend | Same-origin, no external URLs, textContent-only rendering |
| Code execution | None: backend never spawns subprocesses |
| Data at rest | Unencrypted — physical-access risk documented |
