from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from skillrunner.events import RunEvent


class EventLogSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def handle(self, event: RunEvent) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


class RunDirectoryManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(".skillrunner") / "runs"

    def create_run_dir(self, run_id: str) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_dir = self.root / f"{stamp}-{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir
