"""ABOUTME: Tests for recipe executor that runs cached tool calls directly.
ABOUTME: Verifies argument resolution, tool execution, and error handling."""

from __future__ import annotations

import pytest

from proceda.cache.executor import RecipeExecutionError, RecipeExecutor, parse_step_result_key
from proceda.cache.models import (
    ArgumentMapping,
    ArgumentSourceType,
    ResultCapture,
    StepRecipe,
    ToolCallRecipe,
)
from proceda.session import RunSession


def test_parse_step_result_key_valid():
    assert parse_step_result_key("step[2].score") == (2, "score")
    assert parse_step_result_key("step[10].field_name") == (10, "field_name")


def test_parse_step_result_key_invalid():
    with pytest.raises(ValueError):
        parse_step_result_key("bad_format")
    with pytest.raises(ValueError):
        parse_step_result_key("step.score")


def test_resolve_variable():
    session = RunSession.create("s1", "test", variables={"product_id": "P_123"})
    executor = RecipeExecutor(session, None, None, {})
    mapping = ArgumentMapping(source=ArgumentSourceType.VARIABLE, key="product_id")
    assert executor._resolve_argument(mapping) == "P_123"


def test_resolve_variable_missing():
    session = RunSession.create("s1", "test", variables={"x": "1"})
    executor = RecipeExecutor(session, None, None, {})
    mapping = ArgumentMapping(source=ArgumentSourceType.VARIABLE, key="missing_key")
    with pytest.raises(RecipeExecutionError, match="missing_key"):
        executor._resolve_argument(mapping)


def test_resolve_step_result():
    session = RunSession.create("s1", "test")
    step_results = {1: {"score": 42, "label": "A"}}
    executor = RecipeExecutor(session, None, None, step_results)
    mapping = ArgumentMapping(source=ArgumentSourceType.STEP_RESULT, key="step[1].score")
    assert executor._resolve_argument(mapping) == 42


def test_resolve_step_result_missing_step():
    session = RunSession.create("s1", "test")
    executor = RecipeExecutor(session, None, None, {})
    mapping = ArgumentMapping(source=ArgumentSourceType.STEP_RESULT, key="step[5].score")
    with pytest.raises(RecipeExecutionError, match="step 5"):
        executor._resolve_argument(mapping)


def test_resolve_literal():
    session = RunSession.create("s1", "test")
    executor = RecipeExecutor(session, None, None, {})
    mapping = ArgumentMapping(source=ArgumentSourceType.LITERAL, literal_value="strict")
    assert executor._resolve_argument(mapping) == "strict"


async def test_execute_recipe():
    """Full recipe execution with a fake tool executor."""
    session = RunSession.create("s1", "test", variables={"pid": "P_100"})
    calls_made = []

    class FakeToolExecutor:
        async def execute(self, tool_call, emit):
            calls_made.append((tool_call.name, tool_call.arguments))
            return {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "content": '{"pid": "P_100", "score": 5}',
                "is_error": False,
            }

    recipe = StepRecipe(
        step_index=1,
        tool_call_recipes=[
            ToolCallRecipe(
                tool_name="calc_score",
                argument_mappings={
                    "pid": ArgumentMapping(source=ArgumentSourceType.VARIABLE, key="pid"),
                },
            ),
        ],
        result_captures=[ResultCapture(field_name="score", from_tool_call_index=0)],
    )

    async def noop_emit(event):
        pass

    executor = RecipeExecutor(session, FakeToolExecutor(), noop_emit, {})
    captured = await executor.execute(recipe)

    assert len(calls_made) == 1
    assert calls_made[0] == ("calc_score", {"pid": "P_100"})
    assert captured["score"] == 5


async def test_execute_recipe_tool_error():
    """Tool returning error raises RecipeExecutionError."""
    session = RunSession.create("s1", "test", variables={"x": "1"})

    class ErrorToolExecutor:
        async def execute(self, tool_call, emit):
            return {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "content": "Something broke",
                "is_error": True,
            }

    recipe = StepRecipe(
        step_index=1,
        tool_call_recipes=[
            ToolCallRecipe(
                tool_name="bad_tool",
                argument_mappings={
                    "x": ArgumentMapping(source=ArgumentSourceType.VARIABLE, key="x"),
                },
            ),
        ],
    )

    async def noop_emit(event):
        pass

    executor = RecipeExecutor(session, ErrorToolExecutor(), noop_emit, {})
    with pytest.raises(RecipeExecutionError, match="bad_tool"):
        await executor.execute(recipe)
