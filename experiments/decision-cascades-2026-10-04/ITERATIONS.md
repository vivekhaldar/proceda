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
