---
date: 2026-04-28
tags: [research, proceda, voice-agents, vapi, livekit, parloa, integration]
---

# Voice Agent Infrastructure & Proceda Integration Research

**Date:** 2026-04-28
**Author:** Vivek Haldar
**Status:** Research synthesis from deep-dive session with Claude

## TL;DR

- **The wedge is real and unoccupied.** Nobody is doing "upload your SOP document → working voice agent → with audit-trail-by-construction." Voiceflow, Parloa, PolyAI, and the others all start a layer up ("design a conversation in our studio").
- **Architecture choice:** Build Proceda as an **OpenAI-compatible streaming chat-completions endpoint** with a sideband `sop-event` extension stream. This is a one-time investment that lets you address every serious voice infra platform with thin adapters.
- **Phasing:** **Vapi v1** (only closed-shop voice platform with first-class Custom LLM hook → ships in days), **LiveKit v2** (where the actual enterprise traffic lives — OpenAI, Spotify, Meta, Microsoft, Tesla, Salesforce Agentforce), **Pipecat as developer-channel beachhead** (NVIDIA-distributed OSS standard with a thesis identical to Proceda's). Skip Retell and Bland for v1; watch Retell because they have the best ARR/call-volume traction in the category.
- **Real long-term competitor is Parloa** ($3B valuation, $50M+ ARR, 150% NRR, BPO partner channel including TP/ibex/Sopra Steria) — but Parloa is enterprise-direct/top-down, multi-channel, with generic compliance. Proceda's wedge is SOP-document-in, ops-team self-serve, voice-first, vertical compliance artifacts.

---

## Part 1 — Vapi Deep Dive

### Architecture overview

Vapi is fundamentally an **orchestration layer**, not an agent framework. It owns the real-time voice loop and treats "what the agent should do" as a pluggable concern. The hot loop:

```
Caller speech → STT (Deepgram default) → LLM → TTS → Audio back to caller
```

- Whole round-trip target: **<800ms voice-to-voice** (often <500ms).
- All three modules (transcriber / model / voice) are swappable. STT: Deepgram, AssemblyAI, Whisper. LLM: OpenAI, Anthropic, Google Gemini, or **your own server via Custom LLM (BYOM)**. TTS: ElevenLabs, Deepgram Aura, OpenAI TTS, PlayHT.
- Built-in interruption handling, turn-taking, telephony.

This is the architectural seam where Proceda would slot in.

### The four process-following primitives

Ranked from "more flexibility" to "more determinism":

1. **Assistants** — single system prompt + tools + structured outputs. Vapi's recommended default. Works for short flows (<5 sequential steps), drifts off-script under conversational pressure for anything longer. The Hamming AI eval guide calls this the "script dependency trap."
2. **Squads** — multiple assistants that hand off to each other via "Handoff Tools" with explicit trigger conditions. Context preserved across handoffs. Vapi recommends this for medical intake, order management, multi-step service flows.
3. **Workflows** — visual node/edge graph builder. **DEPRECATED for new builds.** Vapi explicitly says: *"We no longer recommend Workflows for new builds. Prefer Assistants or Squads depending on complexity."* Still works, still documented. Nodes: Conversation, API Request, Transfer Call, End Call, Tool, Global. Edges support three condition types — AI-based (`"User wanted to talk about voice agents"`), logical (`{{ city == "San Francisco" }}`), combined (`{{ tier == "VIP" or orders > 50 }}`). Variables extracted via Liquid syntax.
4. **Custom LLM endpoint (BYOM)** — point Vapi at your own OpenAI-compatible `/chat/completions` endpoint with SSE streaming. Vapi POSTs conversation context every turn; your server returns the next utterance + tool calls. **This is the cleanest hook for an external process engine.** Most builders use it to swap base models (e.g., Llama 3); almost nobody uses it to insert a *process engine*.

### Patterns in the wild for process-following on Vapi

1. **Single Assistant + giant system prompt** (the default, ~80% of tutorials). Fragile past a few steps.
2. **Tools-as-state-machine** — encode SOP into the tool graph: `verify_identity` must succeed before `lookup_account` is allowed, gating `process_claim`. System prompt nudges, tool schemas enforce. More reliable but you're hand-rolling a state machine in JSON Schema.
3. **Squads** — split SOP into phases, one assistant per phase, hand off when done.
4. **Custom LLM endpoint** — cleanest external hook, rarely used for orchestration today.

### Why this matters for Proceda

Vapi's deprecation of Workflows is a tailwind: they couldn't make a deterministic flow-builder feel as flexible as agentic LLMs, so they retreated to "use a big prompt." Whoever builds a better SOP-as-graph layer on top of their stable primitives wins this slice. The Custom LLM endpoint is the stable, bet-on-able integration point — Proceda becomes the brain behind a chat-completions URL.

> **Insight.** *Building behind a chat-completions URL is the highest-leverage architectural decision. Instantly usable from any voice platform that supports custom LLMs (Vapi today, others later). Costs almost nothing to maintain. Preserves IP — the SOP graph never leaves your servers.*

---

## Part 2 — Why "Proceda + Vapi" Is a Real Wedge

Three converging signals justify the bet:

### 1. Process drift is the dominant complaint with Vapi voice agents

- Production agents skip steps, drop required information, or call tools with wrong args under conversational pressure.
- The whole voice-eval startup category (Hamming AI, Vapi's own evals product) exists *because* of this.
- Vapi quote: *"small changes in any layer can shift behavior in ways that don't show up until you test with real audio."*

### 2. Compliance demand is real

- Healthcare (HIPAA, FDA 21 CFR Part 11), insurance, finance — each requires *provable* adherence to a defined procedure with full audit trail.
- HACCP compliance case study (Digiqt): voice agents lifted documentation completeness from **76% → 99%** *because* the agent enforced the SOP. That's literally the Proceda pitch in production.

### 3. BPO is your stated GTM target

- Voice is the dominant BPO modality.
- A "process-driven voice" wedge lands you straight inside the **$280B BPO market** Proceda has already identified — not as a side feature, as the primary modality.
- Proceda's existing prospecting list (Accenture, TCS, Cognizant, Concentrix, Teleperformance, Genpact, EXL, WNS, Infosys BPM) is the same buyer set that's adopting voice AI fastest.

---

## Part 3 — Existing Voice + SOP Landscape

Short answer: **not really, not in the wedge being targeted.**

| Platform | What they do | Gap vs. SOP-first voice |
|---|---|---|
| **Vapi / Retell / Bland / Synthflow** | Voice infra + assistant builders | No SOP-document ingestion, no process-adherence guarantees, no built-in audit trail |
| **Voiceflow** | Visual chat/voice builder w/ "Workflows" (deterministic) + "Playbooks" (autonomous) | Closest in concept; you still hand-author the graph; no SOP-doc → graph generation; chat-first heritage |
| **Parloa** | Enterprise contact-center AI, formal four-stage agent dev process | Real competitor in the BPO segment; high-touch, formal, slow — opposite of "dead simple from a doc"; targets large enterprise direct |
| **PolyAI** | Enterprise voice for contact centers, ADK | Same enterprise-direct posture; no SOP-document ingestion |
| **Cresta** | Real-time agent *assist* alongside humans | Different category — augments the human, doesn't replace |
| **OpenAI Agent Builder** | Visual canvas for multi-step agents | Generic; not voice-first; not SOP-doc-driven |

The white space is the intersection of (a) ingestion of an existing SOP document, (b) automatic generation of an executable process graph, (c) voice as first-class output modality, (d) audit trail by construction, (e) self-serve enough that an ops manager not an engineer ships it. **No one in the matrix above hits all five.**

---

## Part 4 — Proposed Proceda + Vapi Architecture

The cleanest architecture:

```
Phone call ──► Vapi (STT/TTS, telephony, turn-taking)
                │
                │ chat/completions over SSE
                ▼
        Your Custom LLM endpoint  ←── this is Proceda
                │
                ├─ SOP graph state (current step, completed steps, extracted vars)
                ├─ LLM reasoning at each step (Claude/GPT, your choice)
                ├─ Tools / external API calls (per the SOP)
                ├─ Audit log (every utterance, decision, tool call, transition)
                └─ Human-in-the-loop escalation hook
```

Vapi sees "an LLM endpoint." Behind the curtain, every turn flows through Proceda's harness: knows which SOP step you're on, whether required info has been collected, what the next legal transitions are, constrains the next LLM call accordingly. Every transition is logged with provenance.

### Primitives map cleanly

| Vapi primitive | Proceda concept |
|---|---|
| Conversation node | Step with utterance + extraction |
| Edge condition | Transition rule |
| API Request node | Tool call |
| Global node | Escalation handler |
| Variable extraction | Structured output per step |

But you're **not regenerating Vapi's flow JSON**. You keep the graph in Proceda and expose only "next utterance" through the OpenAI-compatible endpoint. That keeps IP server-side, lets you change the graph schema without touching Vapi, and gives you portability.

### MVP requirements

1. **Custom LLM adapter service** — OpenAI-compatible `/v1/chat/completions` with SSE streaming, session-keyed by Vapi `call_id`.
2. **Step-state controller** — on each turn: load SOP execution state, build a constrained system prompt for *just this step* + slot-filling tools, run the LLM, parse extractions, advance/branch the graph, persist.
3. **Tool bridge** — SOP-defined tools (HTTP, DB, internal APIs) invokable from within the turn without blowing latency budget.
4. **Voice-friendly SOP authoring** — small additions to Proceda: per-step utterance templates, prosody hints, allowed-deviation tolerance, "always-listen" globals (cancel, hold, transfer).
5. **Live monitoring** — current step, completed steps, time-on-step, one-click "join this call" for human takeover via Vapi's transfer-call.
6. **Compliance pack per call** — JSON + human-readable artifact: SOP version, step path, transcripts, tool calls, timestamps, escalations. Ship this as part of the product, not as an afterthought.

### Honest pushback before building

- **"Dead simple" hides authoring work.** SOPs that work in a Word doc don't work as voice scripts unmodified. Voice needs disambiguation prompts, barge-in handling, repair phrases, turn-level slot-filling. Either Proceda gets smart about *enriching* an SOP with voice-specific scaffolding automatically, or "dead simple" becomes "dead simple after you tweak the generated graph."
- **Vapi-only is a risk.** The voice infra layer is competitive (Retell wins inbound quality, LiveKit/Pipecat are open-source-y). Build the core orchestrator to a clean voice-platform interface; Vapi is your first integration, not your only one. The Custom LLM hook makes this easy because it's a standard chat-completions contract.
- **The deeper moat is data, not the wedge.** The Vapi-adapter slice is small enough that a competent team could clone it in 2-3 weeks. What's hard to clone: (a) compliance-grade audit artifacts that pass vendor security review at a regulated buyer, (b) a library of pre-built SOPs for vertical-specific workflows (healthcare intake, claims FNOL, collections, KYC), (c) eval datasets for "did this call follow the SOP." Plan early for those.
- **Latency math is real.** If your harness adds 300ms+ of decision overhead per turn, Vapi's <800ms loop is broken. Prototype the latency profile before committing.
- **Parloa is your real long-term competitor**, not Vapi. Wedge against them: speed-to-deploy from a doc and self-serve at the ops level. Don't get pulled into their sales motion.

---

## Part 5 — Voice Infrastructure Comparative Analysis

The voice-infra market splits into **two camps** with very different interfaces:

- **Closed orchestrators** (Vapi, Retell, Bland, Synthflow) own the loop and let you extend via tools or — **Vapi alone** — a Custom LLM hook.
- **Open orchestrators** (LiveKit Agents, Pipecat) hand you the loop in code and you build whatever you want.

Proceda needs adapters for both camps; the contract shape is similar.

### Comparative table — process-following primitives

| Platform | Process-following primitive | Status | Custody | Hooks for external orchestrator |
|---|---|---|---|---|
| **Pipecat Flows** | "Predefined paths + dynamic flows," explicit Flow Manager that lives **outside** the context window. JSON-exportable visual editor. | Active, growing. Open source. | You own the runtime (Python). | Native — write code. |
| **Retell Conversation Flow** | Five node types: Conversation, **Subagent**, Function, Logic, End. Per-node LLM choice. | Actively recommended; Retell's flagship for complex flows. | Closed. | Via tools/functions; no external-LLM swap surfaced. |
| **Bland Pathways** | Node + edge graph; conditions gate transitions; agent stays on a node until condition met. | Bland's primary product. | Closed. | Webhooks + tools; no custom-LLM swap surfaced. |
| **LiveKit Agents** | Agent sessions, tasks, task groups, multi-agent handoffs with `chat_ctx` passing full history. | Active OSS, big traction. | You own the runtime (Python/Node). | Native. |
| **Vapi Workflows** | Visual graph (Conversation/API/Transfer/Tool/End/Global nodes, AI/logical/combined edges). | **Deprecated** for new builds. | Closed. | Custom LLM endpoint (BYOM) — only first-class swap among closed shops. |
| **Synthflow** | Visual builder, 50+ integrations, templates. | Active, no-code-first. | Closed. | Tools/webhooks. |

### Two key observations

**(1) Vapi is the outlier in *deprecating* its flow product** while Retell, Bland, and Pipecat are doubling down. Vapi is betting "Assistants + Squads + a really good Custom LLM hook will subsume what Workflows tried to do." That bet creates the wedge for Proceda; the others' bet (richer in-platform flow nodes) makes Proceda harder to insert *if* Retell/Bland keep getting better at it.

**(2) Pipecat's framing is essentially Proceda's thesis, said out loud.** The Daily.co post argues that bigger context windows don't fix structure; you need a Flow Manager *outside* the LLM context that gates which prompts and tools are available at each step. They call the failure mode "context rot." Quote: *"Simply providing access to everything is not the same as providing guidance."* Same argument as Proceda, just with a Python framework instead of an enterprise product. Treat Pipecat Flows as both cousin and teaching tool — its architecture is the cleanest public reference for how a flow engine and a voice runtime hand off control to each other.

> **Insight.** *Of all the closed-shop flow products, Retell's Conversation Flow has the most expressive node taxonomy (note the "Subagent node" — itself a mini-assistant). That's the live target to benchmark against, not the deprecated Vapi Workflows.*

---

## Part 6 — The Common Voice-Infra ↔ SOP-Engine Interface

Looking across all six platforms, the canonical contract is a thin extension on **OpenAI chat completions over SSE** — because:

- It's what Vapi BYOM already requires.
- Every LLM provider speaks it.
- Trivial to wrap inside LiveKit/Pipecat with a few dozen lines of glue code.

### The concrete schema

```
POST  /v1/sop/turn
Headers: Authorization, X-SOP-Session-Id, X-Voice-Platform: vapi|retell|livekit|pipecat|...

Request (OpenAI-compatible + extensions):
{
  "stream": true,
  "messages": [...],                // standard OpenAI conversation history
  "tools": [...],                   // optional: full toolset, the engine picks per step
  "metadata": {
    "call_id":            "...",    // platform's call id
    "sop_id":             "...",    // which SOP is being executed
    "sop_version":        "...",    // pinned SOP version for replay/audit
    "session_state_ref":  "..."     // engine-side state pointer
  }
}

Response: SSE stream with two interleaved event types

  event: openai-delta            // standard chat.completion.chunk
  data: { ...standard delta... }

  event: sop-event               // proceda extensions
  data: {
    "type": "step.entered"       | "step.exited"
          | "variable.extracted" | "tool.required"
          | "tool.result"        | "branch.taken"
          | "escalation.requested" | "audit.entry"
          | "flow.completed",
    "step_id": "...",
    "payload": { ... },
    "ts":       "..."
  }
```

### Why this works as a universal contract

- **Closed orchestrators (Vapi BYOM):** Vapi already calls `/chat/completions` with SSE. Service ignores the `sop-event` channel for that integration (or surfaces relevant ones via Vapi's webhook/control API). Zero schema friction.
- **LiveKit Agents:** Wrap a `proceda.LLMAdapter` class around the same HTTP endpoint. LiveKit's `LLMNode` is already designed to consume an OpenAI-compatible API. The `sop-event` stream surfaces to LiveKit application code for step UI, logs, etc.
- **Pipecat:** Same as LiveKit — write a `processor` that fronts the SOP engine. Pipecat Flows users could even import your engine *as their flow manager*, a natural cross-pollination move.
- **Retell / Bland (closed-no-LLM-hook):** Awkward — flow lives in their dashboard. Integrate via **tools-as-state-carriers** — every node calls a `proceda_advance(step_id, slots)` tool that hits your engine, and your engine returns the next prompt + allowed tools as the tool's response. Works, but you're fighting their UX. De-prioritize until there's pull.

### Two design choices worth being deliberate about

1. **State on the server, not on the wire.** The adapter sends `session_state_ref`, not full SOP execution state. The voice platform doesn't need to know your graph; only your engine does. Keeps the contract small and your graph schema yours to evolve.
2. **`sop-event` is additive, not required.** Base contract is plain OpenAI chat completions, so Vapi works out-of-the-box with no SDK. Events are how *first-class* embedders (LiveKit, Pipecat, your own SDK) get richer integration. Same pattern Anthropic uses with `extra` fields — base compatibility plus opt-in extensions.

> **Insight.** *The `sop-event` channel is your wedge against pure-LLM endpoints. Anyone can stand up a chat-completions URL; nobody else returns "step entered, variable extracted, escalation requested" as machine-readable events alongside the audio. That's what makes a downstream call-center QA tool, a compliance dashboard, or a real-time supervisor view trivial to build — and those are the products that lock you into accounts.*

---

## Part 7 — Parloa Deep Dive

Parloa is the incumbent in the space Proceda is aiming at. Large, well-funded, explicitly chasing the BPO market.

### Funding and trajectory

- **$560M+ total raised in under four years.**
- **Series D in January 2026: $350M at a $3B valuation** — a *triple* from their May 2025 Series C ($120M at $1B), only seven months earlier. That kind of step-up is investors signaling category leadership.
- **>$50M ARR by December 2025, 150% net revenue retention.** Once Parloa gets in, they expand fast inside the account.
- **Built on Microsoft Azure** (Azure AI Services for speech/language). Strength = enterprise procurement / FedRAMP-adjacent posture; constraint = model and infra choices tied to MS.

### Customers and go-to-market

- **Logos:** Allianz, Booking.com, HealthEquity, SAP, Sedgwick, Swiss Life, TeamViewer. Enterprise-heavy, regulated-industry-heavy.
- **Public proof point:** Swiss Life — *96% routing accuracy, 60% faster resolution.*
- **GTM motion:** Direct enterprise sales **plus** a formal BPO partner channel: **TP (Teleperformance), ibex, Sopra Steria** are embedding Parloa into their service contracts. Formalized in September 2025 with a structured Partner Program.
- **Implication:** Parloa is selling *to* the same BPOs Proceda's pitch deck targets as customers. They are already inside that ecosystem.

### Product framing

Positioned as an **"AI Agent Management Platform" (AMP)** with a four-stage workflow:

1. **Design & Integrate** — "Parloa Studio" with prebuilt + custom skills, low-code conversation builder, natural-language briefs that pull from "knowledge, policies, tasks, and integrations."
2. **Test & Iterate** — multi-turn simulation at scale, eval against real customer patterns, versioning.
3. **Deploy & Scale** — multi-channel (voice, chat, messaging), multi-language.
4. **Monitor & Improve** — insights dashboards, data hub, conversation storage, continuous improvement.

The newest concept they're pushing: **Agent Composition** (announced 2026). One *blueprint* agent definition, then "smart environment configuration" via env vars to localize for region, language, channel — instead of cloning agents per market. Pitch: *"Create once, deploy anywhere."* Scaling-out story for enterprises that need 50 variants of one agent across countries.

### Compliance and governance

Generic enterprise table stakes: **GDPR, ISO 27001, SOC 2**, "PII protection," "fine-grained governance." Azure provides "certified security controls" and "quality assurance." Note what's *missing* from public messaging: any specific claim about **provable adherence to a documented SOP or vertical-specific compliance regimes** (HIPAA-grade audit, FDA 21 CFR Part 11, KYC/AML evidence packs). They have certifications; they don't market SOP-conformance as a feature.

### Integration ecosystem

- **CCaaS:** Genesys, Twilio, AWS Connect.
- **CRM:** Salesforce, ServiceNow, Zendesk.
- **BYO STT/TTS/LLM** flexibility — they swap, like Vapi does, but inside their managed platform.

### How Proceda differentiates

| Dimension | Parloa | Proceda's wedge |
|---|---|---|
| **Starting point** | "Design a conversation in our Studio" | "Point at your SOP document; we generate the agent" |
| **Buyer** | Enterprise CX leadership; multi-month procurement | Ops manager, line-of-business, self-serve |
| **Time to first call** | "Weeks, not months" | Should be *hours* — that's the whole pitch |
| **Compliance posture** | Generic enterprise (GDPR/SOC 2/ISO 27001) | Per-call SOP-conformance artifact, vertical-specific (HIPAA, 21 CFR Part 11, FNOL evidence) |
| **Channel** | Multi-channel platform; voice is one of three | Voice-first wedge (with chat as natural extension) |
| **Distribution** | Direct enterprise + BPO partner channel | If selling to BPOs as customers, you compete with Parloa-the-vendor; if selling *through* BPOs, you're entering Parloa's own partner program territory |
| **Architecture** | Closed managed platform | Open chat-completions contract; works with any voice infra |

### Two strategic facts to absorb

1. **You will not beat Parloa head-on at large enterprises in 2026.** They've raised $560M, have the logos, have BPO partnerships, and have a four-stage product story procurement teams understand. Walking into Allianz to displace Parloa is a bad use of runway.
2. **The wedge is *underneath* Parloa.** Mid-market BPOs and shared-services groups not big enough for Parloa's enterprise motion still have hundreds of SOPs per process line. They want self-serve, fast deploy, per-call compliance artifact. Parloa sells top-down with field engineers; you sell bottom-up with a free trial and a Loom video. Genuinely different motion, not a feature war.

### What to watch on Parloa

Concrete signals that would change the strategic picture:

- **An "import your SOP" feature in Parloa Studio.** Obvious feature gap and the most direct overlap. If they ship it, your wedge gets thinner — but also validates the market wants exactly what you're building.
- **A self-serve / PLG tier.** They are very enterprise-direct today. A low-end SKU = explicitly contesting your motion.
- **Vertical-specific compliance products.** "Parloa for Healthcare" or "Parloa for Insurance" with FDA/HIPAA artifacts = direct collision.
- **Open-source SDK or runtime.** Parloa is closed today. Opening even pieces (Vapi's Pipecat-equivalent) changes developer-attention dynamics.

---

## Part 8 — Integration Target Recommendation

> **Insight.** *This isn't a pick-one decision; it's a phasing decision. The technical contract (OpenAI chat completions over SSE) makes it cheap to support multiple voice platforms once Proceda exists behind that URL. The two axes (technical openness, traction) point to different winners. Vapi is the cleanest technical fit. LiveKit is the cleanest market fit. Retell has the best ARR/call-volume traction but no LLM-swap hook.*

### Decision matrix with traction numbers

| Platform | Funding / Valuation | Revenue / Scale | Customer caliber | LLM-swap hook | Integration shape | Effort to ship v1 |
|---|---|---|---|---|---|---|
| **Vapi** | $22–25M total / ~$130M post-money (Bessemer Series A, Dec 2024) | "Millions in revenue in 6 mo" — small-mid | YC, Deepgram, Luma Health, Speaksage | **Yes — first-class BYOM Custom LLM endpoint** | OpenAI-compatible chat completions URL | **Lowest. Days, not weeks.** |
| **LiveKit** | $183M total / **$1B valuation** (Index Series C, Jan 2026) | Not disclosed; powers OpenAI Realtime | **OpenAI, Spotify, Meta, Microsoft, Character.ai, xAI, Tesla, Salesforce Agentforce** | **Native — you write the orchestrator** | Python/Node SDK; `LLMNode` consumes any OpenAI-compat API | Medium. Real engineering. |
| **Retell AI** | $5.1M total seed (no announced A) | **$50M ARR** in 2025 (from $7.2M); 50M+ AI phone calls/month | Healthcare, logistics, real estate, e-commerce — broad mid-market | **No** — closed flow product, no LLM swap surfaced | Tools-as-state-carriers (your engine fronted as a tool) | High. You're fighting the platform. |
| **Pipecat (Daily.co)** | Pipecat is open source; Daily.co privately funded; **NVIDIA AI Enterprise distributes it** | Pipecat Cloud GA after 9 mo beta w/ 1,000+ teams | NVIDIA, AWS, "all foundation AI labs," thousands of startups | **Native — OSS framework, you own everything** | Python processor; Pipecat Flows could be replaced by Proceda | Medium. Cleanest conceptually. |
| **Bland AI** | Series B at $1B (2024), heavy outbound | Strong outbound; less inbound enterprise | Outbound campaigns | No LLM swap | Pathways are closed; tools only | High. Skip. |
| **Synthflow** | Smaller; SMB no-code | SMB scale | Service-business templates | No | No-code-only | Skip — wrong buyer. |

### Callouts the table compresses

- **Retell's $50M ARR with $5.1M total seed funding is wild capital efficiency** — real customers paying real money for *flow-based* voice agents *right now*. Their fastest-growing segment is "enterprise"; their #1 enterprise ask was automated QA — exactly the audit/compliance surface Proceda owns. Simultaneously the perfect proof-of-demand for the wedge and the worst v1 integration target because no BYOM hook.
- **LiveKit's customer list is the ceiling of the market** but somewhat misleading for a downstream sales motion. Spotify, Meta, Tesla aren't *buying* voice agents from third parties; they're *building* voice on LiveKit. So "LiveKit has Spotify" doesn't mean Proceda can sell to Spotify. It means a Proceda-on-LiveKit reference architecture is credible to *anyone* whose engineering team chose LiveKit — a steadily growing pool of regulated mid-market and large enterprises.
- **Pipecat's NVIDIA AI Enterprise distribution is underrated.** When NVIDIA bundles a framework into their enterprise stack, it becomes the de facto reference architecture for every Fortune 500 NVIDIA-aligned shop. If Pipecat Flows becomes the OSS standard for "structure outside the context window," Proceda can either compete with it or *be* it.

### Recommendation: Vapi first, LiveKit second, Pipecat as a developer-relations play

#### Build v1 on Vapi

**Why Vapi specifically, despite LiveKit having vastly more traction:**

1. **Time-to-demo is a strategic weapon right now.** You can stand up Proceda behind Vapi's BYOM endpoint in a week — including realistic latency tuning. Vapi explicitly *designed* the Custom LLM hook to be the integration point you need, with an OpenAI-compatible streaming contract you can target with a single Express/FastAPI server. No competing platform where v1 ships this fast.
2. **Vapi's developer audience is your demand-gen channel.** Hundreds of community builders posting tutorials, agency builders putting up YouTube walk-throughs, YC-network adoption. Vapi's *enterprise* customer caliber is below Retell's mid-market or LiveKit's hyperscalers, but Vapi's *developer mindshare* is huge — and Proceda at MVP is looking for builders to try it on a real SOP.
3. **The Vapi contract is the universal contract.** Whatever you build for Vapi BYOM is 95% of what you need for any future integration. Same endpoint plugs into LiveKit and Pipecat with a thin adapter. Shipping for Vapi first isn't Vapi-specific investment; it's the load-bearing infrastructure for everything else.
4. **Vapi's deprecation of Workflows is a tailwind for you.** They told their own users "use Assistants or Squads instead of Workflows." Those users still want process-following — they just no longer have a great in-platform answer. You become the answer for the slice of Vapi customers building anything past a 5-step flow.

**Honest caveats on Vapi:**
- $22M Series A is real-but-not-huge; voice infra category is consolidating. Non-zero platform risk over a 3-year horizon. Mitigated by (a) building to chat-completions contract not Vapi-proprietary APIs, (b) shipping LiveKit second.
- Vapi is the *least defensible* of the integrations from a competitive POV — anyone who can stand up a chat-completions server can build a BYOM integration. Your moat must be the SOP engine, the audit artifact, and the eval suite — not the Vapi adapter.

#### Build v2 on LiveKit Agents

**Why LiveKit second, not first:**

1. **LiveKit is where regulated enterprise voice traffic lives in 2026.** The customers you eventually want — Allianz, Sedgwick, Swiss Life, healthcare payers, BPOs — are increasingly building on LiveKit because OpenAI does, because Salesforce Agentforce does, and because the open-source-with-enterprise-support posture wins procurement reviews.
2. **LiveKit gives you full custody of the voice loop.** Drive turn-taking from your engine, inject system events mid-turn (real-time supervisor pausing the agent), instrument latency at every stage, ship a self-hosted variant for compliance-sensitive customers who can't have PII traverse a third-party SaaS. Every one of those is a feature an enterprise BPO will eventually demand.
3. **The integration is straightforward once Proceda exists as a chat-completions URL.** LiveKit's `LLMNode` already consumes any OpenAI-compatible endpoint. Work is mostly (a) Python `proceda_llm` adapter (~100–200 lines), (b) surfacing `sop-event` extension stream into LiveKit's session events, (c) packaging as `pip install proceda-livekit`.

**Caveats on LiveKit:**
- Selling to LiveKit users is a *different* motion than selling to Vapi users. LiveKit users are typically engineering teams inside larger companies, not agency builders. Sales motion needs to grow up by the time you ship the LiveKit integration.
- LiveKit's first-party Agents framework is improving fast (multi-agent handoff, semantic turn detection, task groups) — they could ship enough flow primitives in 2026 to absorb part of Proceda's wedge. Counter: Proceda's value isn't "graph nodes," it's "SOP-document-in, audit-trail-out, vertical compliance," which is not on LiveKit's roadmap.

#### Use Pipecat as developer-relations beachhead, not primary platform

- **Conceptually closest thing to Proceda in the OSS world** — Pipecat Flows reached the same conclusion ("structure outside the context window"). Dangerous-as-competitor and useful-as-channel.
- **The play: publish Proceda as a `pipecat-proceda` flow manager replacement** — drop-in compatible with Pipecat Flows but backed by a real SOP engine and audit trail. Puts you in front of Pipecat's developer audience (thousands of teams, NVIDIA AI Enterprise distribution) without trying to displace Pipecat as a framework. Be a recommended way to upgrade from Pipecat Flows when you outgrow it.
- **Hedge against any single closed platform.** Pipecat is open source and vendor-neutral; building credibility there means you can always retreat to "we're a Pipecat-compatible flow engine" if any one closed platform turns hostile.

#### Skip Retell and Bland for v1, but track Retell closely

Skipping Retell is painful — best traction in the category, $50M ARR, 50M calls/month, fastest-growing, just shipped automated QA *because* enterprises asked for it. Their customer base is *literally the people who would buy Proceda*. But:

- **No custom-LLM hook** → integration requires living as a tool inside their flow product, fighting their UX, capping integration quality.
- Their dev-rel and platform direction don't currently signal an LLM-swap is coming — they're betting on "make our flows so good you don't need to swap."

What to do instead: ship Vapi v1, get reference customers, then **lobby Retell for a Custom-LLM hook** with the explicit pitch *"your enterprise customers are asking for SOP-grade audit; you don't want to build that, we already have. Let us be the brain inside your shell."* If they ship the hook, that's your fastest path to the largest installed base. If they don't, you've still validated the wedge with Vapi and LiveKit.

Skip Bland and Synthflow entirely for now — wrong buyer, no integration surface.

---

## Part 9 — Concrete Phasing Plan

> **Insight.** *The whole architecture rests on building Proceda as an OpenAI-compatible streaming chat-completions server with sideband `sop-event` extensions, on day one. Every voice-platform integration after that is an adapter, not a rebuild. Get this right and the second/third platforms cost a small fraction of the first. Watch for the moment Retell ships a custom-LLM hook — that's when the market shape changes. Don't wait; show up to Retell with a working Vapi reference before they ship the hook.*

### Weeks 1–4 — Vapi v1

- Stand up Proceda's chat-completions endpoint.
- Wire it to Vapi BYOM.
- Ship a working demo with one named SOP (e.g., insurance FNOL or healthcare patient intake).
- Hit <500ms first-token latency.
- Get the audit-artifact JSON shape right because you'll never want to migrate it later.

### Weeks 5–8 — Design partners

- Two design partners on Vapi — ideally one in healthcare and one in insurance/BPO.
- Use them to harden voice-specific SOP authoring (utterance templates, barge-in handling, repair phrases).
- Ship the per-call compliance artifact as a real product surface.

### Weeks 9–14 — LiveKit v2

- Build the LiveKit adapter.
- Publish `proceda-livekit` on PyPI.
- Get one design partner who's already on LiveKit.
- Use the LiveKit integration to demonstrate self-hosted / VPC-deployable Proceda for procurement-heavy buyers.

### Weeks 15+ — Pipecat + Retell lobbying

- Pipecat adapter + written "upgrade from Pipecat Flows to Proceda" guide.
- Engage the Pipecat community.
- Start the Retell conversation about a custom-LLM hook with two reference customers in hand.

---

## Part 10 — Technical Design: Control Loop, State Location, and Conversational Robustness

### The two loops, and who owns what

Two loops run simultaneously, and the architecture only works if you're precise about which is which:

**Vapi's loop (the audio/turn loop):**
- Captures audio → STT → accumulates `messages` array → POSTs `/chat/completions` to your server → streams response → TTS → plays audio → listens for next turn.
- State held: audio session, WebRTC/SIP connection, `messages` array, `call_id`. **Transient** — disappears when call ends.
- Vapi has no idea what an SOP is. To Vapi, you're just an LLM.

**Proceda's loop (the process-state loop):**
- Per turn, receives latest `messages` and `call_id`, looks up its session state, decides next action, mutates state, returns next utterance + tool effects.
- State held: SOP execution graph — slots filled, steps completed, eligibility, audit log. **Durable** — Redis (hot path) + Postgres (audit/replay).

**Who's "in charge":** Neither owns "the conversation." But for *"who decides what the agent says next?"* — Proceda, unambiguously. Vapi does voice plumbing; Proceda does the thinking. The fact that Vapi calls Proceda (not the other way around) is just HTTP mechanics.

**Useful frame:** Vapi is to Proceda as a browser is to a web app. Browser owns the rendering loop and dispatches events; app owns the application logic and the database. Same model.

### Where "step 3 of 7" lives

Not in any single place. Concretely:

| What | Where | Why there |
|---|---|---|
| Audio session, transcripts, turn boundaries | Vapi (in-memory + Vapi's storage) | Vapi owns the realtime path |
| `messages` array (raw conversation) | Vapi sends it on every turn; Proceda may store a copy in audit log | Source-of-truth for what was said *to and by* the model |
| **Current SOP execution state** (slots, steps, focus) | **Proceda's session store, keyed by `call_id`** | Survives Vapi retries and process restarts; is the artifact you audit against |
| Audit log (every transition, decision, tool call) | Proceda's durable store (Postgres) | Compliance artifact; outlives the call |
| Voice-tunings (TTS voice, ASR provider, latency tweaks) | Vapi assistant config | Vapi owns the voice modality |

### The session-state record (concrete shape)

```json
{
  "call_id": "vapi_call_abc123",
  "sop_id": "insurance_fnol",
  "sop_version": "3.0.1",
  "started_at": "2026-04-28T18:00:00Z",
  "last_turn_idx": 7,
  "last_message_hash": "sha256:...",   // idempotency on Vapi retries

  "slots": {
    "customer_name":     { "value": "Jane Smith",   "filled_at": "...", "source_turn": 2, "confidence": 0.96 },
    "policy_number":     { "value": "124-456",      "filled_at": "...", "source_turn": 4,
                            "corrected_from": "123-456", "corrected_at_turn": 6 },
    "incident_date":     { "value": "2026-04-27",   "filled_at": "...", "source_turn": 4 },
    "incident_location": { "value": "5th & Main",   "filled_at": "...", "source_turn": 4 },
    "incident_description": null,
    "vehicle_damage_description": null
  },

  "steps": {
    "greet":            { "status": "completed", "completed_at_turn": 1 },
    "verify_identity":  { "status": "completed", "completed_at_turn": 3 },
    "get_policy":       { "status": "completed", "completed_at_turn": 4 },
    "confirm_policy":   { "status": "pending"   },
    "get_incident_date": { "status": "completed", "completed_at_turn": 4, "completed_via": "side_effect" },
    "get_incident_location": { "status": "completed", "completed_at_turn": 4, "completed_via": "side_effect" },
    "get_incident_description": { "status": "eligible" },
    "summarize_and_file": { "status": "blocked", "blocked_by": ["get_incident_description"] }
  },

  "focus": "get_incident_description",   // derived, not a primary key
  "tangent_stack": [],
  "escalations": [],
  "audit_log_ref": "pg://audit/calls/vapi_call_abc123"
}
```

Three things to notice:

1. **No `current_step` integer.** A `focus` field is *derived* from "highest-priority eligible step." User jumps around → focus recalculates without anything having to "go back."
2. **Steps can be completed via side-effect** (a slot filled while the agent was asking about something else). The `completed_via` field tells the audit trail, so you can defend "yes the agent collected the incident date even though it wasn't the question being asked."
3. **`last_message_hash` is the idempotency key.** Vapi retries (network blip, server crash mid-stream) → don't double-advance state; replay the same response.

### Per-turn control flow

```
┌─────────────────────────────────────────────────────────────────┐
│  1. Receive Vapi POST                                            │
│     - messages[]  (full conversation)                            │
│     - metadata: { call_id, assistant_id, ... }                   │
│                                                                  │
│  2. Look up Proceda session state by call_id (Redis ~2ms)        │
│     - First turn? Initialize from sop_id (passed via              │
│       Vapi assistant metadata or first system message)           │
│                                                                  │
│  3. Idempotency check                                            │
│     - hash(messages) == last_message_hash?                       │
│       → Replay cached response (Vapi retried)                    │
│     - else: continue                                             │
│                                                                  │
│  4. Extract from latest user turn                                │
│     - LLM call with structured output: extract ALL slots         │
│       from the user's utterance, not just current focus's        │
│     - Classify intent: ANSWER | CORRECTION | TANGENT |           │
│       REFUSAL | OUT_OF_SCOPE | RESTART                           │
│                                                                  │
│  5. Apply to state                                               │
│     - For each extracted slot: fill (or correct, marking the     │
│       prior value's history)                                     │
│     - For each step whose slots are now all filled: mark         │
│       completed                                                  │
│     - Recompute eligibility graph                                │
│     - Recompute focus = highest-priority eligible step           │
│                                                                  │
│  6. Decide response strategy                                     │
│     - if intent == CORRECTION: acknowledge correction explicitly │
│     - if intent == TANGENT: handle from tangent registry, push   │
│       focus to tangent_stack                                     │
│     - if intent == REFUSAL on required slot: escalate or         │
│       skip-with-flag                                             │
│     - if intent == OUT_OF_SCOPE: redirect, increment             │
│       redirect counter                                           │
│     - else: advance to focus's prompt                            │
│                                                                  │
│  7. Build per-turn LLM prompt                                    │
│     - Tight system prompt scoped to FOCUS step only              │
│     - Inject already-collected context                           │
│     - Include allowed tools for THIS step only                   │
│                                                                  │
│  8. Stream LLM response back to Vapi as SSE chat-completion      │
│     deltas                                                       │
│     - If LLM emits a tool call: intercept, execute server-       │
│       side, fold result into the conversation, continue          │
│       streaming. Vapi never sees the tool.                       │
│                                                                  │
│  9. Persist                                                      │
│     - Write audit_log entries (append-only)                      │
│     - Update session state with last_message_hash                │
│     - Commit before sending [DONE]                               │
└─────────────────────────────────────────────────────────────────┘
```

### The reframe: SOP = constraint graph over slots and actions

The "user jumps around" problem is unsolvable with a forward-only finite state machine. Replace with:

- **Slots** — facts the system needs to know (name, policy number, date, location, description).
- **Actions** — side-effecting operations (lookup CRM, file FNOL ticket, transfer call, hang up).
- **Steps** — units of work, each consuming some slots and possibly invoking an action.
- **Constraints** — partial ordering: "step X cannot run before slots A, B are filled" and "step Y cannot run before action Z has succeeded."

"Step 3 of 7" is a UX label for monitoring dashboards. **Internally there is no current step — there's an eligibility set.** At any moment, the eligible step is whichever has its dependencies met and highest authoring priority. Structurally identical to how Make/Bazel work — they don't care what order you wanted; they care what's blocked on what.

### Handling each conversational scenario

#### Scenario A — User answers ahead

> SOP focus is `get_policy`. User: *"Hi, this is Jane Smith calling about my policy 124-456. I had an accident yesterday on 5th and Main."*

**Mechanism:** Step 4 (extraction) runs against the *whole SOP slot schema*, not just the focus's slot. One utterance fills `customer_name`, `policy_number`, `incident_date`, `incident_location` simultaneously. Step 5 marks `verify_identity`, `get_policy`, `get_incident_date`, `get_incident_location` all completed (some via side-effect). Focus recomputes to `confirm_policy` (because that step requires action, not just a slot).

**Response strategy:** Don't pretend you didn't hear what they said. Agent: *"Thanks Jane. Let me confirm I've got policy 124-456 and an incident yesterday at 5th and Main — does that sound right?"* That's the `confirm_policy` step's prompt, automatically enriched.

Audit log: turn 2 filled four slots, completed three steps via side-effect, advanced focus to `confirm_policy`. A compliance reviewer can see *exactly* how the agent collected each datum.

#### Scenario B — User asks a tangent

> Focus is `get_incident_description`. User: *"Hold on, will this affect my premium?"*

**Mechanism:** Step 4 classifies as `TANGENT`. The SOP author has a **tangent registry** with allowed digressions:

```yaml
tangents:
  - id: premium_impact_question
    triggers: ["affect premium", "raise my rates", "cost more"]
    handler:
      kind: knowledge_base
      kb_id: fnol_faq
    return_to: focus
    audit_tag: faq_premium
```

Push current focus to `tangent_stack`, run handler (KB lookup, canned answer, or sub-flow), pop back. Agent: *"Filing a claim doesn't automatically affect your premium — that depends on fault and your policy specifics. Final determination is by your adjuster. Now, can you describe what happened?"*

**Why a stack and not a flag:** tangents nest. User might tangent during the answer to a tangent. Stack handles naturally.

**Critical authoring choice:** SOP author declares which tangents are allowed. Anything not in the registry falls through to the OUT_OF_SCOPE handler.

#### Scenario C — User corrects a previous answer

> Focus is `get_incident_description`. User: *"Actually, I gave you the wrong policy number — it's 124-456, not 123-456."*

**Mechanism:** Intent → `CORRECTION`. Extraction returns:

```json
{
  "corrections": [
    { "slot": "policy_number", "old_value": "123-456", "new_value": "124-456" }
  ]
}
```

State machine:
1. Mutate slot — but **append, don't overwrite**. Audit log records old value, new value, turn, reason.
2. Re-evaluate steps that depended on `policy_number`. If `get_policy` had triggered an API lookup against the wrong policy, fire **compensation step** that retries against the correct number. (One place authoring rigor matters — if your SOP doesn't model compensations, it can't gracefully handle corrections after side effects.)
3. Response: explicit acknowledgment. *"Got it — updating to 124-456. Let me re-check that policy real quick."* Then resume.

**This is where Proceda's value is most obvious.** A naive Vapi assistant with a big system prompt has no machinery for "apply this correction and undo the side effects from the wrong value." The graph plus compensation actions give you that.

#### Scenario D — User refuses or can't answer

> Focus is `get_incident_date`. User: *"I really don't remember the exact date."*

Each slot has a `required` flag and refusal policy:

```yaml
slot: incident_date
required: true
refusal_policy:
  attempts: 2
  on_exhaustion: escalate
  fallback_value: null
```

Two retries with rephrased prompts. On exhaustion, fire `escalate` action — flag for human review and continue with `incident_date = unknown`, or transfer to live agent.

#### Scenario E — User goes wildly off-topic

> Focus is `verify_identity`. User: *"Tell me a joke."*

Intent `OUT_OF_SCOPE`. Redirect with a counter:

```yaml
out_of_scope_policy:
  redirect_template: "I'd love to chat, but I need to focus on your claim. {focus_prompt}"
  max_redirects_per_call: 3
  on_exhaustion: end_call_with_summary
```

Three off-topic asks → polite hangup with audit entry. Prevents adversarial users from running up your minute meter.

#### Scenario F — User wants to start over

Intent `RESTART`. Mark current execution as `abandoned` in audit log with reason. Initialize new session with same `call_id` but new `execution_id`. Audit trail preserves both attempts.

### The extraction step is where this all lives or dies

Step 4 in the per-turn flow is the load-bearing component. Your extraction model needs to:
- Return structured output covering every slot in the SOP, even if user only addressed one.
- Classify intent (ANSWER, CORRECTION, TANGENT, REFUSAL, OUT_OF_SCOPE, RESTART) reliably.
- Surface confidence scores so you know when to ask a confirming clarification vs. trust the extraction.

Two implementation strategies:

1. **Two-pass per turn:** First a small/fast extraction call (Haiku or specialized fine-tune) returning structured JSON; then main response generation. Cleanest separation but spends ~150-300ms on extraction.
2. **Single-pass with tool calls:** Main response generation uses tool-calling where one of the tools is `update_state` that the model is required to call before speaking. Lower latency, harder to enforce and debug.

For v1, ship two-pass and optimize later. The eval story is much cleaner — independently measure extraction accuracy from response quality.

### Latency budget that makes this real or unreal

Vapi targets <800ms voice-to-voice. Reality:

| Stage | Budget |
|---|---|
| STT (Deepgram, Vapi-side) | ~150ms |
| Network round-trip Vapi → Proceda | ~30ms |
| **Proceda total budget per turn** | **~300-400ms** |
| Network return + TTS first audio | ~200ms |
| Total | ~700-800ms |

Inside Proceda's ~300-400ms budget:

| Step | Target |
|---|---|
| Session lookup (Redis) | <5ms |
| Idempotency check | <1ms |
| Extraction call | 150-250ms — **the long pole** |
| State mutation + audit write (async ok) | <10ms |
| Decide response strategy | <2ms |
| Build per-step prompt | <5ms |
| LLM main call: time-to-first-token | 100-200ms |

Two LLM calls. Can't both be sequential and stay under budget.

**The optimization:** start the main response LLM call *speculatively* on the most likely interpretation (intent=ANSWER, extracted slots from focus) while the extraction call is in flight. ~85% of turns are ANSWER with no correction or tangent — speculative path is right most of the time. When wrong (CORRECTION or TANGENT), abort speculative stream, fall back to slow path. Worst case adds ~200ms; common case loses nothing. Same trick GPU-accelerated LLMs use for speculative decoding, applied at the orchestration layer.

### Two architecture-level invariants

> **Insight.** *Tool execution must live inside Proceda, not Vapi. When the SOP says "look up the policy in CRM," the LLM emits a tool call inside the chat-completions response. Your server intercepts it, executes against your tool registry, folds the result back, continues streaming. Vapi never sees the tool. Keeps audit trail authoritative, lets you compose tool calls without round-tripping through Vapi, means you can swap voice platforms without rewiring tools.*

> **Insight.** *The session state is the artifact, not the conversation. Compliance teams don't want transcripts; they want to verify "did the agent collect required slots A/B/C, did it confirm before action D, did it follow escalation rule E." Your state record + audit log are precisely that artifact, structured. The transcript is supporting evidence. Build the audit log shape with a compliance officer in the room, not a developer.*

### Worked example: end-to-end FNOL call

**SOP:** Insurance FNOL. Slots: `customer_name`, `policy_number`, `incident_date`, `incident_location`, `incident_description`, `vehicle_damage_description`. Steps: `greet`, `verify_identity`, `get_policy` (action: lookup CRM), `confirm_policy`, `gather_incident`, `gather_damage`, `summarize_and_file` (action: file FNOL).

```
Turn 0 (system, on call connect):
  Proceda init: focus = greet, all slots empty.
  Agent: "Hi, this is Aria with Acme Insurance. What's your name?"

Turn 1 (user):
  "Hi Aria, this is Jane Smith. I'm calling about an accident
   yesterday on 5th and Main. My policy is 123-456."

  Extraction → fills: customer_name, policy_number, incident_date,
                       incident_location.
  Steps completed via side-effect: verify_identity, get_policy
    (action triggered async: CRM lookup), get_incident_date,
    get_incident_location.
  Focus → confirm_policy (gated on CRM lookup).

  CRM lookup returns: policy 123-456 → "Robert Smith, 2019 Honda".
  Mismatch flag: caller said "Jane Smith" but policy on "Robert".

  Proceda decides: confirm before proceeding.
  Agent: "Thanks Jane. I see policy 123-456 is registered to
          Robert Smith — are you calling on his behalf, or did I
          maybe get the policy number wrong?"

Turn 2 (user):
  "Oh sorry, the policy is 124-456. I always mix those up."

  Intent → CORRECTION. Slot policy_number: 123-456 → 124-456.
  Compensation: prior CRM lookup invalidated. Re-trigger CRM lookup
    on 124-456 → returns "Jane Smith, 2022 Toyota". Match.
  Audit log: correction event with both values and reason.

  Focus → confirm_policy (now satisfied) → gather_incident
    (description still missing).
  Agent: "No problem — I see policy 124-456 for your 2022 Toyota.
          Can you describe what happened in the accident?"

Turn 3 (user):
  "I was rear-ended at the light. Wait, will this raise my rates?"

  Extraction: incident_description = "rear-ended at the light"
    (partial, needs more detail).
  Intent → ANSWER + TANGENT (premium question).
  Tangent registry hits 'premium_impact_question'.

  Push focus to tangent_stack. Run KB tangent handler.
  Agent: "Being rear-ended is usually not-at-fault, which generally
          doesn't affect your premium — but final determination is
          by your adjuster. Now, can you tell me a bit more about
          what you saw — was the other driver going fast, any
          injuries?"

Turn 4 (user):
  "No injuries, but my bumper is wrecked."

  Extraction: vehicle_damage_description = "bumper wrecked",
    incident_description amended with "no injuries".
  Steps completed: gather_incident, gather_damage.
  Pop tangent_stack.
  Focus → summarize_and_file.

  Agent: "Got it. To confirm: yesterday on 5th and Main, you were
          rear-ended, no injuries, bumper damage. I'll file the
          claim now — give me one moment."

Turn 5: action fires (file_fnol tool), returns claim_id.
  Agent: "All filed — your claim number is FNOL-00891. An adjuster
          will reach out within one business day. Anything else?"

Turn 6 (user): "No, thanks."
  Agent: "You're welcome. Goodbye."
  End call. Final audit artifact assembled.
```

**The audit artifact** for this call (delivered to the BPO's compliance officer the moment the call ends):

```yaml
call_id: vapi_call_abc123
sop: insurance_fnol v3.0.1
duration: 3m17s
outcome: completed
slots_collected: 6/6
required_slots_satisfied: true
corrections_applied: 1
  - policy_number: 123-456 → 124-456 (turn 2, user-initiated)
tangents_handled: 1
  - premium_impact_question (turn 3, allowed by SOP)
escalations: 0
actions_executed:
  - crm_lookup(policy=123-456) → invalidated by correction
  - crm_lookup(policy=124-456) → success
  - file_fnol(...) → claim_id=FNOL-00891
out_of_scope_redirects: 0
deviations_from_sop: none
```

That's the artifact you sell. The "this call provably followed SOP X" claim, machine-readable, audit-ready. **No other voice infra player produces it. That's your moat.**

---

## Part 11 — Market Validation: Is This Solving a Real Problem?

Short answer: **yes, with named failure modes that have crystallized into industry vocabulary, and an entire startup category funded specifically because of this problem.**

### The named failure modes

The industry has converged on names for these failures — when something gets named, you know it's been hit enough times by enough teams that the pattern is well-understood:

**1. Memory drift / context degradation** (Kore.ai): *"An agent's working memory has a fixed capacity, and as the session grows and more context gets added, the agent needs to make room for what's new. It lets older things blur to make space for newer details. Instructions from earlier interactions become too weak to consistently guide the output."* This is the structural reason single-prompt agents fall apart on long flows.

**2. Semantic drift** (Kore.ai): a precise instruction gradually becomes something looser. Example: *"'self-employed applicants below this threshold require manual review' gradually becomes 'self-employed applicants are higher risk.' The output can still sound perfectly coherent, but the agent is no longer applying the rule with the same precision."* The dangerous one for compliance — the agent sounds right and *is* wrong.

**3. Step skipping under context pressure** (Future AGI): *"When context pressure builds, models start skipping steps, inventing policies you never approved, and doing math they will get wrong. Without a state machine, every tool is available at every step, meaning the model might complete an order before collecting required information or behave differently on call one versus call ten thousand."* The "call one versus call ten thousand" clause is the production reality engineers are running into.

**4. Silent failure** (multiple sources): *"Voice agents start hallucinating, skip required steps, and go silent leaving callers hanging. These failures occur without crash logs or error messages, resulting in angry users and an inability to reproduce issues."* Voice failures are uniquely bad — there's no error log, just a confused human on the other end of the line.

**5. Loop / drift in goal-seeking** (Hamming AI): *"Voice agents should track progress toward their goal and steer the conversation forward instead of looping or drifting."* Implicit admission: they often don't.

**6. The 95% pilot-to-production failure rate** (MIT, 2025): based on 150 interviews, 350-employee survey, 300 public deployments. Voice is a subset and consistently flagged as harder than text because of latency + audio + turn-taking. Air-cover stat for "why we need a different approach."

### The market evidence — startups funded specifically against this problem

| Company | Funding / status | What they sell |
|---|---|---|
| **Hamming AI** | $3.8M seed, YC, 2024 | Voice agent testing, simulation, production observability |
| **Cekura** | YC-backed | E2E testing + observability, multi-turn red-teaming |
| **Coval** | Active, enterprise sales motion | Simulation-based regression testing + monitoring |
| **Roark** | Active, enterprise sales | Voice agent QA |
| **Leaping AI** | Active | Voice agent eval comparison |
| **Bluejay** | Active | Voice agent production monitoring |
| **Future AGI** | Active | Voice AI simulation + automated optimization |

Seven companies, all founded since 2023, all addressing one slice of the same problem: voice agents fail in ways that are hard to detect and harder to prevent. Strong demand signal. **And notably, none of them are *fixing* the drift; they're *measuring* it.**

### Platforms admitting the problem and bolting on responses

Even the platforms have shipped features explicitly because of these failures:

- **Retell shipped Automated QA** (Dec 2025) — *"the #1 request of enterprise customers."* Their fastest-growing customer segment was asking for *post-hoc* conformance checking because runtime conformance wasn't holding.
- **Vapi shipped Evals** — *"the best evals come from production issues; when you discover a bad call in logs, you can turn that transcript into a test that becomes a constraint checked on every future change."* Translation: agents will keep breaking; build a test pipeline so each new break stays fixed.
- **Vapi deprecated its visual Workflows builder** and pushed users to Assistants + Squads, then watched users hit drift problems on Assistants past 5 sequential steps.
- **Pipecat Flows exists at all** because the team at Daily.co concluded big context windows don't fix structure. *"Simply providing access to everything is not the same as providing guidance."*

### How people are trying to solve it today (five approaches, none complete)

**1. Bigger / better prompts.** Dominant approach in the wild. Works for short flows, demonstrably fails past 5–7 sequential steps under conversational pressure. **Diagnosis: addresses symptoms, not root cause.**

**2. Visual flow builders (state machines).** Vapi Workflows (deprecated), Retell Conversation Flow (active and richest), Bland Pathways, Voiceflow Workflows. Author the flow as a node-edge graph; conditions gate transitions. **Strengths:** keeps each LLM call's context tight, enforces gating, makes the flow legible. **Weaknesses:** you have to re-author your process in their visual tool (Word doc → drag-and-drop graph), conditions are limited, most platforms still give the LLM enough rope to drift inside a node. Generally don't handle the user-jumps-around problem at all.

**3. Multi-agent handoff.** Vapi Squads, LiveKit Agents, OpenAI Swarm-style. Split flow across specialized agents. **Strengths:** limits context-rot per agent. **Weaknesses:** the handoff itself is brittle (when does Agent A know to hand to Agent B?), context sometimes doesn't transfer cleanly, authors end up coding the *meta*-flow on top — same problem one level up.

**4. "Context engineering" / structure-outside-the-context-window.** Pipecat Flows, LangGraph, emerging Manus-style frameworks. Explicit Flow Manager keeps state in code, not in the LLM context. From Manus: *"As interaction horizons increase, absence of memory governance leads to drift, loss of task invariants, and hallucinations induced by irrelevant, stale, or inconsistently recalled context. Long horizon reliability requires explicit memory control: a compact, structured set of decision critical variables, including goals, constraints, entities, and relations."* **Structurally the right approach** — and what Proceda is doing. **Weakness vs. Proceda:** these are *libraries* you write code against, not *products* that ingest an SOP document.

**5. Eval / observability.** Hamming, Cekura, Coval, etc. Detect drift after the fact via simulation-based regression testing and production monitoring. **Strengths:** catches drift before customers do. **Weaknesses:** by definition reactive — the bad call already happened. Doesn't *prevent* drift; just *measures* it. None enforce SOP conformance at runtime.

### What Proceda + Vapi (or Proceda + any custom-LLM hook) brings that's actually new

Three things the existing approaches don't combine:

**1. Document-in-process-out.** Every other approach requires *re-authoring* the process in their tool's syntax — drag nodes in a visual builder, write Python flow code, design a multi-agent handoff. Proceda's pitch is "point at the SOP doc you already have." Genuinely different from what's shipped. The closest analog is decade-old IVR script generators, which were rule-based and couldn't reason; using LLM extraction to turn an existing SOP into an executable graph is the missing primitive.

This is more important than it sounds because of *who* the buyer is. A Vapi-builder developer is happy to drag nodes; a BPO compliance officer or healthcare ops manager is not. They have SOPs in Word; they don't want to re-author them. The doc-in motion changes the buyer profile.

**2. Audit-trail-by-construction at the per-call level.** Eval tools (Hamming, Cekura) measure conformance *across many calls* and *after* the fact. Proceda produces conformance evidence *for each individual call* in real time, structured by the SOP graph. That's what compliance officers actually need — per-claim evidence, not aggregate stats.

The audit artifact (slots collected, corrections applied with provenance, tangents allowed/denied per registry, escalations, deviations) is something no other voice infra produces. Hamming et al. produce dashboards. Proceda produces *evidence packages*. Different artifact, different buyer.

**3. Process-aware orchestration that's voice-platform-agnostic.** Living behind a chat-completions URL means Proceda is not tied to one voice platform's flow product. Pipecat Flows is conceptually the closest cousin but is a Python framework you author against; LangGraph is a generic library; Vapi/Retell flow products are closed-platform-specific. Proceda being a *neutral engine* that any voice platform with a custom-LLM hook can call is a position no one else occupies.

### Honest assessment of how promising this is

What I believe based on the evidence:

- **The problem is real and well-documented.** Not a hypothesis you have to convince anyone of — buyers already have scar tissue.
- **The existing solutions cover real subsets.** Eval/observability is genuinely useful. Visual flow builders work for short flows. Multi-agent helps. Pipecat-Flows-style structure-outside-context is correct.
- **Nobody combines all five attributes** (doc-in, process-graph-out, voice-first, audit-by-construction, self-serve). That's the wedge.
- **The wedge is defensible if you build the audit artifact deeply.** Anyone can wire a chat-completions endpoint to Vapi BYOM; the moat is the conformance artifact compliance teams sign off on. Build that with a real compliance officer in the room early.
- **The wedge is *not* defensible against "Retell adds SOP ingestion" or "Pipecat Flows adds enterprise audit."** Both are plausible 12-month moves. Speed matters: ship a credible conformance artifact and get a regulated customer reference *before* the closed shops or the OSS framework catch up.

### Threats to track, in order of likelihood

1. **Pipecat Flows ships compliance/audit primitives.** Most likely because the conceptual alignment is closest. Counter: their authoring surface is code, not document; you can be the "Pipecat-compatible enterprise engine."
2. **Retell ships document ingestion.** Their #1 enterprise ask was QA, which means enterprises are already pushing them on conformance. SOP ingestion is a natural next ask. Counter: they don't have a custom-LLM hook, so even if they ship doc ingestion, the engine behind it will be theirs and locked.
3. **Parloa adds a self-serve / PLG tier.** Less likely because their motion is enterprise-direct, but possible.
4. **Eval startups (Hamming, Cekura) add runtime enforcement.** Plausible — they have customer relationships and production data. Counter: they're built around "measure after the fact"; pivoting to "enforce at runtime" is a real product change.

### Answering the skeptic

Skeptic's question: *"Isn't this just another flow builder + observability layer that the platforms will absorb?"*

Honest answer: a generic flow-builder layer would be absorbed. The specific shape that's defensible is **(a) SOP document → graph generation, (b) per-call compliance artifact as the deliverable, (c) vertical specificity in the SOP libraries** (insurance FNOL templates, healthcare intake templates, KYC templates — pre-built flows customers customize rather than authoring from scratch).

That's not a flow builder; it's a **compliance product that happens to be powered by a flow engine**. Compliance products have the property that incumbents avoid building them because the failure mode is regulatory, not technical — closed-platform vendors don't want to be on the hook for "we promised your voice agent followed HIPAA."

**Conclusion:** solving a real problem, in a way that's structurally different from what exists, with a specific defensibility story (compliance artifact + vertical SOP library) that the existing players are unlikely to copy quickly.

---

## Bottom Line

The common interface between voice infra and SOP infra is **OpenAI chat completions over SSE, plus an additive `sop-event` stream for first-class embedders**. Build to that contract and you get Vapi BYOM today, LiveKit/Pipecat with thin adapters, and a defensible position for the day Retell/Bland/Synthflow add custom-LLM hooks (which they will — every closed shop eventually loses to demand for model swap).

Parloa is real, well-funded, and selling to the same BPO ecosystem you're targeting — but they're playing a top-down, multi-channel, "manage agents at scale" game. Your wedge isn't to out-Parloa Parloa; it's to be the **SOP-document-in, audit-trail-out, voice-first, self-serve** product that ops teams ship in days rather than weeks. The architectural choice (Proceda lives behind a chat-completions URL, voice platform agnostic) is what keeps that wedge defensible regardless of how the voice-infra market shakes out below you.

**Phasing:** Vapi v1 (days, not weeks). LiveKit v2 (where the enterprise traffic flows). Pipecat as developer-channel and OSS hedge. Retell as the prize you wait for the market to make integrable. Build behind the OpenAI chat-completions contract so none of these is a one-way door.

---

## Sources

### Vapi
- [Vapi — Build Advanced Voice AI Agents](https://vapi.ai/)
- [Vapi Introduction docs](https://docs.vapi.ai/quickstart/introduction)
- [Vapi Workflows overview](https://docs.vapi.ai/workflows/overview)
- [Vapi Squads docs](https://docs.vapi.ai/squads)
- [Vapi Custom LLM (using your server)](https://docs.vapi.ai/customization/custom-llm/using-your-server)
- [Vapi Custom LLM example repo](https://github.com/VapiAI/example-custom-llm)
- [Vapi MCP Server announcement](https://vapi.ai/blog/bring-vapi-voice-agents-into-your-workflows-with-the-new-vapi-mcp-server)
- [Vapi Evals / production testing](https://vapi.ai/blog/evals)
- [Vapi Prompting Guide](https://docs.vapi.ai/prompting-guide)
- [Vapi Llama 3 Voice Assistant tutorial](https://vapi.ai/blog/llama-3-voice-assistant)
- [Bessemer Venture Partners — Our investment in Vapi](https://www.bvp.com/news/our-investment-in-vapi-the-voice-ai-developer-platform)
- [Sacra — Vapi valuation, funding & news](https://sacra.com/c/vapi/)
- [GlobeNewswire — Vapi $20M Series A led by Bessemer](https://www.globenewswire.com/news-release/2024/12/12/2996317/0/en/Vapi-Dials-in-20M-in-Series-A-Led-by-Bessemer-to-Bring-AI-Voice-Agents-to-Enterprise.html)
- [Hamming AI: How to Test Voice Agents Built with Vapi](https://hamming.ai/blog/how-to-test-voice-agents-built-with-vapi)

### Retell AI
- [Retell AI — Conversation Flow Overview](https://docs.retellai.com/build/conversation-flow/overview)
- [Retell AI — Unlocking Complex Interactions with Conversation Flow](https://www.retellai.com/blog/unlocking-complex-interactions-with-retell-ais-conversation-flow)
- [GlobeNewswire — Retell AI launches enterprise QA, $35M+ ARR](https://www.globenewswire.com/news-release/2025/12/17/3207048/0/en/Retell-AI-Fastest-Growing-AI-Voice-Agent-Platform-Launches-First-Automated-QA-Solution-to-Accelerate-Enterprise-Adoption-of-Voice-AI.html)
- [GetLatka — Retell AI $7.2M revenue with 41 person team in 2025](https://getlatka.com/companies/retellai.com)
- [Yahoo Finance — Retell AI on Wing VC Enterprise Tech 30](https://finance.yahoo.com/sectors/technology/articles/voice-ai-startup-retell-ai-131700326.html)

### Bland AI
- [Bland AI — Conversational Pathways docs](https://docs.bland.ai/tutorials/pathways)
- [Bland AI — Conversational Pathways product page](https://www.bland.ai/product/conversational-pathways)

### LiveKit
- [LiveKit — Build voice, video, and physical AI agents](https://livekit.com/)
- [LiveKit Agents — Introduction](https://docs.livekit.io/agents/)
- [LiveKit Agents on GitHub](https://github.com/livekit/agents)
- [LiveKit — Sequential Pipeline Architecture](https://livekit.com/blog/sequential-pipeline-architecture-voice-agents)
- [LiveKit blog — Series C announcement](https://blog.livekit.io/livekit-series-c/)
- [TechCrunch — LiveKit hits $1B at Series C](https://techcrunch.com/2026/01/22/voice-ai-engine-and-openai-partner-livekit-hits-1b-valuation/)
- [SiliconANGLE — LiveKit $100M Series C](https://siliconangle.com/2026/01/22/livekit-raises-100m-1b-valuation-scale-real-time-ai-media-platform/)

### Pipecat / Daily.co
- [Pipecat — Introduction](https://docs.pipecat.ai/getting-started/introduction)
- [Pipecat on GitHub](https://github.com/pipecat-ai/pipecat)
- [Pipecat Flows on GitHub](https://github.com/pipecat-ai/pipecat-flows)
- [Daily.co — Beyond the Context Window: Why Your Voice Agent Needs Structure](https://www.daily.co/blog/beyond-the-context-window-why-your-voice-agent-needs-structure-with-pipecat-flows/)
- [Daily.co — Pipecat Cloud is Now GA](https://www.daily.co/products/pipecat-cloud/)
- [Daily.co — Daily and NVIDIA collaborate on voice AI](https://www.daily.co/blog/daily-and-nvidia-collaborate-to-simplify-voice-agents-at-scale/)
- [NVIDIA — Pipecat Voice Agent Framework](https://build.nvidia.com/pipecat/voice-agent-framework-for-conversational-ai)

### Parloa
- [Parloa — Platform overview](https://www.parloa.com/platform/)
- [Parloa — Agent Composition announcement](https://www.parloa.com/blog/AI-agent-composition/)
- [Parloa — Enterprise FAQ](https://www.parloa.com/knowledge-hub/parloa-faqs/)
- [TechCrunch — Parloa triples valuation to $3B with $350M raise](https://techcrunch.com/2026/01/15/parloa-triples-its-valuation-in-8-months-to-3b-with-350m-raise/)
- [SiliconANGLE — Parloa raises $350M](https://siliconangle.com/2026/01/15/parloa-raises-350m-make-enterprise-customer-experience-fully-conversational/)
- [Sacra — Parloa valuation, funding & news](https://sacra.com/c/parloa/)
- [Channel Insider — Parloa Partner Program](https://www.channelinsider.com/channel-business/vendor-leadership-and-partner-programs/parloa-ai-partner-program-sept-2025/)
- [Equal Experts — How Parloa launched an agentic AI-powered platform](https://www.equalexperts.com/case-study/how-parloa-launched-the-first-agentic-ai-powered-platform-for-call-centers/)
- [eesel AI — Parloa overview](https://www.eesel.ai/blog/parloa)
- [Synthflow — Parloa Review (competitor view)](https://synthflow.ai/blog/parloa-review)

### Other voice infra & comparisons
- [Voice Agent Infrastructure Stack 2026 (Digital Applied)](https://www.digitalapplied.com/blog/voice-agent-infrastructure-stack-2026-reference)
- [Retell vs Vapi vs Bland vs Synthflow 2026](https://tested.media/retell-vs-vapi-vs-bland-vs-synthflow/)
- [Voiceflow](https://www.voiceflow.com/)
- [Voiceflow Agents docs](https://docs.voiceflow.com/docs/agents)
- [PolyAI Developers](https://poly.ai/developers)
- [OpenAI Agent Builder](https://developers.openai.com/api/docs/guides/agent-builder)

### Voice agent failure modes & eval/observability (Part 11)
- [Kore.ai — Memory drift in AI agents](https://www.kore.ai/blog/memory-drift-in-ai-agents)
- [Future AGI — Why Your Voice Agent Fails in Production](https://futureagi.substack.com/p/why-your-voice-agent-fails-in-production)
- [Hamming AI — Voice Observability: The Missing Discipline in Conversational AI](https://hamming.ai/blog/voice-agent-observability-voice-observability)
- [Bluejay — 7 Reasons Voice Agents Fail in Production](https://getbluejay.ai/resources/voice-agent-production-failures)
- [Webfuse — Top 5 Voice AI Agent Failures and How to Fix Them](https://www.webfuse.com/blog/top-5-voice-ai-agent-failures-and-how-to-fix-them)
- [SignalWire — Why Voice AI Fails Before the First Call](https://signalwire.com/blogs/developers/why-voice-ai-fails)
- [Beconversive — Common Voice AI Agent Challenges and How to Fix Them](https://www.beconversive.com/blog/voice-ai-challenges)
- [Hamming AI homepage](https://hamming.ai/)
- [Hamming AI — $3.8M seed announcement](https://aibusinessweekly.net/p/hamming-ai-3-8-million-voice-agent-testing)
- [Future AGI — Voice AI Simulation Platforms comparison (Cekura, Hamming, Bluejay, Coval)](https://futureagi.com/blogs/voice-ai-simulation-cekura-hamming-bluejay-coval-2025)
- [Leaping AI — Comparing Leading Voice AI Eval Platforms](https://leapingai.com/blog/comparing-leading-voice-ai-eval-platforms)
- [Manus — Context Engineering for AI Agents](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- [Alchemyst — What Is An AI Context Layer For Enterprise Voice Agents](https://getalchemystai.com/blog/ai-context-layer-enterprise-voice-agents)
- [Fortune — MIT report: 95% of generative AI pilots failing](https://fortune.com/2025/08/18/mit-report-95-percent-generative-ai-pilots-at-companies-failing-cfo/)
- [AI Magazine — MIT: Why 95% of Enterprise AI Investments Fail](https://aimagazine.com/news/mit-why-95-of-enterprise-ai-investments-fail-to-deliver)

### Compliance & domain context
- [Voice Agents in Quality Control / HACCP case study (Digiqt)](https://digiqt.com/blog/voice-agents-in-quality-control/)
- [Voice AI Compliance guide (Speechmatics)](https://www.speechmatics.com/company/articles-and-news/your-essential-guide-to-voice-ai-compliance-in-todays-digital-landscape)
