# Business-decision routing experiment

Run from the repository root after code/config freeze and explicit inference authorization.
These commands use the existing credential helper: secrets remain in process memory and are
not written to a shell script, environment file, or experiment artifact.

Prepare all three source plans concurrently. Within a domain, compile each of its N original
steps sequentially and independently review each structurally admitted non-LLM step: N compiler
calls and at most N review calls. Every call uses low reasoning, temperature 0, and max 2,048
tokens. Requests contain the full original source, tool schemas, input field names, current
step index, and admitted prefix; no task values or labels. Malformed/rejected steps become
ordinary LLM fallbacks. Every attempt remains in the preparation directory.

```sh
.venv/bin/python -m benchmarks.sop_bench.prepare_business_plans \
  --config experiments/decision-cascades-2026-10-04/business-decisions-080/configs/business-qwen.yaml \
  --output-dir experiments/decision-cascades-2026-10-04/business-decisions-080/preparation/source-v2
```

The earlier source-v1 preparation remains preserved: three truncated compiler requests, zero
reviewer calls, zero admitted business decisions, and $0.02278755 estimated cost. Include this
failed preparation in cumulative experiment spend, separately from v2 deployment and warm-case
costs; see `analysis/source-v1-failure.json`. Do not overwrite or retry that directory.

Inspect the source plans and review results before starting task cases. Do not adjust plans
against validation labels. The same frozen preparation is used by both business arms. Missing,
changed, or mismatched artifacts cannot trigger task-time compilation. A runtime mismatch
preserves the current case evidence and stops the schedule.

The following shell arrays are compatible with Bash and Zsh:

```sh
business_exp=experiments/decision-cascades-2026-10-04/business-decisions-080
business_arms=(
  --arm "baseline=$business_exp/configs/baseline.yaml"
  --arm "completion=$business_exp/configs/completion.yaml"
  --arm "business-qwen=$business_exp/configs/business-qwen.yaml"
  --arm "business-jev=$business_exp/configs/business-jev.yaml"
)
business_prepared="$business_exp/preparation/source-v2/manifest.json"

# Four frozen development cases per domain, 48 total executions, balanced arm positions.
.venv/bin/python -m benchmarks.sop_bench.run_compiled_schedule \
  "${business_arms[@]}" --prepared-plans "$business_prepared" \
  --run-id business-decisions-080-pilot --split dev --jobs 4 \
  --case-ids "$business_exp/pilot-case-ids.json"
```

After pilot review and authorization, run all 360 original validation cases in every arm.
They are reused validation, not unseen confirmation. Three processes each allow four active
case groups; each case executes its four rotated arms sequentially. The total is 1,440
executions: 640 referral, 592 email, and 208 patient. No `--limit` flag is used.

```sh
business_pids=()
for business_domain in referral_abuse_detection_v1 email_intent patient_intake; do
  .venv/bin/python -m benchmarks.sop_bench.run_compiled_schedule \
    "${business_arms[@]}" --prepared-plans "$business_prepared" \
    --run-id "business-decisions-080-holdout--$business_domain" \
    --domains "$business_domain" --split holdout --jobs 4 &
  business_pids+=("$!")
done
business_exit=0
for business_pid in "${business_pids[@]}"; do
  wait "$business_pid" || business_exit=1
done
test "$business_exit" -eq 0
```

Each schedule writes under `../phase2/runs/`; the existing directory name does not enable
tool or rendering optimization. The four profiles control behavior explicitly. Preparation
is paid once physically. Add its full relevant cost to either independently deployed
business alternative, never half per arm. When combining pilot and full runs, deduplicate
preparation by manifest identity and domain; the same original preparation is not paid again.
Case latency includes cache loading and shared-provider contention. Source-preparation wall
time and each domain's preparation time are reported separately.

Useful offline checks before inference:

```sh
.venv/bin/pytest tests/test_benchmarks/test_prepare_business_plans.py \
  tests/test_benchmarks/test_compiled_schedule.py -q
.venv/bin/ruff check benchmarks/sop_bench/prepare_business_plans.py \
  benchmarks/sop_bench/run_compiled_schedule.py \
  tests/test_benchmarks/test_prepare_business_plans.py \
  tests/test_benchmarks/test_compiled_schedule.py
```
