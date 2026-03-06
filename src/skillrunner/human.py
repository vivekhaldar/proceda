from __future__ import annotations

from typing import Protocol

from skillrunner.session import ApprovalRequest, ClarificationRequest


class HumanInterface(Protocol):
    async def request_approval(self, request: ApprovalRequest) -> bool: ...

    async def request_clarification(self, request: ClarificationRequest) -> str: ...


class TerminalHumanInterface:
    async def request_approval(self, request: ApprovalRequest) -> bool:
        prompt = (
            f"Approve {request.approval_type} for step {request.step_index}"
            f" ({request.step_title})? [y/N]: "
        )
        return input(prompt).strip().lower() in {"y", "yes"}

    async def request_clarification(self, request: ClarificationRequest) -> str:
        if request.options:
            print("Options:", ", ".join(request.options))
        return input(f"Clarification requested: {request.question}\n> ").strip()


class AutoApproveHumanInterface:
    async def request_approval(self, request: ApprovalRequest) -> bool:
        return True

    async def request_clarification(self, request: ClarificationRequest) -> str:
        return request.options[0] if request.options else ""
