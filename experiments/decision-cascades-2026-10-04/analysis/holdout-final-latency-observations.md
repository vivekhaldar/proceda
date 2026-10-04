Latency observations recorded 2026-10-04T21:17:14.977473+00:00

This is a partial, read-only observation of the frozen `holdout-final` run. At capture, referral baseline had 35/160 terminal cases, 35 correct. Other arm/domain blocks had not begun. These observations do not establish a final latency effect or causal attribution.

The case slowdown is almost entirely time inside hosted LLM requests. Sum of recorded provider attempt durations nearly equals total case wall time; scheduler/tool/cleanup remainder stays around 0.20 seconds after first-case setup. The remainder is a difference of measured durations, not a separate instrumented CPU-time measurement.

| Referral case | Case wall seconds | Sum hosted-call seconds | Remaining seconds | Calls |
|---|---:|---:|---:|---:|
| [ACC100003](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--referral_abuse_detection_v1/baseline-low/referral_abuse_detection_v1/ACC100003/terminal.json) | 15.011 | 14.142 | 0.869 | 8 |
| [ACC100015](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--referral_abuse_detection_v1/baseline-low/referral_abuse_detection_v1/ACC100015/terminal.json) | 24.119 | 23.919 | 0.200 | 8 |
| [ACC100027](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--referral_abuse_detection_v1/baseline-low/referral_abuse_detection_v1/ACC100027/terminal.json) | 52.720 | 52.522 | 0.198 | 8 |
| [ACC100031](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--referral_abuse_detection_v1/baseline-low/referral_abuse_detection_v1/ACC100031/terminal.json) | 74.544 | 74.341 | 0.203 | 8 |
| [ACC100035](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--referral_abuse_detection_v1/baseline-low/referral_abuse_detection_v1/ACC100035/terminal.json) | 55.807 | 55.611 | 0.196 | 8 |
| [ACC100036](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--referral_abuse_detection_v1/baseline-low/referral_abuse_detection_v1/ACC100036/terminal.json) | 46.135 | 45.932 | 0.203 | 8 |

Each linked case directory contains `started.json`, `events.jsonl`, and full `providers/*.json` timing/usage records. First 28 completed cases all used eight LLM attempts; mean input size stayed approximately 2.7k tokens/request and mean outputs approximately 53–74 tokens/request. The same workload initially took 13–15 seconds/case, rose to 73–75 seconds, then recovered to 46–58 seconds in subsequently finished cases. A monotonic leak is not established.

At the observed process snapshot, driver PID 11929 used 0.1% CPU; Ollaya was idle during this baseline-only block. No provider errors, 429 responses, retries, or destroyed-task warnings were found in the inspected traces. Successful responses sometimes took 13–16 seconds for only 22–58 output tokens.

The stored response JSON includes usage and weight-version metadata, but does not retain HTTP rate-limit headers. `service_tier` and `system_fingerprint` were null. Thus the audits cannot establish whether specific calls exceeded a quota, were deprioritized, or experienced shared-provider load.

Nebius documents dynamic quotas evaluated in 15-minute windows and says over-limit requests may be served at lower priority when spare capacity exists. Such successful calls would carry `x-ratelimit-over-limit: yes`, which these audits do not capture. Provider-side throttling is therefore plausible, not proven. [Official rate-limit documentation](https://docs.tokenfactory.nebius.com/ai-models-inference/rate-limits), accessed 2026-10-04.

No inference request, configuration change, process interruption, or runtime/harness edit was made for this diagnosis. Continue reporting full observed latency; do not remove slow cases or attribute all timing differences to the cascade.

Update 2026-10-04T21:21:25.169630+00:00: 71/160 referral baseline cases are terminal, all correct. Latency has partly recovered without intervention: cases ACC100071–ACC100078 finished in roughly 19–23 seconds, and [ACC100083](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--referral_abuse_detection_v1/baseline-low/referral_abuse_detection_v1/ACC100083/terminal.json) took 11.390 seconds (11.186 in hosted requests; 0.204 outside). This reversal strengthens the evidence against monotonic local scheduler or cleanup accumulation; it still does not identify a particular provider cause.

Update 2026-10-04T21:41:29.895435+00:00: both referral arms are complete at 160/160 correct. The [canonical paired report](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/analysis/holdout-final-referral_abuse_detection_v1.json) records mean case time 27.526s baseline versus 16.074s hybrid, and hosted requests 1280 versus 1093. This is the observed result of sequential arm blocks. The earlier within-baseline drift and recovery remain part of its interpretation; the measured difference does not isolate direct gate savings from provider load, quota, caching, or run-order effects.

Update 2026-10-04T21:56:04.413106+00:00: patient baseline has49/52 terminal cases, all correct. Hosted delays have risen again in this later domain: [P100085](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--patient_intake/baseline-low/patient_intake/P100085/terminal.json) took 123.806s across 14 LLM attempts, of which 123.559s was inside provider calls and 0.247s outside. The three remaining cases were still advancing through successful calls at capture; none had provider errors. This is a second timing-regime change within a baseline block, not evidence of local decision-model overhead because this arm uses no decision model.

Update 2026-10-04T22:00:24.819910+00:00: hybrid patient timing also changes substantially within its block. Selected completed cases:

| Case | Total seconds | Hosted seconds | Local gate seconds |
|---|---:|---:|---:|
| [P100004](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--patient_intake/hybrid-local-low/patient_intake/P100004/terminal.json) | 104.451 | 90.654 | 13.140 |
| [P100008](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--patient_intake/hybrid-local-low/patient_intake/P100008/terminal.json) | 151.474 | 138.005 | 12.768 |
| [P100010](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--patient_intake/hybrid-local-low/patient_intake/P100010/terminal.json) | 27.025 | 14.588 | 11.848 |
| [P100014](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--patient_intake/hybrid-local-low/patient_intake/P100014/terminal.json) | 27.191 | 17.986 | 8.400 |
| [P100016](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/holdout-final--patient_intake/hybrid-local-low/patient_intake/P100016/terminal.json) | 26.786 | 15.941 | 9.676 |

These selected cases accepted no completion gates. The initial >100-second cases spend most of their time in hosted requests, while local checks add measured overhead. Subsequent cases recover to about 27 seconds without intervention. No cause beyond these measured components is established.
