"""ABOUTME: Load experiment credentials from pass into memory without printing them.
ABOUTME: Uses a bounded subprocess and a Darwin-safe Homebrew PATH.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

PASS_BINARY = Path("/Users/Vivek.Haldar/.homebrew/bin/pass")


def load_pass_credentials(
    entries: Mapping[str, str], *, pass_binary: Path = PASS_BINARY
) -> dict[str, str]:
    """Return environment-name -> secret for paired_experiment.main(credentials=...)."""
    env = dict(os.environ)
    env["PATH"] = str(pass_binary.parent) + os.pathsep + env.get("PATH", "")
    result = {}
    for env_name, entry in entries.items():
        if not env_name or not entry or entry.startswith("-"):
            raise ValueError("Credential mappings require an environment name and pass entry")
        try:
            process = subprocess.run(
                [str(pass_binary), "show", entry],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=env,
                text=True,
                timeout=15,
                check=True,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError(f"Unable to load credential for {env_name}") from exc
        lines = process.stdout.splitlines()
        if not lines or not lines[0].strip():
            raise RuntimeError(f"Empty credential for {env_name}")
        result[env_name] = lines[0].strip()
    return result
