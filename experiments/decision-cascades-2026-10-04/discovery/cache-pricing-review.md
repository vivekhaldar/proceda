# Nebius cached-input pricing and sensitivity review

Reviewed 2026-10-04. Public documentation and saved audits only; no credentials, account bill access, inference, runtime change, or analyzer change.

## Finding and official sources

No official Nebius cached-input discount rate was found in the sources inspected. This is **not proof that no discount exists**. The current estimate remains the documented list-price calculation with cached tokens priced as ordinary input; it is not an observed invoice.

- [Nebius GLM model pricing](https://nebius.com/services/token-factory/models/glm-models-inference) lists GLM-5.3-Flash at **$0.15 per million input tokens and $0.50 per million output tokens**; it provides no separate cached-input price.
- [Nebius OpenHands cookbook](https://dev.nebius.com/cookbook/openhands-agent-canvas) uses the same input/output rates, checked September 5, 2026. Its usage-based example does not document a cached-input discount.
- [Official chat-completion schema](https://docs.tokenfactory.nebius.com/api-reference/inference/create-chat-completion.md) exposes nullable integer `PromptTokensDetails.cached_tokens`; it does not attach a billing rate to that field. Retrieved directly over unauthenticated HTTPS after the web reader failed (HTTP 200, 41,814 bytes).
- [Official Billing & Consumption](https://docs.tokenfactory.nebius.com/other-capabilities/billing-new.md) and [public-serverless guide](https://docs.tokenfactory.nebius.com/public-serverless.md) were also retrieved successfully (4,982 and 1,663 bytes); neither supplied a cache rate. The [documentation index](https://docs.tokenfactory.nebius.com/llms.txt) did not identify a cache-pricing guide.
- [Token Factory pricing](https://nebius.com/token-factory/prices) redirected to the account pricing UI, which yielded no readable table through the web reader. Its older search-index table was not used as current GLM pricing evidence. No account pricing or invoice was inspected.

The saved [model catalog](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/discovery/nebius-models.json) confirms the model ID but contains no pricing fields; its [request metadata](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/discovery/nebius-models-source.json) records the October 4 retrieval. [Saved candidate pricing](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/discovery/nebius-candidate-pricing.json) already identifies the numbers as documented list prices, not an account-specific bill. Z.ai prices, aggregators, and user-submitted feature requests were not treated as pricing authority.

## Cache usage in the completed dev-low-8 batch

Scope: 72 task runs (24 per arm), counting every hosted LLM attempt once from `runs/dev-low-8--*/*/*/*/providers/*.json`. All 573 attempts report usage and both cache counters. In every record, `prompt_tokens_details.cached_tokens == prompt_cache_hit_tokens`, and cached + miss tokens == total prompt tokens. The two cache fields are aliases here; they must not be summed. Decision-model calls are excluded from these hosted-LLM cache totals.

| Arm | Attempts | Input tokens | Cached input | Cached share | Output tokens |
|---|---:|---:|---:|---:|---:|
| baseline-low | 204 | 756,042 | 653,056 | 86.38% | 14,154 |
| hybrid-local-12b-low | 176 | 676,226 | 548,608 | 81.13% | 12,978 |
| hybrid-local-low | 193 | 730,375 | 615,680 | 84.30% | 13,758 |

| Arm / domain | Attempts | Input | Cached input | Output |
|---|---:|---:|---:|---:|
| baseline-low / email_intent | 30 | 85,570 | 66,304 | 3,027 |
| baseline-low / patient_intake | 110 | 498,586 | 453,376 | 7,189 |
| baseline-low / referral_abuse_detection_v1 | 64 | 171,886 | 133,376 | 3,938 |
| hybrid-local-12b-low / email_intent | 31 | 88,670 | 69,888 | 3,031 |
| hybrid-local-12b-low / patient_intake | 105 | 475,405 | 390,144 | 7,105 |
| hybrid-local-12b-low / referral_abuse_detection_v1 | 40 | 112,151 | 88,576 | 2,842 |
| hybrid-local-low / email_intent | 30 | 85,609 | 47,104 | 2,990 |
| hybrid-local-low / patient_intake | 110 | 498,944 | 451,328 | 7,244 |
| hybrid-local-low / referral_abuse_detection_v1 | 53 | 145,822 | 117,248 | 3,524 |

Sources: [completed schedule](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/dev-low-8/schedule.json); example [provider audit](/Users/Vivek.Haldar/repos/proceda/experiments/decision-cascades-2026-10-04/runs/dev-low-8--email_intent/baseline-low/email_intent/E1022/providers/63bb0093ade043e7b3932c50c0fa05d4-001.json). All audit records for the glob above were read, including retries had any been present. Cache usage is observed; cache billing is not. Different cache-hit patterns can also affect latency, so these aggregates do not isolate the causal effect of caching.

## Explicit price sensitivity

Let I be reported input tokens, H cached input tokens, O reported output tokens, and c the **hypothetical** cached-input dollars per million tokens. Holding ordinary input at $0.15/M and output at $0.50/M:

`estimated hosted LLM cost(c) = [0.15 × (I − H) + c × H + 0.50 × O] / 1,000,000`

Reasoning tokens are already included in O and are not added again. The c=0 scenario below is a sensitivity bound, **not a claim that Nebius provides free cached tokens**. It assumes only this billing component changes; it excludes taxes, negotiated prices, credits, actual invoices, and local inference TCO.

| Arm | Full input list price (c=$0.15/M) | Hypothetical free cached input (c=$0/M) | Cached rate where candidate ties baseline |
|---|---:|---:|---:|
| baseline-low | $0.12048330 | $0.02252490 | — |
| hybrid-local-12b-low | $0.10792290 | $0.02563170 | $0.02974494/M |
| hybrid-local-low | $0.11643525 | $0.02408325 | $0.04169387/M |

Both candidates report fewer total input tokens but **more uncached input** than baseline in this batch. Consequently, their apparent hosted-cost advantage reverses below their respective break-even cached-input rates. Above each break-even rate the candidate has a lower estimated hosted-LLM charge; below it baseline is lower, holding the other rates fixed. This sensitivity concerns aggregate development costs, not task quality or the cost of local models.

Recommended reporting language: **“Hosted API cost estimated from reported usage at documented Nebius list prices, treating cached input at the full input rate; actual cached-input billing was not verified. Local compute cost is separate.”**

The analyzer field `cost_usd_lower_bound` is a known-usage subtotal/lower bound **under the chosen fixed price model only**, when usage is missing. It is not a lower bound on the actual invoice: an undocumented cache discount could make the actual bill lower. All usage is known in the batch above, but that does not resolve the tariff assumption. Do not silently substitute another provider’s cached rate or call any hypothetical scenario the actual bill.
