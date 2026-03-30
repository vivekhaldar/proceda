"""Tests for control tool schemas."""

from __future__ import annotations

from proceda.llm.tool_schemas import (
    CONTROL_TOOLS,
    get_control_tool_schemas,
    is_control_tool,
)


class TestControlToolSchemas:
    def test_control_tools_set(self) -> None:
        assert "complete_step" in CONTROL_TOOLS
        assert "request_clarification" in CONTROL_TOOLS
        assert "skip_remaining_steps" in CONTROL_TOOLS

    def test_is_control_tool(self) -> None:
        assert is_control_tool("complete_step")
        assert is_control_tool("request_clarification")
        assert is_control_tool("skip_remaining_steps")
        assert not is_control_tool("some_app_tool")

    def test_get_schemas(self) -> None:
        schemas = get_control_tool_schemas()
        assert len(schemas) == 3
        names = {s["function"]["name"] for s in schemas}
        assert names == {"complete_step", "request_clarification", "skip_remaining_steps"}

    def test_complete_step_schema(self) -> None:
        schemas = get_control_tool_schemas()
        complete = next(s for s in schemas if s["function"]["name"] == "complete_step")
        assert complete["type"] == "function"
        params = complete["function"]["parameters"]
        assert "summary" in params["properties"]
        assert "summary" in params["required"]

    def test_skip_remaining_steps_schema(self) -> None:
        schemas = get_control_tool_schemas()
        skip = next(s for s in schemas if s["function"]["name"] == "skip_remaining_steps")
        assert skip["type"] == "function"
        params = skip["function"]["parameters"]
        assert "summary" in params["properties"]
        assert "summary" in params["required"]

    def test_request_clarification_schema(self) -> None:
        schemas = get_control_tool_schemas()
        clarify = next(s for s in schemas if s["function"]["name"] == "request_clarification")
        params = clarify["function"]["parameters"]
        assert "question" in params["properties"]
        assert "options" in params["properties"]
        assert "question" in params["required"]
