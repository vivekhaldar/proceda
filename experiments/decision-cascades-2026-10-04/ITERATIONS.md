# Development record

This record is chronological. Unsuccessful profiles and raw artifacts are retained.
Only the frozen development partition may guide changes before holdout evaluation.

## Initial implementation — 970cc99

The design preceded code (1316fbe). The first runtime implemented receipt-based
completion and optional proposal verification, with local/hosted backends and
per-attempt audits. The primary LLM is Nebius GLM-5.3-Flash, selected from the live
catalog after successful tool-calling smoke tests. Toy provider smoke latency is
not treated as a domain-performance measurement.

The first experiment ran two development referral cases per arm, in this order:
baseline, Jev completion checks, local Winnow e4b followed by Jev. All six executions
passed strict scoring. Every completion gate abstained, so no LLM continuation was
replaced. Observed differences in LLM time or tokens cannot be attributed to a
successful fast path. Full records are in `runs/dev-initial-referral/`.

Inspection of the decision input found avoidable ambiguity: generated step
instructions requested a not-yet-written completion summary; a tool receipt was
not explicitly described as already recorded; and qualified MCP tool names differed
from the SOP's names. The next candidate removes generated conversation from the
completion gate, supplies original parsed receipts and explicit tool aliases from
the MCP registry, and asks about business work remaining before the runtime's
acknowledgment. It retains the 0.97 threshold. This is a generic harness change;
the SOPs and primary LLM are unchanged. Saved development receipts and deliberately
incomplete counterexamples will test this formulation before larger runs.

The initial runs also exposed an existing asynchronous cleanup race: the session
became terminal before MCP disconnection completed, then the synchronous Agent
wrapper closed its event loop. The paired harness now drains the public session
event stream through its cleanup sentinel before recording completion. The change
applies to both arms, and later latency includes completed cleanup. Initial timings
are retained but are not pooled with corrected-harness timings.

## Artifact retention

The complete upstream source, shared skills, split manifest, configs, implementation,
and analyses are versioned in Git. Large raw experiment runs stay in the local
`runs/` directory, excluded from Git to avoid committing repeated prompts and tool
responses. They are not deleted. Each arm records its code commit and source
hashes, and each case retains inputs, expected outputs separately, events, tool
calls/results, provider attempts, and terminal output. Cost analysis uses every
provider attempt; missing usage remains unknown rather than a zero-dollar claim.

## Normalized receipts — e525ef4

The corrected-harness smoke used six distinct development inputs, each run in
three arms, with one case at a time and rotating arm order across domains. All
18 executions passed. The local→Jev profile reduced referral LLM calls from 16
to 12 through four recorded auto-completions. No email or patient completion
was accepted, and Jev accepted no gate in any domain.

Across six cases per arm, list-price API charges were $0.03826725 for the
baseline and $0.035642532 for local→Jev (6.86% lower). Mean case latency was
18.080 versus 18.699 seconds (3.42% higher). This is an early cost signal,
not a demonstrated joint cost-and-latency win: the sample is small and local
work and unsuccessful hosted checks add overhead. A separate patient LLM-call
difference occurred without a gate acceptance and is generation variation,
not credited to the optimization. Full analyses are in `analysis/dev-v2-*.json`.

Decision-only replay of six real completed receipt sets plus seven deliberately
incomplete counterexamples accepted 4/6 positives with Winnow and 0/6 with Jev,
and falsely accepted 0/7 negatives for either provider. The local wrong-tool
counterexample scored 0.9694, just below 0.97, so the threshold was not lowered.
These diagnostic fixtures are not counted as successful benchmark tasks.

The next profile retains the local backend and falls back directly to the LLM
when it abstains, eliminating a hosted stage that showed no incremental benefit
on development. Jev support and both earlier profiles remain available. Before
the larger run, receipt provenance is being hardened against reused provider
tool-call IDs, with actual human answers retained separately. A four-case
concurrency development probe will precede any holdout concurrency decision.

## Concurrency probe and lower-effort baseline

`dev-local-8` was aborted after its eight baseline referral cases completed
correctly and the hybrid arm had started four cases. LiteLLM's process-global
logging worker repeatedly replaced active task state as the threaded harness
switched between concurrent event loops. The installed library code confirmed
that race. All eight baseline cases used exactly eight successful provider calls,
with no retries; their 62.65–98.26 second case times therefore cannot be explained
by extra calls. Provider queueing may also have contributed, so the logging issue
alone is not claimed to explain the slowdown.

The probe and partial hybrid traces remain in place with an `aborted.json` marker.
Finished requests have audits; requests in flight at termination may have
unreported usage. This prompted write-ahead audit records so future interrupted
attempts preserve their sanitized request and explicit unknown-cost status.
The harness now uses one asyncio loop across its schedule and a semaphore for
case concurrency. The aborted probe is excluded from comparative conclusions.

The GLM upstream model card documents a default maximum reasoning effort and
supports `low`, `high`, and `max`; the hosted endpoint's unspecified default was
not independently established. Nebius documents the explicit `reasoning_effort`
field. New `*-low.yaml` configurations set `low` identically in both arms to test
a more efficient baseline. With installed LiteLLM 1.82.0, forwarding this field
for the OpenAI-compatible GLM model also requires explicitly allowing the
parameter. Earlier configurations and observations remain unchanged.

A separate replay with the installed larger local model, Winnow 12B, accepted
6/6 real completed receipt sets and falsely accepted 0/7 incomplete fixtures.
Its warm calls averaged 1.31 seconds, versus roughly half a second for Winnow 4B;
that coverage/latency tradeoff needs actual SOP evaluation. Both models can remain
resident on this 64 GiB machine. This diagnostic result is not task accuracy.

## Single-loop and explicit-low-effort comparisons — 85ba6a0

The four-referral-case effort probe completed without logging-worker warnings.
Unspecified effort and explicit low effort both passed 4/4. Mean case time was
47.04 versus 20.09 seconds; estimated hosted charges were $0.02055755 versus
$0.013782. Completion-token counts were 10,807 versus 1,851. These are small,
sequential live measurements, not a causal attribution of every time difference
to reasoning effort. Subsequent comparisons set low effort in both arms.

`dev-low-8` ran eight development cases in each of three domains in three arms,
with four concurrent cases per arm and sequential, rotating arm blocks. All
72 cases completed, without provider errors/retries or logging-worker warnings.
Baseline and local 4B each passed 24/24; local 12B passed 23/24. Estimated hosted
charges were $0.12048330, $0.11643525, and $0.10792290, respectively. Hosted calls
were 204, 193, and 176. Mean whole-case times were 15.78, 22.26, and 31.60 seconds.
Neither hybrid demonstrated a general latency win. Local native inference time
was 105.16 seconds for 4B and 394.08 seconds for 12B, excluding idle capacity.

The 4B gate accepted 11/78 checks, all referral read steps; independent receipt
review found the expected tools, arguments, result fields, and successful
execution in all 11. Email and patient gates all abstained. The 12B gate accepted
29/79 checks: 24 referral and five patient. Two patient pharmacy steps lacked
the separately requested special-handling documentation despite acceptance.
Scalar benchmark passes do not establish completion of that prose obligation.
Email E1086's 12B-arm error occurred in the primary LLM before a gate could
intervene; its only completion gate abstained. It remains an end-to-end failure.

Inspection also found that primary LLMs copied synthetic gate-approval claims
and sometimes invented receipt identifiers in later completion summaries.
Actual structured receipts and gate events were correct. Commit 161742d replaces
the model-visible synthetic summary with a plain acknowledgment; authority and
receipt links stay in structured events. A regression test checks the next LLM
context as well as the authoritative events. Full checks pass: 494 tests, Ruff,
and type checking. These changes precede held-out inference.

The remaining development ablation, `dev-verification-smoke`, compares a fresh
baseline with proposal-verification-only and completion-plus-verification using
two cases per domain. Both candidates retain the same 4B model, threshold,
primary LLM, reasoning effort, and repair limit. This tests the optional feature
before final profile selection; no holdout case has been used for tuning.

## Verification results and held-out freeze — 161742d / 00ae813

The verification ablation completed all 18 executions. Baseline passed 6/6;
verification-only and combined candidates each passed 4/6. The two patient cases
failed in each hybrid because the source tool schema requires nine-digit patient
identifiers while the dataset and lookup implementation use six digits. Schema
validation rejected those proposals; repair contexts then exceeded the configured
input guard. No patient app tool executed. These failures, repaired proposals,
and expenses are preserved; the source schema was not weakened to improve scores.

`FINAL_PROFILE.md` was committed at `00ae813` before the full held-out run. It
selects local e4b completion only, keeps proposal verification disabled, and
compares it with the same explicit-low-effort LLM-only profile. The schedule uses
all 360 frozen cases per arm, with no limit or outcome-based exclusions. Runtime,
skills, provider settings, and benchmark execution code remained frozen throughout
the schedule. Later changes are offline accounting, tests, review, and reporting.

## Cache-rate sensitivity and durable trace packaging

The offline analyzer now records cached and uncached token counts, checks the
provider's cache-field aliases for consistency, and computes hypothetical cache
rates of zero, 20%, and 100% of the ordinary input price. The actual cached-input
tariff is unverified. Full-rate costs remain unchanged; all 26 regenerated
canonical development analyses were checked against their earlier counts,
outcomes, latencies, and original cost metrics. Under a hypothetical free-cache
tariff, the 24-case e4b development cost advantage reverses. This limitation is
reported rather than assuming cache hits are billed at full rate.

Raw run directories remain intact. An offline archive utility creates immutable
compressed copies with per-member SHA-256 manifests, refuses unfinished groups,
and permits the aborted probe only with explicit identification. Exact known
credential scans run in memory before packaging. Development archives were
verified and a fresh restoration reproduced counts and full accounting. No
model calls or runtime mutations are part of this packaging process.

## Completed frozen holdout — 720 executions

Both arms completed all 360 frozen cases. Baseline passed 350/360 and the local
completion candidate passed 346/360. Referral passed 160/160 in each arm; the
candidate accepted 187/480 checks, removed 187 hosted calls, and reduced its
full-rate API estimate 15.39%. Email passed 138/148 versus 134/148, with zero
accepted checks and the same 555 hosted calls. Patient passed 52/52 in each arm,
also with zero accepted checks and the same 699 hosted calls.

The full-rate aggregate estimate fell from $1.316093 to $1.23046505 (6.51%), and
mean observed case time fell from 19.873 to 15.196 seconds (23.53%), alongside
four additional strict failures. Sequential arm blocks and substantial provider
timing changes confound latency attribution. The requested broad
accuracy-preserving joint win was not achieved. The narrower referral result
supports selective enablement; no retrospectively combined policy is presented
as another executed experiment.

No runtime, skill, source, or provider-configuration changes were made during
the held-out schedule. A later read-only residency check showed only e4b, so the
freeze's continuous-two-model residency assumption was not established; no manual
loading intervention was made. Native loading durations remain in accounting.
All 720 trace structures and 5,784 provider audits passed independent checks,
with no provider errors, retries, unknown usage, or driver warnings. The final
archive's fresh restoration reproduced the original results and accounting.
All seven archives retain 15,754 files. Final repository checks passed: 505 tests,
Ruff lint/formatting, and types. See `REPORT.md` for all results and limitations.
