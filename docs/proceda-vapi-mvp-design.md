---
date: 2026-04-29
tags: [design, proceda, vapi, voice-agents, mvp]
status: draft
author: Vivek Haldar (with Claude)
---

# Proceda × Vapi MVP — Design Document

> **Reading order.** Skim §0–§2 to understand scope, then jump to §4 for the
> shape of the system, §5 for tradeoffs, §6 for the build plan, and §7 for how
> we'll know it works. §3 (terminology) and §8–§11 are reference.

## 0. TL;DR

Build a single, small, OpenAI-compatible streaming HTTP endpoint —
`POST /v1/chat/completions` — that Vapi treats as a "Custom LLM" (BYOM).
Behind that endpoint sits a new **`VoiceRuntime`** that is a sibling of the
existing `Executor`: it shares the existing `Skill`, `LLMRuntime`,
`MCPOrchestrator`, `RunEvent`, and event-log infrastructure, but has its own
**reactive, per-turn** control loop suited to voice. The MVP runs **one named
SOP** (insurance FNOL) end-to-end, hits **p50 ≤ 350 ms server-side per turn**,
and emits a **per-call audit artifact** that is the wedge.

The MVP is small in surface area and large in care: the surface is one HTTP
endpoint, an extension to the SKILL.md frontmatter, and a per-turn state
machine. The care is in latency, idempotency, and the audit artifact — those
three are the load-bearing engineering decisions. Everything else is defer-able.

The plan is four weeks for a working demo on a real phone number. **It is
explicitly not** a complete voice product: we are deferring tangents,
compensations, refusal policies, multi-tenant, Redis, speculative parallelism,
SOP doc-ingest, and LiveKit. They each get a paragraph in §9 explaining why
later, not now.

---

## 1. Context & Motivation

The companion research note (`docs/voice-agent-infra-integration-research.md`)
makes the case for Vapi-first integration. The relevant facts here:

- Vapi exposes a first-class **Custom LLM hook**: configure a Vapi Assistant
  to point at your own URL and it will POST OpenAI-shaped chat completions on
  every turn. This is the cleanest, most stable insertion point in the voice
  infra ecosystem.
- The contract is just **OpenAI chat completions over SSE**. Whatever we build
  for Vapi is 95% of what is needed for LiveKit and Pipecat later.
- The wedge is **process adherence + audit artifact** — buyers feel the pain
  of voice agents drifting off-script, and there is no incumbent producing a
  per-call SOP-conformance package.

This doc only discusses MVP v1: Vapi only, one SOP, single tenant, in-process
state. Everything else is in §9 (Future Work).

---

## 2. Goals & Non-Goals

### 2.1 Goals (must hit for MVP)

1. **G1 — Working phone call.** A real Vapi-provisioned phone number rings,
   our SOP-driven agent answers, completes one named SOP (insurance FNOL),
   files a mock claim via a tool call, and hangs up.
2. **G2 — Latency.** Server-side per-turn p50 ≤ 350 ms, p95 ≤ 700 ms,
   measured from receipt of `POST /v1/chat/completions` to first SSE delta on
   the wire. (Vapi's voice-to-voice budget is ≤ 800 ms; this leaves headroom
   for STT and TTS.)
3. **G3 — Audit artifact.** At end-of-call, emit a structured artifact (JSON +
   human-readable Markdown) containing: SOP id+version, slot fill timeline
   with provenance, every tool call, every state transition, the full
   transcript, the Vapi `endedReason` (from the end-of-call webhook), and a
   `deviations: []` field. Stored under
   `.proceda/voice-runs/<call_id>/` (canonical addressing — see §4.11 for
   layout details and §4.12 for the chronological index).
4. **G4 — Voice-aware authoring.** SKILL.md is extended with a minimal
   `slots:` declaration and per-step `prompt:` (utterance template). No new
   file format, no separate config tree.
5. **G5 — Reuses Proceda primitives.** New code lives in
   `src/proceda/voice/` and consumes the existing `Skill`, `LLMRuntime`,
   `MCPOrchestrator`, `RunEvent`, and `EventLogWriter`. No fork.
6. **G6 — Idempotent.** Two identical Vapi POSTs (the platform retries) yield
   exactly one state advance and identical SSE output.
7. **G7 — Demo-able from `proceda` CLI.** `proceda voice serve` brings the
   endpoint up; `proceda voice replay <call_id>` re-renders an audit artifact
   from disk.

### 2.2 Non-Goals (explicitly deferred)

- **N1.** Tangent registry, refusal policies, compensation actions. The MVP
  handles them by escalating to "I'll connect you to a human" or by blunt
  re-prompts. We absolutely do *not* try to ship a half-baked tangent
  framework.
- **N2.** Document → SOP graph generation. Authors hand-write SKILL.md with
  slot declarations for v1.
- **N3.** Redis / external state store. In-process dict, single replica.
- **N4.** Multi-tenant. One Proceda process serves one Vapi org.
- **N5.** LiveKit, Pipecat, Retell, Bland adapters.
- **N6.** Speculative parallel extraction + main-response calls.
- **N7.** Web UI for live monitoring. The audit artifact is on disk; a CLI
  command renders it.
- **N8.** SOC 2 / HIPAA-grade encryption-at-rest. **Value-pattern PII
  redaction in transcripts** (SSN, credit-card, account-number patterns)
  is also explicitly out of scope — the existing `redact_secrets` matches
  *key names* like `api_key`/`token`, not free-form values in chat content.
  The MVP audit directory is local-disk-only, never uploaded; that's the
  only PII control we offer. We **must** be honest with any design partner
  that this is a POC.
- **N9.** Custom voice authoring tools (per-step prosody, barge-in tuning).
  The Vapi assistant config owns voice modality knobs.

> **Reversal from earlier draft:** the Vapi `end-of-call-report` webhook is
> now **in MVP scope** (Phase C3). Previous drafts deferred it; that was
> wrong. The webhook is where `endedReason`, the recording URL, and Vapi's
> final transcript live. Without it, the audit artifact is structurally
> incomplete and the §G3 promise is hollow. The reaper still exists as a
> *fallback* (network-flake or webhook-misfire), but the webhook is the
> primary signal.

### 2.3 Non-functional constraints

- Python 3.11+, in keeping with the rest of the package.
- No new top-level dependencies beyond a small async HTTP server (`fastapi` +
  `uvicorn`, both well-vetted) and `sse-starlette` for SSE response framing.
- Pre-commit (ruff + ty + pytest) must remain green. The voice runtime gets
  the same linting bar.
- Pre-existing tests must not regress.

---

## 3. Terminology (used precisely throughout)

- **Turn.** One Vapi POST → one streamed assistant utterance back. Bounded by
  request/response.
- **Step.** A unit of work declared in SKILL.md. May span many turns.
- **Slot.** A named fact the SOP needs (e.g. `policy_number`, `incident_date`).
  Declared in SKILL.md frontmatter.
- **Focus.** The step the agent is currently driving toward, derived from
  "highest-priority eligible step." Not a primary key — recomputed per turn.
- **Action.** A side-effecting tool call (CRM lookup, file claim). Modeled as
  a step that consumes slots and produces facts.
- **Audit artifact.** The end-of-call structured record. The product
  deliverable.
- **VoiceSession.** Per-call durable state. Sibling of the existing
  `RunSession` but slot-aware and turn-indexed.
- **VoiceRuntime.** New top-level class (in `proceda.voice.runtime`) that owns
  the per-turn control loop.
- **VapiAdapter.** The HTTP server that translates Vapi's BYOM contract into
  VoiceRuntime calls.

---

## 4. Architecture

### 4.1 The seven-component picture

```
┌───────────────────────────────────────────────────────────────────┐
│                              Vapi                                  │
│        STT (Deepgram) → assistant turn → POST /chat/completions    │
│        ← SSE deltas ← TTS ← "the next assistant utterance"          │
└──────────────────────────────────────┬────────────────────────────┘
                                       │ HTTPS, SSE
            ┌──────────────────────────▼──────────────────────────┐
            │  VapiAdapter (FastAPI app)        proceda.voice.api │
            │   /v1/chat/completions  /healthz  /v1/voice/audit/* │
            └──────────────────────────┬──────────────────────────┘
                                       │
                                       │   per-turn invocation
                                       ▼
            ┌─────────────────────────────────────────────────────┐
            │  VoiceRuntime               proceda.voice.runtime    │
            │   per-turn control loop (extract → mutate → respond)│
            └──┬─────────────┬─────────────┬───────────────┬─────┘
               │             │             │               │
       ┌───────▼─────┐ ┌─────▼─────┐ ┌─────▼─────┐ ┌──────▼──────┐
       │ SessionStore│ │ SlotEngine│ │ ResponseP-│ │ Audit-      │
       │ (in-mem)    │ │ (extract) │ │ lanner    │ │ Builder     │
       │ proceda.    │ │ proceda.  │ │ proceda.  │ │ proceda.    │
       │ voice.state │ │ voice.slot│ │ voice.plan│ │ voice.audit │
       └──────────────┘ └────┬──────┘ └─────┬─────┘ └──────┬──────┘
                             │              │               │
                             └────► LLMRuntime ◄────────────┘
                                  (existing, extended w/ stream)
                                              │
                                              ▼
                                    MCPOrchestrator (existing)
                                      → user-defined tools
                                              │
                                              ▼
                                  EventLogWriter (existing)
                                  → .proceda/voice-runs/<call_id>/
```

Seven components. Four are new (`VapiAdapter`, `VoiceRuntime`, `SessionStore`,
`SlotEngine`, `ResponsePlanner`, `AuditBuilder` — okay, six new, three reused
near-verbatim, and one extended).

### 4.2 The reuse boundary

| Existing primitive    | Voice MVP relationship                          |
|---|---|
| `Skill`, `SkillStep`  | Reused. Steps still drive flow.                 |
| `Skill` parser        | Extended: parse `slots:` and per-step `prompt:`.|
| `LLMRuntime`          | Extended: add `complete_stream()` returning an `AsyncIterator[LLMDelta]`. |
| `MCPOrchestrator`     | Reused unchanged.                                |
| `ToolExecutor`        | Reused unchanged.                                |
| `RunEvent`            | Reused. Add new `EventType.SLOT_FILLED`, `SLOT_CORRECTED`, `TURN_RECEIVED`, `TURN_COMPLETED`, `INTENT_CLASSIFIED`. |
| `EventLogWriter`      | Reused. New run-dir layout: `.proceda/voice-runs/<call_id>/`. |
| `RunSession`          | **Not** reused. `VoiceSession` is a new dataclass. Trying to mutate `RunSession` to fit voice would drag a lot of CLI/TUI assumptions. |
| `Executor`            | **Not** reused. The voice loop is reactive; the Executor drives a forward-only step pass. Different control structure. See §5.1. |
| `HumanInterface`      | **Not** reused for v1. Voice escalation = "transfer to human", not a `request_clarification` callback. We'll revisit if/when supervisor-takeover lands. |

### 4.3 Module placement

```
src/proceda/voice/
    __init__.py
    api.py              # FastAPI app, /v1/chat/completions handler
    runtime.py          # VoiceRuntime: the per-turn control loop
    state.py            # VoiceSession, SlotValue, StepStatus, SessionStore
    slot.py             # SlotEngine: extraction + intent classification
    plan.py             # ResponsePlanner: focus selection, prompt building
    audit.py            # AuditBuilder, artifact writers
    sse.py              # OpenAI SSE delta framing helpers
    config.py           # VoiceConfig (host/port/auth) loaded from proceda.yaml

src/proceda/skills/parser.py    # extended to parse slots: and prompt:
src/proceda/llm/runtime.py      # add complete_stream()
src/proceda/cli/voice.py        # `proceda voice serve|replay|test`
```

Test mirror:

```
tests/test_voice/
    test_api.py
    test_runtime.py
    test_slot.py
    test_plan.py
    test_audit.py
    test_state.py
    test_e2e.py             # opt-in @pytest.mark.integration
```

---

### 4.4 The Vapi BYOM contract (concrete)

#### Inbound request shape

Vapi POSTs to `POST /v1/chat/completions` with an OpenAI-shaped body **plus
a Vapi-specific envelope**. The canonical body, synthesized from
`docs.vapi.ai/customization/custom-llm/using-your-server` and the official
Flask example at `github.com/VapiAI/server-side-example-python-flask`:

```jsonc
// Headers:
//   Authorization: Bearer <configured-byom-token>
//   Content-Type: application/json
{
  // --- Standard OpenAI fields, forwarded by Vapi ---
  "model": "gpt-4o",                  // ignored by us — assistant-config carry
  "messages": [
    { "role": "system",    "content": "..." },
    { "role": "user",      "content": "..." },
    { "role": "assistant", "content": "..." },
    { "role": "user",      "content": "..." }
  ],
  "temperature": 0.7,                 // ignored by us
  "max_tokens": 250,                  // ignored by us
  "stream": true,                     // ALWAYS true on voice calls; SSE expected
  "tools": [ /* OpenAI-format tool schemas, ignored — our MCP owns tools */ ],

  // --- Vapi-specific envelope ---
  "call": {
    "id": "<call-uuid>",              // our session key
    "orgId": "<org-uuid>",
    "type": "outboundPhoneCall",      // | "inboundPhoneCall" | "webCall"
    "phoneNumber":  { /* E.164 etc. */ },
    "customer":     { "number": "+15551234567" },
    "assistantId":  "<assistant-uuid>",
    "startedAt":    "2026-04-29T17:01:22.001Z"
  },
  "metadata": { /* assistantOverrides.variableValues passthrough */ }
}
```

The MVP reads exactly four things:

- `messages: list[Message]` — source of truth for what was said.
- `call.id: str` — session key.
- `call.assistantId: str` — **primary SOP-resolution key** for v1. The
  `proceda.yaml`'s `voice.assistant_sop_map` (an
  `assistant_id → (sop_id, sop_version)` mapping) is the authoritative
  router. This is the path that *definitely works* against Vapi today.
- `metadata.sop_id`, `metadata.sop_version` — **optional override**, used
  if the `assistant_sop_map` entry has `metadata_overrides_allowed: true`.
  Whether Vapi actually passes `metadata` through to BYOM is unverified
  (§10 q1). Treat passthrough as best-effort and never depend on it for
  correctness.

We **ignore** `model`, `temperature`, `max_tokens`, and `tools`. Our SOP
owns all of those. We treat `tools` in the inbound payload as authoring
noise — Proceda's tool registry is authoritative.

> **Note on schema stability.** The full `call` schema is version-dependent
> (Vapi exposes it at `docs.vapi.ai/api-reference/calls/create/llms-full.txt`).
> Our payload parser must (a) ignore unknown fields, (b) snapshot the parsed
> payload to the audit log under `vapi_request_schema_version`, and (c)
> raise loudly on missing `call.id` or `messages` (the only true required
> fields).

Authentication: Vapi sends the bearer token configured per-Assistant. We
verify it against `VOICE_BYOM_TOKEN` env var (single shared secret for v1).
Out of scope for MVP: HMAC of body, rotating keys, per-tenant secrets.

#### Outbound response shape

Standard OpenAI chat-completion-chunk SSE, content-only:

```
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache

data: {"id":"vc_<call_id>_t<turn_idx>","object":"chat.completion.chunk",
       "choices":[{"index":0,"delta":{"role":"assistant","content":"Thanks "}}]}

data: {...,"delta":{"content":"Jane."}}

...

data: {...,"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

We **do not** surface internal tool calls to Vapi. They execute server-side
inside the same HTTP request. From Vapi's view, the response is plain text.

The `sop-event` extension stream described in the research note is **deferred
to v2** — it is only valuable to first-class embedders (LiveKit, Pipecat),
not to Vapi BYOM, which doesn't read sideband channels.

#### End-of-call detection (webhook-primary, reaper-fallback)

Vapi sends an `end-of-call-report` webhook to a configured server URL when
a call ends. **The MVP subscribes to this webhook** because it is the only
path to a structurally complete audit artifact (it carries `endedReason`,
final transcript, recording URL, and any Vapi-side errors). Architecture:

1. **Primary: `POST /v1/voice/webhooks/end-of-call`.** A second FastAPI
   route (separate from `/v1/chat/completions`) auth'd via a shared HMAC
   over the body using `VOICE_WEBHOOK_SECRET`. On receipt:
   (a) look up session by `message.call.id`;
   (b) **wait for any in-flight turn to drain** — a webhook that arrives
       while the BYOM handler is mid-stream must not finalize before the
       turn's events are committed (otherwise the audit is missing the
       last turn's state). Concretely: `async with session.turn_lock`
       (which the producer also holds at commit time), then
       `if session.in_flight is not None: await session.in_flight.done.wait()`.
       Bounded by `voice.webhook_drain_timeout_ms` (default 5000); on
       timeout, we finalize anyway and tag `finalized_via:
       webhook_with_drain_timeout` so the audit reflects the partial
       state honestly;
   (c) merge `endedReason` and recording metadata into session;
   (d) call `AuditBuilder.finalize(session)`;
   (e) reply `200 OK`.
   Vapi retries on non-2xx with exponential backoff, so the handler must
   be idempotent — a finalized session re-receiving the webhook returns
   the same response without re-finalizing. The `session.finalized` flag
   guards re-entry; it's set inside the same critical section that
   finalizes.
2. **Fallback: session-inactivity reaper.** Sweeps every 30 s; finalizes
   sessions older than 60 s without a turn or webhook. Only fires when
   the webhook fails (network flake, Vapi misfire, server URL
   mis-configured). Tagged in the audit as `finalized_via:
   inactivity_reaper` so we can track how often the fallback is needed.
3. **On-demand finalization.** A `GET /v1/voice/audit/<call_id>` request
   on a non-finalized session forces finalization-from-state (no Vapi
   webhook data) and returns the artifact. Only useful for debugging;
   tag as `finalized_via: on_demand`.

The `finalized_via` tag is itself a load-bearing audit field: it tells a
compliance reviewer how trustworthy the artifact's metadata is.
Webhook-finalized > on-demand > reaper.

---

### 4.5 The per-turn control flow

Inside `VoiceRuntime.handle_turn(call_id, vapi_payload)`:

```
1. Auth + parse                       (  <  1 ms)
2. SessionStore.get_or_create(call_id, sop_id, sop_version)
                                      (  <  1 ms)
3. Idempotency role selection (lock held only during this step)
   - h = sha256(canonicalize(messages))
   - acquire session.turn_lock           ← briefly, just to read/install in_flight
   - if session.in_flight is not None and session.in_flight.msg_hash == h:
        → role = "tail"; capture session.in_flight reference
     elif session.last_completed_hash == h:
        → role = "replay"; capture session.last_response_bytes
     else:
        → role = "produce"; install new InFlightTurn(msg_hash=h)
   - release session.turn_lock           ← producer streams without holding lock
                                      (  <  1 ms)
4. Latest user turn extraction        ( ~150–250 ms — long pole )
   - SlotEngine.extract(messages, session)
   - returns: { extracted_slots, corrections, intent, confidence }
5. State mutation
   - For each slot: fill or correct (with provenance)
   - Recompute step eligibility
   - Recompute focus
                                      (  <  5 ms)
6. ResponsePlanner.plan(session, extraction)
   - Decide response strategy
   - Build per-turn LLM prompt scoped to focus
   - Pick allowed tools for this step from MCPOrchestrator
                                      (  <  5 ms)
2b. (Optional) Stream pre-extraction filler ( first byte: <100 ms )
    - If voice.use_pre_extraction_filler is true and the previous turn's
      focus had `calls:` (i.e., user is likely waiting on a tool), emit
      a static neutral filler — "Let me check that," "One moment," etc.
      — directly from a config-driven template. No LLM call.
    - Static-string framing means first byte hits the wire <100 ms after
      request receipt. This is the only mechanism that meets the L-01a
      target. See §4.5b for filler policy.
6b. (Optional) Stream post-extraction acknowledgment ( <50 ms after step 6 )
    - If plan.user_visible_acknowledgment is set, frame and yield it
      directly from the planner output (no responder LLM round-trip).
    - Still streamed before the responder LLM call begins; bridges the
      time from end-of-extraction to first responder token.
7. Stream main response               ( first token: 100–200 ms )
   - LLMRuntime.complete_stream(prompt, allowed_tools)
   - On tool call: intercept, execute via MCPOrchestrator,
     fold result back, continue stream
   - For each delta: yield to SSE frame writer
8. Persist + commit hash
   - Append turn record + audit-log entries (background task)
   - acquire session.turn_lock briefly:
       session.last_completed_hash = h
       session.last_response_bytes = b"".join(in_flight.chunks)
       session.in_flight = None
     release lock
   - in_flight.finalize()  ← wakes any tailing duplicates
                                      (  <  10 ms async )
9. Emit [DONE]
```

The clock starts at step 1 and the first byte we owe Vapi is in step 2b
(if the pre-extraction filler fires), step 6b (if a post-extraction
acknowledgment is planned), or step 7 (otherwise). So:

- For a **filler turn**: steps 1–2b are critical path; first byte ≤ 100 ms.
- For a **post-extraction acknowledgment turn** (no filler): steps 1–6b
  are critical path; first byte ≤ 500 ms.
- For a **plain-answer turn**: steps 1–7 first-token are critical path;
  first byte ≤ 500 ms.

#### 4.5b Filler policy (when do we say "let me check"?)

The pre-extraction filler is a UX-vs-latency knob. Always-on is annoying
("Let me check… let me check… let me check…"); never-on misses the L-01a
budget for tool-call turns. The MVP rule:

A pre-extraction filler is emitted only when **all** of these hold:

1. `voice.use_pre_extraction_filler` is true (default: true).
2. The previous turn's resolved focus step had `calls:` (we expect a
   tool round-trip).
3. The session's last filler was emitted ≥ N turns ago, where N =
   `voice.filler_min_turn_gap` (default: 3).

When fired, the filler text is sampled from
`voice.filler_templates: list[str]` (default:
`["Let me check.", "One moment.", "Just a sec.", "Looking that up."]`)
and the choice is recorded in the audit (`fillers_emitted: [{turn, text}]`).

Failure mode to avoid: filler fires, then extraction reveals the user
actually changed the topic (tangent / OOS / restart). The filler's text
is generic enough to survive any of those — "Let me check" doesn't
commit to a path. But the planner *must* be able to produce a coherent
follow-up regardless of whether the filler was emitted; it can't assume
the filler set the user's expectations.

#### Critical: idempotency under streaming retries

Vapi can retry a POST while the original is mid-stream. The naïve check
"hash matches `last_message_hash`?" fails because `last_message_hash` is
only set *after* streaming completes — a duplicate arriving during the
first stream sees an empty cache, advances state again, and corrupts the
session.

**Key constraint**: the lock must NOT be held while the producer is
streaming. If it is, a concurrent duplicate can't enter the
"already-in-flight" branch until the original finishes — by which time
the in-flight slot has been cleared and the duplicate just sees a
"completed turn" and replays bytes after the fact. That defeats the
whole purpose of the in-flight buffer.

The right structure is a small **`InFlightTurn`** object that holds the
shared streaming state and a *separate* `asyncio.Condition` for
fan-out. The session-level lock is held only across the
check-or-create critical section.

```python
@dataclass
class InFlightTurn:
    msg_hash: str
    chunks: list[bytes] = field(default_factory=list)   # producer appends
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: Exception | None = None

    async def append(self, chunk: bytes) -> None:
        async with self.cond:
            self.chunks.append(chunk)
            self.cond.notify_all()

    async def finalize(self, error: Exception | None = None) -> None:
        async with self.cond:
            self.error = error
            self.done.set()
            self.cond.notify_all()

    async def subscribe(self) -> AsyncIterator[bytes]:
        idx = 0
        while True:
            async with self.cond:
                while idx >= len(self.chunks) and not self.done.is_set():
                    await self.cond.wait()
                while idx < len(self.chunks):
                    yield self.chunks[idx]
                    idx += 1
                if self.done.is_set():
                    if self.error:
                        raise self.error
                    return
```

The handler holds the session lock just long enough to read or install
the `InFlightTurn`, then releases. Producer and any concurrent duplicates
operate on the `InFlightTurn` directly via its own condvar.

#### Pseudocode for the handler

```python
@router.post("/v1/chat/completions")
async def chat_completions(req: Request) -> StreamingResponse:
    body = await req.json()
    auth_or_403(req.headers)

    call_id = body["call"]["id"]
    sop_id, sop_version = sop_router.resolve(body)   # assistantId-first
    session = await session_store.get_or_create(call_id, sop_id, sop_version)
    msg_hash = hash_messages(body["messages"])

    # Critical section: pick role (producer or replay/tail) under the lock,
    # then exit the lock before streaming.
    role: Literal["produce", "tail", "replay"]
    in_flight: InFlightTurn | None = None
    cached_bytes: bytes | None = None

    async with session.turn_lock:
        if session.in_flight is not None and session.in_flight.msg_hash == msg_hash:
            in_flight = session.in_flight
            role = "tail"
        elif session.last_completed_hash == msg_hash:
            cached_bytes = session.last_response_bytes
            role = "replay"
        else:
            in_flight = InFlightTurn(msg_hash=msg_hash)
            session.in_flight = in_flight
            role = "produce"
    # Lock released — producer streams, tailers consume.

    async def stream() -> AsyncIterator[bytes]:
        if role == "replay":
            yield cached_bytes
            return
        if role == "tail":
            async for chunk in in_flight.subscribe():
                yield chunk
            return
        # role == "produce"
        try:
            async for chunk in voice_runtime.handle_turn(session,
                                                         body["messages"]):
                await in_flight.append(chunk)
                yield chunk
            # Successful completion: commit hash + cached bytes under the lock.
            async with session.turn_lock:
                session.last_completed_hash = msg_hash
                session.last_response_bytes = b"".join(in_flight.chunks)
                session.in_flight = None
            await in_flight.finalize()
        except Exception as e:
            async with session.turn_lock:
                session.in_flight = None
            await in_flight.finalize(error=e)
            raise

    return StreamingResponse(stream(), media_type="text/event-stream")
```

This satisfies the A7 acceptance test ("duplicate's response started
arriving before the original finished") because the duplicate's
`subscribe()` returns a chunk as soon as the producer appends one — it
doesn't wait for the producer to finish.

`voice_runtime.handle_turn` is an async generator. It does extraction,
plans, streams the response, intercepts tool calls, and writes to the
audit log as side effects — all while yielding SSE-framed bytes. The
locking, fan-out, and buffering live in the handler and the
`InFlightTurn` helper, not in `VoiceRuntime`, so the runtime stays
testable in isolation.

---

### 4.6 Slot extension to SKILL.md

Minimal additive change to `skills/parser.py`. The hardened FNOL frontmatter
demonstrates every feature:

```yaml
---
name: insurance_fnol
description: First Notice of Loss intake for auto insurance,
             with required recording-consent disclosure.
sop_version: "0.2.0"

slots:
  - id: consent_to_record_acknowledged    # required-before-progress gating slot
    type: boolean
    required: true
  - id: customer_name
    type: string
    required: true
  - id: policy_number
    type: string
    required: true
    pattern: '^\d{3}-\d{3}$'              # validation hint, not enforcement (v1)
  - id: incident_date
    type: date
    required: true
  - id: incident_location
    type: string
    required: true
  - id: incident_description
    type: string
    required: true
  - id: injuries_reported                  # closed-vocabulary, eval-friendly
    type: enum
    values: [none, minor, serious, unknown]
    required: true
  - id: vehicle_damage_description
    type: string
    required: false
  - id: file_claim_confirmed                # caller-confirmation control slot for step 7
    type: boolean
    required: false

required_tools:
  - crm__lookup_policy
  - claims__file_fnol
---

### Step 1: Greet and disclose recording
---
prompt: "Hi, I'm Aria with Acme Insurance. This call is being recorded for quality and claim-handling purposes — is that okay with you?"
fills: [consent_to_record_acknowledged]
on_mismatch: escalate_to_human
---

### Step 2: Identify caller
---
prompt: "Thanks. Could I get your name and policy number, please?"
fills: [customer_name, policy_number]
---

### Step 3: Verify policy
---
prompt: "Let me pull up policy {policy_number}…"
calls:
  - tool: crm__lookup_policy
    args: { policy_number: "{policy_number}" }
on_mismatch: confirm_then_proceed
---

### Step 4: Gather incident facts
---
prompt: "Can you tell me when and where it happened, and what occurred?"
fills: [incident_date, incident_location, incident_description]
---

### Step 5: Check for injuries
---
prompt: "Were there any injuries — to you, anyone in your vehicle, or anyone else?"
fills: [injuries_reported]
---

### Step 6: Gather vehicle damage
---
prompt: "What damage do you see on the vehicle?"
fills: [vehicle_damage_description]
---

### Step 7: Confirm and file
[CALLER CONFIRM REQUIRED]
---
prompt: "To confirm: on {incident_date} at {incident_location}, {incident_description}. Injuries: {injuries_reported}. Damage: {vehicle_damage_description}. Should I file the claim now?"
caller_confirm:
  slot: file_claim_confirmed
  values_yes: ["yes", "go ahead", "file it", "do it"]
  values_no:  ["no", "wait", "stop", "hold on", "don't"]
  on_no: escalate_to_human
calls:
  - tool: claims__file_fnol
    args: { policy_number: "{policy_number}", incident_date: "{incident_date}", ... }
    requires_slot: { file_claim_confirmed: true }
---
```

#### Why these specific slots

- **`consent_to_record_acknowledged`** is a *required-before-progress* slot
  with `on_mismatch: escalate_to_human`. It is the audit artifact's sharpest
  moment: a compliance officer reading `audit.json` instantly recognizes
  "the agent disclosed recording on this call before substantive
  conversation, and the caller acknowledged." If the agent ever proceeds
  without this slot filled, `deviations_from_sop` lights up unmistakably.
  This pattern (required-before-progress gating slot) generalizes to
  mini-Miranda for collections, GLBA disclosure for financial services,
  HIPAA acknowledgment for healthcare, and so on. Single demo SOP, multiple
  vertical signals.
- **`injuries_reported` as an enum** rather than letting injury status
  collapse into free-form `incident_description` gives the slot extractor
  (§4.8) a closed-vocabulary target with crisp confidence scoring. Enum
  slots are how the extraction eval (§7.7) gets reliable accuracy
  measurements without LLM-as-judge fuzziness.
- **6 required slots + 1 optional** sits at the upper edge of comfortable
  for a 2–4 minute call. Drop any one and the design barely gets exercised.

#### A note on terminology

The FNOL SOP handles auto-policy and crash facts. That is **PII-adjacent**,
not **PHI-adjacent** — PHI is HIPAA-protected health data specifically.
This matters because "PHI" signals to buyers that they should expect
HIPAA-grade controls (BAA, encryption-at-rest, FedRAMP-style audit posture).
We do not have those (§2.2 N8). Calling FNOL "PII-adjacent" is honest and
survivable; calling it "PHI-adjacent" picks an unwinnable fight.

#### Per-step directive grammar (formal)

After the `### Step N: Title` heading and any optional `[MARKER]` line, an
**optional fenced YAML block** carries the per-step directives. The fence
is the same `---` triple-dash used for frontmatter:

```
### Step N: Title
[OPTIONAL_MARKER]
---
prompt: "..."
fills: [...]
calls: [...]
caller_confirm: {...}
on_mismatch: handler_name
---

Free-form description / human notes go here, after the closing fence.
```

Recognized directive keys (closed set; unknown keys are a `proceda lint`
error):

- `prompt: str` — utterance template; `{slot_id}` interpolation against
  current state. Required if the step is voice-eligible.
- `fills: list[slot_id]` — slot IDs the user is expected to provide
  during this step. May be filled out-of-order.
- `calls: list[ToolCall]` — server-side tool invocations.
  `{ tool: str, args: dict, requires_slot: dict | None }`. Multiple
  tools execute in declared order; halt on first failure.
- `caller_confirm: CallerConfirmSpec | None` — see "Approval gates",
  below.
- `on_mismatch: handler_name` — handler from a closed set:
  `escalate_to_human | confirm_then_proceed | retry_once | abort_call`.

A step body without a fenced block is treated as raw content (legacy
SKILL.md skills still parse and run in non-voice mode).

#### Approval gates in voice mode

The existing `[APPROVAL REQUIRED]` and `[PRE-APPROVAL REQUIRED]` markers
mean "pause and wait for a human via `HumanInterface`." In voice mode there
is no TUI prompt and no synchronous human at the keyboard — pretending to
honor those markers by silently skipping them is a **bug**, not "graceful
degradation."

The MVP introduces a new marker — `[CALLER CONFIRM REQUIRED]` — and
**bans** the existing approval markers in voice-mode skills:

- A SKILL.md tagged `voice: true` (in frontmatter) with `[APPROVAL
  REQUIRED]` or `[PRE-APPROVAL REQUIRED]` on any step is a **lint error
  blocking publish.** `proceda lint` rejects it with: *"approval markers
  are not honored in voice mode; use `[CALLER CONFIRM REQUIRED]` with a
  `caller_confirm:` block, or `escalate_to_human` via `on_mismatch:`."*
- `[CALLER CONFIRM REQUIRED]` requires a sibling `caller_confirm:` block
  declaring the boolean confirmation slot, the yes/no synonym sets, and
  the `on_no` handler. The runtime treats this slot exactly like any
  other required slot — extracted from the user's reply, with confidence
  scoring. The action's `requires_slot:` predicate gates execution.
- This makes "approval" a *first-class slot fill*, not a runtime
  interrupt. The audit artifact records the slot value, the user's exact
  utterance that produced it, and the resulting decision. A compliance
  reviewer can replay the determination with the same machinery as any
  other slot.

For escalations to a live human, `on_mismatch: escalate_to_human` triggers
the Vapi `transferCall` control tool (see §4.10b). That is the *only*
voice-mode "approval" path that actually involves a non-runtime human.

Other notes:

- `slots:` lives in frontmatter because slots are global to the SOP, not
  per-step.
- `required_tools:` already exists; nothing changes.

### 4.7 The session-state record

```python
@dataclass
class SlotValue:
    value: Any
    filled_at: datetime
    source_turn: int
    confidence: float
    history: list[SlotChange] = field(default_factory=list)  # corrections

@dataclass
class StepRecord:
    step_index: int
    status: Literal["pending", "eligible", "completed", "blocked", "skipped"]
    completed_at_turn: int | None = None
    completed_via: Literal["direct", "side_effect"] | None = None
    blocked_by: list[int] = field(default_factory=list)

@dataclass
class VoiceSession:
    call_id: str
    sop_id: str
    sop_version: str
    started_at: datetime
    last_turn_idx: int = 0

    # Idempotency state (see §4.5):
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    in_flight: InFlightTurn | None = None             # active producer's turn
    last_completed_hash: str | None = None            # last fully-streamed turn
    last_response_bytes: bytes | None = None          # cached for after-the-fact replay

    # SOP state:
    slots: dict[str, SlotValue | None] = field(default_factory=dict)
    steps: dict[int, StepRecord] = field(default_factory=dict)
    focus: int | None = None
    transcript: list[RunMessage] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    escalations: list[EscalationRecord] = field(default_factory=list)

    # Filler state (see §4.5b):
    last_filler_turn_idx: int | None = None

    # Lifecycle:
    finalized: bool = False
    finalized_via: Literal["webhook", "webhook_with_drain_timeout",
                           "reaper", "on_demand"] | None = None
    vapi_end_of_call: dict | None = None              # raw webhook payload, if received
```

Key invariants:

- **No `current_step` integer.** `focus` is derived per turn from
  "highest-priority eligible-but-not-completed step" (see §5.2 for the
  eligibility/completion distinction). Consequence: when the user
  answers ahead, we mark side-effect completions and recompute focus
  naturally.
- **Slot values are append-only.** Corrections push to `history`, then
  overwrite `value`. Audit can reconstruct full timeline.
- **`(turn_lock, in_flight, last_completed_hash, last_response_bytes)`
  is the idempotency quartet.** On retry we either tail the in-flight
  buffer or re-emit cached bytes; never advance state twice. Bounded by
  one cached response and one in-flight buffer per session — no
  unbounded memory growth.

---

### 4.8 Slot extraction & intent classification

`SlotEngine.extract(messages, session) -> ExtractionResult`.

Implementation: **two-pass per turn.**

Pass 1 (extraction call): a separate LLM call with structured output. Prompt
contains: SOP slot schema, current slot values, latest user turn.
Returns:

```json
{
  "intent": "ANSWER" | "CORRECTION" | "REFUSAL" | "OUT_OF_SCOPE" | "RESTART" | "UNKNOWN",
  "extracted_slots": { "<slot_id>": { "value": "...", "confidence": 0.0–1.0 }, ... },
  "corrections": [
    { "slot": "...", "old_value": "...", "new_value": "...", "confidence": 0.0–1.0 }
  ],
  "notes": "free text the model wants the planner to know about"
}
```

Why two-pass and not single-pass tool-calling: cleaner separation, eval-able
independently, debuggable. The latency hit (~150–250 ms) is real and the
research doc proposes speculative parallelization as the v2 fix.

#### Model choice

For MVP, **the extraction call uses `claude-haiku-4-5-20251001`** (fast,
cheap, fine for slot extraction with structured output). The main response
call uses whatever the operator configured globally (default
`claude-sonnet-4-6`). Both are configurable via `voice.extractor_model` and
`voice.responder_model` in `proceda.yaml`.

#### Intent semantics

- `ANSWER` — straightforward response to focus's prompt. Default path.
- `CORRECTION` — explicit correction of a previously filled slot. Triggers
  the corrections handler (record old value, overwrite, mark dependent steps
  as needing re-eval).
- `REFUSAL` — user declines or can't answer. MVP behavior: re-prompt once
  with a rephrased ask, then escalate.
- `OUT_OF_SCOPE` — user is talking about something the SOP doesn't cover.
  MVP behavior: redirect with a generic template; on third occurrence in a
  call, escalate to "I'll connect you to a human."
- `RESTART` — user wants to start over. MVP behavior: refuse politely and
  continue. (Restart is dangerous mid-action; defer real handling.)
- `UNKNOWN` — extractor isn't sure. Treat as `ANSWER` and lean on the main
  responder to ask a clarifying question.

This is the **smallest non-embarrassing intent set**. The four-bucket
fallback (just `ANSWER`/`CORRECTION`/`OUT_OF_SCOPE`/`UNKNOWN`) was
considered and rejected because not modeling refusal makes long required
slots feel hostile to users.

---

### 4.9 Response planning

`ResponsePlanner.plan(session, extraction) -> ResponsePlan` decides, given
the new state, *what kind* of utterance to generate. Outputs:

```python
@dataclass
class ResponsePlan:
    strategy: Literal[
        "advance_focus",            # ordinary forward step
        "acknowledge_correction",   # explicit "got it, updating to X"
        "confirm_before_action",    # the focus step is action-bearing,
                                    # user asked for it, but a value mismatched
        "redirect_oos",             # out of scope
        "reprompt_after_refusal",
        "escalate_to_human",
        "wrap_up",                  # all required slots filled, focus is end
    ]
    focus_step_index: int | None
    system_prompt: str           # tight, focus-scoped
    allowed_tools: list[dict]    # subset of MCP tools
    user_visible_acknowledgment: str | None   # prefix the responder must say
```

The planner is **plain Python**, not an LLM call. It reads state, applies
deterministic rules, and produces a plan. This is on purpose: the planner
must be cheap (<5 ms) and predictable enough to test without mocking an LLM.

Critical design choice: **the system prompt for the main response call is
small and step-scoped**, not the whole SOP. This is the same insight Pipecat
Flows leans on — keep the LLM's working set tight.

---

### 4.10 Streaming the response and intercepting tool calls

`LLMRuntime.complete_stream()` (new method) is an async generator yielding
`LLMDelta`s. Each delta is one of:

- `TextDelta(content="...")` — pass through to SSE.
- `ToolCallStart(call_id, name, args_partial)` / `ToolCallArgsDelta(...)` /
  `ToolCallEnd(call_id)` — buffer until complete, then execute.
- `Done(finish_reason)` — stream is done.

The wrapper logic in `VoiceRuntime`:

```python
async def _stream_response(plan, session) -> AsyncIterator[bytes]:
    messages = plan.build_messages(session)
    while True:
        async for delta in llm.complete_stream(messages, plan.allowed_tools):
            if isinstance(delta, TextDelta):
                yield sse_frame_text_delta(call_id, turn_idx, delta.content)
            elif isinstance(delta, ToolCallEnd):
                tool_call = collect_tool_call(...)
                result = await mcp_orchestrator.call_tool(tool_call.name, tool_call.args)
                # Append assistant tool-call message + tool result message,
                # restart inner loop to continue the model's turn.
                messages.append(tool_call_message(tool_call))
                messages.append(tool_result_message(tool_call.id, result))
                break  # restart streaming from the new messages
            elif isinstance(delta, Done):
                yield sse_frame_done(call_id, turn_idx)
                return
        else:
            return  # async-for completed without break
```

Two things to call out:

1. **Tool calls block first audio.** If a tool call happens before any text,
   the user hears silence until tool completes. For an MVP this is
   acceptable for one or two short tool calls per turn; we do not chain.
   Mitigation: the planner streams its `user_visible_acknowledgment`
   prefix directly to the SSE wire (step 6b in §4.5) before the responder
   LLM call begins. This gets first-byte to the wire in <50 ms even for
   tool-call turns, masking the tool-execution latency.
2. **Per-turn tool budget.** Hard cap of 3 tool calls per turn; on the 4th,
   we abort and escalate. Prevents runaway loops blowing the latency budget
   silently.

### 4.10b Vapi control-tool exceptions (transferCall, endCall)

§5.6 says "all tools in Proceda; Vapi sees zero tool definitions." That is
true for *MCP-defined* tools. There are exactly **two exceptions**: the
Vapi-side call-control tools that *only* Vapi can execute, because they
manipulate the live audio session that Vapi owns. Without these, the SOP
can't transfer to a human or hang up.

#### Vapi-side configuration contract

`transferCall` and `endCall` are not magic — they are Vapi's "default
tools" that **must be added to the assistant's `tools` array** in the
Vapi dashboard or API for the agent to be allowed to invoke them
([Vapi default tools docs](https://docs.vapi.ai/tools/default-tools)).
The transfer destination(s) live in the tool config, not in our
emitted args. So the integration has two halves:

1. **Vapi-side config (Phase D2):** the assistant's `tools` array
   includes:
   ```jsonc
   {
     "type": "transferCall",
     "destinations": [
       { "type": "number", "number": "+15555550100", "message": "Transferring to a specialist." }
     ]
   }
   ```
   plus a `{ "type": "endCall" }` entry. Without these in the assistant
   config, our emitted tool calls fall on the floor and the call
   continues with no transfer/hangup.

2. **Proceda-side emission (in the SSE stream):** when the planner
   decides to escalate or wrap up, our SSE stream includes a single
   OpenAI-format `tool_calls` chunk naming `transferCall` or `endCall`.
   Vapi's built-in handlers match by tool name and execute the action
   using the destination(s) configured server-side. We do not include
   destination args in the emitted call (Vapi's config supplies them).

The exact streamed shape (subject to week-1 sandbox validation against
Vapi's BYOM expectations):

```jsonc
data: {
  "id": "vc_<call_id>_t<turn_idx>",
  "object": "chat.completion.chunk",
  "choices": [{
    "index": 0,
    "delta": {
      "tool_calls": [{
        "index": 0,
        "id": "call_xyz",
        "type": "function",
        "function": { "name": "transferCall", "arguments": "{}" }
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
data: [DONE]
```

#### What we own vs. what Vapi owns

| Concern | Owned by |
|---|---|
| Decision to transfer/hangup | Proceda (planner strategy) |
| Tool config + destinations | Vapi assistant config (D2) |
| Tool-call SSE shape | Proceda (emitter) |
| Audio handoff after transfer | Vapi |
| Hangup signaling on the line | Vapi |
| Audit event `VAPI_CONTROL_TOOL_EMITTED` | Proceda |

We never define them as MCP tools and the responder LLM never sees them
as available tools (the responder gets *MCP* tools only, from the
planner's `allowed_tools`). This means the SSE response is *not*
"content-only" in the strict sense — two specific tool-call chunks can
appear, by name. We treat it as a narrow exception, not a general
capability. Auditing: every emitted control-tool call is logged as a
`VAPI_CONTROL_TOOL_EMITTED` event with the tool name, args, the emitting
strategy, **and** the assistant config snapshot showing the destination
that Vapi was configured to use (so the audit can prove where a transfer
went, since we don't choose it).

| Behavior the SOP wants | How it fires |
|---|---|
| "Connect me to a human" | `escalate_to_human` planner strategy → emits `transferCall` |
| Step's `on_mismatch: escalate_to_human` triggers | runtime → emits `transferCall` |
| End-of-call after wrap-up utterance | `wrap_up` planner strategy → emits `endCall` |
| OOS redirect cap (3rd OOS) hits | runtime → emits `endCall` with `reason: oos_cap_hit` |

A future v2 SOP-author surface may declare custom `transferCall`
destinations per step. For MVP, the destination is a single
`voice.escalation_destination` config value (e.g. a fallback queue
number).

---

### 4.11 Audit artifact

#### Source of truth

The **append-only event log (`events.jsonl`) is the canonical
record**. Every state-affecting decision (slot fill, slot correction,
step completion, tool call begin/end, escalation, control-tool emission)
emits a `RunEvent` *before* in-memory state is mutated. From any complete
event log, `AuditBuilder.from_events_only(events)` deterministically
reconstructs the audit artifact.

In-memory `VoiceSession` and `state-final.json` are **a derived cache**,
not a source of truth. `AuditBuilder.finalize(session)` is a fast-path
that reads the cache; it asserts in CI that
`from_events_only == finalize` byte-for-byte (modulo timestamps). This
asymmetry is what makes the artifact replayable and gives a compliance
team the structural guarantee that the audit can be re-derived
independently — see AV-02 (§7.5).

#### Run directory layout

```
.proceda/voice-runs/<call_id>/         ← canonical addressing (Vapi call IDs are unique)
    metadata.json                       # call_id, sop_id, sop_version, started_at, ended_at
    events.jsonl                        # one RunEvent per line (existing format) — the truth
    transcript.jsonl                    # one RunMessage per line (clean transcript)
    state-final.json                    # derived cache; not authoritative
    audit.json                          # the artifact (the deliverable)
    audit.md                            # human-readable rendering of audit.json
    vapi-end-of-call-report.json        # raw Vapi webhook payload, if received
    artifacts/                          # any tool-produced artifacts
```

Note the divergence from existing `RunDirectoryManager`'s
`{timestamp}_{shortid}` layout: voice runs use `<call_id>` directly
because the call ID is externally meaningful (it's how Vapi addresses the
call, how `GET /v1/voice/audit/<call_id>` looks it up, and how a
compliance officer references the artifact). The existing
`RunDirectoryManager` is reused for non-voice runs; voice runs get a new
`VoiceRunDirectoryManager` so the two paths don't entangle.

Chronological browsing is served by `.proceda/voice-runs/index.jsonl`,
which append-only logs `{call_id, sop_id, started_at, dir, finalized_via}`
per call. `proceda voice replay --recent N` reads it. No filesystem
traversal of the parent dir is needed.

#### Artifact fields

The artifact (`audit.json`) is built by
`AuditBuilder.from_events_only(events)` and (for fast-path) cached in
`AuditBuilder.finalize(session)`. Fields, exhaustively:

```json
{
  "schema_version": "0.1.0",
  "call_id": "...",
  "sop_id": "insurance_fnol",
  "sop_version": "0.1.0",
  "started_at": "...",
  "ended_at": "...",
  "duration_seconds": 197,
  "outcome": "completed" | "abandoned" | "escalated" | "errored",
  "slots": {
    "<slot_id>": {
      "value": "...",
      "filled_at": "...",
      "source_turn": 4,
      "confidence": 0.96,
      "history": [
        { "value": "...", "set_at": "...", "source_turn": 2, "reason": "user_correction" }
      ]
    },
    ...
  },
  "required_slots_satisfied": true,
  "steps": [
    { "step_index": 1, "status": "completed", "completed_at_turn": 1, "completed_via": "direct" },
    ...
  ],
  "corrections": [
    { "slot": "policy_number", "old": "123-456", "new": "124-456", "turn": 2 }
  ],
  "tool_calls": [
    { "name": "crm__lookup_policy", "args": {...}, "result_status": "ok",
      "duration_ms": 184, "turn": 2,
      "invalidated_by_correction": false }
  ],
  "escalations": [],
  "out_of_scope_redirects": 0,
  "deviations_from_sop": [],
  "vapi_end_of_call": {
    "endedReason": "customer-ended-call",
    "ended_at": "...",
    "recording_url": "https://...",
    "received_via": "webhook" | "reaper" | "on_demand"
  },
  "finalized_via": "webhook" | "reaper" | "on_demand",
  "transcript_ref": "transcript.jsonl"
}
```

`deviations_from_sop` is computed by replaying the event log against the
declared step graph: any `STEP_COMPLETED` whose preconditions weren't met,
any required slot left empty at end-of-call, any tool called outside its
declared step, any `[CALLER CONFIRM REQUIRED]` step that ran an action
without the matching `caller_confirm` slot set to true. **This
computation is the actual product**; everything else is plumbing. It is
deterministic and replayable from the event log alone — that is how a
compliance officer trusts it.

---

### 4.12 Configuration extensions

`proceda.yaml`:

```yaml
voice:
  enabled: true
  host: "0.0.0.0"
  port: 8080
  byom_token_env: "VOICE_BYOM_TOKEN"
  webhook_secret_env: "VOICE_WEBHOOK_SECRET"   # HMAC for end-of-call webhook
  extractor_model: "anthropic/claude-haiku-4-5-20251001"
  responder_model: "anthropic/claude-sonnet-4-6"
  per_turn_tool_budget: 3
  inactivity_finalize_seconds: 60
  reaper_interval_seconds: 30
  audit_dir: ".proceda/voice-runs"
  escalation_destination: "+15555550100"        # transferCall target

  # Authoritative SOP routing: assistantId → SOP. metadata.sop_id may
  # override only when metadata_overrides_allowed is true.
  assistant_sop_map:
    asst_demo_fnol_2026:
      sop_id: insurance_fnol
      sop_version: "0.2.0"
      metadata_overrides_allowed: false
```

All fields have sensible defaults so an empty `voice:` block boots a
no-op server (which 503s every BYOM request, with a clear log message
about the missing `assistant_sop_map`).

---

## 5. Design tradeoffs (the ones that mattered)

### 5.1 New `VoiceRuntime` vs reuse `Executor`

**Decision:** new `VoiceRuntime`.

The existing `Executor` is shaped around "drive the skill from current step
to completion in one async call, with approval gates and a single LLM
conversation." That shape is wrong for voice in three ways:

1. **Voice is reactive per turn.** The Executor's outer loop owns the
   conversation; here Vapi owns the conversation, and we are called with
   each user turn. Inverting that loop is a significant rewrite.
2. **Voice extraction happens *before* the response.** The Executor calls
   LLM once per "step iteration"; voice needs at least two LLM calls per
   user turn (extract + respond), with state mutation in between.
3. **Approval gates in voice are escalations, not pauses.** The
   `HumanInterface` model (synchronous prompt → blocking wait) doesn't fit;
   in voice we don't pause, we transfer.

Trying to retrofit `Executor` to support both modes via a strategy pattern
adds complexity to the most-tested code in the repo. The separate-runtime
choice is: write a new ~400-line `VoiceRuntime` against the same primitives,
keep `Executor` untouched.

Cost: two control loops to maintain. Mitigation: shared primitives
(`Skill`, `LLMRuntime`, `MCPOrchestrator`, `RunEvent`, audit log) absorb
~80% of the surface; the runtimes only differ in their loop. We can
generalize later if patterns converge.

### 5.2 Forward-only steps vs slot-eligibility graph

**Decision:** introduce a slot-eligibility model **inside** the existing
step abstraction, not replace it.

The research note argues "internally there is no current step — there's an
eligibility set." Correct, but a full graph-based rewrite of the SOP
abstraction is too much for an MVP. Compromise:

- Steps remain numbered 1..N as today.
- Each step has **two distinct conditions** that the runtime tracks
  independently:
  - **Prerequisites satisfied** ("eligible" — can we drive toward this
    step?). Step N's prerequisites are: every step M < N has either
    completed *or* has all its `fills:` slots already filled out of order.
    This means step 1 (with no prior steps) is always eligible at call
    start regardless of whether its own `fills:` are populated.
  - **Completion criteria met** ("completed" — is this step done?).
    Every slot in the step's `fills:` is filled, every `calls:` has
    succeeded, and (if `[CALLER CONFIRM REQUIRED]`) the
    `caller_confirm.slot` is set to true.
- `focus = highest-priority step that is eligible AND not yet completed`
  is computed per turn.
- Steps in voice mode can be marked completed via `completed_via:
  side_effect` when their declared `fills:` slots get filled by a turn
  ostensibly answering a different question.
- Step state machine: `pending → eligible → in_progress → completed |
  skipped | blocked`. `in_progress` is "currently the focus."
- Authors still write SKILL.md as a 1..N sequence. The runtime is permitted
  to reach step N early or to advance focus past steps whose slots are
  already filled.

This is the smallest extension that delivers the "user can answer ahead"
behavior. We absolutely *don't* introduce a new graph DSL for the MVP.

### 5.3 In-process state vs Redis

**Decision:** in-process `dict[call_id, VoiceSession]` for v1.

Pros: simpler, faster (Redis adds 1–3 ms per turn even on localhost),
zero ops burden.

Cons: no horizontal scale, no survival across process restart, no shared
state across two replicas.

For an MVP serving one demo phone number, none of those cons bite. Vapi's
retry-on-failure tolerates a brief outage. We finalize sessions on
inactivity, so an orphaned session isn't catastrophic.

V2 carry-over: the `SessionStore` interface is small (`get_or_create`,
`get`, `finalize`, `iter_active`). Swapping to Redis is mechanical when
warranted.

### 5.4 Two-pass extraction vs single-pass tool-calling

**Decision:** two-pass.

Single-pass (the responder model is required to call `update_state(...)`
before speaking) is lower-latency in the happy case but:

- harder to enforce — the model can speak first;
- harder to debug — extraction is entangled with response generation;
- harder to evaluate independently — slot-extraction accuracy is one of the
  two things we *must* measure to know the system works.

Two-pass costs us ~150–250 ms per turn but makes the eval story clean. We
can revisit speculative parallelism (start the responder optimistically
while extraction runs) once the eval harness is in place. Don't optimize
the second LLM call before the first one is right.

### 5.5 Idempotency by message-hash vs explicit turn_idx

**Decision:** SHA-256 of the full `messages` array.

Vapi's BYOM contract does not expose a `turn_idx`; `messages` is the only
ground truth. Hashing the array (canonicalized: stable JSON encoding,
strip non-load-bearing fields like server-set IDs) is reliable and
race-free.

Edge case: Vapi appends `tool` messages between turns in some flows. Our
canonicalization treats those as load-bearing too — they change the hash,
so a retry that includes them gets a fresh state advance. That's correct,
not a bug.

Cost: hashing is ~1 ms even for long conversations. Acceptable.

### 5.6 Tools in Proceda vs tools in Vapi

**Decision:** all tools in Proceda; Vapi sees zero tool definitions.

The opposing option is to declare some tools to Vapi (so they execute
client-side via Vapi's tool framework). Pros: Vapi handles tool retries,
parallelism, observability. Cons: tool execution is no longer in our audit
log; we lose authoritative provenance; we have to bridge two tool systems.

The audit artifact is the wedge. If tool calls are not in our event log,
the artifact is not authoritative. So: tools live in Proceda, period.

### 5.7 SSE framing: server-sent vs raw streaming-response

**Decision:** standard SSE via `sse-starlette`, content events only.

Vapi's BYOM expects OpenAI's `text/event-stream` format. We follow the
shape exactly — `data: {chunk}\n\n` lines, terminated with `data: [DONE]`.
We don't invent `event:` types because Vapi will not consume them.

The research note's `sop-event` extension stream is **not in MVP scope** —
deferred until LiveKit/Pipecat where it's useful.

### 5.7b End-of-call: webhook-primary vs reaper-only

**Decision:** subscribe to Vapi's `end-of-call-report` webhook in MVP.
Reaper is a fallback, not the primary path.

Earlier drafts deferred the webhook because "one less inbound surface."
That was wrong. Without the webhook the audit artifact is missing
canonical Vapi-side metadata: `endedReason`, recording URL, Vapi's
final transcript, and any platform-side errors. G3 is the wedge; an audit
artifact that lacks those fields is hollow.

Cost of inclusion: one additional FastAPI route (~50 LOC), HMAC
verification (~10 LOC), and idempotent finalization (~20 LOC). Total
maybe 100 lines plus tests.

The reaper still earns its keep — it's the only path when the webhook
fires against a misconfigured server URL. But it's a fallback, not a
substitute.

### 5.7c Approval as slot-fill, not runtime interrupt

**Decision:** in voice mode, the existing `[APPROVAL REQUIRED]` markers
are a lint error. Approval is modeled either as `caller_confirm:` (a
boolean slot extracted like any other) or `escalate_to_human` (a Vapi
control-tool emission).

The earlier draft said voice mode would "log
`pre_approval_skipped: voice_mode` and proceed." That preserves the
appearance of oversight while removing it — the worst of both worlds. A
compliance reviewer sees `[APPROVAL REQUIRED]` in the SOP and assumes a
human signed off; in reality nobody did.

Modeling caller-confirmation as a slot has nice properties:

- The slot has provenance (which user utterance set it, with what
  confidence), and the audit shows the determination directly.
- The action's `requires_slot:` predicate is the gate, so the gate is
  enforced by the same code path as any other slot dependency.
- The eval suite covers it for free — confirmation is just another
  enum slot.

The cost is that SOP authors have to re-tag any voice-mode steps that
used `[APPROVAL REQUIRED]`. `proceda lint` catches it with a clear
error message.

### 5.8 Authorship surface: minimal vs full voice DSL

**Decision:** minimal additive YAML in existing SKILL.md.

The temptation is to give SOP authors a richer surface — tangent registry,
refusal policies, prosody, multi-language. The research note describes all
of these. **We do none of them in MVP.** We add `slots:`, per-step
`prompt:`, `fills:`, `calls:`. That's enough to run the demo SOP
end-to-end. Each additional knob has to earn its place by a real customer
need.

The cost of missing tangent handling is that the demo SOP cannot
gracefully field "will this raise my premium?" It will redirect (`OUT_OF_SCOPE`).
We accept that for the MVP demo and make the limitation explicit when
showing it.

---

## 6. Task Breakdown

Sized in IC-engineer-days, aimed at one engineer working in series. Phases
overlap somewhat in practice; the dependency graph is in §6.5.

### 6.1 Phase A — Plumbing (week 1, ~5 days)

**A1. Skeleton package + config**  ·  0.5 d
- Create `src/proceda/voice/` with empty modules.
- Add `VoiceConfig` dataclass; load under `proceda.yaml` `voice:` block.
- Wire `proceda voice serve` Typer subcommand (no-op stub, just boot
  FastAPI on the configured port).
- Acceptance: `proceda voice serve` returns 200 on `/healthz`.

**A2. SKILL.md slot parser extension**  ·  1.5 d
- Extend `skills/parser.py` to parse `slots:` frontmatter, the per-step
  fenced YAML directive block (§4.6 grammar: `prompt:`, `fills:`,
  `calls:`, `caller_confirm:`, `on_mismatch:`), and the new
  `[CALLER CONFIRM REQUIRED]` marker.
- Add `Slot`, `StepDirectives`, `CallerConfirmSpec`, `ToolCall` dataclasses
  in `proceda/skill.py`.
- Backwards-compat: skills without slots/directives still parse.
- Update `proceda lint` to: (a) validate slot/step coherence; (b) reject
  `[APPROVAL REQUIRED]` / `[PRE-APPROVAL REQUIRED]` in voice-mode skills
  with the §4.6 error message; (c) reject unknown directive keys; (d)
  ensure `caller_confirm.slot` is declared in the global `slots:` block.
- Acceptance: parser tests pass for extended, legacy, and lint-error cases.

**A3. `LLMRuntime.complete_stream()`**  ·  1 d
- Add async-generator method on `LLMRuntime` that calls litellm with
  `stream=True` and yields a typed delta sequence (`TextDelta`,
  `ToolCallStart`, `ToolCallArgsDelta`, `ToolCallEnd`, `Done`).
- Reuse the existing retry/backoff logic.
- Acceptance: unit test that streams a recorded fixture (replayed via
  litellm's mock mode) and assembles the expected text + tool calls.

**A4. `EventType` additions + `VoiceSession` dataclass**  ·  0.5 d
- Add the new event types listed in §4.2 to `events.EventType`.
- Define `VoiceSession`, `SlotValue`, `StepRecord` in
  `proceda.voice.state`.
- Acceptance: dataclass round-trips through `to_dict`/`from_dict`.

**A5. `SessionStore` (in-mem) + reaper**  ·  1 d
- In-memory store with TTL. Reaper task started by FastAPI lifespan event,
  ticks every `voice.reaper_interval_seconds`.
- Acceptance: integration test that an idle session is finalized after the
  configured timeout, audit artifact written.

**A6. SSE framing helpers**  ·  0.5 d
- `proceda.voice.sse` exposes `frame_text_delta(call_id, turn_idx, content)`,
  `frame_done(call_id, turn_idx)`. Format-stable, tested against canned
  expected bytes.
- Use `sse-starlette` for the response wrapper.
- Acceptance: unit tests for byte-exact framing.

**A7. Idempotency primitives + InFlightTurn helper**  ·  1 d
- `hash_messages(messages) -> str`: canonicalize and SHA-256.
- `InFlightTurn` dataclass with `msg_hash`, `chunks`, condvar `cond`,
  `done` event, `error` field, and async methods `append(chunk)`,
  `finalize(error=None)`, `subscribe() -> AsyncIterator[bytes]` per §4.5.
- `VoiceSession` fields: `turn_lock`, `in_flight: InFlightTurn | None`,
  `last_completed_hash`, `last_response_bytes` per §4.7.
- Acceptance: (a) stable-hash tests across noisy reorderings; (b) a
  concurrency test that fires two identical POSTs ~10 ms apart against
  a runtime instrumented to slow-stream chunks; asserts byte-identical
  SSE output, single state advance, and that the duplicate's
  response **started arriving before the original finished** (this is
  the bit the lock-while-streaming bug would silently break).

**A8. Vapi end-of-call webhook handler**  ·  0.5 d
- `POST /v1/voice/webhooks/end-of-call` route. HMAC-verify body against
  `VOICE_WEBHOOK_SECRET`. Look up session by `message.call.id`. Merge
  `endedReason`, recording URL, ended_at into session. Trigger
  `AuditBuilder.finalize` (idempotent on already-finalized sessions).
- Acceptance: integration test posts a recorded webhook payload, asserts
  audit artifact contains `vapi_end_of_call.endedReason` and
  `finalized_via: webhook`; second identical webhook returns 200 and
  doesn't duplicate work.

### 6.2 Phase B — The per-turn loop (week 2, ~5 days)

**B1. `SlotEngine.extract`**  ·  1.5 d
- Builds the extraction prompt from slot schema + current state + last user
  turn. Calls LLMRuntime non-streaming. Parses structured output.
- Confidence floor: extracted slots with `confidence < 0.5` are not filled
  but reported as `notes`.
- Acceptance: unit tests with recorded LLM responses for ANSWER, CORRECTION,
  REFUSAL, OUT_OF_SCOPE, RESTART, UNKNOWN cases.

**B2. State mutation logic**  ·  1 d
- Apply extraction to `VoiceSession`: fill slots, append corrections,
  recompute step eligibility and focus.
- Pure-function-able; covered by table-driven tests.
- Acceptance: 12+ table-driven cases covering the canonical scenarios.

**B3. `ResponsePlanner.plan`**  ·  1 d
- Deterministic logic that consumes a `(session, extraction)` pair and
  returns a `ResponsePlan`. Plus a small library of system prompt
  templates.
- Acceptance: golden-file tests for the seven `strategy` cases.

**B4. `VoiceRuntime.handle_turn`**  ·  1.5 d
- Wires extraction → mutation → planning → streaming. Async generator
  yielding SSE bytes. Tool-call interception logic from §4.10.
- Per-turn tool budget enforcement.
- Acceptance: end-to-end test with mocked LLM (recorded fixtures) drives a
  three-turn conversation and produces the expected SSE byte stream.

### 6.3 Phase C — HTTP surface + audit (week 3, ~5 days)

**C1. FastAPI `/v1/chat/completions` handler**  ·  1 d
- Auth, body parsing, session lookup, idempotency check, `StreamingResponse`
  wiring.
- Acceptance: integration test with `httpx.AsyncClient` against a
  TestClient; sends a Vapi-shaped payload, asserts SSE deltas + correct
  state advance.

**C2. `AuditBuilder.finalize`**  ·  1.5 d
- Replays event log + final state into `audit.json`. Computes
  `deviations_from_sop`. Renders `audit.md`.
- Acceptance: golden-file tests for the FNOL scenario; plus a deliberately
  broken event log that produces the expected non-empty deviations.

**C3. End-of-call wiring (webhook + reaper + on-demand)**  ·  1 d
- Webhook handler is built in A8; this task wires its output into
  `AuditBuilder` plus the chronological `index.jsonl`. Reaper sweeps for
  inactive sessions and tags `finalized_via: reaper`. Explicit
  `GET /v1/voice/audit/<call_id>` finalizes on demand and tags
  accordingly.
- Acceptance: integration tests for all three paths; assert that
  `finalized_via` is recorded in `audit.json` correctly for each.

**C4. CLI: `proceda voice replay <call_id>`**  ·  0.5 d
- Reads `.proceda/voice-runs/<id>/` and renders `audit.md` to terminal,
  with optional `--json` flag.
- Acceptance: snapshot test for the FNOL run.

**C5. CLI: `proceda voice test <sop_id>`**  ·  1 d
- Local-loop test harness: simulate a sequence of user turns from a YAML
  scenario file, drive the runtime end-to-end without the HTTP layer, and
  print the audit artifact. This is the developer's REPL for SOP authoring.
- Acceptance: a YAML scenario for the FNOL happy path produces the same
  audit artifact as the HTTP integration test.

**C6. Latency instrumentation**  ·  0.5 d
- Each turn records timings for stages 1–8. Surface in events as
  `TURN_COMPLETED` payload. Add a `--latency-report` flag to `voice replay`.
- Acceptance: latency numbers visible per-turn and aggregated.

### 6.4 Phase D — Live demo (week 4, ~5 days)

**D1. FNOL demo SOP**  ·  1 d
- Author `examples/voice-fnol/SKILL.md` with slots and steps.
- Mock CRM and FNOL-filing MCP servers (existing `examples/` pattern).
- Acceptance: `proceda voice test fnol --scenario happy-path` succeeds.

**D2. Vapi assistant configuration**  ·  1 d
- Provision a Vapi Assistant with our endpoint as Custom LLM
  (`model.provider = "custom-llm"`, `model.url` = our deployed
  `/v1/chat/completions`).
- **Add `transferCall` and `endCall` to the assistant's `tools` array**
  per [docs.vapi.ai/tools/default-tools](https://docs.vapi.ai/tools/default-tools).
  Configure `transferCall.destinations` to include the
  `voice.escalation_destination` from `proceda.yaml`.
- Configure the Vapi `serverUrl` to our
  `/v1/voice/webhooks/end-of-call` route, with the same shared secret
  as `VOICE_WEBHOOK_SECRET` for HMAC.
- Document everything in `examples/voice-fnol/vapi-assistant.json`
  including the tools array, server URL, TTS voice, first-message, and
  transcriber.
- **Validate the streamed control-tool shape** against the live Vapi
  sandbox: emit a synthetic `transferCall` and confirm Vapi actually
  initiates the transfer; same for `endCall`. If the shape is wrong,
  this is the first test that catches it.

**D3. Latency tuning pass**  ·  1.5 d
- Profile a real call. Identify hottest stage. Likely candidates: extractor
  prompt size, message history trimming, JSON parsing overhead.
- Aim for p50 ≤ 350 ms server-side. Document measured numbers in the
  release notes.

**D4. Real-call dry runs + bug bash**  ·  1 d
- Place ≥ 20 calls covering: happy path, mid-call correction, refusal, OOS,
  restart attempt, hang-up mid-step. Triage and fix the worst three issues.

**D5. Demo recording + handoff doc**  ·  1 d
- Record a Loom of a real call. Capture the audit artifact alongside.
- Ship `docs/voice-mvp-getting-started.md` for design partners.

### 6.5 Dependencies

```
A1 ┐
A2 ┘─► B1 ── B2 ── B3 ── B4 ── C1 ── C2 ── C3 ── C4 ── C5
A3 ─────────► B4 ──┘                        ▲
A4 ┐                                        │
A5 ┘─► C1 ────────────────────────────────► C2
A6 ─► B4
A7 ─► C1

C1 ── D1 ── D2 ── D3 ── D4 ── D5
```

A1–A7 are mostly parallelizable across an engineer-day if more bandwidth is
available. Phase B is strictly sequential. C2 (audit) is the critical-path
component for the wedge; do not skimp on it under time pressure.

### 6.6 Effort summary

After two review-pass revisions, current estimates:

| Phase | Days | Detail |
|---|---|---|
| A — plumbing | 6.5 | A1 0.5 + A2 1.5 + A3 1 + A4 0.5 + A5 1 + A6 0.5 + A7 1 + A8 0.5 |
| B — per-turn loop | 5 | B1 1.5 + B2 1 + B3 1 + B4 1.5 |
| C — HTTP + audit | 5.5 | C1 1 + C2 1.5 + C3 1 + C4 0.5 + C5 1 + C6 0.5 |
| D — live demo | 5.5 | D1 1 + D2 1 + D3 1.5 + D4 1 + D5 1 |
| **Total** | **22.5** | |

22.5 working days = ~4.5 calendar weeks single-engineer with no slack.
Realistic expectation including review and small unknowns: **6.5 weeks**.
If the team is one engineer, plan 7.

---

## 7. Testing & Validation Strategy

The voice MVP fails in three distinct ways: it can be **wrong** (skips an
SOP step, mis-fills a slot), **slow** (busts the latency budget), or
**dishonest** (the audit artifact says it followed the SOP when it didn't).
Each failure mode gets a dedicated layer of testing.

### 7.1 Unit tests

Layered exactly per §4.

- `tests/test_voice/test_state.py` — `VoiceSession`,
  `SlotValue.history`, idempotent dataclass round-trips, focus
  recomputation. **>90% line coverage** for `state.py` is the bar; the
  state machine is the spinal cord.
- `tests/test_voice/test_slot.py` — `SlotEngine.extract` against
  recorded LLM fixtures. One fixture per intent class; one per
  multi-slot-in-one-turn case; one per low-confidence case. Use
  pytest's `parametrize` with a fixture matrix.
- `tests/test_voice/test_plan.py` — golden tests for each `strategy`
  case. Pure-function plumbing; trivial to keep at >95%.
- `tests/test_voice/test_runtime.py` — `VoiceRuntime.handle_turn`
  with mocked extractor and recorded responder streams. Drives a
  three-turn FNOL scenario and asserts byte-exact SSE output. The byte
  exactness matters: SSE framing bugs are silent killers.
- `tests/test_voice/test_audit.py` — `AuditBuilder.finalize` with
  hand-crafted event logs. Includes deliberately broken logs that
  produce non-empty `deviations_from_sop`.
- `tests/test_voice/test_sse.py` — framing helpers byte-exact.
- `tests/test_voice/test_state_idempotency.py` — replay with same
  message hash twice; assert one state advance, identical bytes.

The existing `CollectorEventSink` is the workhorse for asserting on emitted
events. Extend `ScriptedHumanInterface`-style with a `ScriptedSlotEngine`
that returns canned extractions for deterministic runtime tests.

### 7.2 Integration tests (mocked LLM)

`tests/test_voice/test_e2e.py`, marked `@pytest.mark.integration`. Boots
the FastAPI app under a `TestClient`, points the runtime at a
`ReplayingLLMRuntime` (returns recorded streams), drives the full
contract:

- **E2E-01 happy path.** Five turns, all required slots filled, file_fnol
  tool succeeds, audit shows zero deviations.
- **E2E-02 ahead-of-script.** First user turn fills four slots; assert
  side-effect step completions in audit.
- **E2E-03 mid-call correction.** Turn 2 corrects a slot; assert old value
  in `slot.history`, audit shows correction record.
- **E2E-04 idempotent retry.** Two identical POSTs; assert one state
  advance, byte-identical SSE.
- **E2E-05 refusal escalation.** User refuses required slot twice; assert
  escalation event and `outcome: escalated`.
- **E2E-06 out-of-scope cap.** Three consecutive OOS turns; assert
  end-call with summary.
- **E2E-07 tool call mid-stream.** Step 2's `crm__lookup_policy` returns,
  response continues, audit logs the call.
- **E2E-08 inactivity finalization.** Open session, reaper fires, audit
  artifact appears on disk.

Each E2E test owns one fixture directory under
`tests/fixtures/voice/<scenario>/` containing the recorded LLM streams.
Re-record only when the prompt or model changes.

### 7.3 Latency tests

`tests/test_voice/test_latency.py`, marked `@pytest.mark.bench`. **Not** in
the default test run (would slow CI). Run nightly + before release.

Two latency targets per turn — first-byte and full-response — are tracked
independently because Vapi's TTS engine starts speaking on the first SSE
delta. Earlier drafts conflated them.

- **L-01a first-byte (pre-extraction filler turn).** Driven 100 turns
  where the filler-policy (§4.5b) fires. Measured: time from request
  received to first SSE delta on the wire. The filler is a static
  config-driven string framed without any LLM call, so timing is
  dominated by request parse + framing. Assert
  **p50 ≤ 100 ms, p95 ≤ 200 ms server-side.** This is the only test
  asserting sub-150 ms first-byte; the filler-eligibility precondition
  is part of the test setup.
- **L-01b first-byte (no-acknowledgment turn).** Same setup but planner
  emits no prefix; first byte must come from the responder. Measured the
  same way. Assert **p50 ≤ 500 ms, p95 ≤ 900 ms server-side** (extractor
  150–250 ms + responder 100–200 ms first-token + plumbing).
- **L-01c full response.** Time from request received to `[DONE]` on the
  wire. Assert **p50 ≤ 1200 ms, p95 ≤ 2500 ms server-side** for typical
  responses (~30 tokens at ~50 tok/s after first token).
- **L-02 tool-call turn p50/p95.** Same as L-01a/c but with one tool call.
  First-byte budget unchanged (acknowledgment prefix masks the tool
  latency); full-response budget: p50 ≤ 1800 ms, p95 ≤ 3500 ms.
- **L-03 burst.** 20 concurrent calls; assert no latency cliff at any
  percentile.

Run against a realistic mock; not against real LLM (slow, flaky, costly,
and the LLM's latency is not what we own anyway).

### 7.4 The "live LLM" smoke test

`tests/test_voice/test_live_smoke.py`, marked
`@pytest.mark.integration_live` (opt-in via env var). Hits the real
extractor and responder once per CI run on `main`. Single canonical FNOL
happy path; asserts only that the run completes and the audit shows
`required_slots_satisfied: true`. Not a regression suite — a guard against
prompt drift breaking everything silently.

### 7.5 Audit-artifact validation

**This is the most important test category** because the artifact is the
product.

- **AV-01 schema conformance.** `audit.json` validates against a JSON
  Schema (checked into `src/proceda/voice/audit_schema.json`). Run on
  every E2E test's output.
- **AV-02 deterministic from event log.** Given the event log alone,
  `AuditBuilder.from_events_only(events)` produces an artifact that
  matches the live finalization byte-for-byte (modulo timestamps).
- **AV-03 deviation detection.** Hand-craft event logs with known
  deviations (slot filled out of step order; tool called in wrong
  step; required slot left empty). Assert `deviations_from_sop` lists
  them exactly.
- **AV-04 corrections preserved.** Replayed audit shows the slot's
  history including pre-correction value.
- **AV-05 PII handling: no transport leak.** The audit directory is
  local-disk-only — no cloud upload, no external HTTP transport, no
  cross-tenant leakage. AV-05 asserts that no code path in
  `src/proceda/voice/` sends file contents over the network. Existing
  `redact_secrets` matches *key names* (`api_key`, `token`, etc.) — it
  does **not** redact PII *values* in transcripts (SSN, account number,
  free-form medical detail). That is N8 and stays out of MVP scope. A
  design partner deploying this gets an explicit "this is a POC; the
  audit directory may contain raw PII" warning in the getting-started
  doc.
- **AV-06 control-tool emission audit.** Every `transferCall` and
  `endCall` emission records a `VAPI_CONTROL_TOOL_EMITTED` event with the
  emitting strategy (`escalate_to_human`, `wrap_up`, `oos_cap_hit`) and
  args. Tested by hand-crafted event logs that assert the audit's
  `escalations[]` and outcome are derived from these events alone.

A compliance-officer-grade artifact is one a regulator can replay. AV-02
is the structural guarantee for that.

### 7.5b The Vapi-side test ladder (this is how we iterate on the integration)

The MVP's primary feedback loop must work **without making real phone
calls.** Each rung exercises more of the stack at higher cost; the engineer
or AI agent climbs the ladder per change. Findings here come from a
research pass on `docs.vapi.ai` (April 2026); the underlying primitives
are version-pinned in §10 q7.

| Rung | What it is | Cost per run | Hits BYOM endpoint? | Catches |
|---|---|---|---|---|
| **0. Local fixture replay** | Captured Vapi `/chat/completions` request bodies replayed against our own server with `httpx.AsyncClient` | free, offline | yes, 100% | per-turn logic, SSE framing, idempotency, audit assembly |
| **1. Vapi Chat API** (`POST https://api.vapi.ai/chat`) — **exploratory** | Text-only multi-turn against a real Vapi assistant | small | **only if** assistant's `model.provider = "custom-llm"` points at us, AND our parser accepts whatever ID Chat API actually sends (see gotcha 6) | request-shape drift, auth, network |
| **2. Simulations — `vapi.webchat`** | Scenario-driven multi-turn with tool mocks + LLM-as-judge rubric | small | yes | end-to-end conformance, regressions in conversational behavior |
| **3. Simulations — `vapi.websocket`** | Full STT/TTS pipeline | **real telephony minutes** | yes | barge-in, endpointing, ASR errors, audio artifacts |
| **4. Real phone call** | Manual smoke before launch | real minutes | yes | everything; the only ground truth |

Rung 0 is the day-to-day loop and is already specified in §7.2. Rung 2 is
what runs in CI on PRs. Rungs 3 and 4 are pre-release only.

#### Five gotchas to bake into the implementation

1. **`Test Suites` is deprecated.** Vapi's docs explicitly redirect to
   `Simulations` (Alpha). Do not write any code against the old test-suites
   API. Wrap Simulations in a thin adapter (`proceda.voice.testkit`) so a
   breaking change is one edit. Tracked as a §11 risk row.
2. **Chat API ≠ BYOM unless wired up.** It is easy to call `POST /chat`,
   get a plausible response, and conclude the integration works — when in
   fact Vapi used a stock OpenAI model server-side. Our test harness must
   verify the assistant's `model.provider` field via `GET /assistant/<id>`
   before asserting on behavior. Add `assert_uses_byom(assistant_id)`
   helper to `proceda.voice.testkit`.
3. **Web SDK is browser-only.** No documented headless Node mode. Anyone
   suggesting otherwise is conflating it with Vapi's server SDKs (which
   place phone calls). Driving a "web call" from Puppeteer with
   `--use-file-for-fake-audio-capture` is a community hack, undocumented
   and brittle. Cut from the test plan; if rung 2 isn't enough, jump
   straight to rung 3.
4. **No mock or free test phone numbers.** Voice tests cost real money and
   are capped at ~15 minutes per run. Budget for it; don't expect rung 3+4
   to scale.
5. **Per-turn latency is not in the BYOM payload nor in
   `end-of-call-report`.** Server-side instrumentation is the only option.
   The §6.4 / Phase C6 instrumentation plan is correct; flagging here
   because earlier drafts assumed Vapi exposed timings (it does not).
6. **Chat API may not provide `call.id`.** Vapi's `POST /chat` is
   chat/session-keyed (likely `chatId`/`sessionId`), not call-keyed like
   the BYOM phone path. Our parser currently *requires* `call.id`
   (§4.4). Until we verify against a live sandbox what the Chat API
   actually sends, **rung 1 is exploratory only** — not a reliable CI
   test surface. Either (a) extend the parser to accept
   `chat.id`/`sessionId` as a fallback session key, or (b) restrict
   rung-1 testing to specific fixtures with a synthetic `call.id`.
   Decision deferred until week-1 verification (see §10 q1).

#### Recommended fixture-record command

`proceda voice fixture record <call_id>` captures the next inbound POST to
disk under `tests/fixtures/voice/<scenario>/`. This is the cheapest way to
seed rung 0 with realistic Vapi-shaped payloads. Decision tracked in §10
q7.

### 7.6 Real-call dry-run protocol (Phase D4)

Not automated. Runs once before demo. Checklist:

1. Place 20 real calls covering: happy path × 6, correction × 4,
   refusal × 2, OOS × 4, restart-attempt × 2, hang-up mid-step × 2.
2. For each call, verify (a) audio quality is reasonable, (b) audit
   artifact exists, (c) the artifact is consistent with what happened.
3. Triage failures by frequency × severity. Fix the worst three.
4. Ship demo only when ≥18/20 calls succeed end-to-end and 0/20 produce
   a wrong-but-internally-consistent audit (an audit that says success
   when it shouldn't).

### 7.7 Eval harness for regressions (foundation, not full system)

A minimal eval harness lives under `tests/eval/voice/`. Each scenario is
a YAML file with: SOP id, sequence of user turns, expected slot-fill
trajectory, expected outcome. The harness uses `proceda voice test` to
drive the scenario against the live extractor + a stub responder, then
diffs the resulting audit against the expected one.

We **don't** ship the full Hamming/Cekura-style simulation suite for
MVP — that's a v2 product, not a test infrastructure. But the
scenario-file format we choose now becomes the eval suite later, so it
gets a small spec doc (`docs/voice-eval-scenario-format.md`) at MVP
time.

### 7.8 Pre-commit + CI

- Pre-commit unchanged: ruff format, ruff check, ty, pytest.
- CI runs default pytest + integration tests on push to PR; bench/live
  tests run nightly on `main`.
- Coverage gate: `proceda.voice` package ≥ 85% line, ≥ 75% branch.

### 7.9 Validation summary

| Failure mode | Test layer that catches it |
|---|---|
| Wrong slot extraction | unit (test_slot) + E2E |
| Wrong state mutation | unit (test_state) + E2E |
| Wrong response strategy | unit (test_plan) |
| Bytes-on-wire bugs | unit (test_sse) + test_runtime |
| Idempotency bugs (incl. concurrent retry race) | E2E-04 + test_state_idempotency + A7 concurrency test |
| Latency regressions (first-byte and full) | bench (L-01a/b/c, L-02, L-03) |
| LLM prompt drift | live smoke |
| Dishonest audit | AV-01..06 |
| Webhook missing or malformed | A8 + C3 integration |
| Approval-marker confusion in voice mode | proceda lint (caught at parse time) |
| Real-world surprises | Phase D4 dry-run |

Every named failure mode has a named test owner. None of them is
"manually verified".

---

## 8. Rollout & success criteria

The MVP ships when, simultaneously:

1. `proceda voice serve` boots cleanly from a fresh clone given a
   `proceda.yaml` and `VOICE_BYOM_TOKEN`.
2. The FNOL demo SOP completes ≥ 18/20 real calls during the dry-run
   protocol (§7.6).
3. p50 turn latency ≤ 350 ms on the demo machine; p95 ≤ 700 ms (L-01).
4. The audit artifact for every successful call contains
   `required_slots_satisfied: true` and `deviations_from_sop: []`.
5. The audit artifact for every escalation contains exactly one
   `escalations[]` entry with the expected reason.
6. AV-01..05 all pass; AV-02 in particular passes for every call in
   the dry-run.
7. The Loom demo plays end-to-end without cuts.

If any one of those misses, we don't call it shipped. We name what missed
and either fix it or scope it out explicitly.

---

## 9. Future work (deferred, with explicit reasons)

- **Tangent registry.** Needs careful authoring UX; right shape only
  becomes clear after design partners give us 5–10 real SOPs. Don't
  guess.
- **Compensation actions.** Coupled to tangent registry — corrections
  after side effects need declared compensations. Punt to v2.
- **Refusal policies (per-slot).** Single global policy in MVP is
  sufficient; per-slot needs author input.
- **Doc → SOP graph generator.** The GTM-defining feature for the
  product, not the infra. Build after the runtime is hardened.
- **LiveKit adapter.** ~1 week effort once the chat-completions URL is
  stable. Plan for week 6+.
- **Pipecat adapter.** Same.
- **`sop-event` extension SSE channel.** Only useful for first-class
  embedders (LiveKit, Pipecat, our own SDK). Ship with LiveKit.
- **Redis state store.** Trivial to add when needed; not before.
- **Speculative parallel extraction.** The cleanest latency win; gates on
  the eval harness to verify the "common case" assumption.
- **Multi-tenant.** Revisit when the second design partner shows up.
- **Encryption-at-rest, FedRAMP, HIPAA BAA.** When a buyer demands them.
  Not before.
- **Web UI for live monitoring.** When the second design partner asks for
  it, not before. CLI replay covers the developer audience.

---

## 10. Open questions (need answers before / during build)

1. **Does Vapi pass `metadata` to BYOM, and what shape do Chat API
   payloads take?** — Two related sub-questions, verifiable in one
   week-1 sandbox session:
   - (a) Does `metadata.sop_id` arrive at our endpoint when set on the
     Vapi Assistant? Our default routing (§4.4) uses `assistantId →
     sop_id` and treats `metadata` as best-effort; if `metadata`
     reliably passes through, we can document it as the preferred
     authoring path. If it gets stripped, the override path is
     deleted from the design.
   - (b) When `POST /chat` (the rung-1 test surface) hits our endpoint,
     does the body include `call.id`, or only `chatId`/`sessionId`?
     Determines whether rung 1 is a usable CI surface or merely
     exploratory (see §7.5b gotcha 6).
2. **Confidence-threshold tuning.** — The 0.5 floor in §4.8 is a
   guess. Tune via the Phase B1 eval cases before the latency pass.
3. **Tool-call latency policy.** — One inline tool call per turn is
   safe; two is borderline. Should we declare a per-step
   `inline: true|false` knob now or wait? **Recommend wait**: ship
   with all tools inline, profile, decide.
4. **Should the responder model be told the slot schema?** — Currently
   the planner builds a step-scoped prompt that doesn't include the
   global slot schema. Including it costs tokens; excluding it might
   make the responder confused about what's already known. Empirical
   question for week 3.
5. **What does a successful FNOL look like to a real claims adjuster?**
   — Answer this before the demo. Otherwise the audit artifact is
   designed for an audience that may not exist. Find one design-partner
   adjuster *before* finalizing the artifact schema.
6. **Should we ship `proceda voice fixture record <call_id>`?** —
   Captures the next inbound Vapi BYOM POST to disk for replay-based
   testing (rung 0 of the §7.5b ladder). Argument for: only cheap way
   to seed fixtures with payloads that match real Vapi-version-of-the-day.
   Argument against: one extra CLI command and a small bit of state
   machinery. **Recommend ship it in Phase C5** — pays back within the
   Phase D4 dry-run, because we'll record canonical payloads from those
   calls for permanent regression coverage.
7. **What's the correct webhook delivery semantics?** — Vapi's
   `end-of-call-report` retries on non-2xx, but does it retry once or
   indefinitely? What's the timeout? The handler is idempotent
   regardless, but knowing the retry policy informs how aggressive our
   "respond immediately, finalize in background" optimization can be.
   Verify alongside q1 in week 1.

---

## 11. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Latency budget blown | medium | high | Bench layer (L-01..03) on CI nightly; D3 tuning pass; haiku for extractor |
| Audit artifact perceived as fluff | medium | high | Get a real adjuster in §10 q6 before locking the schema |
| Vapi BYOM contract drift | low | medium | Pin to current docs; use a `vapi_request_schema_version` audit field; loud parse-error path |
| LLM prompt regression | medium | medium | Live smoke test §7.4; eval scenarios §7.7 |
| In-mem store loses state on crash | low (single replica) | medium | Reaper finalizes on restart; inactivity timeout reasonable; log warning at startup if .proceda/voice-runs has unfinalized sessions |
| SOP author confusion (slot vs step) | medium | medium | `proceda lint` catches mismatches; `voice test` is the REPL; `docs/voice-mvp-getting-started.md` has a worked example |
| Tool exec blocks first audio | high (by design) | medium | Acknowledgment-prefix in plan §4.10; per-turn tool budget |
| Vapi changes BYOM auth | low | high | Centralize auth in one function; design-partner alert path |
| Simulations API churn (Alpha) | medium | medium | Wrap in `proceda.voice.testkit` adapter; pin Vapi SDK version; rung-2 tests are nice-to-have, not load-bearing |
| Test Suites code mistakenly written | low | medium | Linter rule / code-review check: any reference to `/test/test-suites` is a bug |
| Latency instrumentation gap (Vapi exposes none) | medium | high | Server-side per-turn timing is in scope (§6.4 C6); do not depend on Vapi telemetry |
| Webhook delivery failures leave audit incomplete | medium | high | Reaper fallback finalizes from state alone; `finalized_via` flag tells reviewers which path ran; alert on `finalized_via != webhook` rate |
| Idempotency lock contention | low | medium | Per-session lock is per-call; no cross-call contention; bench in L-03 |
| Approval-marker confusion (legacy `[APPROVAL REQUIRED]`) | medium | medium | Lint error blocks publish (§4.6); `proceda lint` runs in pre-commit and CI |
| Acknowledgment-prefix latency optimization breaks | low | high | If planner can't emit a prefix, fall through to plain responder path; L-01b budget covers this case |
| Vapi control-tool exception list expands beyond 2 | low | medium | Whitelist enforcement in code; PR review check for new entries; document why each exists |

The two highest-impact rows are **latency** and **audit credibility**.
Both have named owners (D3, §10 q6) and named test layers (§7.3, §7.5).

---

## 12. Out-of-scope but worth recording

- The research note's `sop-event` extension stream is the right
  long-term wire format for first-class embedders. The MVP doesn't
  emit it, but everything we need to emit it (events, state) is
  already in place. Adding it is a ~1-day patch when LiveKit work
  begins.
- The `VoiceSession` shape is intentionally a strict superset of what
  we'd put in Redis later; no migration debt.
- The audit-artifact JSON schema is **versioned** from day one
  (`schema_version: "0.1.0"`). When (not if) it changes, downstream
  tools can branch on the version field.

---

## 13. Glossary check

If any of the following terms surprise a reader, we have a writing bug,
not a domain bug:

- **BYOM**: Bring Your Own Model — Vapi's term for the Custom LLM hook.
- **SSE**: Server-Sent Events — the HTTP streaming protocol Vapi expects.
- **SOP**: Standard Operating Procedure — the input artifact Proceda
  consumes.
- **FNOL**: First Notice of Loss — the demo SOP, an insurance claim
  intake.
- **Focus**: the step the agent is currently driving toward; derived,
  not stored as a primary key.
- **Audit artifact**: the per-call structured proof-of-conformance
  package. The deliverable.

---

*End of design doc. Review notes welcome inline; substantive disagreements
should result in either a §5 entry being added/edited or a non-goal being
moved into goals.*

---

## Appendix A — Changelog

### v0.4 (2026-04-29) — Second review pass

Eight findings from the v0.3-review-of-the-review folded in. Mostly
concurrency details and platform-contract gaps.

**High-severity fixes:**

- §4.5: idempotency lock no longer wraps the streaming generator. New
  `InFlightTurn` helper (own condvar) lets the producer release the
  session lock before yielding any bytes; concurrent duplicates can
  enter the "tail" branch and start receiving bytes before the original
  finishes. The A7 acceptance test now actually means something.
- §4.5 + §4.5b + §7.3 L-01a: pre-extraction filler concept added.
  Original L-01a budget (≤100 ms first byte after extraction) was
  unphysical. Now the filler is a config-driven static string emitted
  before extraction begins; L-01a tests only fire when the filler
  policy fires. Filler-policy is a UX-vs-latency knob with explicit
  rules (§4.5b).
- §4.10b + §6.4 D2: Vapi control-tool exception no longer hand-wavy.
  `transferCall` and `endCall` must be added to the assistant's `tools`
  array via Vapi config; transfer destinations live there too.
  D2 grew from 0.5 d to 1 d to include this configuration plus a
  live-sandbox validation of the streamed tool-call shape.
- §4.4 webhook handler: webhook finalization now waits for any
  in-flight turn to drain before calling `AuditBuilder.finalize`.
  Bounded by `voice.webhook_drain_timeout_ms`; on timeout, audit is
  tagged `finalized_via: webhook_with_drain_timeout` so partial state
  is honestly disclosed.

**Medium-severity fixes:**

- §4.6 FNOL example: `file_claim_confirmed` slot added to the global
  `slots:` block so the example doesn't fail its own A2 lint rule.
- §5.2: step state machine split into "prerequisites satisfied"
  (eligible) and "completion criteria met" (completed). Earlier wording
  conflated them; step 1 with no prior steps is now correctly always
  eligible regardless of `fills:` state.
- §4.7: `VoiceSession` dataclass aligned with the §4.5 idempotency
  design — now has `turn_lock`, `in_flight`, `last_completed_hash`,
  `last_response_bytes`. Removed the stale `last_message_hash` field.
  Added `finalized_via`, `vapi_end_of_call`, `last_filler_turn_idx`.
- §6.6: schedule summary updated. Total: 20 d → 22.5 d nominal,
  6.5 weeks → 7 weeks realistic.

### v0.3 (2026-04-29) — Internal review pass

Eleven findings from internal review folded in:

**High-severity fixes:**

- §4.6 + §5.7c: removed the "log `pre_approval_skipped: voice_mode` and
  proceed" anti-pattern. Voice-mode SOPs now reject `[APPROVAL REQUIRED]`
  at lint time; approval is modeled as a `caller_confirm:` slot
  (real, audit-able) or as `escalate_to_human` via `transferCall`.
- §2.2 N9 + §4.4 + §5.7b: Vapi `end-of-call-report` webhook moved into
  MVP scope (Phase A8 + C3). Reaper demoted to fallback. `finalized_via`
  is a load-bearing audit field.
- §4.5: idempotency now uses per-session lock + `last_completed_hash` +
  cached response bytes. Prevents state-corruption from concurrent
  retries during streaming. (v0.4 superseded the initial flat-state
  attempt with a proper `InFlightTurn` helper — see v0.4 entry above.)
- §4.10b: new subsection. Two and only two Vapi control-tool emissions
  (`transferCall`, `endCall`) are allowed exceptions to "Vapi sees no
  tools." Without them, transfer/hang-up are impossible.
- §2.2 N8 + §7.5 AV-05: PII-redaction language tightened. The existing
  `redact_secrets` matches key names, not transcript values; AV-05 now
  asserts only "no transport leak," and the design partner gets a
  warning that the audit dir contains raw PII.

**Medium-severity fixes:**

- §7.3: latency budget split into first-byte (L-01a/b) and full-response
  (L-01c) tiers. Acknowledgment prefix streamed directly from planner
  for first-byte ≤ 100 ms; full responses up to 1.2 s.
- §4.11: event log declared canonical, final state demoted to derived
  cache. `from_events_only` is the truth function; `finalize` is a
  fast-path with CI-asserted equality.
- §G3 + §4.11: run directory layout pinned to
  `.proceda/voice-runs/<call_id>/`. Chronological browsing via
  `index.jsonl`. New `VoiceRunDirectoryManager` to avoid entangling with
  existing `RunDirectoryManager`.
- §4.4: SOP routing default is now `assistant_sop_map` keyed by
  `assistantId`. `metadata.sop_id` is an opt-in override only, never
  load-bearing.
- §7.5b gotcha 6: rung 1 (Vapi Chat API) marked exploratory until
  `chatId`/`sessionId` vs `call.id` is resolved. Parser-extension or
  fixture-restriction decision deferred to week-1 verification.
- §4.6: per-step directive grammar formalized as fenced YAML block;
  unknown keys are lint errors.
