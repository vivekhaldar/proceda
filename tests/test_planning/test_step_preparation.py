"""ABOUTME: Tests per-step compilation failure isolation and replayed admission.
ABOUTME: Synthetic source contracts and mocked providers never expose benchmark cases.
"""

import json
from copy import deepcopy

import pytest

from proceda.planning.compiler import _hash, get_or_compile, load_cached_plan
from proceda.skills.parser import parse_skill
from tests.test_planning.test_compiler import SOURCE, answer, compact_candidate, setup


def dependent_source():
    skill = parse_skill("""---
name: stock-access
description: Synthetic dependent contracts
---
### Step 1: Retrieve
Call lookup with record_id.
### Step 2: Classify
Use tool outputs to classify record as open or closed.
### Step 3: Route
Use classification from Step 2 to choose accept or deny.
""")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "stock__lookup",
                "parameters": {
                    "type": "object",
                    "properties": {"record_id": {"type": "string"}},
                    "required": ["record_id"],
                },
            },
        }
    ]
    steps = [
        {
            "index": 1,
            "kind": "tool",
            "tools": [
                {
                    "tool": "stock__lookup",
                    "source_quote": "Call lookup with record_id.",
                    "arguments": {
                        "record_id": {
                            "kind": "input",
                            "key": "record_id",
                            "source_quote": "record_id",
                        }
                    },
                }
            ],
        },
        {
            "index": 2,
            "kind": "decision",
            "decision": {
                "field": "classification",
                "criteria": {"open": "Open", "closed": "Closed"},
                "source_quote": "classify record as open or closed",
                "inputs": {
                    "record": {
                        "kind": "receipt",
                        "step": 1,
                        "tool": "stock__lookup",
                        "path": "",
                        "source_quote": "tool outputs",
                    }
                },
            },
        },
        {
            "index": 3,
            "kind": "decision",
            "decision": {
                "field": "route",
                "criteria": {"accept": "Accept", "deny": "Deny"},
                "source_quote": "choose accept or deny",
                "inputs": {
                    "prior": {
                        "kind": "fact",
                        "step": 2,
                        "key": "classification",
                        "source_quote": "classification from Step 2",
                    }
                },
            },
        },
    ]
    return skill, tools, steps


@pytest.mark.parametrize("rejected_index", [1, 2])
async def test_review_rejection_removes_contract_before_compiling_dependents(
    tmp_path, rejected_index
):
    skill, tools, steps = dependent_source()
    compiler, reviewer, kwargs = setup(tmp_path)
    compiler.complete.side_effect = [answer(step) for step in steps]
    reviewer.complete.side_effect = [
        answer(
            {
                "approved": i != rejected_index,
                "issues": [] if i != rejected_index else ["Unsupported contract"],
            },
            11,
        )
        for i in range(1, rejected_index + 1)
    ]
    result = await get_or_compile(skill, tools, ["record_id"], **kwargs, mode="business_decisions")
    assert result.status == "unsupported" and result.plan is None
    payload = json.loads(result.artifact_path.read_text())["payload"]
    records = payload["step_preparations"]
    assert records[rejected_index - 1]["status"] == "rejected"
    assert all(r["status"] == "invalid" for r in records[rejected_index:])
    assert all(s["kind"] == "llm" for s in payload["validated_plan"]["steps"][rejected_index - 1 :])
    assert compiler.complete.await_count == 3 and reviewer.complete.await_count == rejected_index
    assert result.prompt_tokens == 3 * 7 + rejected_index * 11
    next_request = json.loads(records[rejected_index]["compiler_request"][1]["content"])
    assert next_request["admitted_prefix"][-1]["kind"] == "llm"
    assert next_request["admitted_prefix"][-1]["decision"] is None
    loaded = load_cached_plan(
        result.artifact_path,
        result.cache_key,
        skill,
        tools,
        ["record_id"],
        mode="business_decisions",
    )
    assert loaded.status == "unsupported" and loaded.cache_hit


@pytest.mark.parametrize(
    "mutation",
    [
        lambda records: records[0].update(reviewer_response=None),
        lambda records: records[1]["reviewer_response"].update(
            content='{"approved":false,"issues":["Reject"]}'
        ),
        lambda records: records.reverse(),
        lambda records: records[1]["compiler_request"][1].update(
            content='{"source":"replacement"}'
        ),
    ],
)
async def test_cached_accepted_steps_require_their_original_admission_and_source(
    tmp_path, mutation
):
    skill, tools, steps = dependent_source()
    compiler, reviewer, kwargs = setup(tmp_path)
    compiler.complete.side_effect = [answer(step) for step in steps]
    result = await get_or_compile(skill, tools, ["record_id"], **kwargs, mode="business_decisions")
    assert result.status == "accepted"
    document = json.loads(result.artifact_path.read_text())
    mutation(document["payload"]["step_preparations"])
    document["sha256"] = _hash(document["payload"])
    result.artifact_path.write_text(json.dumps(document))
    loaded = load_cached_plan(
        result.artifact_path,
        result.cache_key,
        skill,
        tools,
        ["record_id"],
        mode="business_decisions",
    )
    assert loaded.status == "corrupt" and loaded.plan is None


async def test_truncated_step_does_not_retry_or_prevent_independent_later_admission(tmp_path):
    skill = parse_skill(SOURCE + "### Step 2: Another Decision\nClassify text as low or high.\n")
    first = answer(compact_candidate(skill))
    first.completion_tokens = 2048
    first.raw_response = {"choices": [{"finish_reason": "length"}]}
    second = deepcopy(compact_candidate(skill))
    second["index"] = 2
    compiler, reviewer, kwargs = setup(tmp_path)
    compiler.complete.side_effect = [first, answer(second)]
    identity = {
        "model": "same-model",
        "max_tokens": 2048,
        "reasoning_effort": "low",
        "temperature": 0,
    }
    kwargs.update(compiler_config=identity, reviewer_config=identity)
    result = await get_or_compile(skill, [], ["text"], **kwargs, mode="business_decisions")
    assert result.status == "accepted"
    assert [step.kind for step in result.plan.steps] == ["llm", "decision"]
    assert result.completion_tokens == 2052
    assert compiler.complete.await_count == 2 and reviewer.complete.await_count == 1
    payload = json.loads(result.artifact_path.read_text())["payload"]
    assert payload["identity"]["compiler_config"] == identity
    assert payload["identity"]["reviewer_config"] == identity
    assert payload["step_preparations"][0]["status"] == "invalid"
    for runtime in (compiler, reviewer):
        for call in runtime.complete.call_args_list:
            assert call.kwargs == {"tools": None, "temperature": 0.0}
            source = json.loads(call.args[0][1]["content"])["source"]
            assert source["input_fields"] == ["text"]
            assert source["raw_skill"] == skill.raw_content
