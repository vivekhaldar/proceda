"""ABOUTME: Checks business-only routing, full policy parity, and Boolean fact typing.
ABOUTME: All inference is mocked and all policies use synthetic source instructions.
"""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from proceda.decision import ChoiceResult, GateResult
from proceda.events import EventType
from proceda.planning.models import ChoiceSpec, StepPlan, ValueRef
from proceda.planning.runner import PlanBindingError, PlanRunner, prepare_choice
from tests.test_planning.test_runner import (
    KEY,
    SCHEMAS,
    decision,
    generated,
    inp,
    plan,
    propose,
    raw_choice,
    receipt,
    receipt_ref,
    render_step,
    session,
    tool_step,
)


def predicate():
    source = "Determine whether the request count is at least 2."
    return StepPlan(
        1,
        "decision",
        source,
        decision=ChoiceSpec(
            "eligible",
            source,
            {"true": "request count is at least 2"},
            {"count": inp("count")},
            source,
            kind="predicate",
        ),
    )


@pytest.mark.parametrize("kind", ["tool", "render", "llm"])
async def test_business_mode_leaves_tool_render_and_other_steps_to_original_executor(kind):
    definition = {
        "tool": tool_step(),
        "render": render_step(1),
        "llm": StepPlan(1, "llm", "Explain the result."),
    }[kind]
    runner = PlanRunner(plan(definition), KEY, SCHEMAS, mode="business_decisions")
    state = session(record_id="R123")
    result, llm, events, usage = await propose(runner, state)
    assert result is None and not events and not usage and not state.planned_step_attempts
    llm.complete.assert_not_called()
    assert runner.completion_allowed(1) is True
    assert runner.completion_allowed(999) is True  # Original executor owns eligibility.


async def test_same_full_source_policy_and_evidence_reach_native_and_constrained_choices():
    definition = decision()
    source = "Count matching indicators. Choose approve if count >= 2; otherwise review."
    definition = replace(definition, source_quote=source)
    native = AsyncMock()
    native.choose.return_value = ChoiceResult(
        "approve", probabilities={"approve": 0.9, "review": 0.1}
    )
    for enabled in (True, False):
        runner = PlanRunner(
            plan(definition), KEY, SCHEMAS, mode="business_decisions", native_decisions=enabled
        )
        result, llm, _, usage = await propose(
            runner, session(message="untrusted case"), decisions=native
        )
        assert result is not None
        if enabled:
            assert native.choose.call_args.kwargs["instructions"] == source
            assert native.choose.call_args.kwargs["state"] == {"message": "untrusted case"}
            llm.complete.assert_not_called()
            assert not usage
        else:
            assert native.choose.await_count == 1  # Only the earlier enabled runner called it.
            messages = llm.complete.call_args.args[0]
            policy = json.loads(messages[0]["content"].split("\n", 1)[1])
            assert policy == {"instructions": source, "criteria": definition.decision.criteria}
            assert json.loads(messages[1]["content"]) == {"message": "untrusted case"}
            assert len(usage) == 1


@pytest.mark.parametrize("native_value", [True, False])
async def test_native_predicate_retains_boolean_value_and_separate_audit_purpose(native_value):
    definition = predicate()
    native = AsyncMock()
    native.evaluate.return_value = GateResult(
        native_value,
        [{"call_id": "p1", "audit_path": "predicate.json"}],
        {"eligible": 0.9 if native_value else 0.1},
        "settled",
    )
    state = session(count=3 if native_value else 0)
    runner = PlanRunner(plan(definition), KEY, SCHEMAS, mode="business_decisions")
    result, llm, events, usage = await propose(runner, state, decisions=native)
    assert result is not None and not usage
    llm.complete.assert_not_called()
    native.choose.assert_not_called()
    kwargs = native.evaluate.call_args.kwargs
    assert kwargs["selected_outcome_threshold"] is True
    assert definition.source_quote in kwargs["questions"]["eligible"]["instructions"]
    assert state.decision_facts[0]["value"] is native_value
    assert state.decision_facts[0]["origin"] == "native_predicate"
    assert state.decision_facts[0]["probabilities"][str(native_value).lower()] == pytest.approx(0.9)
    assert (
        next(e for e in events if e.type is EventType.DECISION_EVALUATED).payload["purpose"]
        == "planned_predicate"
    )


@pytest.mark.parametrize("native_value", [None, 0, "false"])
async def test_unsettled_or_wrongly_typed_native_predicate_uses_strict_boolean_fallback(
    native_value,
):
    definition = predicate()
    native = AsyncMock()
    native.evaluate.return_value = GateResult(native_value)
    state = session(count=0)
    returned = generated(False, raw_response=raw_choice('{"value":false}'))
    result, llm, _, usage = await propose(
        PlanRunner(plan(definition), KEY, SCHEMAS),
        state,
        decisions=native,
        llm_response=returned,
    )
    assert result is not None and usage == [(returned, 1)]
    assert state.decision_facts[0]["value"] is False
    assert state.decision_facts[0]["origin"] == "llm_predicate"
    tool = llm.complete.call_args.kwargs["tools"][0]["function"]
    assert tool["parameters"]["properties"]["value"] == {"type": "boolean"}
    policy = json.loads(llm.complete.call_args.args[0][0]["content"].split("\n", 1)[1])
    assert policy["instructions"] == definition.source_quote
    assert (
        policy["criteria"] == native.evaluate.call_args.kwargs["questions"]["eligible"]["criteria"]
    )


@pytest.mark.parametrize("value", [0, 1, "false", None])
async def test_generated_predicate_never_coerces_values_to_boolean(value):
    state = session(count=0)
    result, _, events, usage = await propose(
        PlanRunner(plan(predicate()), KEY, SCHEMAS), state, llm_response=generated(value)
    )
    assert result is None and not state.decision_facts and len(usage) == 1
    assert events[-1].type is EventType.PLAN_FALLBACK


@pytest.mark.parametrize("tampered", [False, True])
async def test_prior_predicate_fact_preserves_false_and_rejects_integer_substitution(tampered):
    first = predicate()
    second = decision(2, inputs={"prior": ValueRef("fact", step=1, key="eligible")})
    compiled = plan(first, second)
    state = session(count=0)
    await propose(PlanRunner(compiled, KEY, SCHEMAS), state, llm_response=generated(False))
    with pytest.raises(PlanBindingError, match="fact_step_incomplete"):
        prepare_choice(second, state.variables, facts=state.decision_facts, plan=compiled)
    state.completed_steps = [1]
    if tampered:
        state.decision_facts[0]["value"] = 0
        with pytest.raises(PlanBindingError, match="invalid_decision_fact"):
            prepare_choice(
                second,
                state.variables,
                facts=state.decision_facts,
                completed_steps=state.completed_steps,
                plan=compiled,
            )
    else:
        prepared = prepare_choice(
            second,
            state.variables,
            facts=state.decision_facts,
            completed_steps=state.completed_steps,
            plan=compiled,
        )
        assert prepared.state["prior"] is False


async def test_nonexecuting_observation_contract_still_validates_receipt_argument_identity():
    first = tool_step(residual=("Look up the record.",))
    second = decision(2, inputs={"status": receipt_ref("/status")})
    runner = PlanRunner(plan(first, second), KEY, SCHEMAS, mode="business_decisions")
    state = session(record_id="R123")
    assert (await propose(runner, state))[0] is None
    state.completed_steps = [1]
    state.executed_tool_receipts = [receipt(arguments={"record_id": "R999"})]
    result, llm, events, _ = await propose(runner, state, step=2)
    assert result is None and not state.decision_facts
    llm.complete.assert_not_called()
    assert events[-1].payload["reason"] == "receipt_arguments_do_not_match_source"
