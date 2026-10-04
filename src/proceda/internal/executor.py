"""ABOUTME: Core executor: drives step-by-step skill execution.
ABOUTME: Handles LLM loop, approval gates, tool calls, and guard-rail limits."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import jsonschema

from proceda.config import DecisionConfig
from proceda.decision import DecisionService, GateResult
from proceda.events import EventType, RunEvent
from proceda.exceptions import ApprovalRejectedError, ExecutionError
from proceda.human import HumanInterface
from proceda.internal.context import ContextManager
from proceda.internal.tool_executor import ToolExecutor
from proceda.llm.prompts import build_step_prompt, build_system_prompt
from proceda.llm.runtime import LLMResponse, LLMRuntime
from proceda.llm.tool_schemas import get_control_tool_schemas, is_control_tool
from proceda.planning.runner import PlanRunner
from proceda.session import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalRequest,
    ClarificationRecord,
    ClarificationRequest,
    ErrorContext,
    ErrorRecoveryDecision,
    ErrorRecoveryRequest,
    ExecutedToolReceipt,
    RunMessage,
    RunSession,
    RunStatus,
    ToolCall,
)
from proceda.skill import Skill

logger = logging.getLogger(__name__)

# Guard-rail constants
MAX_TEXT_ONLY_ITERATIONS = 5
MAX_TOOL_CALL_ITERATIONS = 50

EmitFn = Callable[[RunEvent], Coroutine[Any, Any, None]]


class Executor:
    """Executes a skill step-by-step, driving the LLM and handling human interactions."""

    def __init__(
        self,
        skill: Skill,
        session: RunSession,
        llm: LLMRuntime,
        tool_executor: ToolExecutor | None,
        human: HumanInterface,
        emit: EmitFn,
        context_manager: ContextManager | None = None,
        tool_schemas: list[dict[str, Any]] | None = None,
        max_text_responses_before_prompt: int = 3,
        max_tool_calls_per_step: int = 20,
        decision_service: DecisionService | None = None,
        decision_config: DecisionConfig | None = None,
        fallback_llm: LLMRuntime | None = None,
        tool_aliases: dict[str, str] | None = None,
        resolve_tool_identity: Callable[[str], tuple[str, str] | None] | None = None,
        plan_runner: PlanRunner | None = None,
    ) -> None:
        self._skill = skill
        self._session = session
        self._llm = llm
        self._tool_executor = tool_executor
        self._human = human
        self._emit = emit
        self._context = context_manager or ContextManager()
        self._app_tool_schemas = tool_schemas or []
        self._max_text_before_prompt = max_text_responses_before_prompt
        self._max_tool_calls_per_step = max_tool_calls_per_step
        self._decisions = decision_service
        self._decision_config = decision_config or DecisionConfig()
        self._fallback_llm = fallback_llm
        self._tool_aliases = dict(tool_aliases or {})
        self._resolve_tool_identity = resolve_tool_identity
        self._plan_runner = plan_runner

    async def execute(self) -> None:
        """Run the full skill from current step to completion."""
        session = self._session

        # Initialize with system prompt
        system_prompt = build_system_prompt(self._skill, session.variables)
        session.add_message(RunMessage.create("system", system_prompt))

        await self._emit(
            RunEvent.create(session.id, EventType.MESSAGE_SYSTEM, {"content": system_prompt[:200]})
        )

        session.set_status(RunStatus.RUNNING)
        await self._emit(
            RunEvent.create(session.id, EventType.RUN_STARTED, {"skill_name": self._skill.name})
        )
        await self._emit_status_change(RunStatus.RUNNING)

        try:
            while session.current_step <= self._skill.step_count:
                step = self._skill.get_step(session.current_step)

                await self._emit(
                    RunEvent.create(
                        session.id,
                        EventType.STEP_STARTED,
                        {"step_index": step.index, "step_title": step.title},
                    )
                )

                # Handle pre-approval
                if step.requires_pre_approval:
                    decision = await self._request_approval(
                        step.index,
                        step.title,
                        "pre_step",
                        f"Step {step.index}: {step.title}\n\n{step.content}",
                    )
                    if decision == ApprovalDecision.REJECT:
                        raise ApprovalRejectedError(f"Pre-approval rejected for step {step.index}")
                    if decision == ApprovalDecision.SKIP:
                        session.skipped_steps.append(step.index)
                        await self._emit(
                            RunEvent.create(
                                session.id,
                                EventType.STEP_SKIPPED,
                                {"step_index": step.index, "reason": "pre-approval skipped"},
                            )
                        )
                        session.advance_step()
                        continue

                # Execute step via LLM loop
                step_result = await self._execute_step(step.index)

                if step_result == "skip_remaining":
                    # Skip all remaining steps — mark current step complete and break
                    remaining = list(range(step.index + 1, self._skill.step_count + 1))
                    for skipped_idx in remaining:
                        session.skipped_steps.append(skipped_idx)
                        await self._emit(
                            RunEvent.create(
                                session.id,
                                EventType.STEP_SKIPPED,
                                {
                                    "step_index": skipped_idx,
                                    "reason": "skip_remaining_steps called",
                                },
                            )
                        )
                    session.complete_current_step()
                    await self._emit(
                        RunEvent.create(
                            session.id,
                            EventType.STEP_COMPLETED,
                            {"step_index": step.index, "step_title": step.title},
                        )
                    )
                    break

                # Handle post-approval
                if step.requires_post_approval:
                    decision = await self._request_approval(
                        step.index,
                        step.title,
                        "post_step",
                        f"Step {step.index} completed. Approval required before advancing.",
                    )
                    if decision == ApprovalDecision.REJECT:
                        raise ApprovalRejectedError(f"Post-approval rejected for step {step.index}")

                session.complete_current_step()
                await self._emit(
                    RunEvent.create(
                        session.id,
                        EventType.STEP_COMPLETED,
                        {"step_index": step.index, "step_title": step.title},
                    )
                )
                session.advance_step()

            session.set_status(RunStatus.COMPLETED)
            await self._emit(
                RunEvent.create(
                    session.id,
                    EventType.RUN_COMPLETED,
                    {
                        "completed_steps": len(session.completed_steps),
                        "total_steps": self._skill.step_count,
                        "prompt_tokens": session.total_prompt_tokens,
                        "completion_tokens": session.total_completion_tokens,
                        "total_tokens": session.total_llm_tokens,
                    },
                )
            )
            await self._emit_status_change(RunStatus.COMPLETED)

        except ApprovalRejectedError:
            session.set_status(RunStatus.CANCELLED)
            await self._emit(
                RunEvent.create(
                    session.id, EventType.RUN_CANCELLED, {"reason": "approval_rejected"}
                )
            )
            await self._emit_status_change(RunStatus.CANCELLED)
        except Exception as e:
            logger.exception("Execution failed")
            session.pending_error = ErrorContext(
                error_type=type(e).__name__,
                message=str(e),
                step_index=session.current_step,
            )
            session.set_status(RunStatus.FAILED)
            await self._emit(
                RunEvent.create(
                    session.id,
                    EventType.RUN_FAILED,
                    {"error": str(e), "step_index": session.current_step},
                )
            )
            await self._emit_status_change(RunStatus.FAILED)
        finally:
            if session.current_step not in session.completed_steps:
                await self._discard_planned_facts(session.current_step)

    async def _execute_step(self, step_index: int) -> str | None:
        """Execute a single step via the LLM loop. Returns 'skip_remaining' to end early."""
        step = self._skill.get_step(step_index)
        session = self._session

        is_last_step = step_index == self._skill.step_count
        step_prompt = build_step_prompt(
            step,
            is_last_step=is_last_step,
            output_fields=self._skill.output_fields,
        )
        session.add_message(RunMessage.create("user", step_prompt, is_critical=True))

        text_only_count = 0
        empty_response_count = 0
        max_empty_responses = 5
        iteration_count = 0
        step_tool_call_count = 0
        hard_cap = self._max_text_before_prompt * 5
        # Temperature escalation: bump temperature after consecutive empty responses
        # to break out of degenerate sampling states.
        temp_escalation = [0.1, 0.3, 0.5, 0.7, 0.9]
        temp_override: float | None = None

        while iteration_count < MAX_TOOL_CALL_ITERATIONS:
            iteration_count += 1

            # Build tools list: control tools + app tools
            all_tools = get_control_tool_schemas() + self._app_tool_schemas

            # Trim context and get LLM response
            trimmed = self._context.trim_messages(session.messages)
            formatted = self._llm.format_messages(trimmed)

            response = None
            if self._plan_runner is not None:
                response = await self._plan_runner.propose(
                    step_index,
                    session,
                    llm=self._llm,
                    decisions=self._decisions,
                    emit=self._emit,
                    track_usage=self._track_usage,
                )
            if response is None:
                response = await self._propose(formatted, all_tools, temp_override, step_index)
            else:
                planned_response = response
                try:
                    response = await self._verify_response(
                        response, formatted, all_tools, temp_override, step_index
                    )
                except Exception:
                    await self._discard_planned_facts(step_index)
                    raise
                if response is not planned_response:
                    await self._discard_planned_facts(step_index)

            # Handle reasoning
            if response.reasoning:
                await self._emit(
                    RunEvent.create(
                        session.id,
                        EventType.MESSAGE_REASONING,
                        {"content": response.reasoning},
                    )
                )

            # Handle text content
            if response.content:
                session.add_message(
                    RunMessage.create("assistant", response.content, tool_calls=response.tool_calls)
                )
                await self._emit(
                    RunEvent.create(
                        session.id,
                        EventType.MESSAGE_ASSISTANT,
                        {"content": response.content},
                    )
                )

            # No tool calls - just text or empty (two-tier handling)
            if not response.tool_calls:
                # Empty responses (no content AND no tool calls) get an immediate
                # nudge rather than silently counting toward the force-complete cap.
                # This recovers from transient LLM failures (e.g. Gemini returning
                # empty choices) without wasting iterations.
                if not response.content:
                    empty_response_count += 1
                    # Escalate temperature to break out of degenerate sampling
                    esc_idx = min(empty_response_count - 1, len(temp_escalation) - 1)
                    temp_override = temp_escalation[esc_idx]
                    if empty_response_count >= max_empty_responses:
                        logger.warning(
                            "Step %d force-completed after %d empty LLM responses",
                            step_index,
                            empty_response_count,
                        )
                        session.add_message(
                            RunMessage.create(
                                "system",
                                "Step force-completed after repeated empty LLM responses.",
                            )
                        )
                        return None
                    session.add_message(
                        RunMessage.create(
                            "user",
                            "Your previous response was empty. Please continue with the "
                            "current step — call the appropriate tool or `complete_step`.",
                        )
                    )
                    continue

                text_only_count += 1

                # Hard tier: force-complete the step
                if text_only_count >= hard_cap:
                    logger.warning(
                        "Step %d force-completed after %d text-only responses",
                        step_index,
                        text_only_count,
                    )
                    session.add_message(
                        RunMessage.create(
                            "system",
                            "Step force-completed after too many text-only responses.",
                        )
                    )
                    return

                # Soft tier: send nudge at each multiple of max_text_before_prompt
                if (
                    text_only_count >= self._max_text_before_prompt
                    and text_only_count % self._max_text_before_prompt == 0
                ):
                    session.add_message(
                        RunMessage.create(
                            "user",
                            "You seem to be stuck. Please call `complete_step` if the step "
                            "is done, or use a tool to make progress.",
                        )
                    )
                continue

            text_only_count = 0
            empty_response_count = 0
            temp_override = None

            # If there's content with tool calls, record the assistant message with tool calls
            if not response.content:
                session.add_message(
                    RunMessage.create("assistant", "", tool_calls=response.tool_calls)
                )

            # Process tool calls
            batch_receipts_start = len(session.executed_tool_receipts)
            for tc in response.tool_calls:
                if is_control_tool(tc.name):
                    result = await self._handle_control_tool(tc)
                    if result == "step_complete":
                        return None
                    if result == "skip_remaining":
                        return "skip_remaining"
                else:
                    await self._handle_app_tool(tc)
                    step_tool_call_count += 1

                    # Circuit breaker: too many app tool calls in one step
                    if step_tool_call_count >= self._max_tool_calls_per_step:
                        error_ctx = ErrorContext(
                            error_type="ToolCallLimitExceeded",
                            message=(
                                f"Step {step_index} made {step_tool_call_count} tool calls "
                                f"(limit: {self._max_tool_calls_per_step})"
                            ),
                            step_index=step_index,
                        )
                        request = ErrorRecoveryRequest(error=error_ctx)

                        await self._emit(
                            RunEvent.create(
                                session.id,
                                EventType.ERROR_RECOVERY_REQUESTED,
                                {
                                    "step_index": step_index,
                                    "error": error_ctx.message,
                                },
                            )
                        )

                        decision = await self._human.request_error_recovery(request)

                        await self._emit(
                            RunEvent.create(
                                session.id,
                                EventType.ERROR_RECOVERY_SELECTED,
                                {
                                    "step_index": step_index,
                                    "decision": decision.value,
                                },
                            )
                        )

                        if decision == ErrorRecoveryDecision.RETRY:
                            step_tool_call_count = 0
                        elif decision == ErrorRecoveryDecision.SKIP:
                            return
                        else:  # CANCEL
                            raise ExecutionError(
                                f"Step {step_index} cancelled after exceeding tool call limit"
                            )

            # Check only a fully processed app-tool batch. Control transitions,
            # failed tools, and final-output steps retain ordinary execution.
            fresh_receipts = session.executed_tool_receipts[batch_receipts_start:]
            if (
                self._decisions is not None
                and self._decision_config.fast_completion
                and (self._plan_runner is None or self._plan_runner.completion_allowed(step_index))
                and not is_last_step
                and response.tool_calls
                and all(not is_control_tool(tc.name) for tc in response.tool_calls)
                and len(fresh_receipts) == len(response.tool_calls)
                and all(
                    r.is_error is False and r.qualified_tool_name is not None
                    for r in fresh_receipts
                )
            ):
                gate = await self._decisions.check_completion(
                    step=step.content,
                    evidence=self._completion_evidence(),
                )
                await self._record_gate("completion", gate, step_index)
                if gate.value is True:
                    receipt_ids = [r.receipt_id for r in fresh_receipts]
                    summary = (
                        f"Step {step.index}: {step.title} completed. "
                        "Tool results are retained for subsequent steps."
                    )
                    tc = ToolCall(ToolCall.generate_id(), "complete_step", {"summary": summary})
                    session.add_message(RunMessage.create("assistant", "", tool_calls=[tc]))
                    await self._handle_control_tool(tc)
                    await self._emit(
                        RunEvent.create(
                            session.id,
                            EventType.STEP_AUTO_COMPLETED,
                            {"step_index": step_index, "receipt_ids": receipt_ids},
                        )
                    )
                    return None

        raise ExecutionError(
            f"Step {step_index} exhausted {MAX_TOOL_CALL_ITERATIONS} iterations "
            "without calling complete_step"
        )

    def _completion_evidence(self) -> dict[str, Any]:
        """Separate recorded business observations from pending runtime bookkeeping."""
        receipts = []
        for receipt in self._session.executed_tool_receipts:
            result = receipt.result
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass
            receipts.append(
                {
                    "receipt_id": receipt.receipt_id,
                    "tool_call_id": receipt.tool_call_id,
                    "qualified_tool_name": receipt.qualified_tool_name,
                    "sop_tool_name": receipt.sop_tool_name,
                    "arguments": receipt.arguments,
                    "result": result,
                    "persisted": True,
                    "current_step": receipt.step_index == self._session.current_step,
                    "is_error": receipt.is_error,
                }
            )
        evidence = {"inputs": dict(self._session.variables), "receipts": receipts}
        if self._session.clarification_records:
            evidence["clarifications"] = self._clarification_evidence()
        return evidence

    def _clarification_evidence(self) -> list[dict[str, Any]]:
        return [
            {**asdict(record), "current_step": record.step_index == self._session.current_step}
            for record in self._session.clarification_records
        ]

    def _observed_tool_receipts(self) -> list[dict[str, Any]]:
        """Preserve actual app observations even when the LLM context was trimmed."""
        return [
            {
                "receipt_id": receipt.receipt_id,
                "tool_call_id": receipt.tool_call_id,
                "tool_name": receipt.qualified_tool_name or receipt.requested_tool_name,
                "arguments": receipt.arguments,
                "result": receipt.result,
                "step_index": receipt.step_index,
                "is_error": receipt.is_error,
            }
            for receipt in self._session.executed_tool_receipts
        ]

    async def _record_gate(self, purpose: str, gate: GateResult, step_index: int) -> None:
        await self._emit(
            RunEvent.create(
                self._session.id,
                EventType.DECISION_EVALUATED,
                {
                    "step_index": step_index,
                    "purpose": purpose,
                    "value": gate.value,
                    "reason": gate.reason,
                    "probabilities": gate.probabilities,
                    "stages": gate.details,
                },
            )
        )

    async def _track_usage(self, response: LLMResponse, step_index: int) -> None:
        session = self._session
        if response.total_tokens > 0:
            session.total_prompt_tokens += response.prompt_tokens
            session.total_completion_tokens += response.completion_tokens
            session.total_llm_tokens += response.total_tokens
            await self._emit(
                RunEvent.create(
                    session.id,
                    EventType.LLM_USAGE,
                    {
                        "step_index": step_index,
                        "prompt_tokens": response.prompt_tokens,
                        "completion_tokens": response.completion_tokens,
                        "total_tokens": response.total_tokens,
                        "cumulative_prompt_tokens": session.total_prompt_tokens,
                        "cumulative_completion_tokens": session.total_completion_tokens,
                        "cumulative_total_tokens": session.total_llm_tokens,
                    },
                )
            )

    @staticmethod
    def _proposal_schema_errors(response: LLMResponse, tools: list[dict]) -> list[str]:
        schemas = {tool["function"]["name"]: tool["function"] for tool in tools}
        errors = []
        for tc in response.tool_calls:
            schema = schemas.get(tc.name)
            if schema is None:
                errors.append(f"Unknown tool: {tc.name}")
                continue
            try:
                jsonschema.validate(tc.arguments, schema.get("parameters", {}))
            except (jsonschema.ValidationError, jsonschema.SchemaError) as exc:
                errors.append(f"{tc.name}: {exc.message}")
        return errors

    async def _propose(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None,
        step_index: int,
    ) -> LLMResponse:
        """Verify proposals before dispatch; discarded attempts still count in usage."""
        response = await self._llm.complete(messages, tools=tools, temperature=temperature)
        await self._track_usage(response, step_index)
        return await self._verify_response(response, messages, tools, temperature, step_index)

    async def _discard_planned_facts(self, step_index: int) -> None:
        # A repaired completion may no longer assert the planned decision. Do not
        # let its provisional facts become authority for a later step.
        discarded = [
            fact for fact in self._session.decision_facts if fact.get("step_index") == step_index
        ]
        self._session.decision_facts[:] = [
            fact for fact in self._session.decision_facts if fact.get("step_index") != step_index
        ]
        if discarded:
            await self._emit(
                RunEvent.create(
                    self._session.id,
                    EventType.PLAN_FACT_DISCARDED,
                    {"step_index": step_index, "facts": discarded},
                )
            )

    async def _verify_response(
        self,
        response: LLMResponse,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None,
        step_index: int,
    ) -> LLMResponse:
        """Apply configured proposal checks to generated and planned actions alike."""
        if self._decisions is None or not self._decision_config.verify_proposals:
            return response
        step = self._skill.get_step(step_index)
        repairs = self._decision_config.max_repairs
        for attempt in range(repairs + 1):
            errors = self._proposal_schema_errors(response, tools)
            gate = None
            if not errors:
                gate = await self._decisions.check_proposal(
                    step=step.content,
                    evidence={
                        "messages": messages,
                        "inputs": self._session.variables,
                        "observed_tool_receipts": self._observed_tool_receipts(),
                        "clarifications": self._clarification_evidence(),
                    },
                    proposal={
                        "content": response.content,
                        "tool_calls": [asdict(tc) for tc in response.tool_calls],
                    },
                    tools=tools,
                    final=step_index == self._skill.step_count,
                    output_fields=self._skill.output_fields,
                )
                await self._record_gate("proposal", gate, step_index)
                if gate.value is True:
                    return response
                # An unavailable/uncertain verifier cannot declare a proposal wrong.
                # Keep the existing LLM path and make that fallback observable.
                if gate.value is None:
                    if attempt == 0:
                        return response
                    raise ExecutionError("Repaired proposal could not be verified")
            rejection = errors or [
                {
                    "reason": gate.reason if gate else "rejected",
                    "probabilities": gate.probabilities if gate else {},
                }
            ]
            await self._emit(
                RunEvent.create(
                    self._session.id,
                    EventType.PROPOSAL_REJECTED,
                    {
                        "step_index": step_index,
                        "attempt": attempt,
                        "reasons": rejection,
                        "content": response.content,
                        "tool_calls": [asdict(tc) for tc in response.tool_calls],
                    },
                )
            )
            if attempt == repairs:
                raise ExecutionError("Proposal failed verification after bounded repair")
            repair_messages = messages + [
                {
                    "role": "user",
                    "content": "A proposed action was rejected before execution. No tools from "
                    "that proposal have run. Produce a corrected next action grounded in the "
                    "original observations. Rejected proposal and checks: "
                    + json.dumps(
                        {
                            "content": response.content,
                            "tool_calls": [asdict(tc) for tc in response.tool_calls],
                            "checks": rejection,
                        }
                    ),
                }
            ]
            response = await (self._fallback_llm or self._llm).complete(
                repair_messages, tools=tools, temperature=temperature
            )
            await self._track_usage(response, step_index)
        raise AssertionError("Unreachable proposal loop")

    async def _handle_control_tool(self, tool_call: ToolCall) -> str | None:
        """Handle a control tool call. Returns 'step_complete' if step is done."""
        session = self._session

        if tool_call.name == "complete_step":
            summary = tool_call.arguments.get("summary", "Step completed.")
            session.add_message(RunMessage.create("tool", summary, tool_call_id=tool_call.id))
            await self._emit(
                RunEvent.create(
                    session.id,
                    EventType.SUMMARY_GENERATED,
                    {"step_index": session.current_step, "summary": summary},
                )
            )
            return "step_complete"

        elif tool_call.name == "skip_remaining_steps":
            summary = tool_call.arguments.get("summary", "Remaining steps skipped.")
            session.add_message(RunMessage.create("tool", summary, tool_call_id=tool_call.id))
            await self._emit(
                RunEvent.create(
                    session.id,
                    EventType.SUMMARY_GENERATED,
                    {"step_index": session.current_step, "summary": summary},
                )
            )
            return "skip_remaining"

        elif tool_call.name == "request_clarification":
            question = tool_call.arguments.get("question", "")
            options = tool_call.arguments.get("options", [])
            recorded_options = tuple(options)

            request = ClarificationRequest(question=question, options=options, context=None)

            session.pending_clarification = request
            session.set_status(RunStatus.AWAITING_INPUT)
            await self._emit_status_change(RunStatus.AWAITING_INPUT)

            await self._emit(
                RunEvent.create(
                    session.id,
                    EventType.CLARIFICATION_REQUESTED,
                    {"question": question, "options": options},
                )
            )

            answer = await self._human.request_clarification(request)
            session.clarification_records.append(
                ClarificationRecord(
                    receipt_id=f"clarification_{uuid.uuid4().hex}",
                    tool_call_id=tool_call.id,
                    step_index=session.current_step,
                    question=question,
                    options=recorded_options,
                    answer=answer,
                )
            )

            await self._emit(
                RunEvent.create(
                    session.id,
                    EventType.CLARIFICATION_RESPONDED,
                    {"answer": answer},
                )
            )

            session.pending_clarification = None
            session.set_status(RunStatus.RUNNING)
            await self._emit_status_change(RunStatus.RUNNING)

            # Feed answer back as tool result
            session.add_message(
                RunMessage.create("tool", answer, tool_call_id=tool_call.id, is_critical=True)
            )

        return None

    async def _handle_app_tool(self, tool_call: ToolCall) -> None:
        """Handle an app (MCP) tool call."""
        session = self._session
        # Snapshot before dispatch: tool clients may retain or mutate their arguments.
        arguments_json = json.dumps(tool_call.arguments, ensure_ascii=False)
        requested_name = tool_call.name
        call_id = tool_call.id
        step_index = session.current_step
        identity = (
            self._resolve_tool_identity(requested_name)
            if self._resolve_tool_identity is not None
            else (requested_name, self._tool_aliases[requested_name])
            if requested_name in self._tool_aliases
            else None
        )

        if self._tool_executor:
            result = await self._tool_executor.execute(tool_call, self._emit)
            content = result.get("content", "")
            receipt = ExecutedToolReceipt(
                receipt_id=f"receipt_{uuid.uuid4().hex}",
                tool_call_id=call_id,
                step_index=step_index,
                requested_tool_name=requested_name,
                qualified_tool_name=identity[0] if identity else None,
                sop_tool_name=identity[1] if identity else None,
                arguments_json=arguments_json,
                result=content,
                is_error=result.get("is_error") if type(result.get("is_error")) is bool else None,
            )
            session.executed_tool_receipts.append(receipt)
            session.add_message(
                RunMessage.create(
                    "tool",
                    content,
                    tool_call_id=tool_call.id,
                    app_name=result.get("tool_name"),
                )
            )
            session.step_tool_results.append({**result, "receipt_id": receipt.receipt_id})
        else:
            # No tool executor - return error
            error_msg = f"Tool '{tool_call.name}' is not available (no MCP apps configured)."
            session.add_message(RunMessage.create("tool", error_msg, tool_call_id=tool_call.id))
            await self._emit(
                RunEvent.create(
                    session.id,
                    EventType.TOOL_FAILED,
                    {
                        "tool_call_id": tool_call.id,
                        "tool_name": tool_call.name,
                        "error": error_msg,
                    },
                )
            )

    async def _request_approval(
        self,
        step_index: int,
        step_title: str,
        approval_type: str,
        context: str,
    ) -> ApprovalDecision:
        """Request approval from the human interface."""
        session = self._session

        request = ApprovalRequest(
            step_index=step_index,
            step_title=step_title,
            approval_type=approval_type,  # type: ignore
            context=context,
            pending_tool_calls=list(session.pending_tool_calls),
            tool_results=list(session.step_tool_results),
        )

        session.pending_approval = request
        session.set_status(RunStatus.AWAITING_APPROVAL)
        await self._emit_status_change(RunStatus.AWAITING_APPROVAL)

        await self._emit(
            RunEvent.create(
                session.id,
                EventType.APPROVAL_REQUESTED,
                {
                    "step_index": step_index,
                    "step_title": step_title,
                    "approval_type": approval_type,
                },
            )
        )

        decision = await self._human.request_approval(request)

        await self._emit(
            RunEvent.create(
                session.id,
                EventType.APPROVAL_RESPONDED,
                {
                    "step_index": step_index,
                    "decision": decision.value,
                },
            )
        )

        session.approval_records.append(
            ApprovalRecord(
                step_index=step_index,
                approval_type=approval_type,  # type: ignore
                decision=decision,
                timestamp=datetime.now(UTC),
            )
        )

        session.pending_approval = None
        session.set_status(RunStatus.RUNNING)
        await self._emit_status_change(RunStatus.RUNNING)

        return decision

    async def _emit_status_change(self, status: RunStatus) -> None:
        await self._emit(
            RunEvent.create(
                self._session.id,
                EventType.STATUS_CHANGED,
                {"status": status.value},
            )
        )
