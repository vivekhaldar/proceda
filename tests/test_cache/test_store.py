"""ABOUTME: Tests for cache store persistence and invalidation.
ABOUTME: Verifies save/load/delete with hash-based cache invalidation."""

from __future__ import annotations

from proceda.cache.models import SkillCache, StepRecipe
from proceda.cache.store import CacheStore

SKILL_CONTENT = "### Step 1: Do something\nInstructions here."


def _make_cache(skill_id: str = "skill_abc") -> SkillCache:
    return SkillCache(
        skill_id=skill_id,
        skill_content_hash=CacheStore.compute_content_hash(SKILL_CONTENT),
        step_recipes={
            1: StepRecipe(step_index=1, confidence=1.0, summary_template="Done"),
        },
        source_run_ids=["run_1", "run_2", "run_3"],
        created_at="2026-03-29T10:00:00+00:00",
    )


def test_save_then_load(tmp_path):
    store = CacheStore(str(tmp_path))
    cache = _make_cache()
    path = store.save(cache)

    assert path.exists()

    loaded = store.load("skill_abc", SKILL_CONTENT)
    assert loaded is not None
    assert loaded.skill_id == cache.skill_id
    assert loaded.source_run_ids == cache.source_run_ids
    assert 1 in loaded.step_recipes


def test_load_stale_hash(tmp_path):
    store = CacheStore(str(tmp_path))
    store.save(_make_cache())

    result = store.load("skill_abc", "DIFFERENT CONTENT")
    assert result is None


def test_load_no_file(tmp_path):
    store = CacheStore(str(tmp_path))
    assert store.load("nonexistent", SKILL_CONTENT) is None


def test_delete(tmp_path):
    store = CacheStore(str(tmp_path))
    store.save(_make_cache())

    assert store.delete("skill_abc") is True
    assert store.delete("skill_abc") is False
    assert store.load("skill_abc", SKILL_CONTENT) is None


def test_list_cached_skills(tmp_path):
    store = CacheStore(str(tmp_path))
    store.save(_make_cache("skill_one"))
    store.save(_make_cache("skill_two"))

    skills = store.list_cached_skills()
    assert set(skills) == {"skill_one", "skill_two"}


def test_list_cached_skills_empty(tmp_path):
    store = CacheStore(str(tmp_path))
    assert store.list_cached_skills() == []
