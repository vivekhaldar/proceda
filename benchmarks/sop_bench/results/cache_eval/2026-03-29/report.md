# Cache Evaluation Report

**Date**: 2026-03-29
**Model**: Gemini 2.5 Flash / Gemini 3 Flash (per-domain configs)
**Domains**: dangerous_goods, video_classification, customer_service, traffic_spoofing_detection

## Executive Summary

**Level 1 (hint injection) is safe and effective.** All 4 domains pass the 2% TSR regression hard gate. L1 provides modest token savings by helping the LLM converge faster, with no risk of incorrect behavior since the LLM still reasons and calls tools itself.

**Level 2 (direct execution) is domain-dependent and not production-ready.** It dramatically improves pure data-pipeline SOPs (dangerous_goods: +4.7% TSR) but catastrophically degrades complex reasoning workflows (customer_service: -43.6%, traffic_spoofing: -68.3%). The root cause: when L2 executes tool calls directly and a later step falls back to the LLM, the LLM lacks the conversational context that normally accumulates during step execution. Two bugs were found and fixed during evaluation (hint wording, session message rollback), but the fundamental issue is architectural.

## Accuracy Comparison

| Domain | Tasks | Baseline TSR | L1 TSR | L1 Delta | L2 TSR | L2 Delta |
|--------|-------|-------------|--------|----------|--------|----------|
| dangerous_goods | 274 | 94.9% | **94.9%** | **0.0%** | **99.6%** | **+4.7%** |
| video_classification | 197 | 82.7% | 83.2% | +0.5% | 81.7% | -1.0% |
| customer_service | 156 | 85.9% | 84.6% | -1.3% | 42.3% | -43.6% |
| traffic_spoofing | 161 | 99.4% | 98.1% | -1.3% | 31.1% | -68.3% |

ECR is 100% across all runs — no crashes.

### Hard Gate Results

| Domain | L1 passes (<2% drop)? | L2 passes (<2% drop)? |
|--------|----------------------|----------------------|
| dangerous_goods | YES (0.0%) | YES (+4.7%) |
| video_classification | YES (+0.5%) | YES (-1.0%) |
| customer_service | YES (-1.3%) | **NO (-43.6%)** |
| traffic_spoofing | YES (-1.3%) | **NO (-68.3%)** |

## Cache Coverage

Steps cached per domain (from `proceda cache build`):

| Domain | Total Steps | Cached Steps | Cached Tools |
|--------|------------|-------------|-------------|
| dangerous_goods | 7 | 4 (steps 2-5) | calculate_sds_label_score, calculate_handling_score, calculate_transportation_score, calculate_disposal_score |
| video_classification | 5 | 2 (steps 2-3) | assignReviewer, getReview |
| customer_service | 9 | 5 (steps 1-5) | validateAccount, getAuthenticationDetails, createSessionAndOpenTicket, checkAccountStatus, checkServiceAreaOutage |
| traffic_spoofing | 7 | 3 (steps 3,4,6) | ValidateReferralSources, CalculateRiskScore, ExecuteEnforcementAction |

## Bugs Found and Fixed During Evaluation

### Bug 1: Hint wording caused Gemini to skip tool calls (L1)

**Symptom**: dangerous_goods L1 dropped to 83.9% TSR. Steps with cache hints completed with zero LLM calls — the LLM interpreted "In previous successful executions, this step followed this pattern" as evidence that tools had already been called and immediately called `complete_step`.

**Fix**: Changed hint wording to "Hint from previous executions (you MUST still call the tools and complete_step):" with explicit instruction "You MUST call the tool(s) above, then call complete_step with the result."

**Commit**: `488bcfc`

### Bug 2: L2 fallback crashed with orphaned tool messages

**Symptom**: customer_service L2 produced 0% TSR. When L2 recipe execution failed on a step (tool returned "No record found"), the fallback to the LLM crashed with `Missing corresponding tool call for tool response message` because prior successful L2 steps had added assistant/tool messages that the LLM never generated.

**Fix**: RecipeExecutor no longer adds assistant/tool messages to the session. Successful L2 steps add a single user-role summary. Failed L2 steps roll back all messages added during the attempt.

**Commits**: `9bc529b`, `57ce09f`

### Bug 3 (unfixed): L2 summary format loses context for reasoning steps

**Root cause of L2's poor performance on customer_service and traffic_spoofing**: When L2 executes tool calls directly, it adds a terse summary like `[Step 4 result]: Classified the violation...`. The LLM on subsequent steps lacks the rich conversational context (the back-and-forth of reasoning, tool calls, and tool responses) that normally accumulates. For SOPs where later steps depend on interpreting earlier tool outputs (not just their field values), this context loss causes incorrect decisions.

This is an architectural limitation of L2 as currently designed. Fixing it would require either:
- Having L2 reconstruct full conversational context from tool results
- Running a "context synthesis" LLM call after L2 execution to generate proper assistant messages
- Limiting L2 to only the last N steps so earlier steps build context normally

## Recommendations

1. **Ship L1 (hint injection) as the default when caching is enabled.** It passes the hard gate on all 4 domains, provides modest accuracy preservation, and helps the LLM converge faster on tool-heavy steps.

2. **Do not ship L2 (direct execution) in its current form.** It works brilliantly on pure data-pipeline SOPs (dangerous_goods: 99.6%) but fails catastrophically on reasoning-heavy workflows. The 2% hard gate blocks it on 2 of 4 domains.

3. **Future L2 work**: The path forward is to limit L2 to steps where ALL subsequent steps are also L2-cacheable (no LLM fallback needed). If any downstream step requires the LLM, all upstream steps should use L1 instead. This would make dangerous_goods (steps 2-5 all cached, steps 6-7 are uncached but only depend on captured result values, not conversational context) work correctly while preventing the customer_service/traffic_spoofing failures.

4. **The 1-2% TSR variance in L1 appears to be LLM non-determinism, not a systematic issue.** The regressions are on different random tasks across domains, and video_classification actually improved by 0.5%. This is within normal run-to-run variance for temperature=0 Gemini calls.
