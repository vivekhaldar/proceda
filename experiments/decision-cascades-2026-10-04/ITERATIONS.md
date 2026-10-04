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
