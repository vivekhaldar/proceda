# Phase 2: compiled bounded steps behind natural-language SOPs

Design recorded 2026-10-04 before phase-2 implementation or inference.
Phase-1 evidence remains frozen at commit `4b82dc7`; see its experiment report.

## Motivation and unchanged objective

Phase 1 removed 187 hosted acknowledgment calls in referral, but accepted no
email or patient completion checks. The full hybrid passed 346/360 versus
350/360, so the requested broad accuracy-preserving improvement is still unmet.
A model that merely detects completed work cannot replace the initial intent
classification, tool planning, or final serialization. This phase addresses
those operations while retaining natural-language authoring and configurable
local/hosted cascades. It must not hide compiler gains inside a decision-model
claim or manufacture improvement by suppressing unresolved SOP obligations.

The preceding explanation turn verified concrete call traces; it yielded evidence
about the next action but made no implementation change. There is no active
inference process to wait for or restart. Work now continues from the clean branch.

## Provider capability and finite decisions

Both providers support native `choice` questions. This is distinct from the
current AND-of-Boolean `GateResult` and needs a separate result type. Use explicit
instructions and an ordered mapping of canonical labels to criteria. Route the
same semantic question through configured providers; unresolved choices return
to the existing LLM. A business label such as "unable to decide" is not runtime
abstention and must retain its prescribed downstream handling.

The portable subset is 2–64 choices. Validate every expected probability, finite
range, sum within stated rounding tolerance, selected label, unique maximum, and
provider truncation before acceptance. Use the selected option's probability,
not the provider's normalized confidence, as the explicit acceptance statistic.
Preserve both raw statistics. A backend may override the ordinary shared
threshold. Do not silently force the highest-scoring class or renormalize data.

Primary references, reviewed against installed Ollaya 0.9.0:
- https://docs.typesafe.ai/api
- https://docs.typesafe.ai/primitives/choice
- https://docs.typesafe.ai/confidence
- https://docs.typesafe.ai/models
- https://ollaya.cobanov.dev/docs/api
- https://github.com/ollaya-dev/ollaya/blob/bdfa46bf2962b519384b61fce4a97177e77f2672/crates/ollaya-api/src/decide.rs
- https://github.com/ollaya-dev/ollaya/blob/bdfa46bf2962b519384b61fce4a97177e77f2672/crates/ollaya-decision/src/answer.rs

Defer ordinal `score`: its confidence calculation differs between providers.
Local state truncation remains an abstention. Questions evaluated together cannot
implicitly depend on each other's answers; dependencies require another stage.

## A source-derived internal plan

The SOP author adds no DSL, model names, labels, or code. An opt-in compiler reads
only the original skill, allowed tool schemas/canonical identities, and input
field names. It receives no case values, benchmark expected outputs, dataset
rows, or historical answers. It produces a small declarative plan, never Python,
JavaScript, SQL, shell, or arbitrary executable code.

The initial plan supports four step kinds:

1. `decision`: a fully covered pure finite classification, optional explicitly
   prescribed regex extraction, and exact input bindings. A native choice returns
   a typed fact; the runtime can complete the step with the exact facts. Any
   remaining analysis, communication, external action, or clarification excludes
   whole-step completion. Extraction must yield a unique evidenced match; missing,
   conflicting, ambiguous, or timed-out extraction falls back.
2. `tool`: an exact initial tool-call batch whose names and argument bindings are
   prescribed by the source. Values come only from exact inputs, prior accepted
   decision facts, successful immutable receipts, or explicit source literals.
   This replaces proposal generation, not the entire step. All residual work goes
   through ordinary LLM continuation. Strict schema/alias conflicts cause fallback.
3. `render`: a final step asking only to report declared fields in a supported
   structured format. Every value must come from a unique successful tool receipt
   or accepted typed fact. Generate actual JSON/XML content; never claim an
   unproduced document or communication exists. Unsupported prose stays generative.
4. `llm`: explicit unsupported/residual work. No guessed plan is executed.

Each plan operation records an exact source quote/span. Entire-step admission
must account for the complete step, not only a convenient clause. A separate
compiler review checks omissions, unsupported work, invented defaults, and
incorrect bindings. Source quoting and model review are not mathematical proofs
of semantic equivalence. Inspect generated plans before benchmark selection,
retain all rejected plans, and test counterexamples with additional obligations.

Do not implement domain-name branches or a referral-specific scoring function.
An arithmetic interpreter is deferred until needed and independently evaluated.
Only extraction operations explicitly specified by the SOP are admissible; do
not implement email intent with benchmark-specific keyword rules.

## Runtime integration and provenance

Keep the public `Agent` API. Add an opt-in planning configuration, disabled by
default. After tool discovery, obtain the cached plan and pass it to Executor.
Before the first normal proposal in a step, try the corresponding plan exactly
once. Retain the attempt marker and accepted facts in RunSession so executor
recreation cannot redispatch a planned prefix. Planned tool proposals still use
the existing dispatcher, receipt ledger, error handling, and pre/post approvals.
They have explicit plan events and must not count as hosted model calls.

A failed binding or schema check dispatches nothing. After executing any prefix,
fallback sees the actual tool history and unresolved original instructions.
Do not roll back, replay, or erase successful effects to retry a plan. Existing
LLM fallback is not an exactly-once side-effect guarantee. Unknown outcomes still
require ordinary recovery. Never infer read-only effects from a tool's name.

Decision facts contain their field, exact selected value, plan hash, step,
provider attempt links, evidence references, and full probabilities. Initial
inputs remain immutable. Generated LLM summaries are not authoritative fact
sources. A missing field is distinct from false, zero, or an empty collection.
Ambiguous or failed receipts cannot supply arguments or rendered outputs.

Completion checks remain optional. A valid plan may identify residual analysis
or communication and decline the completion optimization for that step. A pure
read/capture tool plan may still use the original completion check against the
entire original step. This admission affects overhead, not the meaning of an
accepted completion. Final output changes are limited to validated render plans.

## Cache and inference accounting

Cache identity covers raw SOP bytes including approval markers, tool schemas and
canonical identities, input field names, compiler and reviewer model/config,
prompt hash, interpreter/IR versions, and an explicit cache epoch. Hosted aliases
can drift, so operators must be able to invalidate the cache. Never reuse a plan
against a different permission/tool scope. Cache invalid/unsupported results too,
with provenance; corruption or mismatch falls back rather than executing it.

Use a per-event-loop single-flight lock, atomic immutable publication, and exact
artifact hashes. Concurrent processes may compile redundantly; every attempt
must remain audited and charged, even if another artifact wins publication.
Compiler/reviewer calls use the existing audited LLM runtime. Preserve raw output,
validated plan, rejected candidates, reviews, sources, and cache-hit events.

Benchmark first-use compilation is measured, not free. Record cold/warm case
latency and compilation usage separately, plus full schedule cost including cold
compilation. If a prepared plan is reused, report that one-time cost and explicit
amortization assumptions. No hidden preparatory inference or free extraction.

## Evaluation before any new broad claim

Keep the same three domains and original source data. Their choice remains:
finite procedural precedence (referral), language routing/comparison (email),
and dependent tool calls with richer prose obligations (patient). Existing
patient schemas require nine-digit identifiers while the dataset uses six;
strict planned dispatch must fall back. Do not pad identifiers or silently
relax the schema. Missing documentation must remain an unresolved obligation.

Start with development-only capability and compiler probes. Separate source-only
plan generation from case inference. Preserve the original development/holdout
manifest and group boundaries. Unstarted development cases can test new behavior,
but known prior inspection must not be called unseen data. Do not tune on expected
held-out labels. The original holdout has been inspected and is now a reused
validation set; a later result there is not untouched confirmation.

Compare at least:
- Original-style LLM-only path, planning and decisions disabled.
- Compiled execution with generative choice fallback, decisions disabled.
- Same compiled execution with native decision choices and optional completion.

The middle arm is essential: it attributes deterministic binding/rendering and
one-time compilation separately from actual decision-model contributions. If a
stronger or cheaper generative model is used, include its LLM-only ablation too.
All arms share SOPs, tools, inputs, scoring, and required artifacts. Count cold
compilation, failures, fallback requests, local native work, and every audit.

Freeze code/config/plan hashes before a new paired schedule. Use tighter
interleaving or balanced blocks to reduce the severe provider-order confounding
seen in phase 1. Retain strict final-field accuracy and separately audit required
calls, correct dependent arguments, actual artifact production, and premature
completion. No repeated-run selection solely to obtain a favorable random score.

## Required validation

- Legacy disabled behavior and existing completion/proposal tests stay green.
- Native-choice malformed/unknown labels, missing probabilities, ties, bad sums,
  nonfinite values, truncation, errors, and uncertain results cannot force a label.
- Compiler case-data exclusion, source/cache identity, unsupported operations,
  injected instructions in data, invented defaults, and missing coverage.
- Append a required explanation or send action to an eligible decision step:
  cache invalidates and that step can no longer silently complete.
- Regex ambiguity/missing evidence/timeout and conflicting input bindings fall back.
- Exact receipt identity/step/entity, false/zero handling, schema mismatches, and
  unique output binding; no parsing of arbitrary LLM prose as established facts.
- Approval rejection prevents actions; accepted facts survive executor recreation;
  fallback cannot automatically repeat a planned prefix.
- Synthetic SOPs with renamed fields/tools demonstrate generality.
- Real development choice and end-to-end runs before any wider performance claim.

## Development revision: conservative step fallback

The first two source-only email compilations were structurally invalid. In the
second, the finite decision itself validated, but later tool guards and a render
referenced unsupported evidence. Rejecting the whole procedure discarded that
independently usable decision. Before the next inference, add a generic compiler
recovery: validate steps in source order and replace an invalid step with the
entire original step as `llm`. Remove any facts tentatively introduced by the
invalid step; dependent steps must then validate against the remaining facts.
Never repair a business rule, invent a value, rewrite a quote, or admit an invalid
operation. A malformed root/step count still rejects the whole candidate.

Preserve the unmodified compiler response, every downgrade reason, the resulting
candidate and a separate review of that candidate against the full original SOP.
The independent reviewer must approve even a partially compiled plan. This is
graceful loss of optimization, not a relaxation of execution validation. Report
the two failed attempts and their costs; do not overwrite or relabel their results.

## Execution protocol amendment before smoke v2

The sequential smoke v1 was interrupted after 9 of 36 executions completed,
with the tenth execution retained as incomplete. It is not a completed comparison.
Hosted calls were much slower than during phase 1. To make the same four-arm
comparison practical, smoke v2 uses three concurrent case groups on one event
loop. Each case still runs its four arms sequentially in the frozen cyclic order.
No model, threshold, compiler prompt, SOP, or scoring change accompanies this
protocol amendment. A new run-local shared plan cache is used, so a fresh source
compilation may differ from v1; never attribute between-run differences to
concurrency alone.

The driver records both planned order and actual start/finish timestamps. Case
latencies include shared-provider contention; they are not isolated service
latencies. The first actual caller pays the compiler and reviewer cost. Waiting
cache hits include single-flight waiting in preparation time. All arms in this
run share the same admitted source plan. Interrupted schedules keep provisional
cost accounting and require an offline audit rescan after process exit.
