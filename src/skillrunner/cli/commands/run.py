from __future__ import annotations

from skillrunner.agent import Agent


def run(path: str) -> int:
    agent = Agent.from_path(path)
    result = agent.run()
    print(f"status={result.status.value}")
    print(result.summary)
    print(f"event_log={result.event_log_path}")
    if result.status.value == "completed":
        return 0
    if result.status.value == "cancelled":
        return 3
    return 1
