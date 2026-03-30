"""ABOUTME: Core executor: drives step-by-step skill execution.
ABOUTME: Handles LLM loop, approval gates, tool calls, and guard-rail limits."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from proceda.cache.models import ArgumentSourceType, StepRecipe
from proceda.events import EventType, RunEvent
from proceda.exceptions import ApprovalRejectedError, ExecutionError
from proceda.human import HumanInterface
from proceda.internal.context import ContextManager
from proceda.internal.tool_executor import ToolExecutor
from proceda.llm.prompts import build_step_prompt, build_system_prompt
from proceda.llm.runtime import LLMRuntime
from proceda.llm.tool_schemas import get_control_tool_schemas, is_control_tool
from proceda.session import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalRequest,
    ClarificationRequest,
    ErrorContext,
    ErrorRecoveryDecision,
    ErrorRecoveryRequest,
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
        skill_cache: Any | None = None,
        cache_config: Any | None = None,
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
        self._skill_cache = skill_cache
        self._cache_config = cache_config
        self._step_results: dict[int, dict[str, Any]] = {}

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
                await self._execute_step(step.index)

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

                # Capture tool results for use by later cached steps
                if step.index not in self._step_results and session.step_tool_results:
                    last_result = session.step_tool_results[-1]
                    content = last_result.get("content", "")
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            self._step_results[step.index] = parsed
                    except (json.JSONDecodeError, TypeError):
                        pass

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

    async def _execute_step(self, step_index: int) -> None:
        """Execute a single step via the LLM loop."""
        step = self._skill.get_step(step_index)
        session = self._session

        # Cache consultation
        hint = None
        cache_cfg = self._cache_config
        if self._skill_cache and cache_cfg and cache_cfg.enabled:
            recipe = self._skill_cache.get_recipe(step_index)
            if recipe and recipe.confidence >= cache_cfg.min_confidence:
                if cache_cfg.optimization_level >= 2 and recipe.tool_call_recipes:
                    # Level 2: direct execution with fallback
                    await self._emit(
                        RunEvent.create(
                            session.id,
                            EventType.CACHE_HIT,
                            {
                                "step_index": step_index,
                                "optimization_level": 2,
                                "confidence": recipe.confidence,
                            },
                        )
                    )
                    # Snapshot message count so we can roll back on failure
                    msg_count_before = len(session.messages)
                    tool_results_before = len(session.step_tool_results)
                    try:
                        from proceda.cache.executor import RecipeExecutionError, RecipeExecutor

                        recipe_exec = RecipeExecutor(
                            session, self._tool_executor, self._emit, self._step_results
                        )
                        captured = await recipe_exec.execute(recipe)
                        self._step_results[step_index] = captured
                        summary = recipe.summary_template or f"Step {step_index} completed"
                        # Add as a user-role summary so it doesn't require a tool_call_id
                        session.add_message(
                            RunMessage.create("user", f"[Step {step_index} result]: {summary}")
                        )
                        await self._emit(
                            RunEvent.create(
                                session.id,
                                EventType.SUMMARY_GENERATED,
                                {"step_index": step_index, "summary": summary},
                            )
                        )
                        return
                    except RecipeExecutionError as e:
                        logger.warning(
                            "Recipe failed for step %d: %s. Falling back to LLM.",
                            step_index,
                            e,
                        )
                        # Roll back messages added by the failed recipe to avoid
                        # orphaned tool responses that confuse the LLM
                        session.messages[:] = session.messages[:msg_count_before]
                        session.step_tool_results[:] = session.step_tool_results[
                            :tool_results_before
                        ]
                        await self._emit(
                            RunEvent.create(
                                session.id,
                                EventType.CACHE_FALLBACK,
                                {"step_index": step_index, "error": str(e)},
                            )
                        )
                        # Fall through to LLM execution

                if cache_cfg.optimization_level >= 1:
                    hint = _build_hint_from_recipe(recipe)
                    if not hint:
                        hint = None
                    else:
                        await self._emit(
                            RunEvent.create(
                                session.id,
                                EventType.CACHE_HIT,
                                {
                                    "step_index": step_index,
                                    "optimization_level": 1,
                                    "confidence": recipe.confidence,
                                },
                            )
                        )
            else:
                reason = "no_recipe" if not recipe else "low_confidence"
                await self._emit(
                    RunEvent.create(
                        session.id,
                        EventType.CACHE_MISS,
                        {"step_index": step_index, "reason": reason},
                    )
                )

        is_last_step = step_index == self._skill.step_count
        step_prompt = build_step_prompt(
            step,
            is_last_step=is_last_step,
            output_fields=self._skill.output_fields,
            hint=hint,
        )
        session.add_message(RunMessage.create("user", step_prompt, is_critical=True))

        text_only_count = 0
        iteration_count = 0
        step_tool_call_count = 0
        hard_cap = self._max_text_before_prompt * 5

        while iteration_count < MAX_TOOL_CALL_ITERATIONS:
            iteration_count += 1

            # Build tools list: control tools + app tools
            all_tools = get_control_tool_schemas() + self._app_tool_schemas

            # Trim context and get LLM response
            trimmed = self._context.trim_messages(session.messages)
            formatted = self._llm.format_messages(trimmed)

            response = await self._llm.complete(formatted, tools=all_tools)

            # Track token usage
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

            # No tool calls - just text (two-tier handling)
            if not response.tool_calls:
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

            # If there's content with tool calls, record the assistant message with tool calls
            if not response.content:
                session.add_message(
                    RunMessage.create("assistant", "", tool_calls=response.tool_calls)
                )

            # Process tool calls
            for tc in response.tool_calls:
                if is_control_tool(tc.name):
                    result = await self._handle_control_tool(tc)
                    if result == "step_complete":
                        return
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

        raise ExecutionError(
            f"Step {step_index} exhausted {MAX_TOOL_CALL_ITERATIONS} iterations "
            "without calling complete_step"
        )

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

        elif tool_call.name == "request_clarification":
            question = tool_call.arguments.get("question", "")
            options = tool_call.arguments.get("options", [])

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

        if self._tool_executor:
            result = await self._tool_executor.execute(tool_call, self._emit)
            content = result.get("content", "")
            session.add_message(
                RunMessage.create(
                    "tool",
                    content,
                    tool_call_id=tool_call.id,
                    app_name=result.get("tool_name"),
                )
            )
            session.step_tool_results.append(result)
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


def _build_hint_from_recipe(recipe: StepRecipe) -> str:
    """Build a natural language hint from a StepRecipe for Level 1 injection."""
    if not recipe.tool_call_recipes:
        return ""

    lines = ["Hint from previous executions (you MUST still call the tools and complete_step):"]
    for i, tc in enumerate(recipe.tool_call_recipes, 1):
        arg_descriptions = []
        for arg_name, mapping in tc.argument_mappings.items():
            if mapping.source == ArgumentSourceType.VARIABLE:
                arg_descriptions.append(f"{arg_name} (from variable '{mapping.key}')")
            elif mapping.source == ArgumentSourceType.STEP_RESULT:
                arg_descriptions.append(f"{arg_name} (from {mapping.key})")
            elif mapping.source == ArgumentSourceType.LITERAL:
                arg_descriptions.append(f"{arg_name} = {mapping.literal_value!r}")
        args_text = ", ".join(arg_descriptions)
        lines.append(f"{i}. Call `{tc.tool_name}` with: {args_text}")

    lines.append("You MUST call the tool(s) above, then call complete_step with the result.")
    return "\n".join(lines)
