"""ABOUTME: Generates Python functions from SOP steps using LLM codegen.
ABOUTME: Verifies generated code against held-out traces before accepting."""

from __future__ import annotations

import json
import logging
import re
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from proceda.cache.store import CacheStore
from proceda.compile.models import (
    CompiledSkillManifest,
    CompiledStepInfo,
)
from proceda.events import EventType
from proceda.skill import Skill
from proceda.store.event_log import EventLogReader

logger = logging.getLogger(__name__)


@dataclass
class _StepTrace:
    """Extracted input/output data for one step in one run."""

    variables: dict[str, str]
    prior_steps: dict[int, dict[str, Any]]
    tool_calls: list[tuple[str, dict[str, Any]]]  # (tool_name, args)
    tool_results: list[dict[str, Any]]
    captured_values: dict[str, Any]
    summary: str
    terminated: bool = False


@dataclass
class _RunData:
    """All step data from a single run."""

    run_id: str
    variables: dict[str, str]
    step_traces: dict[int, _StepTrace] = field(default_factory=dict)


def _build_codegen_prompt(
    step_index: int,
    step_title: str,
    step_content: str,
    skill: Skill,
    examples: list[_StepTrace],
    is_last_step: bool,
) -> str:
    """Build the codegen prompt for a single step."""
    # Describe available variables
    var_names = set()
    for ex in examples:
        var_names.update(ex.variables.keys())
    var_list = ", ".join(sorted(var_names))

    # Describe prior step outputs
    prior_desc_lines = []
    for ex in examples:
        for idx, vals in sorted(ex.prior_steps.items()):
            keys = list(vals.keys())
            prior_desc_lines.append(f"  Step {idx}: {', '.join(keys)}")
    prior_desc = "\n".join(sorted(set(prior_desc_lines))) if prior_desc_lines else "  (none)"

    # Format examples
    example_blocks = []
    for i, ex in enumerate(examples, 1):
        lines = [f"### Example {i}"]
        lines.append(f"Input variables: {json.dumps(ex.variables)}")
        if ex.prior_steps:
            lines.append(f"Prior step results: {json.dumps(ex.prior_steps)}")
        for (tool_name, args), result in zip(ex.tool_calls, ex.tool_results):
            lines.append(f"Tool call: {tool_name}({json.dumps(args)}) → {json.dumps(result)}")
        lines.append(f"Expected output: captured_values={json.dumps(ex.captured_values)}")
        if is_last_step and ex.captured_values:
            # Show output fields for last step
            pass
        if ex.terminated:
            lines.append("(step terminated early)")
        example_blocks.append("\n".join(lines))

    examples_text = "\n\n".join(example_blocks)

    output_fields_note = ""
    if is_last_step and skill.output_fields:
        fields = ", ".join(skill.output_fields)
        output_fields_note = textwrap.dedent(f"""
        This is the FINAL step. You MUST set `output_fields` in the StepOutput
        with these keys: {fields}
        The values should be strings suitable for XML output tags.
        """)

    return textwrap.dedent(f"""\
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
        summary: str                             # Human-readable description
        captured_values: dict[str, Any]          # Values for subsequent steps
        output_fields: dict[str, str] | None     # Final output XML fields (last step only)
        terminate: bool = False                  # Stop execution early
        terminate_values: dict[str, str] | None  # Output fields on early termination
    ```

    Write an `async def execute(variables, prior_steps, call_tool)` function where:
    - `variables: dict[str, str]` — session variables
    - `prior_steps: dict[int, dict[str, Any]]` — results from completed steps
    - `call_tool: Callable[[str, dict], Awaitable[dict]]` — calls an MCP tool
    {output_fields_note}
    ## Available variables

    Keys in `variables`: {var_list}

    ## Results from prior steps

    {prior_desc}

    ## Example executions (for reference only — do NOT hardcode these)

    These show what correct execution looks like for a few inputs.
    Your function must handle ANY valid input, including cases not shown here.

    {examples_text}

    ## Rules

    1. Implement the logic described in the SOP step text above. The SOP text
       is the specification — follow its rules, conditions, and formulas exactly.

    2. The examples are test cases, not the spec. Your function must work for
       inputs NOT shown in the examples. Do not hardcode values, thresholds,
       or mappings from the examples — derive them from the SOP text.

    3. If the step says to call a tool, call it via `await call_tool(name, args)`.
       The tool returns a dict. Do not assume specific return values.

    4. Handle edge cases described in the SOP text even if no example covers them.

    5. Do not import external libraries beyond the Python standard library.
       You may use `import re`, `import json`, etc.

    6. Return a StepOutput with appropriate fields.

    Generate ONLY the function body inside `async def execute(variables, prior_steps, call_tool):`.
    Start directly with the function code, no ```python fences, no imports, no class definitions.
    """)


def _extract_function_code(llm_response: str) -> str:
    """Extract the function body from the LLM response."""
    # Strip markdown code fences if present
    code = llm_response.strip()
    code = re.sub(r"^```python\s*\n?", "", code)
    code = re.sub(r"\n?```\s*$", "", code)
    code = code.strip()

    # If the response starts with "async def execute", extract the body
    if code.startswith("async def execute"):
        # Find the first line after the def, strip the def line
        lines = code.split("\n")
        # Skip the def line and any docstring
        body_lines = lines[1:]
        code = "\n".join(body_lines)

    return code


def _build_step_function(function_body: str) -> str:
    """Wrap a function body into a complete module."""
    return textwrap.dedent("""\
    from __future__ import annotations
    import json
    import re
    from dataclasses import dataclass, field
    from typing import Any, Callable, Awaitable

    @dataclass
    class StepOutput:
        summary: str
        captured_values: dict[str, Any] = field(default_factory=dict)
        output_fields: dict[str, str] | None = None
        terminate: bool = False
        terminate_values: dict[str, str] | None = None

    async def execute(
        variables: dict[str, str],
        prior_steps: dict[int, dict[str, Any]],
        call_tool: Callable[[str, dict], Awaitable[dict]],
    ) -> StepOutput:
    """) + textwrap.indent(function_body, "    ")


async def _verify_step(
    function_code: str,
    traces: list[_StepTrace],
    step_index: int,
) -> tuple[int, int, str]:
    """Verify a compiled function against traces.

    Returns (passed, failed, error_message).
    """
    # Write to a temp module and import it
    import importlib.util
    import tempfile

    module_code = _build_step_function(function_code)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(module_code)
        f.flush()
        temp_path = f.name

    try:
        spec = importlib.util.spec_from_file_location("compiled_step", temp_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load spec from {temp_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        execute_fn = mod.execute
    except Exception as e:
        Path(temp_path).unlink(missing_ok=True)
        return 0, len(traces), f"Failed to load compiled function: {e}"

    passed = 0
    failed = 0
    error_msg = ""

    for i, trace in enumerate(traces):
        # Build mock tool caller that replays trace tool results
        tool_call_idx = 0
        tool_results_copy = list(trace.tool_results)

        async def mock_call_tool(name: str, args: dict) -> dict:
            nonlocal tool_call_idx
            if tool_call_idx < len(tool_results_copy):
                result = tool_results_copy[tool_call_idx]
                tool_call_idx += 1
                return result
            return {}

        try:
            output = await execute_fn(trace.variables, trace.prior_steps, mock_call_tool)

            # Compare captured_values
            match = True
            for key, expected_val in trace.captured_values.items():
                actual_val = output.captured_values.get(key)
                if actual_val is None:
                    match = False
                    error_msg = (
                        f"Trace {i}: missing key '{key}' in captured_values. "
                        f"Expected {expected_val}, got keys {list(output.captured_values.keys())}"
                    )
                    break
                # Flexible comparison
                if not _values_match(actual_val, expected_val):
                    match = False
                    error_msg = (
                        f"Trace {i}: key '{key}' mismatch. "
                        f"Expected {expected_val!r}, got {actual_val!r}"
                    )
                    break

            if match:
                passed += 1
            else:
                failed += 1

        except Exception as e:
            failed += 1
            error_msg = f"Trace {i}: execution error: {e}"

    Path(temp_path).unlink(missing_ok=True)
    return passed, failed, error_msg


def _values_match(actual: Any, expected: Any) -> bool:
    """Flexible value comparison for verification."""
    # Exact match
    if actual == expected:
        return True
    # String comparison (case-insensitive, stripped)
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().lower() == expected.strip().lower()
    # Numeric: compare as numbers
    try:
        return float(actual) == float(expected)
    except (ValueError, TypeError):
        pass
    # String vs number
    try:
        return str(actual).strip() == str(expected).strip()
    except (ValueError, TypeError):
        pass
    return False


def _load_runs(
    skill: Skill,
    run_dir_base: str,
    max_runs: int = 10,
) -> list[_RunData]:
    """Load completed baseline runs for a skill."""
    from proceda.store.event_log import RunDirectoryManager

    dir_manager = RunDirectoryManager(run_dir_base)
    all_dirs = dir_manager.list_runs()

    runs = []
    for run_dir in all_dirs:
        reader = EventLogReader(run_dir)
        if not reader.exists:
            continue
        metadata = reader.read_metadata()
        if metadata.get("skill_id") != skill.id:
            continue

        events = reader.read_events()
        # Only use completed, non-cached runs
        if not any(e.type == EventType.RUN_COMPLETED for e in events):
            continue
        if any(e.type in (EventType.CACHE_HIT, EventType.CACHE_MISS) for e in events):
            continue

        variables = metadata.get("variables", {})
        run_data = _RunData(
            run_id=metadata.get("run_id", "unknown"),
            variables=variables,
        )

        # Extract per-step data
        current_step = 0
        step_tool_calls: list[tuple[str, dict]] = []
        step_tool_results: list[dict] = []
        accumulated_results: dict[int, dict[str, Any]] = {}

        for evt in events:
            if evt.type == EventType.STEP_STARTED:
                current_step = evt.payload.get("step_index", 0)
                step_tool_calls = []
                step_tool_results = []

            elif evt.type == EventType.TOOL_CALLED and current_step:
                step_tool_calls.append(
                    (evt.payload.get("tool_name", ""), evt.payload.get("arguments", {}))
                )

            elif evt.type == EventType.TOOL_COMPLETED and current_step:
                result_text = evt.payload.get("result", evt.payload.get("content", ""))
                try:
                    parsed = (
                        json.loads(result_text) if isinstance(result_text, str) else result_text
                    )
                    if isinstance(parsed, dict):
                        step_tool_results.append(parsed)
                    else:
                        step_tool_results.append({})
                except (json.JSONDecodeError, TypeError):
                    step_tool_results.append({})

            elif evt.type == EventType.SUMMARY_GENERATED and current_step:
                summary = evt.payload.get("summary", "")
                # Build captured values from tool results
                captured = {}
                for r in step_tool_results:
                    captured.update(r)

                run_data.step_traces[current_step] = _StepTrace(
                    variables=variables,
                    prior_steps=dict(accumulated_results),
                    tool_calls=list(step_tool_calls),
                    tool_results=list(step_tool_results),
                    captured_values=captured,
                    summary=summary,
                )
                accumulated_results[current_step] = captured

            elif evt.type == EventType.STEP_COMPLETED and current_step:
                # If no summary was generated (force-completed), still record
                if current_step not in run_data.step_traces:
                    captured = {}
                    for r in step_tool_results:
                        captured.update(r)
                    run_data.step_traces[current_step] = _StepTrace(
                        variables=variables,
                        prior_steps=dict(accumulated_results),
                        tool_calls=list(step_tool_calls),
                        tool_results=list(step_tool_results),
                        captured_values=captured,
                        summary="",
                    )
                    accumulated_results[current_step] = captured

        if run_data.step_traces:
            runs.append(run_data)
        if len(runs) >= max_runs:
            break

    return runs


async def compile_skill(
    skill: Skill,
    run_dir_base: str = ".proceda/runs",
    num_traces: int = 10,
    num_prompt_examples: int = 3,
    codegen_model: str = "anthropic/claude-sonnet-4-20250514",
    max_retries: int = 2,
) -> tuple[CompiledSkillManifest, dict[int, str]]:
    """Compile a skill's steps into Python functions.

    Returns (manifest, compiled_functions) where compiled_functions maps
    step_index → function body string (only for successfully compiled steps).
    """
    import litellm

    runs = _load_runs(skill, run_dir_base, max_runs=num_traces)
    if len(runs) < num_prompt_examples + 2:
        raise ValueError(f"Need at least {num_prompt_examples + 2} traces, found {len(runs)}")

    runs = runs[:num_traces]
    prompt_runs = runs[:num_prompt_examples]
    held_out_runs = runs[num_prompt_examples:]

    manifest = CompiledSkillManifest(
        skill_id=skill.id,
        skill_content_hash=CacheStore.compute_content_hash(skill.raw_content),
        compiled_at=datetime.now(UTC).isoformat(),
        model_used=codegen_model,
        num_traces_total=len(runs),
        num_prompt_examples=len(prompt_runs),
        num_held_out=len(held_out_runs),
    )

    compiled_functions: dict[int, str] = {}

    for step in skill.steps:
        step_idx = step.index
        is_last = step_idx == skill.step_count

        # Gather traces for this step
        prompt_traces = [r.step_traces[step_idx] for r in prompt_runs if step_idx in r.step_traces]
        all_traces = [r.step_traces[step_idx] for r in runs if step_idx in r.step_traces]
        held_out_traces = [
            r.step_traces[step_idx] for r in held_out_runs if step_idx in r.step_traces
        ]

        if len(prompt_traces) < 2 or len(held_out_traces) < 1:
            manifest.steps[step_idx] = CompiledStepInfo(
                step_index=step_idx,
                status="failed",
                failure_reason=(
                    f"Not enough traces (prompt={len(prompt_traces)}, "
                    f"held_out={len(held_out_traces)})"
                ),
            )
            continue

        prompt = _build_codegen_prompt(
            step_idx, step.title, step.content, skill, prompt_traces, is_last
        )

        # Try codegen with retries
        best_code = None
        best_passed = 0
        best_error = ""

        for attempt in range(1 + max_retries):
            retry_note = ""
            if attempt > 0 and best_error:
                retry_note = (
                    f"\n\nPrevious attempt failed verification: {best_error}\n"
                    "Fix the issue and try again."
                )

            try:
                response = await litellm.acompletion(
                    model=codegen_model,
                    messages=[{"role": "user", "content": prompt + retry_note}],
                    temperature=0.0,
                    max_tokens=4096,
                )
                code = _extract_function_code(response.choices[0].message.content or "")
            except Exception as e:
                logger.warning("Codegen LLM call failed for step %d: %s", step_idx, e)
                best_error = f"LLM call failed: {e}"
                continue

            if not code.strip():
                best_error = "Empty response from codegen LLM"
                continue

            # Verify against ALL traces
            passed, failed, error = await _verify_step(code, all_traces, step_idx)

            if failed == 0:
                best_code = code
                best_passed = passed
                best_error = ""
                break
            elif passed > best_passed:
                best_code = code
                best_passed = passed
                best_error = error

        if best_code and best_error == "":
            manifest.steps[step_idx] = CompiledStepInfo(
                step_index=step_idx,
                status="compiled",
                verified_against=len(all_traces),
                held_out_passed=len(held_out_traces),
                failures=0,
            )
            compiled_functions[step_idx] = best_code
            logger.info(
                "Step %d: compiled and verified against %d traces", step_idx, len(all_traces)
            )
        else:
            manifest.steps[step_idx] = CompiledStepInfo(
                step_index=step_idx,
                status="failed",
                verified_against=len(all_traces),
                held_out_passed=best_passed,
                failures=len(all_traces) - best_passed,
                failure_reason=best_error,
            )
            logger.info(
                "Step %d: compilation failed (%d/%d passed). %s",
                step_idx,
                best_passed,
                len(all_traces),
                best_error,
            )

    return manifest, compiled_functions
