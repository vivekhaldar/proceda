"""ABOUTME: Exercises source coverage, binding boundaries and unsupported planning operations.
ABOUTME: Uses renamed synthetic procedures without domain-specific execution rules.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from proceda.planning.models import PlanValidationError, validate_partial_plan, validate_plan
from proceda.skills.parser import parse_skill

SOURCE = """---
name: parcel-routing
description: Synthetic source-only planning
output_fields: [status]
---
### Step 1: Choose
Classify message as route or skip. Extract token as one uppercase letter followed by four digits.
### Step 2: Retrieve
If classification is route, call lookup with token. Then explain status.
### Step 3: Return
Report status from lookup as JSON and XML.
"""
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "parcel__lookup",
            "parameters": {
                "type": "object",
                "properties": {"token": {"type": "string"}},
                "required": ["token"],
            },
        },
    }
]


def fixture():
    skill = parse_skill(SOURCE)
    first, second, third = (step.content for step in skill.steps)
    plan = {
        "ir_version": "1",
        "steps": [
            {
                "index": 1,
                "kind": "decision",
                "source_quote": first,
                "residual_obligations": [],
                "extractors": [
                    {
                        "field": "token",
                        "source": {"kind": "input", "key": "message", "source_quote": first},
                        "pattern": r"[A-Z]\d{4}",
                        "group": 0,
                        "source_quote": (
                            "Extract token as one uppercase letter followed by four digits."
                        ),
                    }
                ],
                "decision": {
                    "field": "classification",
                    "instructions": "Classify message.",
                    "criteria": {"route": "Routing request", "skip": "No routing request"},
                    "inputs": {
                        "message": {"kind": "input", "key": "message", "source_quote": first},
                        "token": {"kind": "fact", "step": 1, "key": "token", "source_quote": first},
                    },
                    "source_quote": first,
                },
            },
            {
                "index": 2,
                "kind": "tool",
                "source_quote": second,
                "residual_obligations": ["Then explain status."],
                "tools": [
                    {
                        "tool": "parcel__lookup",
                        "arguments": {
                            "token": {
                                "kind": "fact",
                                "step": 1,
                                "key": "token",
                                "source_quote": second,
                            }
                        },
                        "source_quote": second,
                        "when": {
                            "ref": {
                                "kind": "fact",
                                "step": 1,
                                "key": "classification",
                                "source_quote": second,
                            },
                            "equals": "route",
                            "source_quote": second,
                        },
                    }
                ],
            },
            {
                "index": 3,
                "kind": "render",
                "source_quote": third,
                "residual_obligations": [],
                "render": {
                    "format": "json_xml",
                    "fields": {
                        "status": {
                            "kind": "receipt",
                            "step": 2,
                            "tool": "parcel__lookup",
                            "path": "/status",
                            "source_quote": third,
                        }
                    },
                    "source_quote": third,
                },
            },
        ],
    }
    return skill, plan


def test_dependent_bindings_and_roundtrip_preserve_residual_work():
    skill, raw = fixture()
    plan = validate_plan(raw, skill, TOOLS, ["message"])
    assert plan.steps[1].residual_obligations == ("Then explain status.",)
    assert plan.steps[1].tools[0].when.equals == "route"
    assert plan.steps[2].render.format == "json_xml"
    assert plan.get_step(99) is None
    assert validate_plan(plan.to_dict(), skill, TOOLS, ["message"]) == plan


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["steps"].pop(),
        lambda p: p["steps"][0].update(kind="python"),
        lambda p: p["steps"][0].update(code="return True"),
        lambda p: p["steps"][0].update(source_quote="Classify message"),
        lambda p: p["steps"][0].update(residual_obligations=["Classify message"]),
        lambda p: p["steps"][0]["decision"]["criteria"].update(invented="Unsupported label"),
        lambda p: p["steps"][0]["decision"]["inputs"]["message"].update(key="missing"),
        lambda p: p["steps"][0]["decision"]["inputs"]["message"].update(default="fabricated"),
        lambda p: p["steps"][0]["decision"]["inputs"]["token"].update(key="classification"),
        lambda p: p["steps"][0]["extractors"][0].update(source_quote="not in source"),
        lambda p: p["steps"][0]["extractors"][0].update(pattern="("),
        lambda p: p["steps"][0]["extractors"][0].update(group=8),
        lambda p: p["steps"][1]["tools"][0].update(tool="lookup"),
        lambda p: p["steps"][1]["tools"][0].update(arguments={}),
        lambda p: p["steps"][1]["tools"][0]["when"].update(equals="invented"),
        lambda p: p["steps"][2]["render"]["fields"]["status"].update(step=3),
        lambda p: p["steps"][2]["render"]["fields"]["status"].update(path="status"),
        lambda p: p["steps"][2]["render"]["fields"]["status"].update(path="/~9"),
        lambda p: p["steps"][2]["render"].update(format="python"),
        lambda p: p["steps"][2]["render"]["fields"].update(extra={}),
    ],
)
def test_invalid_or_uncovered_plans_never_validate(mutation):
    skill, raw = fixture()
    mutation(raw)
    with pytest.raises(PlanValidationError):
        validate_plan(raw, skill, TOOLS, ["message"])


def test_added_obligation_invalidates_old_source_coverage():
    skill, raw = fixture()
    updated = parse_skill(
        SOURCE.replace("Classify message", "Email an explanation. Classify message")
    )
    with pytest.raises(PlanValidationError, match="entire original step"):
        validate_plan(raw, updated, TOOLS, ["message"])


def test_llm_fallback_requires_whole_source_and_no_payload():
    skill, raw = fixture()
    source = skill.steps[0].content
    raw["steps"][0] = {
        "index": 1,
        "kind": "llm",
        "source_quote": source,
        "residual_obligations": [source],
    }
    # Downstream references cannot pretend this LLM step produced accepted typed facts.
    with pytest.raises(PlanValidationError, match="Fact ref"):
        validate_plan(raw, skill, TOOLS, ["message"])


def test_receipt_binding_does_not_accept_arbitrary_llm_output_or_input():
    skill, raw = fixture()
    ref = raw["steps"][2]["render"]["fields"]["status"]
    ref.clear()
    ref.update(kind="input", key="message", source_quote=skill.steps[2].content)
    with pytest.raises(PlanValidationError, match="Rendered values"):
        validate_plan(raw, skill, TOOLS, ["message"])


def test_source_literals_preserve_false_and_zero_without_defaults():
    source = """---\nname: values\ndescription: synthetic\n---\n### Step 1: Call
Call configured with enabled false and count 0.
"""
    skill = parse_skill(source)
    text = skill.steps[0].content
    tool = {
        "type": "function",
        "function": {
            "name": "configured",
            "parameters": {
                "properties": {"enabled": {"type": "boolean"}, "count": {"type": "integer"}},
                "required": ["enabled", "count"],
            },
        },
    }
    plan = {
        "ir_version": "1",
        "steps": [
            {
                "index": 1,
                "kind": "tool",
                "source_quote": text,
                "residual_obligations": [],
                "tools": [
                    {
                        "tool": "configured",
                        "source_quote": text,
                        "arguments": {
                            k: {"kind": "literal", "value": v, "source_quote": text}
                            for k, v in {"enabled": False, "count": 0}.items()
                        },
                    }
                ],
            }
        ],
    }
    result = validate_plan(deepcopy(plan), skill, [tool], [])
    assert result.steps[0].tools[0].arguments["enabled"].value is False
    assert result.steps[0].tools[0].arguments["count"].value == 0


def test_partial_plan_preserves_independent_choice_and_drops_bad_tool_and_render():
    skill, raw = fixture()
    raw["steps"][1]["tools"][0]["when"]["equals"] = "invented"
    original = deepcopy(raw)
    plan, downgrades = validate_partial_plan(raw, skill, TOOLS, ["message"])
    assert [step.kind for step in plan.steps] == ["decision", "llm", "llm"]
    assert [entry["step_index"] for entry in downgrades] == [2, 3]
    assert "Literal" in downgrades[0]["reason"]
    assert "prior admitted tool plan" in downgrades[1]["reason"]
    assert raw == original
    for step in plan.steps[1:]:
        assert step.residual_obligations == (skill.get_step(step.index).content,)
        assert not step.tools and step.render is None
    assert validate_plan(plan.to_dict(), skill, TOOLS, ["message"]) == plan
    with pytest.raises(PlanValidationError):
        validate_plan(raw, skill, TOOLS, ["message"])


def test_partial_plan_rolls_back_extractor_facts_created_before_step_failure():
    skill, raw = fixture()
    # The extractor validates before the invalid decision. The next step depends
    # only on that extractor, so leaked tentative facts would incorrectly admit it.
    raw["steps"][0]["decision"]["criteria"]["invented"] = "Unsupported"
    del raw["steps"][1]["tools"][0]["when"]
    plan, downgrades = validate_partial_plan(raw, skill, TOOLS, ["message"])
    assert [step.kind for step in plan.steps] == ["llm", "llm", "llm"]
    assert [entry["step_index"] for entry in downgrades] == [1, 2, 3]
    assert "Fact ref" in downgrades[1]["reason"]


def test_partial_plan_never_registers_a_partly_valid_tool_step():
    skill, raw = fixture()
    raw["steps"][1]["tools"].append({"tool": "invented"})
    plan, downgrades = validate_partial_plan(raw, skill, TOOLS, ["message"])
    assert [step.kind for step in plan.steps] == ["decision", "llm", "llm"]
    assert "prior admitted tool plan" in downgrades[1]["reason"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw.update(extra="unsupported"),
        lambda raw: raw.update(ir_version="unknown"),
        lambda raw: raw.update(steps={}),
        lambda raw: raw["steps"].pop(),
        lambda raw: raw["steps"].append(deepcopy(raw["steps"][0])),
    ],
)
def test_partial_plan_does_not_recover_malformed_roots_or_step_counts(mutation):
    skill, raw = fixture()
    mutation(raw)
    with pytest.raises(PlanValidationError):
        validate_partial_plan(raw, skill, TOOLS, ["message"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda step: step.update(kind="python", code="return True"),
        lambda step: step.update(source_quote="replacement source"),
        lambda step: step.update(index=99),
        lambda step: step["decision"]["inputs"]["message"].update(default="fabricated"),
        lambda step: step.update(extractors="malformed"),
    ],
)
def test_partial_plan_replaces_invalid_step_without_repairing_its_operation(mutation):
    skill, raw = fixture()
    mutation(raw["steps"][0])
    plan, downgrades = validate_partial_plan(raw, skill, TOOLS, ["message"])
    assert downgrades[0]["step_index"] == 1
    first = plan.steps[0]
    assert first.kind == "llm" and first.source_quote == skill.steps[0].content
    assert first.residual_obligations == (skill.steps[0].content,)
    assert first.decision is None and not first.extractors and not first.tools


def test_partial_plan_recovers_malformed_step_value_and_render_format():
    skill, raw = fixture()
    raw["steps"][2]["render"]["format"] = []
    plan, downgrades = validate_partial_plan(raw, skill, TOOLS, ["message"])
    assert plan.steps[2].kind == "llm" and len(downgrades) == 1
    raw["steps"][0] = None
    plan, downgrades = validate_partial_plan(raw, skill, TOOLS, ["message"])
    assert all(step.kind == "llm" for step in plan.steps)
    assert [entry["step_index"] for entry in downgrades] == [1, 2, 3]
