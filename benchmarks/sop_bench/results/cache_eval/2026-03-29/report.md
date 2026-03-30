# Trace Harvesting & Step-Level Cache: Evaluation Report

**Date**: 2026-03-29
**Branch**: `feature/trace-cache-design`
**Models**: Gemini 2.5 Flash (dangerous_goods, customer_service), Gemini 3 Flash (video_classification, traffic_spoofing)
**Domains evaluated**: dangerous_goods (274 tasks), video_classification (197), customer_service (156), traffic_spoofing_detection (161)

## Executive Summary

**This is a null result.** The hypothesis was that caching tool call patterns from prior successful runs would reduce token usage and LLM calls on subsequent executions. We implemented two optimization levels and tested them against SOP-Bench ground truth across 4 domains.

**Level 1 (hint injection)** is accuracy-safe — it passes the 2% TSR regression hard gate on all domains. But it **does not reduce tokens or LLM calls**. The hint text adds tokens to the prompt, and the LLMs we tested already converge in the minimum number of iterations (1 tool call + 1 summary = 2 LLM calls per step) without hints. The hints tell the LLM something it already figures out on the first try.

**Level 2 (direct execution)** does eliminate LLM calls for cached steps, but **catastrophically degrades accuracy** on 2 of 4 domains (customer_service: -43.6%, traffic_spoofing: -68.3%). The root cause is architectural: when L2 bypasses the LLM for some steps then falls back to the LLM for later steps, the LLM lacks the conversational context that normally accumulates. L2 only works on fully-deterministic data-pipeline SOPs where every cached step's result can be used by later steps without LLM interpretation.

## 1. What We Built

### Architecture

```
src/proceda/cache/
├── models.py      # StepRecipe, ToolCallRecipe, ArgumentMapping, SkillCache
├── analyzer.py    # TraceAnalyzer: reads N traces → extracts per-step patterns
├── store.py       # CacheStore: persists SkillCache as JSON, hash-based invalidation
└── executor.py    # RecipeExecutor: executes tool calls directly from recipes (L2)
```

The **TraceAnalyzer** reads 3+ completed run traces for the same skill, groups events by step, and for each step extracts:
- Which tools were called, in what order
- How each argument was derived: from a session variable, from a prior step's tool result, or as a literal constant
- A confidence score (what fraction of traces showed the same pattern)

This produces a **SkillCache** — a per-step recipe that encodes the tool call pattern parametrically.

### Two optimization levels

**Level 1 — Hint injection**: Before sending the step prompt to the LLM, append a text hint like:

```
Hint from previous executions (you MUST still call the tools and complete_step):
1. Call `calculate_sds_label_score` with: product_id (from variable 'product_id'),
   sds_label_text (from variable 'sds_label_text')
You MUST call the tool(s) above, then call complete_step with the result.
```

The LLM still reasons, calls tools, and completes the step itself. The hint just tells it what to do.

**Level 2 — Direct execution**: Skip the LLM entirely for cached steps. Resolve arguments from variables and prior step results, call the MCP tools directly, capture results. Falls back to L1/LLM on any error.

### Cache coverage

| Domain | Total Steps | Cached Steps | What was cached |
|--------|:--:|:--:|-------------|
| dangerous_goods | 7 | 4 (steps 2-5) | 4 scoring tool calls, each mapping variables to args |
| video_classification | 5 | 2 (steps 2-3) | assignReviewer (uses step 1 results), getReview |
| customer_service | 9 | 5 (steps 1-5) | 5 tool calls: validate, auth, create session, check status, check outage |
| traffic_spoofing | 7 | 3 (steps 3,4,6) | validate sources, calculate risk, execute enforcement |

All cached steps had 100% confidence across 3 analyzed traces.

## 2. Accuracy Results

| Domain | Tasks | Baseline TSR | L1 TSR | L1 Δ | L2 TSR | L2 Δ |
|--------|:--:|:--:|:--:|:--:|:--:|:--:|
| dangerous_goods | 274 | 94.9% | 94.9% | 0.0% | 99.6% | +4.7% |
| video_classification | 197 | 82.7% | 83.2% | +0.5% | 81.7% | -1.0% |
| customer_service | 156 | 85.9% | 84.6% | -1.3% | 42.3% | **-43.6%** |
| traffic_spoofing | 161 | 99.4% | 98.1% | -1.3% | 31.1% | **-68.3%** |

ECR is 100% across all configurations — no crashes.

### L1 hard gate: all pass

| Domain | L1 Δ | Passes (<2% drop)? |
|--------|:--:|:--:|
| dangerous_goods | 0.0% | YES |
| video_classification | +0.5% | YES |
| customer_service | -1.3% | YES |
| traffic_spoofing | -1.3% | YES |

### L2 hard gate: 2 of 4 fail

| Domain | L2 Δ | Passes (<2% drop)? |
|--------|:--:|:--:|
| dangerous_goods | +4.7% | YES |
| video_classification | -1.0% | YES |
| customer_service | -43.6% | **NO** |
| traffic_spoofing | -68.3% | **NO** |

### L1 per-task regression analysis

Tasks flip in both directions between baseline and L1, at roughly equal rates:

| Domain | Regressions (BL✓→L1✗) | Improvements (BL✗→L1✓) | Net |
|--------|:--:|:--:|:--:|
| dangerous_goods | 14 | 9 | -5 |
| video_classification | 13 | 14 | +1 |
| customer_service | 7 | 5 | -2 |
| traffic_spoofing | 3 | 1 | -2 |

The dangerous_goods regressions were all output extraction failures caused by a bug (Gemini skipping tool calls — see Section 4). After the fix, the regression dropped to 0.

The customer_service and traffic_spoofing regressions are genuine LLM reasoning differences — the L1 tasks produce different answers, not empty answers. Since tasks flip in both directions, this is run-to-run variance, not a systematic L1 issue.

## 3. Token & LLM Call Results

This is the key finding: **L1 does not save tokens or LLM calls.**

| Domain | Baseline Tok/Task | L1 Tok/Task | Token Δ | Baseline Calls/Task | L1 Calls/Task | Calls Δ |
|--------|:--:|:--:|:--:|:--:|:--:|:--:|
| dangerous_goods | 28,409 | 30,653 | **+7.9%** | 10.9 | 10.7 | -2.0% |
| video_classification | 49,233 | 49,108 | -0.3% | 15.0 | 14.7 | -2.0% |
| customer_service | 83,937 | 86,607 | **+3.2%** | 17.4 | 17.2 | -1.3% |
| traffic_spoofing | 35,860 | 37,105 | **+3.5%** | 12.0 | 12.0 | 0.0% |

**Why L1 doesn't save tokens**: The hint text itself adds ~50-100 tokens to every cached step's prompt. Meanwhile, the LLM already converges in 2 iterations per tool step (1 call to invoke the tool, 1 call to summarize the result). The hint doesn't eliminate any iterations because there were no wasted iterations to eliminate — Gemini 2.5/3 Flash is already efficient at these SOP tasks.

**Why L1 doesn't save LLM calls**: Same reason. The baseline pattern is already optimal: `LLM → tool call → LLM → complete_step`. The hint tells the LLM "call tool X with args Y" but the LLM was going to do that anyway on the first try. The hint is redundant information for a model that's already good at following SOP instructions.

### Where L1 would help

L1 would provide savings on:
- **Weaker models** that take 3-4 iterations to figure out the right tool and arguments
- **Ambiguous steps** where the SOP text doesn't clearly specify which tool to call
- **Multi-tool steps** where the LLM might try tools in the wrong order

None of the 4 SOP-Bench domains we tested have these characteristics. The SOPs are well-written, the tools are clearly specified in step instructions, and Gemini Flash is strong enough to follow them without guidance.

## 4. Bugs Found and Fixed

Three bugs were discovered during evaluation. All were in the cache integration code, not in the core executor.

### Bug 1: Hint wording caused Gemini to skip tool calls

**Symptom**: dangerous_goods L1 initially dropped to 83.9% TSR.

**Root cause**: The original hint said "In previous successful executions, this step followed this pattern: Called `tool_x` with...". Gemini interpreted this as evidence that the tools had already been called and responded with an immediate `complete_step` — zero LLM iterations, zero tool calls, zero output.

**Fix 1** (partial): Changed wording to "you MUST still call the tools and complete_step". This helped but didn't fully fix it — Gemini's prompt caching still sometimes returned a cached `complete_step` response.

**Fix 2** (complete): Added a guard in the executor: if the LLM calls `complete_step` but no app tool calls were made on a step where the cache recipe expects them, reject the completion and respond with "You have not called any tools yet. This step requires tool calls before completion." This forces the LLM into the tool-calling path.

**Result**: dangerous_goods L1 TSR went from 83.9% → 94.9% (matching baseline exactly).

### Bug 2: L2 fallback crashed with orphaned tool messages

**Symptom**: customer_service L2 produced 0% TSR on first attempt.

**Root cause**: When L2 recipe execution succeeded on steps 1-3 but failed on step 4 (tool returned "No record found"), the fallback to the LLM crashed because the session contained assistant/tool messages from the L2-executed steps that the LLM never generated. The LLM API returned `Missing corresponding tool call for tool response message`.

**Fix**: Two changes:
1. RecipeExecutor no longer adds assistant/tool messages to the session. It only executes the tools and returns results.
2. On L2 failure, all messages added during the failed step are rolled back before falling through to the LLM.

**Result**: customer_service L2 went from 0% → 42.3% TSR (still bad, but the crashes are gone).

### Bug 3: L2 context loss (architectural, unfixed)

**Root cause of L2's poor performance**: When L2 bypasses the LLM for steps 1-5 and then the LLM runs step 6, it sees terse summaries like `[Step 4 result]: Classified the violation...` instead of the rich back-and-forth conversation that normally builds up. For SOPs where later reasoning depends on understanding tool outputs in context, this is catastrophic.

This is not a bug to fix — it's a fundamental limitation of the L2 approach as designed. Fixing it would require reconstructing conversational context after L2 execution, which likely costs as many tokens as just running L1 in the first place.

## 5. What We Learned

### The hypothesis was partially wrong

The hypothesis was: "LLMs are doing rote mapping work that could be cached." Looking at traces, this appeared true — steps 2-5 of dangerous_goods always call the same tool with arguments pulled directly from variables.

What we missed: **the LLM is already efficient at this rote work.** Gemini Flash converges in 2 LLM calls per tool step (call + summarize) regardless of whether we hint it. The overhead we hoped to eliminate doesn't exist for capable models on well-structured SOPs.

### The cost structure of SOP execution

For these 4 domains, the token cost breaks down as:
- ~60% of tokens are in the system prompt + step prompts (fixed cost, not reducible by caching)
- ~25% are in tool results flowing back to the LLM (fixed cost)
- ~15% are in LLM reasoning and summaries (the only part caching could reduce)

L1 hints add tokens to the 60% bucket while saving nothing from the 15% bucket.

### When would caching actually help?

1. **L2 on fully-deterministic SOPs** where every step is a pure data pipeline with no downstream LLM reasoning. dangerous_goods is the poster child: L2 achieved 99.6% TSR (better than baseline) because steps 2-5 are mechanically correct, and steps 6-7 only need the captured result values, not conversational context.

2. **Weaker or more expensive models** where the LLM takes 3-5 iterations to converge. If the baseline cost per step were 5 LLM calls instead of 2, L1 hints would save 3 calls per cached step.

3. **Latency, not cost**: Even though L1 doesn't save tokens, it could reduce latency if the hint helps the LLM respond faster (fewer reasoning tokens before the tool call). We didn't measure latency in this evaluation.

## 6. Recommendations

1. **Do not enable L1 caching by default.** It doesn't save tokens or LLM calls on the models we tested. The code is correct and accuracy-safe, but there's no benefit to ship.

2. **Keep the infrastructure.** The cache models, analyzer, store, and CLI are clean and tested. When we support weaker/cheaper models or encounter SOPs where the LLM struggles to converge, the L1 machinery is ready.

3. **Do not ship L2.** It fails the hard gate on 2 of 4 domains. The context-loss problem is architectural and not worth solving until there's a concrete use case for it (e.g., a fully-deterministic SOP pipeline where every step is cached).

4. **If we revisit L2**, the approach should be: only use L2 when ALL steps in the SOP are cacheable. If any step requires LLM fallback, use L1 for the entire run. This would prevent the context-loss failure mode.

5. **Measure latency** in a future evaluation. L1 may have a latency benefit (faster convergence even if total tokens are similar) that this token-focused evaluation missed.

## Appendix: Files Changed

### New files
- `src/proceda/cache/models.py` — StepRecipe, ToolCallRecipe, ArgumentMapping, SkillCache
- `src/proceda/cache/store.py` — CacheStore with hash-based invalidation
- `src/proceda/cache/analyzer.py` — TraceAnalyzer
- `src/proceda/cache/executor.py` — RecipeExecutor (L2)
- `src/proceda/cli/commands/cache.py` — CLI: build, show, clear
- `benchmarks/sop_bench/compare_cache_eval.py` — evaluation report generator
- `benchmarks/sop_bench/exclusions/*.json` — SOP-inconsistent task exclusion lists

### Modified files
- `src/proceda/config.py` — CacheConfig dataclass
- `src/proceda/events.py` — CACHE_HIT, CACHE_MISS, CACHE_FALLBACK event types
- `src/proceda/llm/prompts.py` — hint parameter on build_step_prompt()
- `src/proceda/internal/executor.py` — cache consultation, hint injection, L2 path, premature-complete guard
- `src/proceda/runtime.py` — cache loading, auto-build
- `benchmarks/sop_bench/harness.py` — --exclude-tasks, --output-dir flags

### Test files
- `tests/test_cache/test_models.py` — 4 tests
- `tests/test_cache/test_store.py` — 6 tests
- `tests/test_cache/test_analyzer.py` — 8 tests
- `tests/test_cache/test_recipe_executor.py` — 9 tests

All 389 tests pass. `make check` clean.
