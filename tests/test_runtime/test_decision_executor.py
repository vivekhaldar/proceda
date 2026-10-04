"""ABOUTME: Verifies decision fast paths preserve tool, approval, and failure boundaries.
ABOUTME: Tests runtime integration rather than live model accuracy.
"""

from unittest.mock import AsyncMock

import pytest

from proceda.config import DecisionConfig, LLMConfig, ProcedaConfig
from proceda.decision import GateResult
from proceda.events import CollectorEventSink, EventType
from proceda.exceptions import ConfigError
from proceda.human import AutoApproveHumanInterface
from proceda.internal.executor import Executor
from proceda.llm.runtime import LLMResponse, LLMRuntime
from proceda.session import RunMessage, RunSession, RunStatus, ToolCall
from proceda.skills.parser import parse_skill

SKILL = """---
name: receipt-test
description: A two step test
---
### Step 1: Read
Read the record with lookup.
### Step 2: Finish
Report the record's status.
"""
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    }
]


def response(name="complete_step", args=None, tokens=2):
    return LLMResponse(
        None,
        [ToolCall(ToolCall.generate_id(), name, args or {"summary": "done"})],
        prompt_tokens=tokens,
        total_tokens=tokens,
    )


def setup(responses, gate=True, error=False, skill_text=SKILL, verify=False):
    skill = parse_skill(skill_text)
    session = RunSession.create(skill.id, skill.name, {"record_id": "R1"})
    sink = CollectorEventSink()
    llm = AsyncMock()
    llm.complete.side_effect = responses
    llm.format_messages = LLMRuntime(LLMConfig()).format_messages
    tools = AsyncMock()

    async def execute(tc, emit):
        return {
            "tool_call_id": tc.id,
            "tool_name": tc.name,
            "content": '{"status":"active"}',
            "is_error": error,
        }

    tools.execute.side_effect = execute
    decisions = AsyncMock()
    decisions.check_completion.return_value = GateResult(gate, reason="fixture")
    decisions.check_proposal.return_value = GateResult(True)
    executor = Executor(
        skill,
        session,
        llm,
        tools,
        AutoApproveHumanInterface(),
        sink.handle,
        tool_schemas=TOOLS,
        decision_service=decisions,
        decision_config=DecisionConfig(enabled=True, verify_proposals=verify),
    )
    return executor, session, llm, tools, decisions, sink


async def test_fast_path_eliminates_only_nonfinal_continuation():
    ex, session, llm, tools, decisions, sink = setup(
        [
            response("lookup", {"id": "R1"}),
            response(),
        ]
    )
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    assert llm.complete.await_count == 2
    assert tools.execute.await_count == 1
    assert decisions.check_completion.await_count == 1
    assert any(e.type is EventType.STEP_AUTO_COMPLETED for e in sink.events)
    evidence = decisions.check_completion.call_args.kwargs["evidence"]
    assert evidence["receipts"][0]["result"] == {"status": "active"}
    assert evidence["inputs"] == {"record_id": "R1"}


@pytest.mark.parametrize("gate", [False, None])
async def test_unaccepted_completion_uses_original_loop_without_repeating_tool(gate):
    ex, session, llm, tools, decisions, _ = setup(
        [
            response("lookup", {"id": "R1"}),
            response(),
            response(),
        ],
        gate=gate,
    )
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    assert llm.complete.await_count == 3
    assert tools.execute.await_count == 1


async def test_failed_receipt_never_qualifies_for_fast_path():
    ex, session, llm, _, decisions, _ = setup(
        [
            response("lookup", {"id": "R1"}),
            response(),
            response(),
        ],
        error=True,
    )
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    decisions.check_completion.assert_not_awaited()
    assert llm.complete.await_count == 3


async def test_final_step_keeps_generative_completion():
    ex, session, llm, _, decisions, _ = setup(
        [
            response(),
            response("lookup", {"id": "R1"}),
            response(),
        ]
    )
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    decisions.check_completion.assert_not_awaited()
    assert llm.complete.await_count == 3


async def test_post_approval_still_runs_after_fast_completion():
    text = SKILL.replace("Read the record", "[APPROVAL REQUIRED]\nRead the record")
    ex, session, _, _, _, sink = setup(
        [
            response("lookup", {"id": "R1"}),
            response(),
        ],
        skill_text=text,
    )
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    types = [e.type for e in sink.events]
    assert types.index(EventType.STEP_AUTO_COMPLETED) < types.index(EventType.APPROVAL_REQUESTED)


async def test_rejected_proposal_cannot_execute_tools_and_usage_counts_repairs():
    ex, session, llm, tools, decisions, _ = setup(
        [
            response("lookup", {"id": "wrong"}, tokens=3),
            response("lookup", {"id": "R1"}, tokens=5),
            response(tokens=7),
        ],
        verify=True,
    )
    decisions.check_proposal.side_effect = [
        GateResult(False, probabilities={"grounded": 0.01}, reason="ungrounded"),
        GateResult(True),
        GateResult(True),
    ]
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    assert tools.execute.await_count == 1
    assert tools.execute.call_args.args[0].arguments == {"id": "R1"}
    assert session.total_llm_tokens == 15
    assert llm.complete.await_count == 3


async def test_schema_rejection_precedes_inference_and_effects():
    ex, session, _, tools, decisions, _ = setup(
        [
            response("unknown"),
            response("unknown"),
        ],
        verify=True,
    )
    await ex.execute()
    assert session.status is RunStatus.FAILED
    tools.execute.assert_not_awaited()
    decisions.check_proposal.assert_not_awaited()


async def test_uncertain_repair_does_not_override_confirmed_rejection():
    ex, session, _, tools, decisions, _ = setup(
        [
            response("lookup", {"id": "bad"}),
            response("lookup", {"id": "R1"}),
        ],
        verify=True,
    )
    decisions.check_proposal.side_effect = [GateResult(False), GateResult(None)]
    await ex.execute()
    assert session.status is RunStatus.FAILED
    tools.execute.assert_not_awaited()


def test_disabled_default_and_profile_configuration():
    assert not ProcedaConfig.from_dict({}).decisions.enabled
    config = ProcedaConfig.from_dict(
        {
            "llm": {"api_base": "https://api.example.test/v1", "timeout": 15},
            "decisions": {
                "enabled": True,
                "backends": [{"name": "local", "kind": "ollaya"}],
                "fallback_llm": {"model": "openai/strong"},
            },
        }
    )
    assert config.llm.timeout == 15
    assert config.decisions.fallback_llm.model == "openai/strong"
    assert config.decisions.backends[0].kind == "ollaya"
    with pytest.raises(ConfigError):
        ProcedaConfig.from_dict({"decisions": {"enabled": True}})
    with pytest.raises(ConfigError):
        ProcedaConfig.from_dict({"decisions": {"threshold": 0.4}})


@pytest.mark.parametrize(
    "value",
    [
        {"enabled": "false"},
        {"fast_completion": "true"},
        {"verify_proposals": 1},
        {"threshold": True},
        {"max_repairs": False},
    ],
)
def test_decision_config_rejects_ambiguous_types(value):
    with pytest.raises(ConfigError):
        ProcedaConfig.from_dict({"decisions": value})


async def test_preserved_receipts_include_prior_observations_without_model_summaries():
    ex, session, _, _, decisions, _ = setup([response("lookup", {"id": "R1"}), response()])
    prior = ToolCall("prior", "lookup", {"id": "dependency"})
    session.add_message(RunMessage.create("assistant", "", tool_calls=[prior]))
    session.add_message(
        RunMessage.create(
            "tool", '{"dependency_status":"verified"}', tool_call_id="prior", app_name="lookup"
        )
    )
    session.add_message(RunMessage.create("assistant", "Unsubstantiated status: denied"))
    await ex.execute()
    receipts = decisions.check_completion.call_args.kwargs["evidence"]["receipts"]
    assert len(receipts) == 2
    assert receipts[0]["arguments"] == {"id": "dependency"}
    assert receipts[0]["result"] == {"dependency_status": "verified"}
    assert receipts[0]["current_step"] is False
    assert receipts[1]["current_step"] is True
    assert "Unsubstantiated" not in str(receipts)


async def test_multi_tool_batch_is_fully_observed_before_gate():
    batch = LLMResponse(
        None,
        [ToolCall("first", "lookup", {"id": "R1"}), ToolCall("second", "lookup", {"id": "R2"})],
    )
    ex, session, _, tools, decisions, _ = setup([batch, response()])
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    assert tools.execute.await_count == 2
    decisions.check_completion.assert_awaited_once()
    receipts = decisions.check_completion.call_args.kwargs["evidence"]["receipts"]
    assert [r["tool_call_id"] for r in receipts] == ["first", "second"]


async def test_initial_uncertain_verifier_preserves_original_proposal():
    ex, session, llm, tools, decisions, _ = setup(
        [response("lookup", {"id": "R1"}), response()], verify=True
    )
    decisions.check_proposal.return_value = GateResult(None)
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    assert llm.complete.await_count == 2
    assert tools.execute.await_count == 1
