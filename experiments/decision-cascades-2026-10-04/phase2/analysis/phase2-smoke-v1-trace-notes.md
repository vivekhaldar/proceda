# Phase-2 smoke: provisional attribution notes

The schedule is still running. These are concrete trace observations, not an aggregate result or a claim of broad improvement. No inference was performed for this review.

## Referral source plan

The accepted source-only plan `71f7db077d9ddb9cd005b442d9eae5834cc6b2bacab9a96b52548de0b5af1b35` compiles steps 1–3 into exact tool proposals. It retains the field-recording clauses as residual obligations in steps 1–2. Steps 4–5 remain LLM work: calculating the violation scores, selecting the enforcement action, and producing the final report. The plan contains no business choice and no compiled final renderer.

Source: `../runs/phase2-smoke-v1/plan-cache/71f7db077d9ddb9cd005b442d9eae5834cc6b2bacab9a96b52548de0b5af1b35.json`.

## Concrete first-case attribution: ACC100030

All three completed arms below returned the strictly correct final field.

| Arm | Execution hosted calls | Cold compiler/reviewer calls | Native calls | Case seconds | Preparation seconds |
|---|---:|---:|---:|---:|---:|
| baseline | 8 | 0 | 0 | 173.13 | 0 |
| compiled-LLM | 5 | 2 | 0 | 138.28 | 43.79 |
| compiled-local | 5 | 0 | 1 | 70.28 | cache-hit overhead |

Three `planning.action` tool events explain the compiled-LLM path's three removed proposal-generation calls. Those are compiler/binding savings, not decision-model savings. The compiled-local path made exactly the same five execution LLM calls as compiled-LLM and added one local completion check. That check abstained: `obligations_satisfied=0.9373`, `normal_advance_permitted=0.8642`, below the 0.97 threshold; the audited check took 1.546 seconds. It removed no additional LLM call.

Compiled-local's lower wall time therefore cannot be credited to skipping an LLM call. Even subtracting measured cold preparation, compiled-LLM took about 94.49 execution seconds versus about 70.28 for compiled-local. The difference illustrates changing provider response times within the interleaved schedule, not demonstrated native-decision benefit.

Sources: each arm's `referral_abuse_detection_v1/ACC100030/phase2-observation.json`, `events.jsonl`, and full `providers/` audits under `../runs/phase2-smoke-v1/`. The local decision audit is `compiled-local/referral_abuse_detection_v1/ACC100030/providers/decisions/decision-b2008ecdc75b4abd93fc11fd16e722a9.json`.

## Final analysis method

`analyze_smoke_attribution.py` refuses to produce aggregate output without the completed schedule marker and all terminal records. It distinguishes actual compiler/reviewer attempts by the frozen prompt hashes, ordinary and bounded-choice LLM calls, native choices, Boolean completion checks, recorded plan actions, and fallback events. Cold compilation remains charged to its actual first caller and separately reported as a shared one-time cost. Full input list-price assumptions, unknown usage, and local-compute exclusions come from the existing all-attempt accountant.
