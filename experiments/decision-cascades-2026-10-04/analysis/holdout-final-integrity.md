Integrity check completed 2026-10-04T22:07:38.321585+00:00.

All 720 terminal cases are present across six frozen arm/domain blocks. All declared case IDs, input projections, expected outputs, strict grades, prediction records, event session IDs, and provider-audit manifests match. Runtime/config/source validation issues: **0**.

Every block records execution commit `00ae813f28f67c00dd9276df5866329567348d66` and the same hashes for 48 runtime/runner files. Those hashes match both the execution commit and current files. The complete pinned source/skill/split manifest remains unchanged. Each block matches its expected arm configuration; all primary LLM settings are identical.

Provider audits: **5784**; LLM attempts: **4881**; decision requests sent: **903**. Provider errors: **0**; retry audits: **0**; unknown usage on sent requests: **0**; driver warnings: **0**.

| Domain | Arm | Terminal | Correct |
|---|---|---:|---:|
| referral_abuse_detection_v1 | baseline-low | 160/160 | 160 |
| referral_abuse_detection_v1 | hybrid-local-low | 160/160 | 160 |
| email_intent | hybrid-local-low | 148/148 | 134 |
| email_intent | baseline-low | 148/148 | 138 |
| patient_intake | baseline-low | 52/52 | 52 |
| patient_intake | hybrid-local-low | 52/52 | 52 |

[Machine-readable integrity evidence](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/analysis/holdout-final-integrity.json) includes timestamps, profile/source hashes, model IDs, terminal file hashes, and per-block counts.

These checks do not certify every natural-language SOP obligation. Scalar output mismatches are preserved. Hosted timing varies substantially, and rate-limit response headers were not captured. Charges remain list-price estimates, with cache billing and local TCO unverified. Later `ollaya ps` snapshots show only e4b resident; the frozen profile’s assumption that both installed local models remained resident is not supported throughout the run.
