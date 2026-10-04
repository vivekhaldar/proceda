# Profile frozen before held-out inference

Frozen on 2026-10-04, after development and before any holdout inference.
Implementation commit: `161742d`. Each actual arm manifest records its execution
commit, source hashes, exact inputs, and full effective configuration.

## Comparison

- Baseline: `configs/baseline-low.yaml`, decisions disabled.
- Candidate: `configs/hybrid-local-low.yaml`, Winnow e4b in Ollaya, completion
  checks only, threshold 0.97, 18,000-character input guard, 45-second timeout.
- Both: Nebius `zai-org/GLM-5.3-Flash`, explicit low reasoning effort,
  temperature 0, maximum 2,048 output tokens, identical skills and tool bridge.
- Four concurrent cases within each arm, a single asyncio loop, sequential arm
  blocks. Arm order rotates by domain: baseline/candidate for referral,
  candidate/baseline for email, baseline/candidate for patient.
- Every frozen holdout case: 160 referral, 148 email, 52 patient, **360 per arm**.
  No `--limit`, no whole-case retries, and no exclusion based on outcomes.
- Both local models remain resident as in development, but only e4b receives
  candidate requests. No concurrent model experiment will run during this test.

## Selection rationale

The eight-case-per-domain development comparison passed 24/24 in the baseline
and 24/24 with e4b. The candidate removed 11 hosted calls through reviewed,
accepted referral completion checks. Estimated hosted charges fell 3.36%, while
mean whole-case latency rose from 15.78 to 22.26 seconds. This is a hypothesis of
lower API expense with comparable observed correctness, **not a prediction of
a joint latency and cost win**. Local hardware and energy costs are unmeasured.
The full holdout's domain weights follow the original frozen dataset, not the
balanced development smoke sample.

Jev was exercised in actual development cascades and saved traces, but provided
no additional accepted completion in the tested formulation. Removing that
stage avoids its expense and latency. Winnow 12B accepted more checks but was
slower, had one end-to-end email error, and accepted two patient steps lacking
required documentation despite passing scalar benchmark labels. It is not
selected. None of these observations establish calibrated risk bounds.

The final proposal-verification ablation used two development cases per domain
in baseline, verifier-only, and combined modes. Both hybrids passed referral and
email but failed both patient cases. Strict schema validation rejected source
patient IDs such as `P100002`: the source tool schema requires `^P[0-9]{9}$` while
the dataset supplies six digits. The original tool implementation accepts these
IDs. Repaired proposals could not be verified within the configured context
guard, so the runtime failed explicitly. We preserve this source inconsistency
and the failures; we do not weaken the schema or change the frozen skills to
improve a score. Verification demonstrated no benefit on this development set
and remains an opt-in feature, excluded from the final candidate.

One generic presentation fix follows the 24-case development comparison:
synthetic completion summaries now contain a plain acknowledgment. Gate
authority and receipt IDs stay in structured events, preventing LLM summaries
from copying authority claims or inventing receipt IDs. This is covered by a
context-level regression test. Full checks pass: 494 tests, Ruff, and types.

## Decision rules and reporting

The initial design's success criteria remain unchanged: report strict task
success, API-cost estimates, and latency separately. An efficiency claim requires
comparable observed correctness and lower API expense and/or latency, with every
opposing regression disclosed. Neither nonsignificance nor a 0.97 gate threshold
proves accuracy equivalence. Report paired outcomes and uncertainty, all failure
costs, local inference time, and missing accounting data. Estimated API costs use
the documented input/output list prices, not an account invoice or total TCO.

No settings will be tuned while this run is executing. If its outcomes motivate
another implementation/profile, label that later work as using holdout feedback;
do not present a reused split as untouched validation.

```sh
.venv/bin/python -m benchmarks.sop_bench.run_paired_schedule \
  --run-id holdout-final --profiles baseline-low hybrid-local-low \
  --split holdout --jobs 4
```

Raw artifacts will be under `runs/holdout-final/` and
`runs/holdout-final--<domain>/<arm>/`. The source and split remain those in
`source-manifest.json`; no domain has been added or dropped after observing scores.
