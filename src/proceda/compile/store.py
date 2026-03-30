"""ABOUTME: Persists and loads compiled step functions to disk.
ABOUTME: Handles manifest and per-step Python files with hash-based invalidation."""

from __future__ import annotations

import importlib.util
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from proceda.cache.store import CacheStore
from proceda.compile.models import CompiledSkillManifest

logger = logging.getLogger(__name__)

COMPILED_DIR = ".proceda/compiled"


class CompiledSkillStore:
    """Reads and writes compiled step functions to the filesystem."""

    def __init__(self, base_dir: str = COMPILED_DIR) -> None:
        self._base_dir = Path(base_dir)

    def save(
        self,
        manifest: CompiledSkillManifest,
        functions: dict[int, str],
    ) -> Path:
        """Save compiled manifest and step functions."""
        from proceda.compile.compiler import _build_step_function

        skill_dir = self._base_dir / manifest.skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Write manifest
        manifest_path = skill_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")

        # Write step functions
        for step_idx, code in functions.items():
            step_path = skill_dir / f"step_{step_idx}.py"
            module_code = _build_step_function(code)
            step_path.write_text(module_code, encoding="utf-8")

        return skill_dir

    def load_manifest(
        self, skill_id: str, current_skill_content: str
    ) -> CompiledSkillManifest | None:
        """Load manifest, returning None if missing or stale."""
        skill_dir = self._base_dir / skill_id
        manifest_path = skill_dir / "manifest.json"
        if not manifest_path.exists():
            return None

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = CompiledSkillManifest.from_dict(data)

        current_hash = CacheStore.compute_content_hash(current_skill_content)
        if manifest.skill_content_hash != current_hash:
            logger.warning("Compiled skill %s is stale (hash mismatch)", skill_id)
            return None

        return manifest

    def load_step_function(self, skill_id: str, step_index: int) -> Callable[..., Any] | None:
        """Load and return the execute() function for a compiled step."""
        step_path = self._base_dir / skill_id / f"step_{step_index}.py"
        if not step_path.exists():
            return None

        try:
            spec = importlib.util.spec_from_file_location(
                f"compiled_step_{step_index}", str(step_path)
            )
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.execute
        except Exception as e:
            logger.warning("Failed to load compiled step %d: %s", step_index, e)
            return None

    def delete(self, skill_id: str) -> bool:
        """Delete all compiled files for a skill."""
        import shutil

        skill_dir = self._base_dir / skill_id
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            return True
        return False
