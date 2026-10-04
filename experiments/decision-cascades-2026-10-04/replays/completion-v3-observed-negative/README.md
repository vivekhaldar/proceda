# Observed completion-gate diagnostics

These two fixtures preserve actual **development-set** states and questions from `dev-low-8`, with the original winnow:12b probabilities and source-audit SHA-256. Both source cases passed the benchmark's six output-field checks. No model was called to create these files.

The reviewer expectation is **abstain or reject**, because Step 5 asks to document any special handling requirements while the tool returns only `pharmacy_check: yes`. It does not report requirements or say that none exist. This is a diagnostic judgment about missing evidence, not benchmark ground truth or proof that special requirements actually exist. The directory name “observed-negative” refers to this review expectation.

For any later authorized replay, send only `state` and `questions` to the decision service. Keep `review`, `observed`, and `provenance` outside model input. Preserve the originals and save replay outputs separately. These fixtures do not modify SOP-Bench scores and must not be mixed into scored task results or called holdout examples.

The source audits were already sanitized; the copies additionally pass through the runtime audit sanitizer. Exact state/question equality to the source was checked.

| Fixture | Original acceptance probabilities | Source audit |
|---|---|---|
| [P100012, Step 5](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/replays/completion-v3-observed-negative/P100012-step-5.json) | obligations 0.9739; advance 0.9764; threshold 0.97 | [Full audit](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/dev-low-8--patient_intake/hybrid-local-12b-low/patient_intake/P100012/providers/decisions/decision-9bf365661d5e4db8a475c3e2cf5e657e.json) |
| [P100015, Step 5](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/replays/completion-v3-observed-negative/P100015-step-5.json) | obligations 0.9724; advance 0.9718; threshold 0.97 | [Full audit](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/dev-low-8--patient_intake/hybrid-local-12b-low/patient_intake/P100015/providers/decisions/decision-3d5263ed07ae4ee5b3378466ac9912d7.json) |
