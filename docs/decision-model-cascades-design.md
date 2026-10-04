# Decision models inside Proceda

Status: initial design, written before implementation, 2026-10-04.
Branch: `codex/decision-model-cascades`.

## Background and motivation

Proceda turns a natural-language SOP into sequential `SKILL.md` steps and runs
those steps through an LLM with scoped MCP tools and control tools. Its public
contract already hides prompts and execution loops from the SOP author. The
next useful abstraction is therefore internal: reserve generative models for
unbounded content and argument construction, and use decision models for bounded
judgments where they can improve the complete process's economics or correctness.

The motivating examples are ticket classification, extraction of arbitrary
policy numbers/dates, and dependent tool calls such as finding the authenticated
customer's latest order before fetching shipping status. A decision-only API
cannot generate arbitrary arguments. Conversely, generating prose to decide
whether a completed tool receipt satisfies a step can be unnecessary work.

Sources:
- https://youtu.be/HBVInCcfH1c (local/hosted decision cascades)
- https://youtu.be/MCc8vpD7doQ (enterprise tasks and extraction-verification cascade)
- https://enchiridionlabs.online/specialized-harness-engineering.html
- https://enchiridionlabs.online/sop-bench-results.html
- https://docs.typesafe.ai/api and https://docs.typesafe.ai/confidence
- https://docs.typesafe.ai/cookbooks/sde_cascade

Jev currently supplies hosted bounded decisions, not arbitrary generation or
verified local weights. Ollaya supplies local open-weight decision models. Nebius
Token Factory supplies the generative models in this experiment. Backend roles
and deployment location are independently configurable. No customer-facing DSL,
decision schema, or hand-authored domain code is required.

## Goals and success criteria

1. Existing natural-language skills run unchanged with decisions disabled.
2. An opt-in configuration enables local/hosted decision cascades internally.
3. Decisions can replace redundant non-final step-completion LLM turns and can
   verify a generative proposal before it reaches tool execution.
4. Failed, malformed, over-context, truncated, or uncertain decision responses
   fall back to generative execution; they never imply successful completion.
5. All provider calls, candidate proposals, gate outcomes, executed tools, costs,
   timings, failures, and final outputs remain available for later analysis.
6. Compare actual Proceda executions on 2–3 SOP-Bench domains against a disabled
   decision baseline with the same SOP, tools, inputs, and primary LLM.

We will report quality and efficiency separately. A quality win means greater
strict all-field task success; an efficiency win means matching success with
lower measured total cost and/or latency, explicitly reporting any opposing
metric regression. A Pareto win must not hide accuracy loss or tail latency
regression. Neither a unit test nor a scripted fixture is model performance.
We will iterate using development examples, freeze settings, and evaluate a
held-out split. Full-domain descriptive results will identify development cases.
If a held-out result is used to tune again, that reuse must be disclosed and
cannot be presented as an untouched independent validation.

## Architecture

### The public surface stays natural language

Authors continue to supply a `SKILL.md` or run Proceda's natural-language
conversion flow. Runtime configuration selects providers and policies. Model
names and thresholds never appear in the SOP. Existing Agent/Runtime interfaces
remain valid; no mandatory new annotations are introduced.

### A capability-specific internal decision service

Add `proceda.decision` with an asynchronous decision client and ordered backend
cascade. Each request contains typed bounded questions plus structured state.
Adapters support TypeSafe's `/v1/systemone` and Ollaya's compatible decision
endpoint. Each backend configuration declares endpoint, model, credential env
name, context guard, timeout, and acceptance thresholds. Model probabilities are
routing signals, not guarantees of empirical accuracy. No credential is included
in request/response audit records or configuration snapshots.

The service distinguishes accepted positive, accepted negative, uncertain,
provider failure, and unsupported input. It records every stage and reason for
fallthrough. An unavailable decision backend does not make ordinary LLM execution
unavailable. A local-only configuration must never secretly call a hosted verifier.

### Receipt-based completion fast path

After app tools return, and only after the entire proposed tool-call batch has
finished, evaluate two bounded obligations against the exact current step and
actual observed receipts:

- Have all mandatory actions and information requirements of this step been met?
- Is normal advancement appropriate, with no unresolved error, required
  clarification, early-exit requirement, or remaining work?

If both clear configured thresholds, synthesize the existing `complete_step`
control transition with a deterministic receipt-based summary. The executor
still runs its existing post-step approval handling. Never optimize the final
step this way: final output generation and extraction remain explicit. Never
perform this check before any app tool has run, on a failed receipt, or after
only part of a batch. Preserve original tool results in context; a generated
summary is not the source of truth.

This replaces an LLM continuation with a bounded decision check. Uncertainty
simply continues the existing LLM loop. No tool is re-executed by this fallback.
The optimization is generic to SOP steps, not tied to selected benchmark IDs.

### Proposal verification and bounded repair

Optionally inspect generative proposals before dispatch. First validate tool
names and argument schemas deterministically. Then ask decision models whether
the selected actions and arguments are grounded in the supplied state and current
SOP step, and whether a proposed completion/final answer satisfies its obligations.
Trusted instructions and untrusted task/tool content are kept distinct.

Rejected proposals are not appended as executed actions. Give the primary or
configured stronger Nebius LLM a bounded repair opportunity with the gate result;
verify the replacement too. No policy accepts the strongest model merely because
it is the last one. Exhaustion is explicit failure/review, not success. Verification
must stay configurable because checking every call may add more latency than it
saves. Compare fast completion alone, verification alone, and their combination
on development data before selecting the final profile.

### Configuration and compatibility

Add optional decision configuration to `ProcedaConfig`. It contains an enabled
flag, ordered decision backends, completion/proposal feature flags, acceptance
thresholds, bounded repair count, and optional stronger LLM configuration. Extend
LLM configuration with an explicit base URL and timeout and honor its existing
credential-environment property, enabling Nebius without global monkeypatches.
Disabled mode must execute the original inference path. New fields default to
disabled and preserve legacy configuration behavior.

### Observability and failure handling

Provider-level audits save exact sanitized requests and responses, usage,
monotonic elapsed time, backend/model, attempt, gate outcome, and failures.
Runtime events link these calls to run and step IDs. All discarded candidates,
repairs, decision calls, and retries count toward reported inference cost.
Use unique per-arm/per-case paths, preserve partial runs, and resume only cases
with authoritative terminal records from the same frozen configuration.

Do not promise universal exactly-once effects or production-grade statistical
calibration in this change. Existing approval and tool-dispatch boundaries stay
in charge of writes. Audit trace retention is essential to identify false positive
completion gates and model/tool/data errors independently.

## Evaluation plan

Initial candidates are patient intake (66 cases; six-tool dependencies), referral
abuse v1 (200 cases; explicit scores and precedence), and email intent (186 cases;
language-heavy routing, pending a current tool audit). Final choice and reasons
will be recorded before paid domain runs. If a candidate has irreconcilable tool
or label problems, record the evidence and select a clean domain before inference.
Do not select domains after observing a favorable hybrid score.

Pin the public SOP-Bench revision and preserve CSV/SOP/tool hashes. Separate
inputs from expected outputs before constructing any model request. Any SOP
conversion/repair must depend only on SOP/tool schemas, occur before comparison,
and produce identical frozen skills for both arms. Existing stale skills must not
make one arm follow different business rules. No benchmark labels in runtime
code, SOP text, context, model routing, or acceptance rules.

Compare:
- LLM-only: original Proceda inference behavior, same Nebius primary model.
- Hybrid: same primary plus configurable local/Jev decisions and optional repair.
- Relevant development ablations, including a stronger LLM-only arm if escalation
  is used, so a gain from extra LLM compute is not misattributed to decision models.

Use deterministic seeded development/held-out partitions and balanced arm order.
Record exact normalized all-field task success, per-field accuracy, execution
completion, actual tool names/arguments/order, model/decision calls and tokens,
end-to-end and inference latency (mean/median/p95), known provider cost, local
inference time, gate acceptance/false acceptance, repairs, and provider errors.
Do not price local compute at a fictitious hosted token rate. Report local compute
separately and explicitly label hosted-cost comparisons.

The legacy harness has substring grading and uncounted fallback extraction.
The paired harness must use strict field equality, deterministic extraction only,
real terminal session status, and complete attempt accounting. The same harness
must evaluate both arms. Preserve raw benchmark scores; any tool/SOP-consistent
analysis is separate and may not replace raw scores to claim a win.

Small smoke runs check adapters and trace integrity. Development runs choose a
profile. Freeze code/config/model revisions before held-out runs. Save all attempts,
not just the winning iteration, and publish a detailed report including failures,
paired differences, uncertainty, and limitations. Keep artifacts outside ignored
ephemeral run folders under `experiments/decision-cascades-2026-10-04/` with manifests.

## Risks and rejected shortcuts

- A decision verifier can be confidently wrong. Inspect gate errors and calibrate
  against complete-task outcomes; never equate a 0.99 score with 99% correctness.
- Small local context windows can silently truncate. Reject or bypass ineligible
  inputs; log truncation responses and do not accept them.
- Verifying every generated action can lose on latency; the completion fast path
  addresses a concrete redundant call in the current executor.
- Context scope and model-call overhead may dominate cheap decision inference.
  Measure the entire case, including network, retries, and final extraction.
- Do not hardcode SOP arithmetic or labels to manufacture a hybrid win. Ordinary
  schema validation is shared infrastructure; semantic improvements must come
  through the same natural-language runtime used by other skills.
- A model/data/tool bug is not a routing success. Preserve all unsuccessful work
  and state honestly if the requested comparative win has not yet been achieved.

## Implementation sequence

1. Save this design and freeze source provenance.
2. Implement provider/config/audit support and generic decision service tests.
3. Wire completion gating and optional proposal verification into the executor.
4. Build strict paired harness; audit selected domains and model capabilities.
5. Run development experiments, analyze traces, and revise generic mechanisms.
6. Freeze final profile, run held-out and full-domain comparisons, produce report.
