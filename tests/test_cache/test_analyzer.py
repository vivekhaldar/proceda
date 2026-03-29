"""ABOUTME: Tests for trace analyzer that extracts step patterns from run traces.
ABOUTME: Uses synthetic traces written via EventLogWriter to verify pattern detection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from proceda.cache.analyzer import TraceAnalyzer
from proceda.cache.models import ArgumentSourceType
from proceda.events import EventType, RunEvent


def _write_trace(
    base_dir: Path,
    run_id: str,
    variables: dict[str, str],
    steps: list[dict[str, Any]],
) -> Path:
    """Write a synthetic run trace to disk.

    Each step dict can have:
        tool_calls: list of (tool_name, arguments) tuples
        tool_results: list of result dicts (parallel to tool_calls)
        summary: str
        has_clarification: bool
    """
    run_dir = base_dir / run_id
    run_dir.mkdir(parents=True)

    # Write metadata
    (run_dir / "metadata.json").write_text(
        json.dumps({"run_id": run_id, "skill_id": "test_skill", "variables": variables}),
        encoding="utf-8",
    )

    events: list[RunEvent] = []

    events.append(
        RunEvent.create(
            run_id,
            EventType.RUN_CREATED,
            {"skill_name": "test", "skill_id": "test_skill", "step_count": len(steps)},
        )
    )
    events.append(RunEvent.create(run_id, EventType.RUN_STARTED, {"skill_name": "test"}))

    for step_info in steps:
        step_idx = step_info["index"]
        events.append(
            RunEvent.create(
                run_id,
                EventType.STEP_STARTED,
                {"step_index": step_idx, "step_title": f"Step {step_idx}"},
            )
        )

        if step_info.get("has_clarification"):
            events.append(
                RunEvent.create(run_id, EventType.CLARIFICATION_REQUESTED, {"question": "?"})
            )

        for i, (tool_name, args) in enumerate(step_info.get("tool_calls", [])):
            events.append(
                RunEvent.create(
                    run_id,
                    EventType.TOOL_CALLED,
                    {
                        "tool_call_id": f"tc_{step_idx}_{i}",
                        "tool_name": tool_name,
                        "arguments": args,
                    },
                )
            )
            results = step_info.get("tool_results", [])
            result = results[i] if i < len(results) else {}
            events.append(
                RunEvent.create(
                    run_id,
                    EventType.TOOL_COMPLETED,
                    {
                        "tool_call_id": f"tc_{step_idx}_{i}",
                        "tool_name": tool_name,
                        "result": json.dumps(result),
                    },
                )
            )

        summary = step_info.get("summary", f"Step {step_idx} done")
        events.append(
            RunEvent.create(
                run_id,
                EventType.SUMMARY_GENERATED,
                {"step_index": step_idx, "summary": summary},
            )
        )
        events.append(
            RunEvent.create(
                run_id,
                EventType.STEP_COMPLETED,
                {"step_index": step_idx, "step_title": f"Step {step_idx}"},
            )
        )

    events.append(
        RunEvent.create(
            run_id,
            EventType.RUN_COMPLETED,
            {"completed_steps": len(steps), "total_steps": len(steps)},
        )
    )

    # Write events
    with open(run_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for evt in events:
            f.write(evt.to_json() + "\n")

    return run_dir


def test_variable_mapping(tmp_path):
    """3 traces, args from variables → VARIABLE mappings, confidence 1.0."""
    runs = []
    for i, pid in enumerate(["P_100", "P_200", "P_300"]):
        run_dir = _write_trace(
            tmp_path,
            f"run_{i}",
            variables={"product_id": pid, "label": f"label_{i}"},
            steps=[
                {
                    "index": 1,
                    "tool_calls": [("calc_score", {"product_id": pid, "label": f"label_{i}"})],
                    "tool_results": [{"score": i + 1}],
                }
            ],
        )
        runs.append(run_dir)

    analyzer = TraceAnalyzer(min_traces=3)
    cache = analyzer.analyze(runs, "skill content")

    assert cache is not None
    recipe = cache.get_recipe(1)
    assert recipe is not None
    assert recipe.confidence == 1.0
    assert len(recipe.tool_call_recipes) == 1

    tc = recipe.tool_call_recipes[0]
    assert tc.tool_name == "calc_score"
    assert tc.argument_mappings["product_id"].source == ArgumentSourceType.VARIABLE
    assert tc.argument_mappings["product_id"].key == "product_id"
    assert tc.argument_mappings["label"].source == ArgumentSourceType.VARIABLE
    assert tc.argument_mappings["label"].key == "label"


def test_step_result_mapping(tmp_path):
    """Step 2 uses a result from step 1 → STEP_RESULT mapping."""
    runs = []
    for i, pid in enumerate(["P_100", "P_200", "P_300"]):
        score = (i + 1) * 10
        run_dir = _write_trace(
            tmp_path,
            f"run_{i}",
            variables={"product_id": pid},
            steps=[
                {
                    "index": 1,
                    "tool_calls": [("get_score", {"product_id": pid})],
                    "tool_results": [{"product_id": pid, "score": score}],
                },
                {
                    "index": 2,
                    "tool_calls": [("use_score", {"score_value": str(score)})],
                    "tool_results": [{"result": "ok"}],
                },
            ],
        )
        runs.append(run_dir)

    analyzer = TraceAnalyzer(min_traces=3)
    cache = analyzer.analyze(runs, "skill content")

    assert cache is not None
    recipe = cache.get_recipe(2)
    assert recipe is not None
    assert recipe.confidence == 1.0

    tc = recipe.tool_call_recipes[0]
    assert tc.argument_mappings["score_value"].source == ArgumentSourceType.STEP_RESULT
    assert "step[1]" in tc.argument_mappings["score_value"].key
    assert "score" in tc.argument_mappings["score_value"].key


def test_literal_mapping(tmp_path):
    """Same literal arg across all runs → LITERAL mapping."""
    runs = []
    for i in range(3):
        run_dir = _write_trace(
            tmp_path,
            f"run_{i}",
            variables={"x": f"val_{i}"},
            steps=[
                {
                    "index": 1,
                    "tool_calls": [("do_thing", {"mode": "strict", "x": f"val_{i}"})],
                    "tool_results": [{"ok": True}],
                }
            ],
        )
        runs.append(run_dir)

    analyzer = TraceAnalyzer(min_traces=3)
    cache = analyzer.analyze(runs, "skill content")

    assert cache is not None
    recipe = cache.get_recipe(1)
    assert recipe is not None
    tc = recipe.tool_call_recipes[0]
    assert tc.argument_mappings["mode"].source == ArgumentSourceType.LITERAL
    assert tc.argument_mappings["mode"].literal_value == "strict"
    assert tc.argument_mappings["x"].source == ArgumentSourceType.VARIABLE


def test_inconsistent_tool_sequence(tmp_path):
    """Different tool sequences → low confidence."""
    runs = []
    for i in range(3):
        tool_name = "tool_a" if i < 2 else "tool_b"
        run_dir = _write_trace(
            tmp_path,
            f"run_{i}",
            variables={"x": "v"},
            steps=[
                {
                    "index": 1,
                    "tool_calls": [(tool_name, {"x": "v"})],
                    "tool_results": [{"ok": True}],
                }
            ],
        )
        runs.append(run_dir)

    analyzer = TraceAnalyzer(min_traces=3)
    cache = analyzer.analyze(runs, "skill content")

    assert cache is not None
    recipe = cache.get_recipe(1)
    # Either no recipe or low confidence
    if recipe is not None:
        assert recipe.confidence < 1.0


def test_clarification_step_not_cached(tmp_path):
    """Steps with clarification requests are not cacheable."""
    runs = []
    for i in range(3):
        run_dir = _write_trace(
            tmp_path,
            f"run_{i}",
            variables={"x": f"v{i}"},
            steps=[
                {
                    "index": 1,
                    "tool_calls": [("tool_a", {"x": f"v{i}"})],
                    "tool_results": [{"ok": True}],
                    "has_clarification": True,
                }
            ],
        )
        runs.append(run_dir)

    analyzer = TraceAnalyzer(min_traces=3)
    cache = analyzer.analyze(runs, "skill content")

    assert cache is not None
    recipe = cache.get_recipe(1)
    assert recipe is None or recipe.confidence == 0.0


def test_fewer_than_min_traces(tmp_path):
    """Fewer than min_traces → returns None."""
    runs = []
    for i in range(2):
        run_dir = _write_trace(
            tmp_path,
            f"run_{i}",
            variables={"x": f"v{i}"},
            steps=[
                {
                    "index": 1,
                    "tool_calls": [("tool_a", {"x": f"v{i}"})],
                    "tool_results": [{"ok": True}],
                }
            ],
        )
        runs.append(run_dir)

    analyzer = TraceAnalyzer(min_traces=3)
    cache = analyzer.analyze(runs, "skill content")
    assert cache is None


def test_unmappable_argument(tmp_path):
    """Argument that doesn't match any source → confidence 0 for that step."""
    runs = []
    for i in range(3):
        # arg value is random, not from variables or prior results
        run_dir = _write_trace(
            tmp_path,
            f"run_{i}",
            variables={"x": f"v{i}"},
            steps=[
                {
                    "index": 1,
                    "tool_calls": [("tool_a", {"x": f"v{i}", "mystery": f"random_{i * 7}"})],
                    "tool_results": [{"ok": True}],
                }
            ],
        )
        runs.append(run_dir)

    analyzer = TraceAnalyzer(min_traces=3)
    cache = analyzer.analyze(runs, "skill content")

    assert cache is not None
    recipe = cache.get_recipe(1)
    assert recipe is None or recipe.confidence == 0.0


def test_no_tool_calls_step(tmp_path):
    """Steps with no tool calls get confidence 0 (not cacheable at L2)."""
    runs = []
    for i in range(3):
        run_dir = _write_trace(
            tmp_path,
            f"run_{i}",
            variables={"x": f"v{i}"},
            steps=[
                {
                    "index": 1,
                    "tool_calls": [],
                    "tool_results": [],
                }
            ],
        )
        runs.append(run_dir)

    analyzer = TraceAnalyzer(min_traces=3)
    cache = analyzer.analyze(runs, "skill content")

    assert cache is not None
    recipe = cache.get_recipe(1)
    assert recipe is None or recipe.confidence == 0.0
