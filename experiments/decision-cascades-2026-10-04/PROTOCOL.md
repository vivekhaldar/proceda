# Paired SOP-Bench protocol

Frozen source preparation: 2026-10-04. This document describes the experiment before domain inference. Unit tests and source audits are not model-performance results.

## Source, scope, and split

The full Amazon Science SOP-Bench source is preserved in `source/SOP-Bench/` from git archive of `f6d43858b94cf439854038101bce0eecd3e16eae`, with its LICENSE and an `UPSTREAM_REVISION` marker. No nested Git checkout is required. The original Proceda baseline is commit `5dfc2b9`; actual runs record their current commit and source-file hashes. Both arms use the same current harness, shared skills, MCP wrapper, primary LLM, task inputs, and upstream tools. The disabled-decision arm follows the original generative path; this is not a comparison between two different SOP authoring systems.

`source-manifest.json` freezes the source SOP, tool code, tool schemas, metadata, labeled and unlabeled CSV hashes, shared skill hashes, and exact development/holdout IDs. The source-derived natural-language skills in `skills/` are identical in all arms. Per-skill provenance records the source-only edits. No benchmark output values or domain-specific decision code were added to skills or runtime.

| Domain | Development | Frozen holdout | Total |
| --- | ---: | ---: | ---: |
| Referral abuse v1 | 40 | 160 | 200 |
| Email intent | 38 | 148 | 186 |
| Patient intake | 14 | 52 | 66 |
| Total | 92 | 360 | 452 |

Splitting is label-blind, deterministic, approximately 20% development and 80% holdout. Its seed is recorded in the manifest. Previously inspected examples are assigned to development before selection. Email cases sharing a product ID stay in the same partition. No training is performed; candidate instructions, model choice, thresholds, and routing policy can be revised using development only. The benchmark and historical skills are public and previously studied, so this is a newly frozen experimental holdout, not a claim of pristine unseen data.

## Domain selection and known limitations

- Referral v1 is a useful finite-decision and tool-gathering workload. Its two classes are reasonably balanced (105 Account Closure, 95 No Action). Its current SOP now gives closure-category precedence. The stale local highest-score wording was replaced with the exact current source rule in a shared skill, before comparison. This is procedural scoring, not a broad test of subjective language understanding.
- Email intent supplies short natural-language classification and conditional evidence retrieval. It was restored in the pinned upstream revision. Four intent classes have 44/46/48/48 cases. Fourteen repeated product IDs have different stored catalog fields across rows, while lookup tools select the first match. Preserve this behavior identically in both arms; do not repair rows after observing a score. Report raw accuracy, and disclose this tool-data ambiguity. Grouping avoids duplicate products leaking across development and holdout, but similar language templates may remain.
- Patient intake exercises six dependent checks and acts as a control where decision routing may save little. The tool results themselves are benchmark lookups. Eighteen rows have blank alcohol consumption and fourteen blank exercise frequency. The existing MCP compatibility bridge replaces empty strings with `N/A`; this same behavior applies to both arms and is included in the recorded harness-source identity. The shared skill corrects two nonexistent prescription input variable references based on source schemas and explicitly reports all six outputs.
- The original mock tool managers may read their labeled CSV to implement prescribed tool outputs. That is part of the benchmark simulator, not permission to expose extra labels to inference. Only declared input columns reach Agent variables; raw expected values are written separately in each case's `expected.json` and used only by the scorer.

## Arms and evaluation order

The required baseline uses decisions disabled. Hybrid profiles enable configurable Jev/local decisions and retain the same primary Nebius model. If a stronger generative fallback is used, include a stronger-LLM-only development ablation. Compare fast completion, proposal verification, and their combination on development, then freeze the selected profile before holdout.

Run every frozen holdout case in each selected arm (360 per arm if all three domains remain selected). `--limit` is for smoke/development work; any limited run is explicitly marked and is not full-holdout evidence. Domain selection precedes model results. Preserve unsuccessful runs and every tuned development configuration. If holdout feedback is used to tune, disclose reuse and do not present a subsequent run as untouched validation.

The CLI runs one named arm at a time. The experiment driver must balance arm order across development repetitions or alternating domain blocks, use the same case concurrency, and record that order in its analysis. Independent simultaneous baseline/hybrid jobs can interfere with local compute and should not be called paired latency measurements. The shared default is one case at a time, with an upper limit of two. Report warm/cold local-model conditions.

## Running and credentials

```sh
.venv/bin/python -m benchmarks.sop_bench.paired_experiment --prepare
.venv/bin/python -m benchmarks.sop_bench.paired_experiment \
  --config experiments/decision-cascades-2026-10-04/configs/baseline.yaml \
  --arm baseline --split dev --domains patient_intake --limit 2 \
  --run-id development-001 --jobs 1
```

The source-preparation command never calls a model. Inference requires explicit config, arm, and run ID. A caller may load secrets with `experiment_credentials.load_pass_credentials({ENV_NAME: password_store_entry})` and pass the returned mapping in memory to `paired_experiment.main(argv, credentials=...)`. It uses a bounded 15-second pass subprocess with Homebrew on PATH, reads the first secret line, and never prints/writes credentials. Existing environment credentials also work.

Each arm receives a separate `runs/<run-id>/<arm>/run.json`, which records configuration, code/source fingerprints, exact IDs, concurrency, and split. Each case has inputs, expected values, incremental events/messages/tool traces, provider audit records, and an authoritative terminal record. The final summary and append-only predictions file refer to these cases. A repeated run requires `--resume` and identical code, configuration, task set, and provenance. Terminal cases are never rerun or overwritten. Partial cases remain intact and require a new run ID to retry. Provider retries occur within one case and remain in provider audit files; the harness performs no whole-case automatic retries.

## Scoring and reporting

Grade exact normalized values for every required field: casefold, trim, and normalize spaces/hyphens to underscores. No substring matching, label-bearing tool-output shortcut, semantic grading, or uncounted model-based output extraction. Only explicit assistant/step-summary structured output is extracted. A noncompleted session cannot score as successful even if partial text contains a correct answer. Keep raw predictions and raw expected values for audit.

Report all-field task success, per-field success, actual terminal completion, paired disagreements, final missing fields, tool names/arguments/order, gate outcomes, repairs, provider errors, hosted tokens/cost, and end-to-end case latency. Provider-level raw records are authoritative for all attempts, including rejected candidates and retries; RunResult token totals are labeled only as reported runtime totals. Sum every stage of a cascade. Report local inference time separately rather than assigning a fictional hosted token price. Separate source/tool-consistent analysis, if any, from raw benchmark scores.

Provide mean/median/p95 latency and statistical uncertainty. With 52–160 holdout cases per domain, small observed differences are not strong evidence of a universal risk bound. A useful result can be a measured efficiency gain with comparable observed task success, a quality gain with its extra expense stated, or a candid finding that a decision cascade did not improve the tested profiles. Unit-test success establishes none of these.
