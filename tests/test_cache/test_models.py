"""ABOUTME: Tests for cache data models.
ABOUTME: Verifies serialization round-trips and recipe lookup."""

from __future__ import annotations

from proceda.cache.models import (
    ArgumentMapping,
    ArgumentSourceType,
    ResultCapture,
    SkillCache,
    StepRecipe,
    ToolCallRecipe,
)


def _make_cache() -> SkillCache:
    """Build a SkillCache with all argument source types for testing."""
    return SkillCache(
        skill_id="test_skill_123",
        skill_content_hash="abcdef1234567890",
        step_recipes={
            1: StepRecipe(
                step_index=1,
                tool_call_recipes=[],
                result_captures=[],
                summary_template="Step 1 done",
                confidence=0.5,
            ),
            2: StepRecipe(
                step_index=2,
                tool_call_recipes=[
                    ToolCallRecipe(
                        tool_name="calculate_score",
                        argument_mappings={
                            "product_id": ArgumentMapping(
                                source=ArgumentSourceType.VARIABLE,
                                key="product_id",
                            ),
                            "label": ArgumentMapping(
                                source=ArgumentSourceType.STEP_RESULT,
                                key="step[1].label_text",
                            ),
                            "mode": ArgumentMapping(
                                source=ArgumentSourceType.LITERAL,
                                literal_value="strict",
                            ),
                        },
                    ),
                ],
                result_captures=[
                    ResultCapture(field_name="score", from_tool_call_index=0),
                ],
                summary_template="Scored {product_id}: {score}",
                confidence=1.0,
            ),
        },
        source_run_ids=["run_aaa", "run_bbb", "run_ccc"],
        created_at="2026-03-29T10:00:00+00:00",
    )


def test_round_trip_serialization():
    cache = _make_cache()
    data = cache.to_dict()
    restored = SkillCache.from_dict(data)

    assert restored.skill_id == cache.skill_id
    assert restored.skill_content_hash == cache.skill_content_hash
    assert restored.source_run_ids == cache.source_run_ids
    assert restored.created_at == cache.created_at
    assert set(restored.step_recipes.keys()) == set(cache.step_recipes.keys())

    # Check step 2 recipe in detail
    r2 = restored.step_recipes[2]
    assert r2.step_index == 2
    assert r2.confidence == 1.0
    assert len(r2.tool_call_recipes) == 1

    tc = r2.tool_call_recipes[0]
    assert tc.tool_name == "calculate_score"
    assert tc.argument_mappings["product_id"].source == ArgumentSourceType.VARIABLE
    assert tc.argument_mappings["product_id"].key == "product_id"
    assert tc.argument_mappings["label"].source == ArgumentSourceType.STEP_RESULT
    assert tc.argument_mappings["label"].key == "step[1].label_text"
    assert tc.argument_mappings["mode"].source == ArgumentSourceType.LITERAL
    assert tc.argument_mappings["mode"].literal_value == "strict"

    assert len(r2.result_captures) == 1
    assert r2.result_captures[0].field_name == "score"
    assert r2.result_captures[0].from_tool_call_index == 0


def test_get_recipe_found():
    cache = _make_cache()
    assert cache.get_recipe(2) is not None
    assert cache.get_recipe(2).tool_call_recipes[0].tool_name == "calculate_score"


def test_get_recipe_missing():
    cache = _make_cache()
    assert cache.get_recipe(99) is None


def test_empty_cache_round_trip():
    cache = SkillCache(
        skill_id="empty",
        skill_content_hash="0000000000000000",
    )
    data = cache.to_dict()
    restored = SkillCache.from_dict(data)
    assert restored.skill_id == "empty"
    assert restored.step_recipes == {}
    assert restored.source_run_ids == []
