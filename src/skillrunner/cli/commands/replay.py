from __future__ import annotations

from skillrunner.replay import read_event_log


def replay(path: str) -> int:
    events = read_event_log(path)
    for evt in events:
        print(f"{evt['timestamp']} {evt['type']} {evt['payload']}")
    return 0
