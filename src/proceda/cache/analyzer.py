"""ABOUTME: Extracts execution patterns from historical traces to build step-level cache.
ABOUTME: Compares N traces to identify stable tool call patterns and argument mappings."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from proceda.cache.models import (
    ArgumentMapping,
    ArgumentSourceType,
    ResultCapture,
    SkillCache,
    StepRecipe,
    ToolCallRecipe,
)
from proceda.cache.store import CacheStore
from proceda.events import EventType, RunEvent
from proceda.store.event_log import EventLogReader

logger = logging.getLogger(__name__)


@dataclass
class _RunTrace:
    """Parsed data from a single run's trace."""

    run_id: str
    variables: dict[str, str]
    events: list[RunEvent]


@dataclass
class _StepPattern:
    """Extracted pattern from a single step in a single run."""

    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    has_clarification: bool = False
    summary: str = ""


class TraceAnalyzer:
    """Analyzes multiple run traces to extract cacheable step patterns."""

    def __init__(self, min_traces: int = 3) -> None:
        self._min_traces = min_traces

    def analyze(
        self,
        run_dirs: list[Path],
        skill_raw_content: str,
    ) -> SkillCache | None:
        """Analyze traces from multiple runs and build a SkillCache."""
        traces = []
        for run_dir in run_dirs:
            trace = self._load_run(run_dir)
            if trace is not None:
                traces.append(trace)

        if len(traces) < self._min_traces:
            logger.info("Not enough successful traces (%d < %d)", len(traces), self._min_traces)
            return None

        traces = traces[: self._min_traces]

        # Group events by step for each trace
        steps_per_run = [self._group_events_by_step(t.events) for t in traces]
        variables_per_run = [t.variables for t in traces]

        # Find all step indices present in all runs
        all_step_sets = [set(s.keys()) for s in steps_per_run]
        common_steps = all_step_sets[0]
        for s in all_step_sets[1:]:
            common_steps &= s

        # Build recipes step by step, accumulating result captures for cross-step refs
        step_recipes: dict[int, StepRecipe] = {}
        captured_results_per_run: list[dict[int, dict[str, Any]]] = [{} for _ in traces]

        for step_idx in sorted(common_steps):
            patterns = []
            for run_idx, steps in enumerate(steps_per_run):
                pattern = self._extract_step_pattern(steps[step_idx])
                patterns.append(pattern)

            recipe = self._compare_patterns(
                step_idx, patterns, variables_per_run, captured_results_per_run
            )

            if recipe.confidence > 0:
                step_recipes[step_idx] = recipe

            # Track tool results for cross-step references
            for run_idx, pattern in enumerate(patterns):
                for _tool_name, _args in pattern.tool_calls:
                    pass
                # Extract result fields from TOOL_COMPLETED events in this step
                for evt in steps_per_run[run_idx].get(step_idx, []):
                    if evt.type == EventType.TOOL_COMPLETED:
                        content = evt.payload.get("result", evt.payload.get("content", ""))
                        if isinstance(content, str):
                            try:
                                parsed = json.loads(content)
                                if isinstance(parsed, dict):
                                    captured_results_per_run[run_idx][step_idx] = parsed
                            except (json.JSONDecodeError, TypeError):
                                pass
                        elif isinstance(content, dict):
                            captured_results_per_run[run_idx][step_idx] = content

        return SkillCache(
            skill_id="",  # Caller should set this from the Skill object
            skill_content_hash=CacheStore.compute_content_hash(skill_raw_content),
            step_recipes=step_recipes,
            source_run_ids=[t.run_id for t in traces],
            created_at=datetime.now(UTC).isoformat(),
        )

    def _load_run(self, run_dir: Path) -> _RunTrace | None:
        """Load a single run trace. Returns None if run was not successful."""
        reader = EventLogReader(run_dir)
        if not reader.exists:
            return None

        metadata = reader.read_metadata()
        events = reader.read_events()

        # Check for successful completion
        if not any(e.type == EventType.RUN_COMPLETED for e in events):
            return None

        return _RunTrace(
            run_id=metadata.get("run_id", "unknown"),
            variables=metadata.get("variables", {}),
            events=events,
        )

    def _group_events_by_step(self, events: list[RunEvent]) -> dict[int, list[RunEvent]]:
        """Group events by step_index, splitting on STEP_STARTED."""
        groups: dict[int, list[RunEvent]] = {}
        current_step: int | None = None

        for evt in events:
            if evt.type == EventType.STEP_STARTED:
                current_step = evt.payload.get("step_index")
                if current_step is not None:
                    groups[current_step] = []
            elif current_step is not None:
                if evt.type in (EventType.STEP_COMPLETED, EventType.STEP_SKIPPED):
                    groups[current_step].append(evt)
                    current_step = None
                else:
                    groups[current_step].append(evt)

        return groups

    def _extract_step_pattern(self, step_events: list[RunEvent]) -> _StepPattern:
        """Extract the tool call pattern from one step's events."""
        tool_calls: list[tuple[str, dict[str, Any]]] = []
        has_clarification = False
        summary = ""

        for evt in step_events:
            if evt.type == EventType.TOOL_CALLED:
                tool_name = evt.payload.get("tool_name", "")
                arguments = evt.payload.get("arguments", {})
                tool_calls.append((tool_name, arguments))
            elif evt.type == EventType.CLARIFICATION_REQUESTED:
                has_clarification = True
            elif evt.type == EventType.SUMMARY_GENERATED:
                summary = evt.payload.get("summary", "")

        return _StepPattern(
            tool_calls=tool_calls,
            has_clarification=has_clarification,
            summary=summary,
        )

    def _compare_patterns(
        self,
        step_index: int,
        patterns: list[_StepPattern],
        variables_per_run: list[dict[str, str]],
        prior_results_per_run: list[dict[int, dict[str, Any]]],
    ) -> StepRecipe:
        """Compare patterns across N runs to build a StepRecipe."""
        # Steps with clarification are not cacheable
        if any(p.has_clarification for p in patterns):
            return StepRecipe(step_index=step_index, confidence=0.0)

        # Steps with no tool calls — not cacheable at L2 but we still note them
        if all(len(p.tool_calls) == 0 for p in patterns):
            return StepRecipe(step_index=step_index, confidence=0.0)

        # Check consistency: same number of tool calls with same tool names
        reference = patterns[0]
        ref_sequence = [name for name, _args in reference.tool_calls]

        matching = sum(
            1 for p in patterns if [name for name, _args in p.tool_calls] == ref_sequence
        )
        confidence = matching / len(patterns)

        if confidence < 0.5:
            return StepRecipe(step_index=step_index, confidence=confidence)

        # Derive argument mappings for each tool call
        tool_call_recipes: list[ToolCallRecipe] = []
        for tc_idx, (tool_name, _ref_args) in enumerate(reference.tool_calls):
            # Collect argument values across runs for this tool call
            all_args: dict[str, list[Any]] = {}
            for p in patterns:
                if tc_idx < len(p.tool_calls):
                    _name, args = p.tool_calls[tc_idx]
                    for arg_name, arg_value in args.items():
                        all_args.setdefault(arg_name, []).append(arg_value)

            mappings: dict[str, ArgumentMapping] = {}
            for arg_name, values in all_args.items():
                if len(values) != len(patterns):
                    # Argument not present in all runs
                    confidence = 0.0
                    break
                mapping = self._infer_argument_source(
                    arg_name, values, variables_per_run, prior_results_per_run
                )
                if mapping is None:
                    confidence = 0.0
                    break
                mappings[arg_name] = mapping

            if confidence == 0.0:
                return StepRecipe(step_index=step_index, confidence=0.0)

            tool_call_recipes.append(
                ToolCallRecipe(tool_name=tool_name, argument_mappings=mappings)
            )

        # Derive result captures (fields from tool results)
        result_captures: list[ResultCapture] = []
        # We capture all fields from tool results — they may be used by later steps
        for tc_idx in range(len(reference.tool_calls)):
            # Look at what the first run's tool result contained
            # (we already stored these in prior_results_per_run for later steps)
            pass

        # Build summary template from first run
        summary_template = reference.summary if reference.summary else ""

        return StepRecipe(
            step_index=step_index,
            tool_call_recipes=tool_call_recipes,
            result_captures=result_captures,
            summary_template=summary_template,
            confidence=confidence,
        )

    def _infer_argument_source(
        self,
        arg_name: str,
        values_across_runs: list[Any],
        variables_per_run: list[dict[str, str]],
        prior_results_per_run: list[dict[int, dict[str, Any]]],
    ) -> ArgumentMapping | None:
        """Infer the source of a single argument by comparing across runs."""
        n = len(values_across_runs)

        # Try VARIABLE source
        if variables_per_run[0]:
            for var_key in variables_per_run[0]:
                if all(
                    str(values_across_runs[i]) == str(variables_per_run[i].get(var_key))
                    for i in range(n)
                ):
                    return ArgumentMapping(
                        source=ArgumentSourceType.VARIABLE,
                        key=var_key,
                    )

        # Try STEP_RESULT source
        if prior_results_per_run[0]:
            for step_idx, fields in prior_results_per_run[0].items():
                for field_name in fields:
                    if all(
                        str(values_across_runs[i])
                        == str(prior_results_per_run[i].get(step_idx, {}).get(field_name))
                        for i in range(n)
                    ):
                        return ArgumentMapping(
                            source=ArgumentSourceType.STEP_RESULT,
                            key=f"step[{step_idx}].{field_name}",
                        )

        # Try LITERAL source
        if len(set(str(v) for v in values_across_runs)) == 1:
            return ArgumentMapping(
                source=ArgumentSourceType.LITERAL,
                literal_value=values_across_runs[0],
            )

        return None
