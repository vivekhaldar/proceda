"""ABOUTME: Checks source-only compilation, independent review and immutable cached outcomes.
ABOUTME: Mock runtimes expose requests and charges without live inference.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from proceda.llm.runtime import LLMResponse
from proceda.planning.compiler import get_or_compile, load_cached_plan
from proceda.skills.parser import parse_skill

SOURCE = """---
name: dispatch
description: Synthetic bounded decision
---
### Step 1: Decide
Classify text as low or high.
"""


def candidate(skill=None):
    skill = skill or parse_skill(SOURCE)
    source = skill.steps[0].content
    return {
        "ir_version": "1",
        "steps": [
            {
                "index": 1,
                "kind": "decision",
                "source_quote": source,
                "residual_obligations": [],
                "decision": {
                    "field": "priority",
                    "instructions": "Classify per source.",
                    "criteria": {"low": "Low priority", "high": "High priority"},
                    "inputs": {"text": {"kind": "input", "key": "text", "source_quote": source}},
                    "source_quote": source,
                },
            }
        ],
    }


def answer(content, tokens=7):
    return LLMResponse(
        json.dumps(content),
        [],
        prompt_tokens=tokens,
        completion_tokens=2,
        total_tokens=tokens + 2,
        raw_response={"preserved": content},
    )


def setup(tmp_path, raw=None, review=None):
    compiler = AsyncMock()
    compiler.complete.return_value = answer(raw if raw is not None else candidate())
    reviewer = AsyncMock()
    reviewer.complete.return_value = answer(review or {"approved": True, "issues": []}, 11)
    kwargs = dict(
        compiler=compiler,
        reviewer=reviewer,
        cache_dir=tmp_path,
        compiler_config={"model": "compiler-v1", "temperature": 0},
        reviewer_config={"model": "reviewer-v1", "temperature": 0},
    )
    return compiler, reviewer, kwargs


async def compile_plan(kwargs, skill=None, tools=None, fields=None):
    return await get_or_compile(
        skill or parse_skill(SOURCE),
        tools or [],
        fields if fields is not None else ["text"],
        **kwargs,
    )


async def test_source_only_requests_and_exact_outputs_are_preserved(tmp_path):
    compiler, reviewer, kwargs = setup(tmp_path)
    result = await compile_plan(kwargs)
    assert result.status == "accepted" and result.plan is not None
    assert (result.prompt_tokens, result.completion_tokens) == (18, 4)
    for llm in (compiler, reviewer):
        request = llm.complete.call_args.args[0]
        assert "CASE_VALUE_NEVER_PASSED" not in json.dumps(request)
        source = json.loads(request[1]["content"])
        source = source.get("source", source)
        assert source["input_fields"] == ["text"]
        assert source["raw_skill"] == SOURCE
        assert set(source) == {"raw_skill", "steps", "output_fields", "tools", "input_fields"}
        assert llm.complete.call_args.kwargs == {"tools": None, "temperature": 0.0}
    payload = json.loads(result.artifact_path.read_text())["payload"]
    assert payload["compiler_response"]["content"] == compiler.complete.return_value.content
    assert (
        payload["reviewer_response"]["raw_response"] == reviewer.complete.return_value.raw_response
    )
    assert "probabilistic" in payload["semantic_validation"]
    assert result.attempt_path.is_file()


async def test_case_values_cannot_be_supplied_as_input_fields(tmp_path):
    compiler, reviewer, kwargs = setup(tmp_path)
    with pytest.raises(ValueError, match="field names only"):
        await compile_plan(kwargs, fields={"text": "CASE_VALUE_NEVER_PASSED"})
    compiler.complete.assert_not_called()
    reviewer.complete.assert_not_called()
    assert not list(tmp_path.iterdir())


async def test_cache_single_flight_counts_only_first_callers_usage(tmp_path):
    compiler, reviewer, kwargs = setup(tmp_path)

    async def delayed(*args, **kw):
        await asyncio.sleep(0)
        return answer(candidate())

    compiler.complete.side_effect = delayed
    results = await asyncio.gather(compile_plan(kwargs), compile_plan(kwargs))
    assert sorted(r.cache_hit for r in results) == [False, True]
    assert sum(r.prompt_tokens for r in results) == 18
    assert compiler.complete.call_count == reviewer.complete.call_count == 1
    before = results[0].artifact_path.read_bytes()
    await compile_plan(kwargs)
    assert results[0].artifact_path.read_bytes() == before
    assert len(list(tmp_path.glob("attempts/*/*.json"))) == 1


@pytest.mark.parametrize(
    "change", ["source", "approval", "tool", "inputs", "compiler", "reviewer", "epoch"]
)
async def test_source_scope_and_model_changes_invalidate_cache(tmp_path, change):
    compiler, reviewer, kwargs = setup(tmp_path)
    original = await compile_plan(kwargs)
    source, tools, fields = SOURCE, [], ["text"]
    if change == "source":
        source += "\n"
    elif change == "approval":
        source += "[APPROVAL REQUIRED]\n"
    elif change == "tool":
        tools = [{"type": "function", "function": {"name": "other", "parameters": {}}}]
    elif change == "inputs":
        fields += ["other"]
    elif change == "compiler":
        kwargs["compiler_config"] = {"model": "compiler-v2"}
    elif change == "reviewer":
        kwargs["reviewer_config"] = {"model": "reviewer-v2"}
    else:
        kwargs["cache_epoch"] = "new-epoch"
    skill = parse_skill(source)
    compiler.complete.return_value = answer(candidate(skill))
    changed = await compile_plan(kwargs, skill, tools, fields)
    assert changed.cache_key != original.cache_key
    assert not changed.cache_hit
    assert compiler.complete.call_count == 2


async def test_input_field_order_does_not_change_cache(tmp_path):
    compiler, reviewer, kwargs = setup(tmp_path)
    first = await compile_plan(kwargs, fields=["text", "other"])
    second = await compile_plan(kwargs, fields=["other", "text"])
    assert first.cache_key == second.cache_key and second.cache_hit


@pytest.mark.parametrize("kind", ["unsupported", "invalid", "review-rejected"])
async def test_nonexecutable_results_are_cached_with_raw_candidate(tmp_path, kind):
    raw = candidate()
    review = None
    if kind == "unsupported":
        source = parse_skill(SOURCE).steps[0].content
        raw["steps"][0] = {
            "index": 1,
            "kind": "llm",
            "source_quote": source,
            "residual_obligations": [source],
        }
    elif kind == "invalid":
        raw["code"] = "return True"  # Malformed root is never recovered.
    else:
        review = {"approved": False, "issues": ["Decision criterion invents unsupported meaning"]}
    compiler, reviewer, kwargs = setup(tmp_path, raw, review)
    first = await compile_plan(kwargs)
    second = await compile_plan(kwargs)
    assert first.plan is None and second.plan is None and second.cache_hit
    assert first.status == ("rejected" if kind == "review-rejected" else kind)
    assert compiler.complete.call_count == 1
    assert reviewer.complete.call_count == (0 if kind == "invalid" else 1)
    assert json.loads(first.artifact_path.read_text())["payload"]["compiler_response"]["content"]


async def test_full_source_quote_does_not_override_reviewer_on_missing_obligation(tmp_path):
    skill = parse_skill(SOURCE + "Then email an explanation.\n")
    compiler, reviewer, kwargs = setup(
        tmp_path,
        candidate(skill),
        {"approved": False, "issues": ["Pure classification omits the required email"]},
    )
    result = await compile_plan(kwargs, skill)
    assert result.plan is None and result.status == "rejected"
    assert "required email" in result.errors[0]


async def test_reviewer_rejects_wrong_natural_language_regex_translation(tmp_path):
    skill = parse_skill(SOURCE + "Extract token as an uppercase letter followed by four digits.\n")
    raw = candidate(skill)
    raw["steps"][0]["extractors"] = [
        {
            "field": "token",
            "source": {"kind": "input", "key": "text", "source_quote": skill.steps[0].content},
            "pattern": r"\d{4}",
            "group": 0,
            "source_quote": skill.steps[0].content,
        }
    ]
    compiler, reviewer, kwargs = setup(
        tmp_path, raw, {"approved": False, "issues": ["Regex omits required uppercase letter"]}
    )
    result = await compile_plan(kwargs, skill)
    assert result.status == "rejected" and result.plan is None
    reviewer.complete.assert_called_once()


async def test_corrupt_artifact_falls_back_without_recompilation_or_overwrite(tmp_path):
    compiler, reviewer, kwargs = setup(tmp_path)
    first = await compile_plan(kwargs)
    doc = json.loads(first.artifact_path.read_text())
    doc["payload"]["status"] = "unsupported"
    changed = json.dumps(doc)
    first.artifact_path.write_text(changed)
    second = await compile_plan(kwargs)
    assert second.status == "corrupt" and second.plan is None and second.cache_hit
    assert first.artifact_path.read_text() == changed
    compiler.complete.assert_called_once()


@pytest.mark.parametrize(
    "config",
    [
        {"api_key": "DO_NOT_STORE"},
        {"api_base": "https://user:DO_NOT_STORE@example.test"},
        {"nested": {"authorization": "DO_NOT_STORE"}},
    ],
)
async def test_credentials_rejected_before_any_request_or_file(tmp_path, config):
    compiler, reviewer, kwargs = setup(tmp_path)
    kwargs["compiler_config"] = deepcopy(config)
    with pytest.raises(ValueError, match="credentials"):
        await compile_plan(kwargs)
    compiler.complete.assert_not_called()
    assert not list(tmp_path.iterdir())


async def test_provider_failure_is_cached_without_leaking_exception_secret(tmp_path):
    compiler, reviewer, kwargs = setup(tmp_path)
    compiler.complete.side_effect = RuntimeError("Authorization: DO_NOT_STORE")
    first = await compile_plan(kwargs)
    second = await compile_plan(kwargs)
    assert first.status == "error" and second.cache_hit
    assert "DO_NOT_STORE" not in first.artifact_path.read_text()
    compiler.complete.assert_called_once()


async def test_read_only_loader_checks_review_scope_and_exact_file_hash(tmp_path):
    compiler, reviewer, kwargs = setup(tmp_path)
    first = await compile_plan(kwargs)
    skill = parse_skill(SOURCE)
    loaded = load_cached_plan(first.artifact_path, first.cache_key, skill, [], ["text"])
    assert loaded.plan == first.plan and loaded.cache_hit
    assert loaded.prompt_tokens == loaded.completion_tokens == 0
    assert loaded.artifact_sha256 == hashlib.sha256(first.artifact_path.read_bytes()).hexdigest()
    changed = parse_skill(SOURCE.replace("description: Synthetic", "description: Different"))
    rejected = load_cached_plan(first.artifact_path, first.cache_key, changed, [], ["text"])
    assert rejected.status == "corrupt" and rejected.plan is None
    compiler.complete.assert_called_once()


@pytest.mark.parametrize(
    "review",
    [
        {"approved": True, "issues": ["Still an unresolved obligation"]},
        {"approved": "true", "issues": []},
        {"approved": True},
    ],
)
async def test_malformed_or_inconsistent_review_cannot_publish_executable_plan(tmp_path, review):
    compiler, reviewer, kwargs = setup(tmp_path, review=review)
    result = await compile_plan(kwargs)
    assert result.plan is None and result.status in {"invalid", "rejected"}


async def test_duplicate_json_keys_do_not_silently_override_compiler_plan(tmp_path):
    compiler, reviewer, kwargs = setup(tmp_path)
    compiler.complete.return_value = LLMResponse(
        '{"ir_version":"unsupported","ir_version":"1","steps":[]}', []
    )
    result = await compile_plan(kwargs)
    assert result.status == "invalid" and result.plan is None
    reviewer.complete.assert_not_called()


async def test_invalid_step_is_reviewed_as_full_source_llm_and_original_is_retained(tmp_path):
    raw = candidate()
    raw["steps"][0]["code"] = "return True"
    compiler, reviewer, kwargs = setup(tmp_path, raw)
    result = await compile_plan(kwargs)
    assert result.status == "unsupported" and result.plan is None
    payload = json.loads(result.artifact_path.read_text())["payload"]
    assert payload["compiler_candidate"] == raw
    assert payload["compiler_response"]["content"] == compiler.complete.return_value.content
    assert payload["downgrades"][0]["step_index"] == 1
    review_request = json.loads(reviewer.complete.call_args.args[0][1]["content"])
    assert review_request["source"]["raw_skill"] == SOURCE
    assert review_request["downgrades"] == payload["downgrades"]
    reviewed = review_request["candidate"]["steps"][0]
    assert reviewed["kind"] == "llm"
    assert reviewed["residual_obligations"] == [parse_skill(SOURCE).steps[0].content]
    assert "code" not in reviewed


async def test_partial_compilation_preserves_choice_but_review_can_still_reject(tmp_path):
    skill = parse_skill(SOURCE + "### Step 2: Explain\nExplain the classification.\n")
    raw = candidate(skill)
    raw["steps"].append(
        {
            "index": 2,
            "kind": "tool",
            "source_quote": skill.steps[1].content,
            "residual_obligations": [],
            "tools": [{"tool": "unsupported"}],
        }
    )
    compiler, reviewer, kwargs = setup(
        tmp_path, raw, {"approved": False, "issues": ["Surviving criterion is uncertain"]}
    )
    result = await compile_plan(kwargs, skill)
    assert result.status == "rejected" and result.plan is None
    review_request = json.loads(reviewer.complete.call_args.args[0][1]["content"])
    assert [step["kind"] for step in review_request["candidate"]["steps"]] == ["decision", "llm"]
    assert review_request["candidate"]["steps"][1]["source_quote"] == skill.steps[1].content
    assert review_request["source"]["raw_skill"] == skill.raw_content


async def test_partial_compilation_cache_revalidates_recovery_and_review(tmp_path):
    from proceda.planning.compiler import _hash

    skill = parse_skill(SOURCE + "### Step 2: Explain\nExplain the classification.\n")
    raw = candidate(skill)
    raw["steps"].append(None)
    compiler, reviewer, kwargs = setup(tmp_path, raw)
    result = await compile_plan(kwargs, skill)
    assert result.status == "accepted"
    assert [step.kind for step in result.plan.steps] == ["decision", "llm"]
    loaded = load_cached_plan(result.artifact_path, result.cache_key, skill, [], ["text"])
    assert loaded.plan == result.plan
    document = json.loads(result.artifact_path.read_text())
    document["payload"]["downgrades"] = []
    # Even a recomputed integrity hash cannot conceal divergence from the
    # original compiler candidate and the deterministic recovery operation.
    document["sha256"] = _hash(document["payload"])
    result.artifact_path.write_text(json.dumps(document))
    corrupt = load_cached_plan(result.artifact_path, result.cache_key, skill, [], ["text"])
    assert corrupt.status == "corrupt" and corrupt.plan is None
    compiler.complete.assert_called_once()
    reviewer.complete.assert_called_once()


async def test_recovery_semantics_version_invalidates_cache(tmp_path, monkeypatch):
    import proceda.planning.compiler as module

    compiler, reviewer, kwargs = setup(tmp_path)
    first = await compile_plan(kwargs)
    monkeypatch.setattr(module, "RECOVERY_VERSION", "changed")
    second = await compile_plan(kwargs)
    assert second.cache_key != first.cache_key and not second.cache_hit
    old = load_cached_plan(first.artifact_path, first.cache_key, parse_skill(SOURCE), [], ["text"])
    assert old.status == "corrupt" and old.plan is None
    assert compiler.complete.call_count == reviewer.complete.call_count == 2
