# PocketAI Demo Guide

> A guided 5-minute demo. Everything here runs offline on the machine in
> front of you — nothing is faked except where explicitly labelled.

---

## 0. Before you start

- Windows 10/11 x64, 8 GB RAM or more, a few GB free disk.
- Close heavy programs (browser tabs included) so the RAM guard stays happy.
- Budget: ~1 minute setup, ~4 minutes of guided stops. Engineering and
  Deep Analysis answers take a few minutes each on CPU — run them last or
  skip live and point at the sample conversation instead.

## 1. Start (≈ 30 s)

1. Double-click `launcher\START_AI.bat`.
2. Preflight checks the machine, picks a hardware profile, starts the model
   server and backend, and opens the browser at `http://127.0.0.1:8090/`.

**First-run overlay** appears on first launch: what PocketAI is, what it can
do, and **live hardware status** (backend version, model state, profile,
free RAM). Click *Start using PocketAI* — it never shows again.

**Point out:** the three status pills in the header — model state, hardware
profile, free RAM — update live every 15 seconds.

## 2. Quick answers are instant (≈ 20 s)

The mode selector defaults to **✨ Auto**. Type:

```
hey
```

The model answers directly — no clarifying questions, no interrogation.
Then try:

```
Build me a simple HTML page with a heading and a button
```

You get the HTML. **Point out:** the meta line under the answer
(`PocketAI • Coding Mode · N tok in Ns`) — mode and timing only; internal
workflow stages stay hidden.

## 3. Mode routing (≈ 40 s)

Type (or click the **Coding** chip on the Demo tab first):

```
Explain this traceback: ValueError: invalid literal for int() with base 10: 'abc'
```

The answer reads like a seasoned debugger wrote it — Auto classified the
message into Coding mode, which ran the debugging and code-review stages
internally, and none of that machinery is shown to the user. There are 15
skills behind the scenes; each mode combines the ones it needs.

## 4. Documents: load samples, then ask (≈ 60 s)

1. Open the **Demo** tab → click **Load sample documents**. Two small files
   (study notes + a project brief) are indexed locally.
2. Back in **Chat**, tick the **docs** checkbox and ask:

```
What are the two stages of photosynthesis?
```

The answer is grounded in the uploaded notes, with sources injected into the
prompt. **Point out:** retrieval is local BM25 over SQLite — no embedding
model, nothing uploaded anywhere. The Documents tab shows the index and a
raw search box.

## 5. The skip button: Just answer (≈ 20 s)

Click **Just answer** (small button next to Send) with any message. It
overrides every workflow — the meta line reads
`PocketAI • Direct Mode`. This is the "stop being clever,
just answer" escape hatch.

## 6. Deep workflows: Engineering (≈ 2–4 min)

Switch the mode selector to **🏗 Engineering** and send:

```
design a production banking architecture
```

Internally this runs the full engineering workflow — requirement
interrogation, architecture review, security review, production readiness,
a multi-perspective council and a quality pass — as one composed model
call. What arrives is a single clean answer: no `## Requirement Analysis`
headers, no internal reports, no skill names.

**Point out:** enable **Developer mode** in the About tab and the meta
line reveals the stages that ran. **✨ Auto** classifies the message with
a zero-token rule-based check and picks the mode by itself — "hey" stays
quick, and the banking prompt escalates to the engineering workflow on its
own.

While it generates: this is a 4B model on CPU at ~9 tokens/second — the
latency *is* the demo of honest 8 GB constraints.

## 7. About tab (≈ 20 s)

Open **About**: version, live system status (backend, model server, profile,
server context, RAM, CPU), the mode reference table, the **Developer mode**
toggle, **known limitations** stated plainly, and pointers to the docs. A
portfolio-grade product states what it cannot do.

## Talking points

- **Offline & private** — loopback-only ports, no telemetry, no accounts;
  history and documents live on the drive and travel with it.
- **8 GB engineering** — hardware profiles, a per-request RAM guard that
  refuses instead of freezing, single-slot inference, thinking mode off by
  default (all measured, see `docs/PERFORMANCE.md`).
- **Skills are markdown** — add a folder, restart, done.
- **Honest demo** — the sample conversation on the Demo tab is labelled
  illustrative, not live output.

## Reset for the next audience

1. `launcher\STOP_AI.bat`.
2. Delete `storage\pocket_ai.db` (chat history) and
   `rag\vector_store\documents.db` + `rag\uploads\` (documents).
3. In the browser, clear site data for `127.0.0.1:8090` (or use InPrivate)
   to bring back the first-run overlay and reset the saved mode.
4. Start again with `START_AI.bat`.
