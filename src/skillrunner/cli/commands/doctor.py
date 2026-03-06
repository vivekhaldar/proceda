from __future__ import annotations

import shutil

from skillrunner.config import load_config


def doctor() -> int:
    cfg = load_config()
    print("Python: ok")
    print(f"Config run_root: {cfg.run_root}")
    print(f"Runtime executable present: {'yes' if shutil.which('python') else 'unknown'}")
    return 0
