"""End-to-end runtime tests: verify the full run path from Runtime.run() to RunResult."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from proceda.config import ProcedaConfig
from proceda.events import CollectorEventSink, EventType
from proceda.exceptions import ExecutionError
from proceda.llm.runtime import LLMResponse
from proceda.runtime import Runtime
from proceda.session import RunStatus, ToolCall
from proceda.skills.parser import parse_skill

GREETING_SKILL = """\
---
name: toy-greeting
description: A simple greeting skill for testing.
---

### Step 1: Greet
Say hello.

### Step 2: Farewell
Say goodbye.
"""


def _make_complete_step_response(summary: str) -> LLMResponse:
    """Create an LLMResponse that calls complete_step."""
    return LLMResponse(
        content=summary,
        tool_calls=[
            ToolCall(
                id=ToolCall.generate_id(),
                name="complete_step",
                arguments={"summary": summary},
            )
        ],
    )


@pytest.mark.asyncio
async def test_runtime_runs_to_completion():
    """The runtime executes a skill through all steps and returns COMPLETED."""
    skill = parse_skill(GREETING_SKILL)
    config = ProcedaConfig()
    collector = CollectorEventSink()

    responses = [
        _make_complete_step_response("Said hello."),
        _make_complete_step_response("Said goodbye."),
    ]
    call_count = 0

    async def mock_complete(messages, tools=None, **kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    runtime = Runtime(config=config)

    with patch("proceda.llm.runtime.LLMRuntime.complete", side_effect=mock_complete):
        result = await runtime.run(skill, event_sinks=[collector])

    assert result.status == RunStatus.COMPLETED
    assert result.completed_steps == 2
    assert result.total_steps == 2

    # Verify lifecycle events were emitted
    event_types = [e.type for e in collector.events]
    assert EventType.RUN_CREATED in event_types
    assert EventType.RUN_STARTED in event_types
    assert EventType.RUN_COMPLETED in event_types
    assert EventType.STEP_STARTED in event_types
    assert EventType.STEP_COMPLETED in event_types


@pytest.mark.asyncio
async def test_runtime_emits_events_in_order():
    """Lifecycle events appear in the correct order."""
    skill = parse_skill(GREETING_SKILL)
    config = ProcedaConfig()
    collector = CollectorEventSink()

    responses = [
        _make_complete_step_response("Step 1 done."),
        _make_complete_step_response("Step 2 done."),
    ]
    call_count = 0

    async def mock_complete(messages, tools=None, **kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    runtime = Runtime(config=config)

    with patch("proceda.llm.runtime.LLMRuntime.complete", side_effect=mock_complete):
        await runtime.run(skill, event_sinks=[collector])

    event_types = [e.type for e in collector.events]

    # run.created must come before run.started
    assert event_types.index(EventType.RUN_CREATED) < event_types.index(EventType.RUN_STARTED)
    # run.started must come before run.completed
    assert event_types.index(EventType.RUN_STARTED) < event_types.index(EventType.RUN_COMPLETED)
    # run.completed should be the last lifecycle event (before the final status.changed)
    last_status_idx = len(event_types) - 1 - event_types[::-1].index(EventType.STATUS_CHANGED)
    assert event_types.index(EventType.RUN_COMPLETED) < last_status_idx


SKILL_WITH_REQUIRED_TOOLS = """\
---
name: tool-requiring
description: Skill that requires a nonexistent tool
required_tools:
  - nonexistent__magic_tool
---

### Step 1: Use tool
Use the magic tool.
"""


@pytest.mark.asyncio
async def test_runtime_fails_on_missing_required_tools():
    """Bug 5: Runtime should fail at startup if required tools are not available."""
    skill = parse_skill(SKILL_WITH_REQUIRED_TOOLS)
    config = ProcedaConfig()
    runtime = Runtime(config=config)

    with pytest.raises(ExecutionError, match="Required tools not available"):
        await runtime.start(skill)
