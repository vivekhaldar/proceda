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

**Input**: SKILL.md + N successful run traces (N >= 10 recommended, minimum 5)

**Philosophy**: Try to compile every step. Don't try to classify steps as
"compilable" or "not compilable" upfront — you can't know from the SOP text
alone whether the logic is deterministic. Instead: generate code for every
step, verify against held-out traces, and keep only the steps that pass.
Steps requiring language understanding or judgment will fail verification
naturally because the generated code can't reproduce the LLM's nuanced
reasoning across diverse inputs.

**Trace split**: Divide N traces into two sets:
- **Prompt examples** (K=3): shown to the codegen LLM as input/output pairs
- **Held-out verification** (N-K): used only for verification, never seen by the codegen LLM

This train/test split is critical for catching overfitting. If the generated
code passes on 7 unseen traces, it's implementing the actual logic, not
memorizing the 3 examples.

**For each step**:

1. **Extract examples** from all N traces: for each run, collect the step's
   inputs (variables + prior step outputs) and outputs (captured values, tool
   results, summary, whether it terminated).

2. **Build the codegen prompt** (see full prompt below).

3. **Call the codegen LLM** (one-time, offline) to generate the function.

4. **Verify** against ALL N traces (including the K prompt examples):
   - For each trace, call `execute(variables, prior_steps, mock_tool_caller)`
     where `mock_tool_caller` replays the actual tool results from the trace.
   - Compare `StepOutput.captured_values` against the trace's values.
   - Compare `output_fields` on the final step.
   - If **any** held-out trace doesn't match, the step is not compilable.

5. **Save** verified functions to `.proceda/compiled/{skill_id}/step_{N}.py`.

### The codegen prompt

The prompt is designed around one principle: **the SOP text is the
specification, the traces are test cases.** The LLM should implement the
rules described in the SOP, not pattern-match on the examples.

```
You are generating a Python function that implements one step of a Standard
Operating Procedure (SOP). The function will replace an LLM in executing
this step, so it must correctly implement the step's logic for ANY valid
input, not just the examples shown.

## The SOP step to implement

Step {step_index}: {step_title}

{step_content}

## Function interface

```python
@dataclass
class StepOutput:
    summary: str                             # Human-readable description of what happened
    captured_values: dict[str, Any]          # Values for use by subsequent steps
    output_fields: dict[str, str] | None     # Final output XML fields (last step only)
    terminate: bool = False                  # Stop execution early
    terminate_values: dict[str, str] | None  # Output fields on early termination

async def execute(
    variables: dict[str, str],               # Session variables
    prior_steps: dict[int, dict[str, Any]],  # Results from completed steps
    call_tool: Callable[[str, dict], Awaitable[dict]],  # MCP tool caller
) -> StepOutput:
```

## Available variables

These keys are always present in `variables`:
{variable_names_and_descriptions}

## Results from prior steps

Steps 1 through {step_index - 1} have already completed. Their results are
in `prior_steps[step_index]` as a dict. Here is what each prior step
produces:
{prior_step_output_descriptions}

## Example executions (for reference only — do NOT hardcode these)

These show what correct execution looks like for a few inputs.
Your function must handle ANY valid input, including cases not shown here.

{for each of K prompt examples:}
### Example {i}
Input variables: {variables}
Prior step results: {prior_steps}
{if step has tool calls:}
Tool call: {tool_name}({args}) → {result}
{end}
Expected output: captured_values={captured_values}, output_fields={output_fields}
{if terminated:} (terminated with: {terminate_values})
{end for}

## Rules

1. Implement the logic described in the SOP step text above. The SOP text
   is the specification — follow its rules, conditions, and formulas exactly.

2. The examples are test cases, not the spec. Your function must work for
   inputs NOT shown in the examples. Do not hardcode values, thresholds,
   or mappings from the examples — derive them from the SOP text.

3. If the step says to call a tool, call it via `await call_tool(name, args)`.
   The tool returns a dict. Do not assume specific return values — use
   whatever the tool returns.

4. Handle edge cases described in the SOP text even if no example covers
   them. For instance, if the SOP says "if score is missing, impute it",
   implement that logic even if all examples have valid scores.

5. Do not import external libraries beyond the Python standard library.

6. Return a StepOutput with:
   - `summary`: a brief human-readable description of what happened
   - `captured_values`: a dict of values that later steps might need
   - `output_fields`: only on the final step, a dict of the output XML fields
   - `terminate` / `terminate_values`: only if the SOP says to terminate early

Generate ONLY the `async def execute(...)` function body. No imports,
no class definitions, no test code.
```

### Why this prompt avoids overfitting

1. **"The SOP text is the specification"** — The LLM is told to implement
   from the written rules, not from the examples. A step that says
   "if score <= 8, class is A; if score <= 12, class is B" gets those
   thresholds from the text, not from seeing that score=9 maps to B in
   one example.

2. **"Do NOT hardcode these"** — Explicit instruction against the most
   common overfitting pattern: extracting constants from examples.

3. **"Handle edge cases described in the SOP text even if no example
   covers them"** — Forces the LLM to reason about the full input space,
   not just the demonstrated inputs.

4. **Train/test split** — Only 3 examples are shown; 7+ are held out for
   verification. Code that memorizes 3 examples will fail on the 7 unseen
   ones. This is the strongest overfitting defense.

5. **"Do not assume specific return values"** — Prevents hardcoding tool
   results. The function must handle whatever the tool returns.

### What happens when compilation fails

A step fails compilation when the generated code can't reproduce the
held-out trace outputs. This tells you something useful:

- **The step requires language understanding**: the code can't replicate
  the LLM's nuanced reasoning. Example: "identify red flags and exercise
  caution" — no function can implement this without an LLM.

- **The step has complex conditional logic** not fully covered by the
  traces. More traces might help, or the step may be genuinely too complex
  to compile.

- **The codegen LLM made a mistake**: retry with a different prompt or
  model. The compiler can attempt up to M retries with feedback about which
  trace failed and how.

In all cases, the step stays on the LLM at runtime. No harm done.

### Verification details

Comparison is done on `captured_values` and `output_fields`, not on
`summary` text. Summaries are for human consumption and don't need to
match verbatim — they just need to be reasonable descriptions.

For `captured_values`, comparison uses these rules:
- Numeric values: exact match (int) or within epsilon (float)
- String values: case-insensitive stripped match
- Missing keys in either side: mismatch

For tool-calling steps, the verifier provides a `mock_tool_caller` that
replays the tool results recorded in the trace. This ensures the function
is tested against real tool behavior without actually calling the tools.

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

### No upfront classification needed

You don't decide "is this step compilable?" beforehand. The compiler
tries every step. Verification tells you which ones worked.

Steps requiring judgment or language understanding fail verification
naturally — the generated code can't reproduce the LLM's nuanced
reasoning across diverse inputs. Steps with deterministic logic pass
verification because the code implements the same rules the LLM was
following.

This eliminates false negatives (steps you thought were too complex but
are actually compilable) and false positives (steps you thought were
simple but have hidden complexity). The verifier is the judge, not a
heuristic.

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
  "num_traces_total": 10,
  "num_prompt_examples": 3,
  "num_held_out": 7,
  "steps": {
    "1": {"status": "compiled", "verified_against": 10, "held_out_passed": 7, "failures": 0},
    "2": {"status": "compiled", "verified_against": 10, "held_out_passed": 7, "failures": 0},
    "6": {"status": "compiled", "verified_against": 10, "held_out_passed": 7, "failures": 0},
    "7": {"status": "compiled", "verified_against": 10, "held_out_passed": 7, "failures": 0},
    "4": {"status": "failed", "verified_against": 10, "held_out_passed": 4, "failures": 3,
          "failure_reason": "Could not reproduce held-out outputs for 3 traces"}
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

2. **How many traces?** The train/test split requires enough traces for both the prompt examples (K=3) and held-out verification. With N=10, you get 3 prompt + 7 held-out. With N=5, you get 3 prompt + 2 held-out — thin but workable. For dangerous_goods, the disposal_score=0 imputation case needs to appear in at least one held-out trace for the compiled step 6 to be verified against it.

3. **Retry on failure**: When verification fails, the compiler could retry with the failing trace's input/output added to the prompt examples (expanding K from 3 to 4) and the error message. This gives the codegen LLM feedback about what went wrong. Max M=3 retries per step before giving up.

4. **Incremental recompilation**: When new traces reveal branches the compiled code doesn't handle (runtime fallback fires), those traces should be added to the verification set. If the compiled function fails on the new trace, recompile with the expanded trace set. This lets compilation improve over time.

5. **Mixed mode context**: When some steps are compiled and others use the LLM, the compiled steps add a user-role message `[Step N]: summary`. This worked poorly for L2 (see cache eval report). The difference here: compiled steps produce richer, more descriptive summaries (they're generated by the codegen LLM to be informative, not terse). But this needs to be validated empirically — if the LLM on step N+1 needs conversational context from step N, a summary may not suffice.

6. **CLI UX**: `proceda compile <skill_path>` for offline compilation. `proceda compile --verify` to re-verify against new traces. `proceda compile --show` to display compiled functions and their verification status.
