from __future__ import annotations

import json
from pathlib import Path


def read_event_log(path: str | Path) -> list[dict]:
    events = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
