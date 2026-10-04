# Source-compiled choice diagnostic

This probe measures a finite decision extracted from an SOP. It does not run the
SOP, execute tools, prepare prose artifacts, or claim end-to-end task success.
There is no LLM fallback during choice evaluation. A backend abstention remains
different from an accepted business label such as `unable to decide`.

The compiler and independent reviewer receive only the frozen skill text, the
allowed source tool schemas, and task input **field names**. Neither receives
task values, example answers, dataset rows, development IDs, or reference labels.
Their full provider audits are retained. A compiler rejection is retained and
does not produce an executable plan.

The evaluator validates the compiled plan against the same source. It accepts
only explicitly listed IDs in the existing development split. Eligible choice
steps must have a reference output field and depend only on inputs, source
literals, or that step's approved regex extractions. Receipt-dependent choices
need a later, separate diagnostic. The shared runtime binder performs input
binding and extraction; this probe contains no domain-specific questions or
classification rules.

All predictions are persisted before the scorer opens the answer-bearing CSV.
Inputs, bound evidence/provenance, predictions, expected values, strict enum
scores, and raw provider attempts have separate files. New run and compilation
directories are exclusive: rerunning a name fails instead of overwriting it.

## Commands

Run from the repository root. These commands make real provider calls only when
explicitly executed; importing the module and running its tests make none.
Use new names for every compilation or evaluation.

```sh
.venv/bin/python -m benchmarks.sop_bench.choice_probe --load-pass compile \
  --domain email_intent --name email-choice-v1 \
  --config experiments/decision-cascades-2026-10-04/configs/baseline-low.yaml
```

`compile` calls the compiler and reviewer only. By default they use the same
model configuration as separate audited calls; `--reviewer-config` can override
the reviewer. Compilation artifacts and audits are under `phase2/plans/NAME`;
the compiler's hash-bound cache is under `phase2/cache`.
Source quotes and model review establish traceability, not proof of semantic
equivalence; inspect generated criteria and regexes before interpreting results.

After checking that the reviewed plan contains an eligible decision:

```sh
.venv/bin/python -m benchmarks.sop_bench.choice_probe evaluate \
  --domain email_intent --run-id email-choice-local-v1 \
  --plan experiments/decision-cascades-2026-10-04/phase2/plans/email-choice-v1/plan.json \
  --config experiments/decision-cascades-2026-10-04/configs/hybrid-local-low.yaml \
  --case-ids E1093 E1105 E1110 E1114 E1121
```

The configuration's `decisions.backends` and thresholds select local Winnow,
hosted Jev, or an ordered combination. To test Jev, use the appropriate existing
profile and add `--load-pass` **before** `evaluate`. Credentials are loaded into
memory using the existing bounded `pass` helper; they are never written to a
credential file. Existing environment credentials are also supported. Programmatic
callers can use `main_async(argv, credentials={...})` on a shared event loop.

`--step N` disambiguates multiple eligible choices. Omitting it is allowed only
when exactly one eligible choice exists. Unsupported, rejected, or ambiguous
plans fail before decision-model inference.

## Interpretation

`summary.json` reports accepted coverage, accuracy among accepted answers,
correct answers over all cases (including abstentions), errors, thresholds,
backend attempts, tokens, known provider charges, unknown usage/charges, and
native local timing. It also inventories missing, unreadable, and unfinished
provider audits. A threshold is an acceptance rule, not a calibration claim.

Compiler/reviewer cost is separate and must be included when assessing an
amortized deployment. Provider charges do not include local hardware/electricity
cost; their estimates use configured token rates, not a billing invoice. Native
local duration comes from raw response audit fields and is marked
unknown when absent. This diagnostic cannot establish whole-workflow savings,
tool fidelity, artifact completion, or a deployable replacement for the LLM.

The five email IDs above were not model-evaluated in the earlier development
runs. They remain development data. The previously completed phase-one holdout
is not an untouched final test for phase two.
