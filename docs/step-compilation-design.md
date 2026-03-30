# Step Compilation: Generating Code from SOP Steps

## 1. Motivation

The trace caching experiment (see `cache_eval/2026-03-29/report.md`) showed that L1 hint injection doesn't save tokens because capable LLMs already converge in 2 iterations per tool step. The LLM is already efficient at calling tools — there's nothing to optimize there.

But many SOP steps aren't tool calls at all. They're **pure computation** — arithmetic, conditional logic, lookups — that the LLM spends tokens reasoning about when a 5-line Python function would produce the same result with zero LLM calls and zero tokens.

### Example: dangerous_goods

| Step | What it does | LLM calls | Could be code? |
|------|-------------|:---------:|:--------------:|
| 1 | Validate product_id format (regex check) | 1 | Yes |
| 2 | Call `calculate_sds_label_score(product_id, sds_label_text)` | 2 | Yes — it's just `tool(var_a, var_b)` |
| 3 | Call `calculate_handling_score(product_id, guidelines)` | 2 | Yes |
| 4 | Call `calculate_transportation_score(product_id, requirements)` | 2 | Yes |
| 5 | Call `calculate_disposal_score(product_id, guidelines)` | 2 | Yes |
| 6 | Sum 4 scores, impute missing, validate range 4-20 | 1 | **Yes — pure arithmetic** |
| 7 | Map score to class A/B/C/D, output XML | 1 | **Yes — lookup table** |

Total baseline: 11 LLM calls, ~28K tokens per task.
If all 7 steps were compiled: **0 LLM calls, 0 tokens** (plus 4 tool calls that cost nothing).

For 274 tasks, that's eliminating ~7.8M tokens per evaluation run.

### What's different from L1/L2 caching

| | L1 (hints) | L2 (direct execution) | Compilation |
|---|---|---|---|
| Tool-call steps | Hints LLM which tool to call | Calls tool directly, skips LLM | Calls tool directly via generated code |
| Pure-reasoning steps | Cannot help (no tool recipe) | Cannot help (no tool recipe) | **Generates code to replace the reasoning** |
| Context for downstream | Full (LLM still runs) | Broken (terse summaries) | Full (code produces structured output) |
| Correctness guarantee | LLM still reasons | None (hope for the best) | **Verified against N traces before use** |

The key insight: compilation targets the steps that caching couldn't touch — the pure-reasoning steps where the LLM does arithmetic, conditionals, and formatting that a function handles trivially.

## 2. Design

### How it works

```
SKILL.md + N traces
       │
       ▼
┌──────────────┐     For each step:
│   Compiler   │     1. Extract input/output examples from traces
│              │     2. Send step text + examples to LLM with codegen prompt
│   (one-time  │     3. LLM generates a Python function
│    offline)  │     4. Verify function against ALL N traces
│              │     5. If verified, save the function
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Compiled    │     Per-step Python functions stored as:
│  Skill       │     .proceda/compiled/{skill_id}/step_{N}.py
│              │     Each has: execute(variables, prior_step_results, tool_caller) → StepOutput
└──────┬───────┘
       │
       ▼ (at runtime)
┌──────────────┐
│  Executor    │     For each step:
│              │     1. If compiled function exists → call it (zero LLM)
│              │     2. Else → normal LLM loop
└──────────────┘
```

### Step function interface

Every compiled step function has the same signature:

```python
@dataclass
class StepOutput:
    """Result of a compiled step execution."""
    summary: str                          # Human-readable summary (for logging/display)
    captured_values: dict[str, Any]       # Values available to subsequent steps
    output_fields: dict[str, str] | None  # Final output XML fields (last step only)
    terminate: bool = False               # If True, stop execution (e.g., invalid product_id)
    terminate_values: dict[str, str] | None = None  # Output fields on termination


async def execute(
    variables: dict[str, str],
    prior_steps: dict[int, dict[str, Any]],
    call_tool: Callable[[str, dict], Awaitable[dict]],
) -> StepOutput:
    """Execute this step.

    Args:
        variables: Session variables from the skill run.
        prior_steps: Results from previously completed steps.
            Maps step_index → captured_values dict.
        call_tool: Async function to call an MCP tool by name with arguments.
            Returns the tool result as a dict.
    """
    ...
```

### Example: dangerous_goods compiled steps

**Step 1** (validation):
```python
async def execute(variables, prior_steps, call_tool):
    product_id = variables["product_id"]
    import re
    if not re.match(r"^P_\d+$", product_id):
        return StepOutput(
            summary=f"Product ID {product_id} failed format validation.",
            captured_values={},
            output_fields=None,
            terminate=True,
            terminate_values={"hazard_score": "0", "hazard_class": "Unable to Decide"},
        )
    return StepOutput(
        summary=f"Product ID {product_id} validated successfully.",
        captured_values={"product_id_valid": True},
        output_fields=None,
    )
```

**Steps 2-5** (tool calls):
```python
async def execute(variables, prior_steps, call_tool):
    result = await call_tool("calculate_sds_label_score", {
        "product_id": variables["product_id"],
        "sds_label_text": variables["sds_label_text"],
    })
    score = result.get("sds_label_score", 0)
    if not (1 <= score <= 5):
        score = 0  # Invalid score
    return StepOutput(
        summary=f"SDS label score for {variables['product_id']}: {score}",
        captured_values={"safety_score": score},
        output_fields=None,
    )
```

**Step 6** (computation):
```python
async def execute(variables, prior_steps, call_tool):
    scores = {
        "safety_score": prior_steps.get(2, {}).get("safety_score", 0),
        "handling_score": prior_steps.get(3, {}).get("handling_score", 0),
        "transportation_score": prior_steps.get(4, {}).get("transportation_score", 0),
        "disposal_score": prior_steps.get(5, {}).get("disposal_score", 0),
    }
    missing = sum(1 for v in scores.values() if not v or v == 0)
    if missing > 2:
        return StepOutput(
            summary="More than 2 component scores missing.",
            captured_values={"hazard_score": 0},
            output_fields=None,
            terminate=True,
            terminate_values={"hazard_score": "0", "hazard_class": "Unable to Decide"},
        )
    non_zero = [v for v in scores.values() if v and v > 0]
    max_score = max(non_zero) if non_zero else 0
    imputed = {k: (v if v and v > 0 else max_score) for k, v in scores.items()}
    hazard_score = sum(imputed.values())
    return StepOutput(
        summary=f"Cumulative hazard score: {hazard_score} ({' + '.join(str(v) for v in imputed.values())})",
        captured_values={"hazard_score": hazard_score, **imputed},
        output_fields=None,
    )
```

**Step 7** (classification + output):
```python
async def execute(variables, prior_steps, call_tool):
    hazard_score = prior_steps.get(6, {}).get("hazard_score", 0)
    if hazard_score <= 0:
        hazard_class = "Unable to Decide"
    elif hazard_score <= 8:
        hazard_class = "A"
    elif hazard_score <= 12:
        hazard_class = "B"
    elif hazard_score <= 16:
        hazard_class = "C"
    else:
        hazard_class = "D"
    return StepOutput(
        summary=f"Hazard class: {hazard_class} (score: {hazard_score})",
        captured_values={"hazard_class": hazard_class},
        output_fields={"hazard_score": str(hazard_score), "hazard_class": hazard_class},
    )
```

### The compilation process

**Input**: SKILL.md + N successful run traces (N >= 3)

**For each step**:

1. **Extract examples** from traces: for each run, collect the step's inputs (variables + prior step outputs) and outputs (captured values, tool results, summary, whether it terminated).

2. **Build a codegen prompt** that includes:
   - The step's text from SKILL.md
   - The `execute()` function signature and `StepOutput` dataclass
   - N input/output examples from traces
   - Instructions: "Generate a Python function that implements this step's logic. The function must produce the same outputs as the examples for all given inputs."

3. **Call the LLM** (one-time, offline) to generate the function.

4. **Verify** the generated function against ALL N traces:
   - For each trace, call `execute(variables, prior_steps, mock_tool_caller)` where `mock_tool_caller` returns the actual tool results from the trace.
   - Compare the function's `StepOutput.captured_values` against the trace's actual captured values.
   - Compare `output_fields` if it's the final step.
   - If any trace doesn't match, the compilation failed for this step — mark it as uncompilable.

5. **Save** the verified function to `.proceda/compiled/{skill_id}/step_{N}.py`.

### Verification is the key

The generated code is **never trusted blindly**. It must reproduce the exact outputs from N independent traces before being used. This is fundamentally different from L2 caching, which had no verification step.

If the LLM generates incorrect code, verification catches it. If the step has hidden conditional logic not covered by the N examples, verification may pass but the code could fail on new inputs — this is a known limitation, mitigated by using more traces (N >= 5-10) and by falling back to the LLM at runtime when the compiled function raises an exception or returns unexpected results.

### Runtime execution

In the executor, at the top of `_execute_step()`:

```python
compiled_fn = self._load_compiled_step(step_index)
if compiled_fn is not None:
    try:
        output = await compiled_fn(
            session.variables,
            self._step_results,
            self._call_tool,
        )
        # Record results for downstream steps
        self._step_results[step_index] = output.captured_values
        # Handle termination
        if output.terminate:
            # Set output fields and stop execution
            ...
            return
        # Handle output fields (final step)
        if output.output_fields:
            summary = self._format_output_fields(output)
        else:
            summary = output.summary
        session.add_message(RunMessage.create("user", f"[Step {step_index}]: {summary}"))
        await self._emit(RunEvent.create(..., SUMMARY_GENERATED, ...))
        return
    except Exception as e:
        logger.warning("Compiled step %d failed: %s. Falling back to LLM.", step_index, e)
        await self._emit(RunEvent.create(..., COMPILE_FALLBACK, ...))
        # Fall through to normal LLM execution
```

### What's compilable vs. what's not

**Compilable** (deterministic logic):
- Format validation (regex, string checks)
- Tool calls with arguments derived from variables or prior step results
- Arithmetic (sum scores, compute averages, apply thresholds)
- Lookup tables (score → class mapping)
- Conditional branching on known fields (if status == "Terminated", close case)
- Output formatting (XML tags, JSON)

**Not compilable** (requires judgment):
- Interpreting unstructured text ("identify red flags and exercise caution")
- Multi-factor reasoning ("determine the appropriate escalation path based on the nature of the problem")
- Steps with `[APPROVAL REQUIRED]` markers (human-in-the-loop)
- Steps with `request_clarification` calls

Looking at our 4 domains:

| Domain | Compilable steps | Not compilable | Potential savings |
|--------|:---:|:---:|---|
| dangerous_goods | 7 of 7 | 0 | **100% — entire SOP becomes code** |
| traffic_spoofing | 5 of 7 (steps 1-4, 6) | 2 (step 5: APPROVAL, step 6: reasoning about enforcement action determination is in the SOP text but the tool just executes it) | ~70% LLM call reduction |
| customer_service | 3 of 10 (steps 1-3) | 7 (steps 4-10 involve conditional branching that depends on tool results in complex ways) | ~30% LLM call reduction |
| video_classification | 1 of 7 (step 1 validation) | 6 (subjective content moderation) | ~15% LLM call reduction |

### Storage format

```
.proceda/compiled/
└── {skill_id}/
    ├── manifest.json       # Which steps are compiled, verification status, hash
    ├── step_1.py           # Compiled function for step 1
    ├── step_2.py           # Compiled function for step 2
    └── ...
```

`manifest.json`:
```json
{
  "skill_id": "e9ac31854bee661d",
  "skill_content_hash": "a1b2c3d4...",
  "compiled_at": "2026-03-30T10:00:00+00:00",
  "model_used": "claude-sonnet-4-20250514",
  "num_traces_used": 5,
  "steps": {
    "1": {"status": "compiled", "verified_against": 5, "failures": 0},
    "2": {"status": "compiled", "verified_against": 5, "failures": 0},
    "6": {"status": "compiled", "verified_against": 5, "failures": 0},
    "7": {"status": "compiled", "verified_against": 5, "failures": 0}
  }
}
```

### Security considerations

Generated code runs in the same process as Proceda. This is acceptable because:
1. The code is generated offline by a trusted LLM, not by user input
2. It's verified against traces before being saved
3. The SKILL.md author controls what logic the code implements
4. The `call_tool` interface limits what the code can do (only MCP tool calls)

If sandboxing is needed later, the compiled functions could run in a subprocess with restricted imports.

## 3. Comparison: Caching vs. Compilation

| | L1 Cache (hints) | Compilation |
|---|---|---|
| Token savings | None (adds tokens) | **100% for compiled steps** |
| LLM call savings | None (~2% on our benchmarks) | **100% for compiled steps** |
| Accuracy risk | Low (1-2% variance) | **None if verified** (falls back to LLM on failure) |
| Handles tool-call steps | Yes (hints which tool) | Yes (calls tool directly) |
| Handles pure-reasoning steps | No | **Yes** |
| One-time cost | Cheap (trace analysis) | Moderate (LLM codegen + verification) |
| Maintenance | Cache invalidates on SKILL.md change | Same — recompile on SKILL.md change |

## 4. Open Questions

1. **Which LLM for codegen?** The compilation LLM needs to be good at code generation. Claude or GPT-4 may produce better code than Gemini Flash. The codegen model doesn't need to be the same as the runtime model.

2. **How many traces for verification?** More is better (covers more edge cases). 3 is the minimum. 10 would catch most conditional branches. For dangerous_goods, the disposal_score=0 imputation case needs to appear in at least one trace for the compiled step 6 to handle it correctly.

3. **Incremental compilation**: Should we recompile when new traces reveal previously-unseen branches? Or is the initial compilation + verification sufficient?

4. **Mixed mode**: For SOPs where some steps are compilable and others aren't, the compiled steps produce structured `StepOutput` that the LLM can consume on the next step. Is `[Step N]: summary_text` as a user message sufficient context, or does the LLM need the full tool-call-and-response conversation format?

5. **CLI UX**: `proceda compile <skill_path>` for offline compilation. `proceda compile --verify` to re-verify against new traces. `proceda compile --show` to display compiled functions.
