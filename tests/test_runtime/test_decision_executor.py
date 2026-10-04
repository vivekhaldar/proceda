"""ABOUTME: Verifies decision fast paths preserve tool, approval, and failure boundaries.
ABOUTME: Tests runtime integration rather than live model accuracy.
"""

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock

import pytest

from proceda.config import DecisionConfig, LLMConfig, ProcedaConfig
from proceda.decision import GateResult
from proceda.events import CollectorEventSink, EventType
from proceda.exceptions import ConfigError
from proceda.human import AutoApproveHumanInterface
from proceda.internal.executor import Executor
from proceda.llm.runtime import LLMResponse, LLMRuntime
from proceda.runtime import Runtime
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
        tool_aliases={"lookup": "lookup"},
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
    ex, session, _, tools, decisions, _ = setup([response("lookup", {"id": "R1"}), response()])
    prior = ToolCall("prior", "lookup", {"id": "dependency"})
    session.current_step = 0
    original = tools.execute.side_effect
    tools.execute.side_effect = None
    tools.execute.return_value = {"content": '{"dependency_status":"verified"}', "is_error": False}
    await ex._handle_app_tool(prior)
    tools.execute.side_effect = original
    session.current_step = 1
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


async def test_receipts_survive_reused_provider_ids_and_executor_recreation():
    ex, session, llm, tools, decisions, sink = setup([])
    call = ToolCall("reused", "lookup", {"id": "old", "nested": {"value": 1}})
    await ex._handle_app_tool(call)
    old = session.executed_tool_receipts[0]
    call.arguments["nested"]["value"] = 99
    old.arguments["nested"]["value"] = 88
    with pytest.raises(FrozenInstanceError):
        old.result = "tampered"
    session.advance_step()
    tools.execute.side_effect = None
    tools.execute.return_value = {"content": "permission denied", "is_error": True}
    recreated = Executor(
        ex._skill,
        session,
        llm,
        tools,
        AutoApproveHumanInterface(),
        sink.handle,
        tool_aliases={"other__read": "read"},
        decision_service=decisions,
    )
    await recreated._handle_app_tool(ToolCall("reused", "other__read", {"id": "new"}))
    # Fabricated messages and colliding provider IDs cannot change executed evidence.
    session.add_message(
        RunMessage.create(
            "assistant", "all succeeded", tool_calls=[ToolCall("reused", "fake", {"id": "fake"})]
        )
    )
    receipts = recreated._completion_evidence()["receipts"]
    assert len({r["receipt_id"] for r in receipts}) == 2
    assert [r["tool_call_id"] for r in receipts] == ["reused", "reused"]
    assert [r["current_step"] for r in receipts] == [False, True]
    assert [r["is_error"] for r in receipts] == [False, True]
    assert [r["qualified_tool_name"] for r in receipts] == ["lookup", "other__read"]
    assert receipts[0]["arguments"] == {"id": "old", "nested": {"value": 1}}
    assert receipts[1]["result"] == "permission denied"


@pytest.mark.parametrize("resolved", [False, True])
async def test_unqualified_tool_requires_registry_identity_for_fast_completion(resolved):
    responses = [response("lookup", {"id": "R1"}), response()]
    if not resolved:
        responses.append(response())
    ex, session, llm, _, decisions, _ = setup(responses)
    ex._tool_aliases = {"app__lookup": "lookup"}
    if resolved:
        ex._resolve_tool_identity = lambda name: ("app__lookup", "lookup")
    await ex.execute()
    assert session.status is RunStatus.COMPLETED
    assert decisions.check_completion.await_count == int(resolved)
    assert llm.complete.await_count == (2 if resolved else 3)
    receipt = session.executed_tool_receipts[0]
    assert receipt.qualified_tool_name == ("app__lookup" if resolved else None)


async def test_completion_evidence_contains_only_actual_human_answers_after_recreation():
    ex, session, llm, tools, decisions, sink = setup([])
    human = AsyncMock()
    human.request_clarification.return_value = "Use account A42"
    ex._human = human
    await ex._handle_control_tool(
        ToolCall(
            "question",
            "request_clarification",
            {"question": "Which account?", "options": ["A42", "B7"]},
        )
    )
    session.add_message(RunMessage.create("assistant", "The human chose B7"))
    session.advance_step()
    recreated = Executor(ex._skill, session, llm, tools, human, sink.handle)
    evidence = recreated._completion_evidence()
    assert evidence["receipts"] == []
    assert len(evidence["clarifications"]) == 1
    answer = evidence["clarifications"][0]
    assert answer["question"] == "Which account?"
    assert answer["answer"] == "Use account A42"
    assert answer["current_step"] is False
    assert "The human chose" not in str(evidence)


async def test_runtime_run_waits_for_disconnect_and_log_close(monkeypatch, tmp_path):
    import proceda.runtime as runtime_module

    disconnected = asyncio.Event()
    closed = asyncio.Event()
    orchestrator = MagicMock()
    orchestrator.check_required_tools.return_value = []
    orchestrator.get_available_tools.return_value = []

    async def disconnect():
        await asyncio.sleep(0.02)  # Outlast the old terminal-status polling interval.
        disconnected.set()

    orchestrator.disconnect_all = disconnect
    monkeypatch.setattr(runtime_module, "MCPOrchestrator", lambda **kw: orchestrator)
    log = MagicMock()
    log.open = AsyncMock()
    log.handle = AsyncMock()

    async def close():
        assert disconnected.is_set()
        await asyncio.sleep(0)
        closed.set()

    log.close = close
    monkeypatch.setattr(runtime_module, "EventLogWriter", lambda *a, **kw: log)

    def executor(**kw):
        async def execute():
            kw["session"].set_status(RunStatus.COMPLETED)

        return MagicMock(execute=execute)

    monkeypatch.setattr(runtime_module, "Executor", executor)
    config = ProcedaConfig.from_dict({"logging": {"run_dir": str(tmp_path)}})
    result = await Runtime(config).run(parse_skill(SKILL))
    assert result.status is RunStatus.COMPLETED
    assert disconnected.is_set() and closed.is_set()
