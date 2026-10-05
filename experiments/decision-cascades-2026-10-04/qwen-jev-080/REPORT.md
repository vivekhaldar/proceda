# Qwen and Jev completion checks in Proceda

Prepared for Vivek Haldar on October 4, 2026, Pacific time. This report covers the requested repeat with Qwen 3.8 27B as the main LLM and hosted Jev as a completion checker, with both yes probabilities required to reach 0.8.

**Jev removed 605 LLM calls, a 23.8% reduction, and reduced the estimated combined API charge by 20.0%.** The savings came from referral abuse and patient intake. Email gained no avoided calls. Final-task correctness was **357/360 for Qwen-only and 356/360 for Qwen plus Jev**. The one additional error occurred in email after a rejected completion check, so it was not produced by an accepted check skipping a step. The aggregate does not establish accuracy equivalence or an improvement across every domain.

All 720 requested executions finished. The comparison reused the same original 360 validation cases: 160 referral, 148 email, and 52 patient. The separate 12-execution development pilot also completed, but is excluded from the results below. The full comparison took about **10 minutes 18 seconds** using three concurrent domain processes and up to four case groups per process. Each case's two arms ran sequentially, with the first arm alternating across cases.

## Configuration and scope

| Setting | Qwen-only baseline | Qwen plus Jev |
|---|---|---|
| Main model | `Qwen/Qwen3.8-27B`, Nebius Token Factory | Same |
| Generation | Temperature 0, reasoning effort low, max output 2,048 tokens | Same |
| Completion checker | Disabled | Hosted `jev-1.13.0` |
| Acceptance | Ordinary LLM continuation | Both yes probabilities ≥ 0.8 |
| Proposal verification | Disabled | Disabled |
| SOP compilation | Disabled | Disabled |
| Checker timeout and character guard | Not applicable | 45 seconds and 18,000 characters |

The SOPs, tool implementations, case split, strict scorer, completion questions, receipt evidence, and fallback logic are unchanged. This is the earlier completion-only experiment; the newer SOP compiler contributes no savings here. The main LLM still selects tools, handles interpretation and reasoning, and produces the final answer. Jev checks whether an executed tool batch has already fulfilled the current non-final step and permits normal advancement. On an accepted check, Proceda issues its existing `complete_step` transition. A negative or uncertain check returns control to Qwen.

The domains retain their earlier roles: referral exercises retrieval followed by explicit rules, email exercises language classification and catalog comparison, and patient intake exercises dependent tool calls plus documentation obligations. These public cases have already been inspected, so this is reused validation rather than an untouched confirmation set.

The frozen implementation commit is `9ec2e979cbc24903115bf26dd2acaa068049fad2`. The [protocol](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/qwen-jev-080/protocol.json), [baseline configuration](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/qwen-jev-080/configs/baseline.yaml), and [hybrid configuration](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/qwen-jev-080/configs/hybrid.yaml) contain the exact settings. Changing the main LLM, checker model, and threshold together means comparisons with the older GLM/Winnow experiment cannot isolate any one of those changes. The paired comparison here isolates adding the configured Jev completion path to Qwen.

## Accuracy and LLM calls

Accuracy requires the run to finish and every required final field to match the original strict scorer. It does not certify every prose obligation in the SOP.

| Domain | Qwen-only correct | Qwen plus Jev correct | LLM requests | Fewer LLM requests |
|---|---:|---:|---:|---:|
| Referral abuse | 160/160 | 160/160 | 1,280 → 800 | 480 (37.5%) |
| Email intent | 145/148 | 144/148 | 558 → 558 | 0 (0.0%) |
| Patient intake | 52/52 | 52/52 | 699 → 574 | 125 (17.9%) |
| **Total** | **357/360 (99.17%)** | **356/360 (98.89%)** | **2,537 → 1,932** | **605 (23.8%)** |

All 605 accepted checks have matching automatic-completion events, with no intervening LLM generation. Their sum exactly matches the reduction in actual LLM requests. There were no retries or provider errors to explain the difference. The audit distinguishes these direct bypasses from mere variation in generation length or unrelated changes in request counts.

**Fewer LLM calls does not mean fewer total hosted requests.** Adding 906 hosted Jev checks increases total inference requests from 2,537 to **2,838**. The Jev requests are much cheaper than the LLM turns they replace, and the measured total cost and mean case latency still fall.

## Where Jev removed work

| Domain and step | Checks | Accepted checks and avoided continuations |
|---|---:|---:|
| Referral 1 — investigate account | 160 | 160 |
| Referral 2 — analyze traffic patterns | 160 | 160 |
| Referral 3 — retrieve enforcement guidelines | 160 | 160 |
| Patient 3 — calculate lifestyle risk | 52 | 37 |
| Patient 4 — calculate overall risk | 52 | 52 |
| Patient 6 — register patient | 52 | 36 |
| Patient 1, 2, 5 — insurance, prescription benefits, pharmacy | 156 | 0 |
| Email 2 — catalog evidence and action | 114 | 0 |
| **Total** | **906** | **605** |

There were also 176 negative decisions and 125 uncertain decisions. Both outcomes used the ordinary LLM continuation.

### Referral

Each baseline case used eight LLM requests; each hybrid used five. All three required application tools still ran, with the same account bindings and retained results. Jev replaced the three follow-up LLM acknowledgments after retrieval. Scoring the evidence and selecting the final enforcement action remained Qwen's work.

For example, in `ACC100003`, the three pairs of yes probabilities were 0.96/0.94, 0.95/0.91, and 0.96/0.92. Each exceeded 0.8. The runtime advanced after each recorded retrieval, and both arms returned the correct No Action result. [Hybrid trace](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/phase2/runs/qwen-jev-080-holdout--referral_abuse_detection_v1/hybrid/referral_abuse_detection_v1/ACC100003/events.jsonl).

### Patient intake

The 125 saved continuations followed lifestyle-risk calculation, overall-risk calculation, and registration. Every case still executed all six required tools in order. Result propagation from earlier tools into later calls and the final reported fields was checked separately from final accuracy.

No insurance, prescription-benefit, or pharmacy check was accepted. Their SOP steps include documentation requirements that a returned status alone does not demonstrate. For overall risk, the SOP explicitly delegates calculation to `calculateOverallRisk`; absence of the tool's internal weights is not by itself evidence of premature completion. The simulated tool result still cannot establish clinical validity.

The original inputs contained missing lifestyle values. In 23 cases per arm, the main model requested clarification before using `"None"`; eight accepted hybrid lifestyle checks involved such cases. The recorded answers were included in Jev's evidence. **These were not unsupported values invented by the model.** However, the benchmark's automatic human interface selected the first offered option; there was no real patient verification or human waiting time. That limits what the experiment establishes about real deployments. The initial input-only review was superseded by the [review including clarification evidence](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/qwen-jev-080/analysis/patient-input-review-v2.json).

### Email intent

Jev rejected all 114 completion checks, so it removed no calls. The step includes comparisons, action selection, and response or documentation work beyond retrieving a catalog record. Cases using a generic branch without an app-tool batch did not invoke this checker.

Three email cases failed in both arms: `E1102` classified a bundle-pricing question as a pricing complaint; `E1254` treated an inventory-sync question as a listing-status concern; and `E1256` requested clarification for an underspecified description change where the benchmark expects an update.

The sole additional hybrid failure was `E1076`: the seller said the product has five attachments rather than four, while the catalog returned only “Food processor.” Baseline selected update description; hybrid selected no action. Jev had rejected completion with probabilities 0.27/0.13, and Qwen continued normally. The strict regression is retained. This trace supports a generation/interpretation difference, not an accepted gate skipping the comparison. Temperature zero does not make separate provider executions identical. [Hybrid trace](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/phase2/runs/qwen-jev-080-holdout--email_intent/hybrid/email_intent/E1076/events.jsonl).

## API cost and observed latency

| Domain | Estimated API charge | Cost change | Mean case latency | Latency change |
|---|---:|---:|---:|---:|
| Referral abuse | $2.9693 → $2.0474 | -31.0% | 8.70s → 6.66s | -23.4% |
| Email intent | $1.3452 → $1.3470 | +0.1% | 5.24s → 5.35s | +1.9% |
| Patient intake | $2.1184 → $1.7551 | -17.2% | 14.74s → 13.63s | -7.5% |
| **Total** | **$6.4329 → $5.1495** | **−20.0%** | **8.15s → 7.13s** | **−12.6%** |

The hybrid's charge comprises **$5.0900142 for Qwen and $0.059465448 for Jev**. These are usage-based estimates, not invoices: Qwen input/output rates are $0.45/$3.00 per million tokens; Jev input is $0.042 per million and output is free. Rates were checked against the [Nebius catalog](https://tokenfactory.nebius.com/endpoints?search=Qwen3.8-27B) and [Jev documentation](https://docs.typesafe.ai/models).

Cached-input billing was not independently verified. The headline estimate prices all input at the ordinary input rate. Hypothetical cached-input rates of 0%, 20%, and 100% of the ordinary rate leave aggregate savings of **10.7%, 14.6%, and 20.0%**, respectively; these are sensitivity scenarios rather than billed charges. Smaller parameter count did not imply a lower list price: Qwen's listed rates are higher than the GLM Flash rates used for the previous report.

Overall median latency changed from 7.78s to 6.11s; p95 changed from 15.15s to 14.40s. These are whole-case observations under concurrent load, including tool setup and cleanup, Jev calls, and clarification automation. Alternating arm order improves comparability, but shared-provider contention and timing variation remain. The results do not isolate service latency or establish a production latency guarantee.

## What the threshold results establish

The 0.8 rule was fixed as requested; no threshold tuning occurred after inspecting these outcomes. Recounting the recorded probability pairs gives the following diagnostic:

| Threshold applied to the same recorded scores | Referral accepts | Patient accepts | Email accepts |
|---|---:|---:|---:|
| 0.80 | 480 | 125 | 0 |
| 0.90 | 480 | 93 | 0 |
| 0.95 | 3 | 0 | 0 |
| 0.97 | 0 | 0 | 0 |

This is score-based coverage on observed inputs, not a rerun at those thresholds. Different decisions could change later states. It does not calibrate the probabilities or prove an acceptable false-completion rate. Full alternative policies would require separate executions. [Coverage evidence](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/qwen-jev-080/analysis/threshold-coverage-observed.json).

## Evidence and reproducibility

The completed comparison contains **5,375 provider audits: 4,469 Qwen requests and 906 Jev requests**, with no provider errors, incomplete audits, or unknown usage. The separate Jev pilot brings the preserved total to 732 executions and 5,487 provider audits. Independent checks re-extracted and rescored every final result, verified frozen source/config/input hashes and execution traces, and recomputed provider accounting. All accepted completion events have matching executed receipt evidence; that structural check is not a proof of every semantic obligation.

- [Full paired results and per-case metrics](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/qwen-jev-080/analysis/full-final-audit-paired.json)
- [Every completion check and receipt audit](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/qwen-jev-080/analysis/full-final-audit-completion.json)
- [Independent integrity report](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/qwen-jev-080/analysis/integrity.json)
- [Patient semantic review](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/qwen-jev-080/analysis/patient-semantic-review.json)
- [Provider and model compatibility evidence](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/qwen-jev-080/analysis/model-compatibility.json)
- [Verified Jev trace archive](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/phase2/archives/qwen-jev-080-completed-v1.tar.gz)
- [Jev archive manifest](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/phase2/archives/qwen-jev-080-completed-v1.manifest.json)
- [Jev restoration verification](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/phase2/archives/qwen-jev-080-completed-v1.verification.json)
- [Interrupted Laya trace archive](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/phase2/archives/qwen-laya-080-interrupted-v1.tar.gz)

Raw case directories remain in `/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/phase2/runs`. Each retains inputs, expected fields stored separately from model inputs, events, messages, tool calls/results, all provider attempts, final scoring, configuration, and source hashes. The interrupted Laya experiment and its in-flight unknown charges are preserved separately and excluded from the Jev comparison. The archives include manifests, credential-scan results, and verification by fresh extraction.

The supported conclusion is selective: **Jev demonstrably eliminated redundant LLM continuations in referral and patient intake at this configuration. Email incurred checker overhead without a call reduction.** The one additional email failure, reused benchmark cases, simulated tools and human responses, and uncalibrated acceptance probabilities prevent a blanket claim of accuracy-preserving enterprise automation.
