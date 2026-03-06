from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from skillrunner.events import CompositeEventSink, RunEvent
from skillrunner.human import AutoApproveHumanInterface, HumanInterface
from skillrunner.internal.context import RuntimeContext
from skillrunner.internal.summary import build_summary
from skillrunner.llm import LLMRuntime
from skillrunner.session import ApprovalRecord, ApprovalRequest, RunMessage, RunResult, RunSession, RunStatus
from skillrunner.skill import Skill, SkillStep
from skillrunner.store import EventLogSink, RunDirectoryManager


@dataclass(slots=True)
class RunHandle:
    session: RunSession
    event_log_path: Path


class Runtime:
    def __init__(
        self,
        *,
        llm: LLMRuntime | None = None,
        human: HumanInterface | None = None,
        sink: CompositeEventSink | None = None,
        run_dir_manager: RunDirectoryManager | None = None,
    ) -> None:
        self.llm = llm or LLMRuntime()
        self.human = human or AutoApproveHumanInterface()
        self.sink = sink or CompositeEventSink()
        self.run_dir_manager = run_dir_manager or RunDirectoryManager()

    async def run(self, skill: Skill, session: RunSession | None = None) -> RunResult:
        session = session or RunSession.create(skill_id=skill.id, skill_name=skill.name)
        run_dir = self.run_dir_manager.create_run_dir(session.id)
        log_path = run_dir / "events.jsonl"
        self.sink.add_sink(EventLogSink(log_path))
        await self._emit(session, "run.created", {"skill_id": skill.id, "skill_name": skill.name})
        session.status = RunStatus.RUNNING
        session.started_at = session.last_activity_at
        await self._emit(session, "run.started", {"status": session.status.value})

        ctx = RuntimeContext()
        failed_step: int | None = None
        try:
            for step in skill.steps:
                session.current_step = step.index
                await self._emit(session, "step.started", {"step_index": step.index, "title": step.title})
                if step.requires_pre_approval:
                    approved = await self._request_approval(session, step, "pre_step")
                    if not approved:
                        session.status = RunStatus.CANCELLED
                        failed_step = step.index
                        break

                decision = await self.llm.decide_step(step, ctx.message_history)
                if decision.request_clarification:
                    session.status = RunStatus.AWAITING_INPUT
                    await self._emit(
                        session,
                        "clarification.requested",
                        {"question": decision.request_clarification, "step_index": step.index},
                    )
                msg = RunMessage(
                    id=f"msg_{session.id}_{step.index}",
                    role="assistant",
                    content=decision.assistant_message,
                    timestamp=session.last_activity_at,
                )
                session.messages.append(msg)
                ctx.add_message(msg.content)
                await self._emit(session, "message.assistant", {"content": msg.content})

                if step.requires_post_approval:
                    approved = await self._request_approval(session, step, "post_step")
                    if not approved:
                        session.status = RunStatus.CANCELLED
                        failed_step = step.index
                        break

                await self._emit(session, "step.completed", {"step_index": step.index, "title": step.title})

            if session.status == RunStatus.RUNNING:
                session.status = RunStatus.COMPLETED
        except Exception as exc:  # noqa: BLE001
            failed_step = session.current_step or 1
            session.status = RunStatus.FAILED
            await self._emit(session, "run.failed", {"error": str(exc), "failed_step": failed_step})

        summary = build_summary(session.status, completed_steps=max(session.current_step, 0), failed_step=failed_step)
        if session.status == RunStatus.COMPLETED:
            await self._emit(session, "run.completed", {"summary": summary})
        elif session.status == RunStatus.CANCELLED:
            await self._emit(session, "run.cancelled", {"summary": summary})

        return RunResult(
            session_id=session.id,
            status=session.status,
            summary=summary,
            completed_steps=session.current_step,
            failed_step=failed_step,
            event_log_path=log_path,
        )

    async def _request_approval(self, session: RunSession, step: SkillStep, approval_type: str) -> bool:
        session.status = RunStatus.AWAITING_APPROVAL
        req = ApprovalRequest(
            step_index=step.index,
            step_title=step.title,
            approval_type=approval_type,  # type: ignore[arg-type]
            context=step.content,
        )
        session.pending_approval = req
        await self._emit(
            session,
            "approval.requested",
            {"step_index": step.index, "step_title": step.title, "approval_type": approval_type},
        )
        approved = await self.human.request_approval(req)
        session.approval_records.append(
            ApprovalRecord(
                step_index=step.index,
                approval_type=approval_type,  # type: ignore[arg-type]
                approved=approved,
                timestamp=session.last_activity_at,
            )
        )
        session.pending_approval = None
        session.status = RunStatus.RUNNING
        await self._emit(
            session,
            "approval.responded",
            {"step_index": step.index, "approval_type": approval_type, "approved": approved},
        )
        return approved

    async def _emit(self, session: RunSession, event_type: str, payload: dict) -> None:
        event = RunEvent(run_id=session.id, type=event_type, payload=payload)
        await self.sink.handle(event)


def run_sync(runtime: Runtime, skill: Skill, session: RunSession | None = None) -> RunResult:
    return asyncio.run(runtime.run(skill, session=session))
