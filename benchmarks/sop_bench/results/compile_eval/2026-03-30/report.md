# Step Compilation Evaluation Report

**Date**: 2026-03-30
**Branch**: `feature/trace-cache-design`
**Codegen model**: Gemini 2.5 Flash (via OpenRouter)
**Runtime models**: Gemini 2.5 Flash / Gemini 3 Flash (per-domain configs)
**Domains**: dangerous_goods, video_classification, customer_service, traffic_spoofing_detection

## Executive Summary

Step compilation generates Python functions from SOP steps using LLM codegen, verified against held-out traces. The hypothesis was that deterministic steps (arithmetic, tool calls, lookups) could be replaced with code, eliminating LLM calls and tokens entirely.

**The compilation machinery works** — it successfully generates code, verifies it against traces, and executes at runtime with proper fallback. But **accuracy drops significantly** on 3 of 4 domains, failing the 2% hard gate.

The core problem: **10 traces aren't enough to verify that generated code generalizes.** Code that passes verification on 10 examples fails on the broader task set (34% TSR on video_classification, down from 83%). This is the overfitting we tried to prevent with the train/test split, but the held-out set (7 traces) was too small to catch it.

## Compilation Results

| Domain | Steps | Compiled | Failed | Reason for failures |
|--------|:--:|:--:|:--:|---|
| dangerous_goods | 7 | 5 | 2 | Steps 3,4: codegen returned empty captured_values on some traces |
| video_classification | 7 | **7** | 0 | All passed 10-trace verification |
| traffic_spoofing | 6 | **6** | 0 | All passed 10-trace verification |
| customer_service | 10 | 2 | 8 | Missing session_token propagation, reasoning steps fail verification |

## Accuracy

| Domain | Baseline TSR | Compiled TSR | Delta | Hard gate? |
|--------|:-:|:-:|:-:|:-:|
| dangerous_goods | 94.9% | 89.1% | -5.8% | **FAIL** |
| video_classification | 82.7% | 34.0% | -48.7% | **FAIL** |
| customer_service | 85.9% | 73.7% | -12.2% | **FAIL** |
| traffic_spoofing | 99.4% | 88.2% | -11.2% | **FAIL** |

All 4 domains fail the 2% hard gate.

## Token & LLM Call Savings

| Domain | BL Tok/Task | Compiled Tok/Task | Token Δ | BL Calls/Task | Compiled Calls/Task | Calls Δ |
|--------|:-:|:-:|:-:|:-:|:-:|:-:|
| dangerous_goods | 28,409 | 10,169 | **-64%** | 10.9 | 4.2 | **-61%** |
| video_classification | 49,233 | 0 | **-100%** | 15.0 | 0.0 | **-100%** |
| customer_service | 83,937 | 74,066 | -12% | 17.4 | 14.6 | -16% |
| traffic_spoofing | 35,860 | 0 | **-100%** | 12.0 | 0.0 | **-100%** |

The token savings are real. Fully-compiled domains (video_classification, traffic_spoofing) use zero tokens. But the accuracy makes these savings meaningless.

## 3-Way Comparison: Baseline vs L1 Cache vs Compilation

| Domain | Baseline TSR | L1 TSR | Compiled TSR | BL Tok/Task | L1 Tok/Task | Compiled Tok/Task |
|--------|:-:|:-:|:-:|:-:|:-:|:-:|
| dangerous_goods | 94.9% | 94.9% | 89.1% | 28,409 | 30,653 | 10,169 |
| video_classification | 82.7% | 83.2% | 34.0% | 49,233 | 49,108 | 0 |
| customer_service | 85.9% | 84.6% | 73.7% | 83,937 | 86,607 | 74,066 |
| traffic_spoofing | 99.4% | 98.1% | 88.2% | 35,860 | 37,105 | 0 |

L1 caching preserves accuracy but doesn't save tokens. Compilation saves tokens but loses accuracy. Neither achieves the goal of saving tokens without losing accuracy.

## Why Compilation Failed

### 1. Overfitting on 10 traces

video_classification compiled all 7 steps with 100% verification pass rate on 10 traces. But it scored 34% on 197 tasks. The generated code learned patterns specific to 10 examples (e.g., specific confidence thresholds, particular tool result shapes) that don't generalize.

The train/test split (3 prompt + 7 held-out) wasn't enough. The held-out set covers ~3.5% of the total task diversity. Edge cases, unusual inputs, and rare conditional branches aren't represented.

### 2. Context loss (same as L2 caching)

When compiled steps produce terse `[Step N]: summary` messages instead of the full conversational context the LLM normally builds, downstream LLM steps make worse decisions. This is the same problem we saw with L2 caching — it's fundamental to any approach that bypasses the LLM for some steps.

dangerous_goods: compiled steps 6-7 fell back to the LLM with errors because `prior_steps` had a different shape than expected.

customer_service: only 2 of 10 steps compiled, but those 2 steps produced summaries that lacked detail the LLM needed for step 3 onward.

### 3. Tool result shape variance

The compiled code expects specific keys in tool results (e.g., `result["handling_score"]`). Some tasks return results with different shapes, extra keys, or different formatting. The generated code is brittle to these variations because it was trained on 10 examples of consistent tool results.

## What Would Fix This

1. **More traces** — 50-100 traces instead of 10 would dramatically improve verification coverage. But this raises the bar: you need 50+ successful runs before compilation is even attempted.

2. **Runtime verification** — Instead of trusting compiled code blindly at runtime, compare its output against an LLM execution on a sample of tasks. If divergence exceeds a threshold, disable the compiled step.

3. **Hybrid approach** — Only compile steps that are truly mechanical (tool calls with variable-derived arguments, pure arithmetic). Use the LLM for all steps involving interpretation, classification, or multi-factor reasoning. The compiler's verification should reject these steps, but 10 traces aren't enough to reliably distinguish mechanical from judgmental steps.

4. **Don't compile reasoning steps** — The design doc predicted that subjective steps would fail verification. They didn't — because 10 traces weren't diverse enough. A stricter approach: only compile steps that have zero LLM reasoning (pure tool calls or pure arithmetic from the SOP text). If the step text contains words like "evaluate," "determine," "classify based on," "exercise caution" — don't even attempt compilation.

## Speed Results

The one unambiguous win: fully-compiled SOPs are fast.

| Domain | Baseline time/task | Compiled time/task | Speedup |
|--------|:-:|:-:|:-:|
| dangerous_goods | ~15s | ~5s (mixed) | ~3x |
| video_classification | ~20s | **0.2s** | **100x** |
| customer_service | ~30s | ~26s (mostly LLM) | ~1.2x |
| traffic_spoofing | ~13s | **0.2s** | **65x** |

For use cases where speed matters more than accuracy (e.g., batch preprocessing, approximate filtering), compilation could be valuable even at lower accuracy.
