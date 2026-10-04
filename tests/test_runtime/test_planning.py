"""ABOUTME: Tests planned execution through normal dispatch, approval and accounting.
ABOUTME: Ensures cached compilation never receives case values and remains optional.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from proceda.config import LLMConfig, ProcedaConfig
from proceda.decision import GateResult
from proceda.events import CollectorEventSink, EventType
from proceda.exceptions import ConfigError
from proceda.llm.runtime import LLMResponse
from proceda.planning.models import ProcedurePlan, StepPlan, ToolPlan, ValueRef
from proceda.planning.runner import PlanRunner
from proceda.runtime import Runtime
from proceda.session import RunSession, RunStatus, ToolCall
from proceda.skills.parser import parse_skill
from tests.test_runtime.test_decision_executor import SKILL, TOOLS, response, setup


def plan_for(executor, *, residual=()):
    return PlanRunner(
        ProcedurePlan(
            "1",
            (
                StepPlan(
                    1,
                    "tool",
                    executor._skill.get_step(1).content,
                    residual_obligations=residual,
                    tools=(
                        ToolPlan("lookup", {"id": ValueRef("input", key="record_id")}, "lookup"),
                    ),
                ),
                StepPlan(2, "llm", executor._skill.get_step(2).content),
            ),
        ),
        "test-plan",
        TOOLS,
    )


async def test_planned_prefix_uses_dispatch_receipts_and_skips_llm_proposal():
    ex, session, llm, tools, decisions, sink = setup([response()])
    ex._plan_runner = plan_for(ex)
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    assert tools.execute.await_count == 1
    assert llm.complete.await_count == 1
    assert decisions.check_completion.await_count == 1
    assert session.executed_tool_receipts[0].arguments == {"id": "R1"}
    assert session.planned_step_attempts == [1, 2]
    assert any(e.type is EventType.PLAN_ACTION for e in sink.events)


async def test_residual_work_forces_continuation_without_completion_overhead():
    ex, session, llm, tools, decisions, _ = setup([response(), response()])
    ex._plan_runner = plan_for(ex, residual=("Explain the result",))
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    assert llm.complete.await_count == 2
    assert tools.execute.await_count == 1
    decisions.check_completion.assert_not_awaited()


async def test_planned_proposal_obeys_optional_verification_before_dispatch():
    ex, session, llm, tools, decisions, _ = setup([response(), response()], verify=True)
    ex._plan_runner = plan_for(ex)
    decisions.check_proposal.side_effect = [GateResult(False), GateResult(True), GateResult(True)]
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    tools.execute.assert_not_awaited()
    assert llm.complete.await_count == 2  # Repair and second-step generation.
    assert decisions.check_proposal.await_count == 3


async def test_repaired_planned_completion_cannot_publish_stale_facts():
    ex, session, _, _, decisions, _ = setup([response(), response()], verify=True)
    planned = AsyncMock()

    async def propose(step_index, current, **kwargs):
        if step_index != 1 or current.planned_step_attempts:
            return None
        current.planned_step_attempts.append(1)
        current.decision_facts.append({"step_index": 1, "field": "result", "value": "old"})
        return response(tokens=0)

    planned.propose.side_effect = propose
    ex._plan_runner = planned
    decisions.check_proposal.side_effect = [GateResult(False), GateResult(True), GateResult(True)]
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    assert session.decision_facts == []


@pytest.mark.parametrize("cache_hit,tokens", [(False, 17), (True, 0)])
async def test_compiler_gets_field_names_only_and_usage_charged_once(tmp_path, cache_hit, tokens):
    skill = parse_skill(SKILL)
    session = RunSession.create(skill.id, skill.name, {"record_id": "SECRET_CASE_VALUE"})
    config = ProcedaConfig.from_dict({"planning": {"enabled": True, "cache_dir": str(tmp_path)}})
    config.llm = LLMConfig(model="test-model", max_tokens=2048)
    sink = CollectorEventSink()
    result = SimpleNamespace(
        plan=None,
        cache_key="key",
        artifact_path=tmp_path / "plan.json",
        artifact_sha256="digest",
        attempt_path=None,
        cache_hit=cache_hit,
        status="unsupported",
        errors=(),
        prompt_tokens=tokens,
        completion_tokens=tokens,
    )
    with patch("proceda.runtime.get_or_compile", new=AsyncMock(return_value=result)) as compile_:
        runner = await Runtime(config)._prepare_plan(
            skill, session, TOOLS, str(tmp_path), sink.handle
        )
    assert runner is None
    assert compile_.call_args.args[2] == ["record_id"]
    assert "SECRET_CASE_VALUE" not in repr(compile_.call_args)
    assert compile_.call_args.kwargs["compiler_config"]["max_tokens"] == 8192
    assert "audit_dir" not in compile_.call_args.kwargs["compiler_config"]
    assert session.total_prompt_tokens == tokens
    assert session.total_llm_tokens == 2 * tokens
    usage = [e for e in sink.events if e.type is EventType.LLM_USAGE]
    assert len(usage) == (0 if cache_hit else 1)


async def test_compiler_failure_preserves_original_runtime(tmp_path):
    skill = parse_skill(SKILL)
    session = RunSession.create(skill.id, skill.name)
    config = ProcedaConfig.from_dict({"planning": {"enabled": True}})
    sink = CollectorEventSink()
    with patch("proceda.runtime.get_or_compile", new=AsyncMock(side_effect=OSError("unavailable"))):
        result = await Runtime(config)._prepare_plan(skill, session, [], str(tmp_path), sink.handle)
    assert result is None
    assert sink.events[-1].payload["status"] == "fallback"
    assert sink.events[-1].payload["error_type"] == "OSError"
    assert sink.events[-1].payload["preparation_elapsed_seconds"] >= 0


@pytest.mark.parametrize(
    "parameters", [{"$ref": "https://example.invalid/schema"}, {"type": "unknown-type"}]
)
async def test_invalid_tool_contract_falls_back_after_charging_compilation(tmp_path, parameters):
    skill = parse_skill(SKILL)
    session = RunSession.create(skill.id, skill.name)
    sink = CollectorEventSink()
    result = SimpleNamespace(
        plan=ProcedurePlan("1", (StepPlan(1, "llm", skill.get_step(1).content),)),
        cache_key="key",
        artifact_path=tmp_path / "plan.json",
        artifact_sha256="digest",
        attempt_path=tmp_path / "attempt.json",
        cache_hit=False,
        status="accepted",
        errors=(),
        prompt_tokens=17,
        completion_tokens=3,
    )
    schemas = [{"function": {"name": "app__lookup", "parameters": parameters}}]
    with patch("proceda.runtime.get_or_compile", new=AsyncMock(return_value=result)):
        runner = await Runtime()._prepare_plan(skill, session, schemas, str(tmp_path), sink.handle)
    assert runner is None
    assert session.total_llm_tokens == 20
    assert sink.events[-1].type is EventType.PLAN_FALLBACK
    assert sink.events[-1].payload["reason"] == "invalid_tool_contract"


def test_planning_config_defaults_and_compiler_override():
    assert ProcedaConfig().planning.enabled is False
    config = ProcedaConfig.from_dict(
        {"planning": {"enabled": True, "compiler_llm": {"model": "compiler", "max_tokens": 9000}}}
    )
    assert config.planning.compiler_llm.model == "compiler"
    assert config.planning.compiler_llm.max_tokens == 9000


@pytest.mark.parametrize(
    "planning",
    [False, [], {"enabled": "yes"}, {"cache_dir": ""}, {"cache_epoch": 1}, {"compiler_llm": []}],
)
def test_planning_config_rejects_invalid_values(planning):
    with pytest.raises(ConfigError):
        ProcedaConfig.from_dict({"planning": planning})


async def test_runtime_cold_then_warm_compiled_choice_and_render(tmp_path):
    skill = parse_skill("""---
name: request-routing
description: Route a request.
output_fields:
  - status
---
### Step 1: Classify
Read request and choose status approve or review.
### Step 2: Report
Report status as JSON.
""")
    first_source, last_source = (skill.get_step(i).content for i in (1, 2))
    candidate = {
        "ir_version": "1",
        "steps": [
            {
                "index": 1,
                "kind": "decision",
                "source_quote": first_source,
                "residual_obligations": [],
                "decision": {
                    "field": "status",
                    "instructions": first_source,
                    "criteria": {"approve": "Approve request", "review": "Review request"},
                    "inputs": {
                        "request": {"kind": "input", "key": "request", "source_quote": first_source}
                    },
                    "source_quote": first_source,
                },
            },
            {
                "index": 2,
                "kind": "render",
                "source_quote": last_source,
                "residual_obligations": [],
                "render": {
                    "format": "json",
                    "fields": {
                        "status": {
                            "kind": "fact",
                            "step": 1,
                            "key": "status",
                            "source_quote": last_source,
                        }
                    },
                    "source_quote": last_source,
                },
            },
        ],
    }
    config = ProcedaConfig.from_dict(
        {
            "planning": {"enabled": True, "cache_dir": str(tmp_path / "plans")},
            "logging": {"run_dir": str(tmp_path / "runs")},
        }
    )
    generated = [
        LLMResponse(json.dumps(candidate), [], prompt_tokens=7, completion_tokens=3),
        LLMResponse('{"approved":true,"issues":[]}', [], prompt_tokens=5, completion_tokens=1),
        LLMResponse(
            None,
            [ToolCall("a", "record_decision", {"value": "approve"})],
            prompt_tokens=2,
            completion_tokens=1,
            total_tokens=3,
        ),
        LLMResponse(
            None,
            [ToolCall("b", "record_decision", {"value": "review"})],
            prompt_tokens=2,
            completion_tokens=1,
            total_tokens=3,
        ),
    ]
    runtime = Runtime(config)
    with patch(
        "proceda.llm.runtime.LLMRuntime.complete", new=AsyncMock(side_effect=generated)
    ) as llm:
        cold = await runtime.run(skill, variables={"request": "FIRST_CASE_VALUE"})
        assert runtime.last_session.decision_facts[0]["value"] == "approve"
        warm = await runtime.run(skill, variables={"request": "SECOND_CASE_VALUE"})
        assert runtime.last_session.decision_facts[0]["value"] == "review"
    assert cold.status is warm.status is RunStatus.COMPLETED
    assert cold.total_tokens == 19
    assert warm.total_tokens == 3
    assert llm.await_count == 4
    assert "FIRST_CASE_VALUE" not in repr(llm.await_args_list[:2])
    assert "SECOND_CASE_VALUE" not in repr(llm.await_args_list[:2])
