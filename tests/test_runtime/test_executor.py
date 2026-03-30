# ABOUTME: Tests for executor: iteration exhaustion, pre-approval context,
# ABOUTME: empty response recovery, two-tier text handling, and tool call circuit breaker.

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from proceda.events import CollectorEventSink, EventType
from proceda.human import AutoApproveHumanInterface, ScriptedHumanInterface
from proceda.internal.executor import Executor
from proceda.llm.runtime import LLMResponse
from proceda.session import (
    ApprovalDecision,
    ErrorRecoveryDecision,
    RunSession,
    RunStatus,
    ToolCall,
)
from proceda.skills.parser import parse_skill

ONE_STEP_SKILL = """\
---
name: one-step
description: A single step skill
---

### Step 1: Do something
Do the thing.
"""

THREE_STEP_SKILL = """\
---
name: three-step
description: A three step skill
---

### Step 1: Validate input
Check if the input is valid.

### Step 2: Process data
Process the validated data.

### Step 3: Output results
Produce the final output.
"""

PRE_APPROVAL_SKILL = """\
---
name: pre-approval-test
description: Skill with pre-approval step
---

### Step 1: Dangerous step
[PRE-APPROVAL REQUIRED]
Run the dangerous operation on production.
"""


def _make_complete_response(
    summary: str = "Done.",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> LLMResponse:
    return LLMResponse(
        content=summary,
        tool_calls=[
            ToolCall(
                id=ToolCall.generate_id(),
                name="complete_step",
                arguments={"summary": summary},
            )
        ],
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _make_text_only_response(text: str = "Thinking...") -> LLMResponse:
    return LLMResponse(content=text, tool_calls=[])


class TestIterationExhaustion:
    """Bug 1: Step should fail (not silently complete) when iterations are exhausted."""

    @pytest.mark.asyncio
    async def test_step_fails_on_iteration_exhaustion(self) -> None:
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        # LLM always returns text-only responses, never calls complete_step
        call_count = 0

        async def mock_complete(messages, tools=None):
            nonlocal call_count
            call_count += 1
            return _make_text_only_response(f"Still thinking... ({call_count})")

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=AutoApproveHumanInterface(),
            emit=collector.handle,
            # High threshold so iteration exhaustion fires before force-complete
            max_text_responses_before_prompt=100,
        )

        await executor.execute()

        assert session.status == RunStatus.FAILED
        assert session.pending_error is not None
        assert "exhausted" in session.pending_error.message.lower()
        # Step should NOT be in completed_steps
        assert 1 not in session.completed_steps


class TestPreApprovalContext:
    """Bug 3: Pre-approval should include step title and content in context."""

    @pytest.mark.asyncio
    async def test_pre_approval_context_contains_step_content(self) -> None:
        skill = parse_skill(PRE_APPROVAL_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        captured_requests: list = []

        class CapturingHuman:
            async def request_approval(self, request):
                captured_requests.append(request)
                return ApprovalDecision.APPROVE

            async def request_clarification(self, request):
                return "ok"

            async def request_error_recovery(self, request):
                from proceda.session import ErrorRecoveryDecision

                return ErrorRecoveryDecision.CANCEL

        async def mock_complete(messages, tools=None):
            return _make_complete_response("Done.")

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=CapturingHuman(),
            emit=collector.handle,
        )

        await executor.execute()

        assert len(captured_requests) == 1
        context = captured_requests[0].context
        assert "Dangerous step" in context
        assert "dangerous operation on production" in context


class TestFailureStatusChanged:
    """Bug 6: Failure path should emit STATUS_CHANGED event."""

    @pytest.mark.asyncio
    async def test_failure_emits_status_changed(self) -> None:
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        async def mock_complete(messages, tools=None):
            raise RuntimeError("LLM exploded")

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=AutoApproveHumanInterface(),
            emit=collector.handle,
        )

        await executor.execute()

        assert session.status == RunStatus.FAILED

        event_types = [e.type for e in collector.events]
        assert EventType.RUN_FAILED in event_types
        assert EventType.STATUS_CHANGED in event_types

        # STATUS_CHANGED with "failed" should come after RUN_FAILED
        status_events = [
            e
            for e in collector.events
            if e.type == EventType.STATUS_CHANGED and e.payload.get("status") == "failed"
        ]
        assert len(status_events) == 1


def _make_empty_response() -> LLMResponse:
    return LLMResponse(content="", tool_calls=[])


class TestEmptyResponseRecovery:
    """Empty LLM responses get immediate nudges rather than silently counting."""

    @pytest.mark.asyncio
    async def test_empty_responses_get_nudge_then_recover(self) -> None:
        """LLM returns empty twice, then recovers with complete_step."""
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        call_count = 0

        async def mock_complete(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _make_empty_response()
            return _make_complete_response("Recovered.")

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=AutoApproveHumanInterface(),
            emit=collector.handle,
        )

        await executor.execute()

        assert session.status == RunStatus.COMPLETED
        # Two nudge messages for empty responses
        nudge_msgs = [
            m for m in session.messages if m.role == "user" and "empty" in m.content.lower()
        ]
        assert len(nudge_msgs) == 2

    @pytest.mark.asyncio
    async def test_force_complete_after_max_empty_responses(self) -> None:
        """After 5 consecutive empty responses, step is force-completed."""
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        async def mock_complete(messages, tools=None):
            return _make_empty_response()

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=AutoApproveHumanInterface(),
            emit=collector.handle,
        )

        await executor.execute()

        assert session.status == RunStatus.COMPLETED
        force_msgs = [
            m
            for m in session.messages
            if m.role == "system"
            and "empty" in m.content.lower()
            and "force-completed" in m.content
        ]
        assert len(force_msgs) == 1

    @pytest.mark.asyncio
    async def test_empty_count_resets_on_tool_call(self) -> None:
        """Empty response counter resets when a tool call happens."""
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        call_count = 0

        async def mock_complete(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return _make_empty_response()
            if call_count == 4:
                # Tool call resets the counter
                return _make_complete_response("Done after recovery.")
            return _make_empty_response()

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=AutoApproveHumanInterface(),
            emit=collector.handle,
        )

        await executor.execute()

        assert session.status == RunStatus.COMPLETED
        # Should have completed successfully (tool call on call 4 reset the counter)
        assert 1 in session.completed_steps


def _make_skip_remaining_response(summary: str = "Early exit.") -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[
            ToolCall(
                id=ToolCall.generate_id(),
                name="skip_remaining_steps",
                arguments={"summary": summary},
            )
        ],
    )


class TestSkipRemainingSteps:
    """skip_remaining_steps control tool: early termination of the procedure."""

    @pytest.mark.asyncio
    async def test_skip_remaining_ends_run_at_step_1(self) -> None:
        """Calling skip_remaining_steps at step 1 skips steps 2 and 3."""
        skill = parse_skill(THREE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        async def mock_complete(messages, tools=None):
            return _make_skip_remaining_response("Invalid input detected.")

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=AutoApproveHumanInterface(),
            emit=collector.handle,
        )

        await executor.execute()

        assert session.status == RunStatus.COMPLETED
        assert 1 in session.completed_steps
        assert 2 in session.skipped_steps
        assert 3 in session.skipped_steps

        # STEP_SKIPPED events for steps 2 and 3
        skip_events = collector.of_type(EventType.STEP_SKIPPED)
        assert len(skip_events) == 2
        assert skip_events[0].payload["step_index"] == 2
        assert skip_events[1].payload["step_index"] == 3

    @pytest.mark.asyncio
    async def test_skip_remaining_at_last_step_just_completes(self) -> None:
        """Calling skip_remaining_steps at the last step is equivalent to complete_step."""
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        async def mock_complete(messages, tools=None):
            return _make_skip_remaining_response("Done early.")

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=AutoApproveHumanInterface(),
            emit=collector.handle,
        )

        await executor.execute()

        assert session.status == RunStatus.COMPLETED
        assert 1 in session.completed_steps
        assert len(session.skipped_steps) == 0


def _make_app_tool_response(tool_name: str = "app__do_thing") -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[
            ToolCall(
                id=ToolCall.generate_id(),
                name=tool_name,
                arguments={},
            )
        ],
    )


class TestTwoTierTextResponse:
    """Item 5: Two-tier consecutive text response handling."""

    @pytest.mark.asyncio
    async def test_force_complete_after_hard_cap(self) -> None:
        """With max_text_before_prompt=2, hard cap=10. After 10 text responses, step
        should be force-completed. Nudge messages should appear at multiples of 2."""
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        call_count = 0

        async def mock_complete(messages, tools=None):
            nonlocal call_count
            call_count += 1
            return _make_text_only_response(f"Still thinking... ({call_count})")

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=AutoApproveHumanInterface(),
            emit=collector.handle,
            max_text_responses_before_prompt=2,
        )

        await executor.execute()

        # Step force-completed, run should complete (not fail)
        assert session.status == RunStatus.COMPLETED

        # Nudges should have been sent at text_only_count 2, 4, 6, 8
        nudge_msgs = [m for m in session.messages if m.role == "user" and "stuck" in m.content]
        assert len(nudge_msgs) == 4

        # Force-complete system message should be present
        force_msgs = [
            m for m in session.messages if m.role == "system" and "force-completed" in m.content
        ]
        assert len(force_msgs) == 1


class TestToolCallCircuitBreaker:
    """Item 6: Per-step max tool calls circuit breaker."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_fires_on_limit(self) -> None:
        """LLM keeps making app tool calls. With limit=3, after 3 calls, error recovery
        fires. With CANCEL decision, ExecutionError should be raised."""
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        async def mock_complete(messages, tools=None):
            return _make_app_tool_response("app__do_thing")

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        mock_tool_executor = AsyncMock()
        mock_tool_executor.execute = AsyncMock(
            return_value={"content": "ok", "tool_name": "app__do_thing"}
        )

        human = ScriptedHumanInterface(error_decisions=[ErrorRecoveryDecision.CANCEL])

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=mock_tool_executor,
            human=human,
            emit=collector.handle,
            max_tool_calls_per_step=3,
        )

        await executor.execute()

        assert session.status == RunStatus.FAILED
        assert session.pending_error is not None
        assert "cancelled" in session.pending_error.message.lower()

        # Error recovery events should be emitted
        event_types = [e.type for e in collector.events]
        assert EventType.ERROR_RECOVERY_REQUESTED in event_types
        assert EventType.ERROR_RECOVERY_SELECTED in event_types

    @pytest.mark.asyncio
    async def test_circuit_breaker_retry_resets_counter(self) -> None:
        """RETRY resets the counter, then hitting limit again with CANCEL stops."""
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        async def mock_complete(messages, tools=None):
            return _make_app_tool_response("app__do_thing")

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        mock_tool_executor = AsyncMock()
        mock_tool_executor.execute = AsyncMock(
            return_value={"content": "ok", "tool_name": "app__do_thing"}
        )

        # First RETRY (resets counter), then CANCEL on second breach
        human = ScriptedHumanInterface(
            error_decisions=[ErrorRecoveryDecision.RETRY, ErrorRecoveryDecision.CANCEL]
        )

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=mock_tool_executor,
            human=human,
            emit=collector.handle,
            max_tool_calls_per_step=3,
        )

        await executor.execute()

        assert session.status == RunStatus.FAILED

        # Should have 2 error recovery requested events (one per breach)
        recovery_events = collector.of_type(EventType.ERROR_RECOVERY_REQUESTED)
        assert len(recovery_events) == 2

    @pytest.mark.asyncio
    async def test_circuit_breaker_skip_advances(self) -> None:
        """SKIP decision: step is not completed but execution continues."""
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        async def mock_complete(messages, tools=None):
            return _make_app_tool_response("app__do_thing")

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        mock_tool_executor = AsyncMock()
        mock_tool_executor.execute = AsyncMock(
            return_value={"content": "ok", "tool_name": "app__do_thing"}
        )

        human = ScriptedHumanInterface(error_decisions=[ErrorRecoveryDecision.SKIP])

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=mock_tool_executor,
            human=human,
            emit=collector.handle,
            max_tool_calls_per_step=3,
        )

        await executor.execute()

        # SKIP returns from _execute_step, then execute() calls complete_current_step
        # and the run completes
        assert session.status == RunStatus.COMPLETED


class TestTokenTracking:
    """Token usage tracking: LLM_USAGE events emitted and cumulative counts tracked."""

    @pytest.mark.asyncio
    async def test_llm_usage_event_emitted(self) -> None:
        """LLM_USAGE event should be emitted after each LLM call with token counts."""
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        async def mock_complete(messages, tools=None):
            return _make_complete_response(
                "Done.", prompt_tokens=100, completion_tokens=50, total_tokens=150
            )

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=AutoApproveHumanInterface(),
            emit=collector.handle,
        )

        await executor.execute()

        assert session.status == RunStatus.COMPLETED

        usage_events = collector.of_type(EventType.LLM_USAGE)
        assert len(usage_events) == 1
        payload = usage_events[0].payload
        assert payload["prompt_tokens"] == 100
        assert payload["completion_tokens"] == 50
        assert payload["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_cumulative_token_counts_on_session(self) -> None:
        """Session should accumulate token counts across multiple LLM calls."""
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        call_count = 0

        async def mock_complete(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return LLMResponse(
                    content=f"thinking {call_count}",
                    tool_calls=[],
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                )
            return _make_complete_response(
                "Done.", prompt_tokens=200, completion_tokens=80, total_tokens=280
            )

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=AutoApproveHumanInterface(),
            emit=collector.handle,
        )

        await executor.execute()

        assert session.status == RunStatus.COMPLETED
        # 2 text-only calls (100 each) + 1 complete call (200) = 400 prompt tokens
        assert session.total_prompt_tokens == 400
        # 2 * 50 + 80 = 180
        assert session.total_completion_tokens == 180
        # 2 * 150 + 280 = 580
        assert session.total_llm_tokens == 580

    @pytest.mark.asyncio
    async def test_run_completed_includes_token_totals(self) -> None:
        """RUN_COMPLETED event should include cumulative token counts."""
        skill = parse_skill(ONE_STEP_SKILL)
        session = RunSession.create(skill.id, skill.name)
        collector = CollectorEventSink()

        async def mock_complete(messages, tools=None):
            return _make_complete_response(
                "Done.", prompt_tokens=500, completion_tokens=200, total_tokens=700
            )

        llm = AsyncMock()
        llm.complete = mock_complete
        llm.format_messages = lambda msgs: [{"role": "user", "content": "test"}]

        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=None,
            human=AutoApproveHumanInterface(),
            emit=collector.handle,
        )

        await executor.execute()

        completed_events = collector.of_type(EventType.RUN_COMPLETED)
        assert len(completed_events) == 1
        payload = completed_events[0].payload
        assert payload["prompt_tokens"] == 500
        assert payload["completion_tokens"] == 200
        assert payload["total_tokens"] == 700
