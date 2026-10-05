# Bounded business decisions behind natural language SOPs

Design recorded before implementation and new inference, on October 4 2026 Pacific time.
Base branch codex/decision-model-cascades at af88a81; new branch codex/bounded-business-decisions.

## Motivation and scope

The completed Qwen and Jev experiment replaced 605 completion acknowledgments, not
business judgments. Its main Qwen model still classified intent, interpreted policy,
and chose outcomes. The next experiment asks whether Jev can perform those bounded
judgments directly while authors continue to write ordinary natural-language SOPs.

The current branch already contains experimental source-only compilation, strict
receipt bindings, native Choice transport, and constrained generative fallback.
That machinery was disabled in the completed Qwen/Jev comparison. Reuse it, but
remove attribution confounds: business mode must not replace tool proposals or
final rendering, and must not change the existing completion-check eligibility.
No domain-name switches, hand-authored benchmark plans, expected-output access,
keyword classifier, or domain-specific arithmetic solver may enter runtime code.

## Public configuration

Preserve Agent, SKILL.md, and deployment-selected decision backends. Add two
validated planning settings: mode, either full (existing default behavior) or
business_decisions; and native_decisions, a Boolean defaulting to true. The latter
selects native decision-model inference versus constrained main-LLM inference for
the same business plan. It does not disable the completion checker.

Implementation guard added before inference: require_cached defaults to false and
can prohibit compiler calls at task execution. The prepared benchmark sets it true,
so a source/schema/config mismatch safely falls back instead of silently compiling
a different plan or creating an unrecorded preparation charge. This operational
guard does not change compiler cache identity.

In business_decisions mode, only admitted decision steps execute through the plan.
Tool and render operations are not executed by the interpreter. Source-derived
tool contracts may still be retained as non-executing observation contracts so a
later decision can bind the correct earlier tool, arguments, and result fields.
All ordinary tools, approvals, final reporting, and non-admitted steps use the
existing executor. Its completion checks retain the previous eligibility rules.

## Supported decisions and evidence

Support two pure, fully represented forms:

1. A finite choice whose canonical outcomes are explicitly named by the source.
   Include exact outcome provenance and reject accidental word substrings.
2. A Boolean predicate when the original step explicitly asks whether a fully
   defined condition holds. True and false are logical results, not invented
   business labels. An unspecified criterion is not a Boolean decision contract.

A decision step may include an explicitly prescribed, evidenced regex extraction
as already supported. It cannot silently omit a required tool call, communication,
document, explanation, numerical artifact, approval, clarification, or later branch.
Mixed steps remain generative in this iteration. Model inference may apply explicit
source arithmetic, precedence, thresholds, and exceptions to choose a finite result;
this does not authorize executable formulas or invented arithmetic in bindings.
If the SOP separately requires intermediate scores to be emitted, the entire step
is not covered by returning only a label. Independent source review must reject it.

Both native and constrained main-LLM paths receive the ENTIRE original immutable
step as their policy, not only compiler-authored paraphrases. Structured evidence
comes from immutable inputs, recorded clarification evidence where binding supports
it, accepted prior facts, and unique successful receipts matching admitted source
contracts. Missing, failed, duplicated, wrong-entity or wrong-argument observations
cause fallback. LLM summaries do not become authoritative evidence.

For native choice, retain exact label/probability validation, finite ranges, unique
maximum, normalization tolerance and selected-probability threshold. For predicates,
use native Boolean inference with the same selected-outcome probability threshold:
accept true only at p(true)>=0.8, false only at p(false)>=0.8. Uncertainty or invalid
responses fall through to a constrained Qwen call using the same full policy and
evidence. Invalid constrained output returns to the ordinary full-step loop. A
business outcome false, ineligible, or unable to decide is not runtime abstention.

Typed facts retain plan identity, evidence provenance, stage audits and origin.
Predicate facts must be actual Boolean values, preserving false versus zero and
absence. Publish facts only through the existing completed-step lifecycle; retain
recreation/retry protections and discard provisional facts on repair or failure.

## Source compilation under the requested model budget

Use Qwen/Qwen3.8-27B on Nebius, temperature 0, reasoning effort low, max_tokens 2048,
for source compilation and independent source review as well as runtime inference.
Do not inherit the existing implicit 8192-token compiler default for this experiment.
Compilation receives only original SOP text, tool schemas, input FIELD NAMES, and
source-derived prior contracts. No benchmark case values or reference answers enter
compiler or reviewer prompts.

Use a compact source-plan wire form in business mode to avoid copying large source
passages repeatedly into the model output. The harness deterministically attaches
the complete original step text and decision instructions by exact step identity
before strict validation and independent review. Preserve the raw compact response,
expanded candidate, downgrades, review and prompt/version hashes. Nested operations
and references still require exact source quotes. The compiler should emit ordinary
LLM steps where no pure business decision or necessary observation contract exists.
Truncation, invalid JSON, unsupported operations or rejected review cause conservative
fallback; no automatic budget increase, case-driven repair or hidden preparation.
Version the prompt, expansion contract and cache identity so old plans cannot pass
as new artifacts. Existing full-mode compiler behavior remains compatible.

The canonical internal plan remains explicit and inspectable. Deterministic source
attachment does not prove semantic coverage: the separate review sees the expanded
plan and the complete SOP, and runtime bindings still enforce evidence constraints.

### Source-only preparation revision before any task inference

At implementation commit 130edb3, all three initial whole-plan compiler responses
hit the requested 2048-token output limit, including reasoning. No reviewer ran and
no plan was admitted. Preserve source-v1 and its three paid attempts ($0.02278755
at the stated list rates). No task case or reference outcome informed this revision.

Version 2 compiles one compact step at a time in source order, with the same model,
temperature, low reasoning and 2048-token limit on EVERY compiler/reviewer call.
Scope each request to its exact current step and previously admitted source
contracts. Expand against immutable original instructions into a candidate with
the admitted prefix and ordinary LLM placeholders elsewhere. Strictly validate,
then independently review each proposed non-LLM step in original source context.
Malformed, truncated, failed, or rejected steps become ordinary LLM steps before
compiling successors; downstream references to lost contracts cannot be admitted.
There is no automatic retry at a larger budget and no task-driven source repair.

Criteria identify source-named outcomes concisely; the full original policy is
always attached, so criteria need not duplicate every arithmetic rule. Preserve
all per-step raw responses, expanded candidates, reviews and downgrade reasons.
The frozen loader replays the complete admission sequence, requiring successful
review for every surviving optimized step. Zero admitted decisions is unsupported.
Cost includes every step compiler call and every non-LLM step review. The original
full planning mode retains its existing whole-plan preparation path.

Source-v2 at abc6f4d fit the requested budget but admitted zero decisions: email
produced a list where the evidence schema requires a named mapping, and referral
interpreted the ban on executable expressions as also excluding model-evaluated
scoring. Patient retained no business decision. Preserve all 22 requests and
$0.08124525 preparation cost. Before any task inference, prompt version 3 makes
the evidence-object shape explicit and distinguishes source-prescribed numerical
classification from emitting executable code. It also reiterates both identifier
boundaries for regex extraction. No schema, acceptance rule, source SOP, model,
threshold or token budget changes, and no benchmark labels inform this correction.

## Domain expectations to test rather than assume

Email Step 1 is a five-way intent decision plus explicitly prescribed product-ID
extraction. Step 2 contains comparison and communication work, so it generally
remains generative. Referral Step 4 is a candidate finite policy decision over the
observed account and traffic results with explicit scoring and severity precedence.
Whether Jev can implement that policy accurately is an empirical question.

Patient intake mandates the six existing application tools. Its risk calculations
remain delegated to those tools; the interpreter must not replace their execution.
Eligibility wording lacks some explicit thresholds and shares a step with required
registration. Preserve that step rather than inventing an eligibility shortcut.
This domain may have zero native business-decision coverage and is retained as a
limiting case. Existing missing-input clarification behavior and source-schema
mismatches must be preserved, not silently repaired to obtain gains.

## Four arm benchmark and attribution

Reuse the frozen three domains and original 360 validation cases without retuning
on their known labels. Report that these are reused cases, not fresh confirmation.
All runtime Qwen calls use low reasoning, temperature 0 and max_tokens 2048; all
native Jev calls use jev-1.13.0 and threshold 0.8. Completion-only settings, tool
schemas, SOPs, oracle implementations, scoring and failure handling stay unchanged.

A. Qwen only, no compilation or decision models.
B. Qwen plus existing Jev completion checks, planning disabled.
C. Business-decisions plan with constrained Qwen business choices and the same Jev
   completion checks; tool/render optimizations disabled.
D. The identical plan with Jev business choices, constrained Qwen fallback, and the
   same Jev completion checks; tool/render optimizations disabled.

D versus C measures the incremental native business-choice contribution. B versus
A measures completion checking. C versus B measures structured decision execution
and compiler effects. D versus A measures the combined runtime. Do not attribute
all of D's gains to native business decisions.

Compile and review source plans before task execution where practical, freeze one
artifact per domain for both C and D, and retain all unsuccessful preparation.
Preparation cost is separate, never free: report actual once-paid cost and warm
execution, and show the same full preparation cost for either independently deployed
compiled alternative. Do not split it in half merely because C and D share it in
the physical experiment. If preparation falls back or is charged on first use, retain
that fact and actual caller attribution; do not hide it in another arm.

Use development-only compatibility probes first without tuning to validation labels.
Then run 1440 executions, four arms per case, with balanced rotated arm order and
bounded parallel case groups. Freeze all code/config/source before inference. Save
actual ordering, every request/response/usage attempt, facts, fallbacks, tool traces,
final scores, source plans and hashes. Distinguish native choice/predicate calls,
completion checks, constrained generation and ordinary generation in accounting.

## Required validation and interpretation

Tests must cover independent routing controls, unchanged completion behavior in
business mode, full policy parity across native and generative paths, compact-plan
expansion identity, source-only preparation, exact outcomes, Boolean typing, and
uncertain/malformed fallback. Exercise failed/duplicate/wrong-argument receipts,
missing versus false, original policy precedence and required residual obligations.
Keep existing disabled-planning behavior and full-mode tests passing.

Inspect generated plans before the final freeze. Test renamed synthetic policies,
not runtime domain shortcuts. Audit accepted decisions against final task results
and observable source obligations. Count accepted native decisions that actually
replace an LLM call separately from completion bypasses and total hosted requests.
Report accuracy, paired regressions/improvements, cost including preparation and
Jev, latency under concurrency, and unsupported coverage. A lower cost with lower
accuracy is a tradeoff, not an accuracy-preserving improvement. Zero coverage in a
domain is a result, not permission to weaken its source contract.
