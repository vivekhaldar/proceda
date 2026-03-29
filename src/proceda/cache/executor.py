"""ABOUTME: Executes cached step recipes by resolving arguments and calling tools directly.
ABOUTME: Used for Level 2 optimization — bypasses the LLM loop for high-confidence steps."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from proceda.cache.models import ArgumentMapping, ArgumentSourceType, StepRecipe
from proceda.session import RunMessage, RunSession, ToolCall

logger = logging.getLogger(__name__)

_STEP_RESULT_RE = re.compile(r"step\[(\d+)\]\.(.+)")


class RecipeExecutionError(Exception):
    """Raised when a recipe cannot be executed and fallback is needed."""


class RecipeExecutor:
    """Executes a StepRecipe by resolving arguments and calling tools directly."""

    def __init__(
        self,
        session: RunSession,
        tool_executor: Any,
        emit: Any,
        step_results: dict[int, dict[str, Any]],
    ) -> None:
        self._session = session
        self._tool_executor = tool_executor
        self._emit = emit
        self._step_results = step_results

    async def execute(self, recipe: StepRecipe) -> dict[str, Any]:
        """Execute all tool calls in a recipe and return captured results."""
        if self._tool_executor is None:
            raise RecipeExecutionError("No tool executor available")

        tool_results: list[dict[str, Any]] = []

        for tc_recipe in recipe.tool_call_recipes:
            # Resolve arguments
            resolved_args: dict[str, Any] = {}
            for arg_name, mapping in tc_recipe.argument_mappings.items():
                resolved_args[arg_name] = self._resolve_argument(mapping)

            # Create a ToolCall and execute
            tool_call = ToolCall(
                id=ToolCall.generate_id(),
                name=tc_recipe.tool_name,
                arguments=resolved_args,
            )

            # Add assistant message with tool call for conversation consistency
            self._session.add_message(RunMessage.create("assistant", "", tool_calls=[tool_call]))

            result = await self._tool_executor.execute(tool_call, self._emit)

            # Add tool result message
            content = result.get("content", "")
            self._session.add_message(
                RunMessage.create(
                    "tool",
                    content,
                    tool_call_id=tool_call.id,
                    app_name=result.get("tool_name"),
                )
            )
            self._session.step_tool_results.append(result)

            if result.get("is_error"):
                raise RecipeExecutionError(
                    f"Tool '{tc_recipe.tool_name}' returned error: {content}"
                )

            # Parse result
            try:
                parsed = json.loads(content) if isinstance(content, str) else content
            except (json.JSONDecodeError, TypeError):
                parsed = {}

            tool_results.append(parsed if isinstance(parsed, dict) else {})

        # Capture result fields
        captured: dict[str, Any] = {}
        for rc in recipe.result_captures:
            if rc.from_tool_call_index < len(tool_results):
                result_dict = tool_results[rc.from_tool_call_index]
                if rc.field_name in result_dict:
                    captured[rc.field_name] = result_dict[rc.field_name]

        # Also capture all fields from all tool results for cross-step use
        for result_dict in tool_results:
            for k, v in result_dict.items():
                if k not in captured:
                    captured[k] = v

        return captured

    def _resolve_argument(self, mapping: ArgumentMapping) -> Any:
        """Resolve an argument mapping to a concrete value."""
        if mapping.source == ArgumentSourceType.VARIABLE:
            value = self._session.variables.get(mapping.key)
            if value is None:
                raise RecipeExecutionError(
                    f"Variable '{mapping.key}' not found in session variables. "
                    f"Available: {list(self._session.variables.keys())}"
                )
            return value

        elif mapping.source == ArgumentSourceType.STEP_RESULT:
            step_idx, field = parse_step_result_key(mapping.key)
            step_data = self._step_results.get(step_idx)
            if step_data is None:
                raise RecipeExecutionError(
                    f"No results captured for step {step_idx}. "
                    f"Available steps: {list(self._step_results.keys())}"
                )
            value = step_data.get(field)
            if value is None:
                raise RecipeExecutionError(
                    f"Field '{field}' not found in step {step_idx} results. "
                    f"Available: {list(step_data.keys())}"
                )
            return value

        elif mapping.source == ArgumentSourceType.LITERAL:
            return mapping.literal_value

        else:
            raise RecipeExecutionError(f"Unknown source type: {mapping.source}")


def parse_step_result_key(key: str) -> tuple[int, str]:
    """Parse 'step[2].sds_label_score' into (2, 'sds_label_score')."""
    match = _STEP_RESULT_RE.match(key)
    if not match:
        raise ValueError(f"Invalid step result key format: {key!r}")
    return int(match.group(1)), match.group(2)
