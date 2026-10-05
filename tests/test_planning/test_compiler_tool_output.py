"""ABOUTME: Exercises strict structured compiler output and its cached evidence replay.
ABOUTME: Uses synthetic plans and mocked providers; no paid inference or domain fixtures.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict

import pytest

from proceda.llm.runtime import LLMResponse
from proceda.planning import compiler as module
from proceda.session import ToolCall
from proceda.skills.parser import parse_skill
from tests.test_planning.test_compiler import SOURCE, compact_candidate, compile_plan, setup


def structured_response(arguments=None, *, name="record_source_step", raw_arguments=None):
    arguments = deepcopy(arguments if arguments is not None else compact_candidate())
    if arguments.get("decision"):
        arguments["decision"].setdefault("kind", "choice")
    return LLMResponse(
        None,
        [ToolCall("source-step-1", name, arguments)],
        prompt_tokens=11,
        completion_tokens=7,
        total_tokens=18,
        raw_response={
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "source-step-1",
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": raw_arguments
                                    if raw_arguments is not None
                                    else json.dumps(arguments),
                                },
                            }
                        ],
                    },
                }
            ],
        },
    )


def test_exact_structured_step_and_serialized_tool_call_replay_have_identical_meaning():
    response = structured_response()
    expected = response.tool_calls[0].arguments
    assert module._parse_business_response(response) == expected
    restored = LLMResponse(**asdict(response))
    assert isinstance(restored.tool_calls[0], dict)
    assert module._parse_business_response(restored) == expected
    restored.raw_response = None
    with pytest.raises(ValueError):
        module._parse_business_response(restored)


@pytest.mark.parametrize("fault", ["wrong_name", "multiple_calls"])
def test_structured_compiler_requires_exactly_one_expected_raw_and_parsed_call(fault):
    response = structured_response(
        name="other_function" if fault == "wrong_name" else "record_source_step"
    )
    if fault == "multiple_calls":
        response.tool_calls *= 2
        response.raw_response["choices"][0]["message"]["tool_calls"] *= 2
    with pytest.raises(ValueError):
        module._parse_business_response(response)


@pytest.mark.parametrize("fault", ["duplicate_key", "nonfinite", "different_value", "bool_integer"])
def test_raw_compiler_arguments_reject_ambiguity_nonfinite_values_and_typed_disagreement(fault):
    arguments = {"index": 1, "kind": "llm"}
    if fault == "duplicate_key":
        raw = '{"index":1,"index":1,"kind":"llm"}'
    elif fault == "different_value":
        raw = '{"index":2,"kind":"llm"}'
    elif fault == "bool_integer":
        raw = '{"index":true,"kind":"llm"}'
    else:
        arguments = compact_candidate()
        arguments["decision"]["kind"] = "choice"
        arguments["decision"]["inputs"] = {
            "threshold": {"kind": "literal", "value": float("nan"), "source_quote": "NaN"}
        }
        raw = json.dumps(arguments)
    response = structured_response(arguments, raw_arguments=raw)
    with pytest.raises(ValueError):
        module._parse_business_response(response)


def test_complete_looking_structured_output_with_length_stop_is_rejected():
    response = structured_response()
    response.raw_response["choices"][0]["finish_reason"] = "length"
    with pytest.raises(ValueError):
        module._parse_business_response(response)


@pytest.mark.parametrize("fault", ["missing_quote", "misnested_inputs"])
def test_structured_schema_rejects_ungrounded_or_misnested_reference_shapes(fault):
    arguments = compact_candidate()
    if fault == "missing_quote":
        del arguments["decision"]["inputs"]["text"]["source_quote"]
    else:
        arguments["decision"]["inputs"] = arguments["decision"]["inputs"]["text"]
    with pytest.raises(ValueError):
        module._parse_business_response(structured_response(arguments))


async def test_compiler_tool_schema_is_audited_and_revalidated_with_raw_response(tmp_path):
    compiler, reviewer, kwargs = setup(tmp_path)
    compiler.complete.return_value = structured_response()
    result = await compile_plan(kwargs | {"mode": "business_decisions"})
    assert result.status == "accepted" and result.plan is not None
    assert compiler.complete.call_args.kwargs["tools"] == [module.BUSINESS_STEP_TOOL]
    assert reviewer.complete.call_args.kwargs["tools"] is None
    document = json.loads(result.artifact_path.read_text())
    record = document["payload"]["step_preparations"][0]
    assert record["compiler_tools"] == [module.BUSINESS_STEP_TOOL]
    assert record["compiler_response"]["tool_calls"][0]["arguments"]["index"] == 1
    loaded = module.load_cached_plan(
        result.artifact_path,
        result.cache_key,
        parse_skill(SOURCE),
        [],
        ["text"],
        mode="business_decisions",
    )
    assert loaded.plan == result.plan and loaded.cache_hit
    record["compiler_tools"][0]["function"]["name"] = "other_function"
    document["sha256"] = module._hash(document["payload"])
    result.artifact_path.write_text(json.dumps(document))
    altered = module.load_cached_plan(
        result.artifact_path,
        result.cache_key,
        parse_skill(SOURCE),
        [],
        ["text"],
        mode="business_decisions",
    )
    assert altered.status == "corrupt" and altered.plan is None


async def test_changed_structured_schema_changes_identity_and_invalidates_old_cache(
    tmp_path, monkeypatch
):
    compiler, _, kwargs = setup(tmp_path)
    compiler.complete.return_value = structured_response()
    result = await compile_plan(kwargs | {"mode": "business_decisions"})
    assert result.status == "accepted"
    replacement = deepcopy(module.BUSINESS_STEP_TOOL)
    replacement["function"]["description"] += " Changed schema contract."
    monkeypatch.setattr(module, "BUSINESS_STEP_TOOL", replacement)
    identity = module.compiler_identity(
        parse_skill(SOURCE),
        [],
        ["text"],
        mode="business_decisions",
        compiler_config=kwargs["compiler_config"],
        reviewer_config=kwargs["reviewer_config"],
    )
    assert module._hash(identity) != result.cache_key
    loaded = module.load_cached_plan(
        result.artifact_path,
        result.cache_key,
        parse_skill(SOURCE),
        [],
        ["text"],
        mode="business_decisions",
    )
    assert loaded.status == "corrupt" and loaded.plan is None
