from skillrunner.session import RunResult, RunStatus


def build_summary(status: RunStatus, completed_steps: int, failed_step: int | None = None) -> str:
    if status == RunStatus.COMPLETED:
        return f"Run completed successfully ({completed_steps} steps)."
    if status == RunStatus.CANCELLED:
        return f"Run cancelled at step {failed_step}."
    if status == RunStatus.FAILED:
        return f"Run failed at step {failed_step}."
    return f"Run ended with status={status}."
