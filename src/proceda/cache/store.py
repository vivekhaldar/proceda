"""ABOUTME: Persists and loads step-level execution caches to disk.
ABOUTME: Handles cache invalidation based on SKILL.md content hash."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from proceda.cache.models import SkillCache

logger = logging.getLogger(__name__)

CACHE_DIR = ".proceda/cache"


class CacheStore:
    """Reads and writes SkillCache objects to the filesystem."""

    def __init__(self, base_dir: str = CACHE_DIR) -> None:
        self._base_dir = Path(base_dir)

    def save(self, cache: SkillCache) -> Path:
        """Write a SkillCache to disk."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        path = self._base_dir / f"{cache.skill_id}.json"
        path.write_text(json.dumps(cache.to_dict(), indent=2), encoding="utf-8")
        return path

    def load(self, skill_id: str, current_skill_content: str) -> SkillCache | None:
        """Load a SkillCache from disk, returning None if missing or stale."""
        path = self._base_dir / f"{skill_id}.json"
        if not path.exists():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))
        cache = SkillCache.from_dict(data)

        current_hash = self.compute_content_hash(current_skill_content)
        if cache.skill_content_hash != current_hash:
            logger.warning(
                "Cache for skill %s is stale (hash mismatch: %s vs %s)",
                skill_id,
                cache.skill_content_hash,
                current_hash,
            )
            return None

        return cache

    def delete(self, skill_id: str) -> bool:
        """Delete the cache file for a skill. Returns True if deleted."""
        path = self._base_dir / f"{skill_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def list_cached_skills(self) -> list[str]:
        """Return skill IDs that have cache files on disk."""
        if not self._base_dir.exists():
            return []
        return [p.stem for p in self._base_dir.glob("*.json")]

    @staticmethod
    def compute_content_hash(content: str) -> str:
        """Compute SHA256 hash of SKILL.md content (first 16 hex chars)."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]
