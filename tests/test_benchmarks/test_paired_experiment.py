"""ABOUTME: Tests strict grading, label isolation and immutable paired run artifacts.
ABOUTME: Uses fake agents only; no benchmark test is allowed to call a model API.
"""

from __future__ import annotations

import asyncio
import copy
import csv
import dataclasses
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.sop_bench import paired_experiment as paired
from proceda.config import LLMConfig, ProcedaConfig
from proceda.events import EventType, RunEvent
from proceda.session import RunStatus
from proceda.skills.loader import load_skill


def event(kind, **payload):
    return RunEvent.create("test-run", kind, payload)


@pytest.mark.parametrize(
    "prediction,expected,correct",
    [
        ("invalid", "valid", False),
        ("not_failed", "failed", False),
        ("Account Closure", "account_closure", True),
        (True, "True", True),
        ("no", "yes", False),
        (None, "None", False),
        ("wrong answer", "answer", False),
    ],
)
def test_strict_normalization(prediction, expected, correct):
    assert paired.strict_score({"x": prediction}, {"x": expected}) == {"x": correct}


def test_missing_output_fails():
    assert paired.strict_score({}, {"x": "valid"}) == {"x": False}


def test_no_llm_extraction_or_tool_output_shortcut(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No model extraction allowed")

    monkeypatch.setattr("benchmarks.sop_bench.output_extractor._extract_via_llm", forbidden)
    events = [
        event(EventType.TOOL_COMPLETED, result='{"x": "correct"}'),
        event(EventType.MESSAGE_ASSISTANT, content="The answer is correct."),
    ]
    assert paired.extract_deterministic(events, ["x"]) == {}


def test_latest_structured_answer_wins_without_merging_stale_fields():
    events = [
        event(EventType.MESSAGE_ASSISTANT, content="<x>old</x><y>old</y>"),
        event(EventType.SUMMARY_GENERATED, summary="<x>new</x>"),
    ]
    assert paired.extract_deterministic(events, ["x", "y"]) == {"x": "new"}


def test_grouped_split_reproducible_and_label_blind():
    rows = [{"email_id": f"E{i}", "product_id": f"P{i // 2}", "answer": "one"} for i in range(40)]
    rows[0]["email_id"] = "E1117"
    first = paired.split_rows(rows, "email_intent")
    altered = [{**r, "answer": "different"} for r in reversed(rows)]
    assert paired.split_rows(altered, "email_intent") == first
    assert "E1117" in first["dev"] and "E1" in first["dev"]
    assert not set(first["dev"]) & set(first["holdout"])
    assert len(first["dev"]) + len(first["holdout"]) == len(rows)
    for i in range(2, 40, 2):
        assert (f"E{i}" in first["dev"]) == (f"E{i + 1}" in first["dev"])


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    data = tmp_path / "upstream/src/amazon_sop_bench/benchmarks/data"
    exp = tmp_path / "experiment"
    for domain in paired.DOMAINS:
        d = data / domain
        d.mkdir(parents=True)
        key = paired.ID_COLUMNS[domain]
        columns = [key, "product_id", "result"] if key != "product_id" else [key, "result"]
        rows = [
            {key: f"case{i}", "product_id": f"P{i}", "result": "label-only-secret"}
            for i in range(5)
        ]
        for csv_name in ("test_set_with_outputs.csv", "test_set_without_outputs.csv"):
            with (d / csv_name).open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
        (d / "metadata.json").write_text(
            json.dumps({"input_columns": [key], "output_columns": ["result"]})
        )
        (d / "sop.txt").write_text("Read the task and return its outcome.")
        (d / "tools.py").write_text("# Synthetic test source, never executed.\n")
        (d / "toolspecs.json").write_text("[]")
        skill = exp / "skills" / domain
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: test\ndescription: test\n---\n\n### Step 1: Report\nReport.\n"
        )
    monkeypatch.setattr(paired, "source_revision", lambda path: paired.SOURCE_COMMIT)
    monkeypatch.setattr(paired, "source_code_identity", lambda: {"commit": "fixed-test-code"})
    paired.freeze_sources(data, exp)
    return data, exp


def handle_for_result(result):
    async def events():
        yield event(EventType.RUN_COMPLETED)

    return SimpleNamespace(events=events, to_result=lambda: result)


@pytest.fixture
def fake_agent(monkeypatch):
    calls = []

    def from_path(path, config):
        calls.append({"path": str(path), "config": copy.deepcopy(config)})

        async def create_session(*, variables, event_sinks):
            calls[-1]["variables"] = variables
            assert "result" not in variables
            for sink in event_sinks:
                await sink.handle(
                    event(EventType.MESSAGE_ASSISTANT, content="<result>done</result>")
                )
                await sink.handle(
                    event(EventType.TOOL_CALLED, tool_name="read", arguments=variables)
                )
            return handle_for_result(
                SimpleNamespace(
                    status=RunStatus.COMPLETED,
                    session_id="fake-session",
                    prompt_tokens=3,
                    completion_tokens=2,
                    total_tokens=5,
                )
            )

        return SimpleNamespace(create_session=create_session)

    monkeypatch.setattr(paired.Agent, "from_path", from_path)
    return calls


def options(experiment):
    data, exp = experiment
    return dict(
        config=ProcedaConfig(),
        arm="baseline",
        split="dev",
        domains=["patient_intake"],
        run_id="test",
        data_dir=data,
        experiment_dir=exp,
    )


def test_freeze_rejects_changed_sop(experiment):
    data, exp = experiment
    (data / "patient_intake/sop.txt").write_text("Modified")
    with pytest.raises(ValueError, match="Frozen source"):
        paired.verify_sources(data, exp)


def test_source_pin_is_enforced(experiment, monkeypatch):
    data, exp = experiment
    monkeypatch.setattr(paired, "source_revision", lambda path: "not-the-pin")
    with pytest.raises(ValueError, match="pinned"):
        paired.verify_sources(data, exp)


def test_actual_agent_interface_label_isolation_and_artifacts(experiment, fake_agent):
    result = paired.evaluate(**options(experiment))
    assert result["completed"] == 1 and result["correct"] == 0
    assert len(fake_agent) == 1
    case = next(Path(result["run_dir"]).glob("patient_intake/*"))
    assert json.loads((case / "expected.json").read_text())["result"] == "label-only-secret"
    for name in ("inputs.json", "events.jsonl", "messages.jsonl", "tools.jsonl"):
        assert "label-only-secret" not in (case / name).read_text()
    assert (case / "terminal.json").exists()
    assert fake_agent[0]["config"].llm.audit_dir == str(case / "providers")
    assert fake_agent[0]["config"].logging.run_dir == str(case / "runtime")


def test_resume_is_immutable_and_does_not_rerun_terminal(experiment, fake_agent):
    args = options(experiment)
    first = paired.evaluate(**args)
    with pytest.raises(ValueError, match="already exists"):
        paired.evaluate(**args)
    second = paired.evaluate(**args, resume=True)
    assert first == second and len(fake_agent) == 1
    args["config"].llm.temperature = 0.1
    with pytest.raises(ValueError, match="Resume refused"):
        paired.evaluate(**args, resume=True)


def test_resume_restores_missing_aggregate_entry(experiment, fake_agent):
    args = options(experiment)
    first = paired.evaluate(**args)
    predictions = Path(first["run_dir"]) / "predictions.jsonl"
    predictions.unlink()  # Simulate interruption after terminal record before aggregate append.
    paired.evaluate(**args, resume=True)
    assert len(predictions.read_text().splitlines()) == 1
    assert len(fake_agent) == 1


def test_partial_case_is_preserved_and_not_retried(experiment, fake_agent):
    args = options(experiment)
    first = paired.evaluate(**args)
    terminal = next(Path(first["run_dir"]).glob("patient_intake/*/terminal.json"))
    terminal.unlink()
    with pytest.raises(ValueError, match="Partial case retained"):
        paired.evaluate(**args, resume=True)
    assert len(fake_agent) == 1


def test_noncompleted_result_cannot_score_success(experiment, monkeypatch):
    async def create_session(**kwargs):
        return handle_for_result(
            SimpleNamespace(status=RunStatus.CANCELLED, session_id="cancelled")
        )

    monkeypatch.setattr(
        paired.Agent,
        "from_path",
        lambda *a, **k: SimpleNamespace(create_session=create_session),
    )
    result = paired.evaluate(**options(experiment))
    assert result["completed"] == 0 and result["correct"] == 0
    terminal = json.loads(
        next(Path(result["run_dir"]).glob("patient_intake/*/terminal.json")).read_text()
    )
    assert terminal["status"] == "cancelled"


def test_exception_is_terminal_and_secret_redacted(experiment, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("secret-value failed")

    monkeypatch.setattr(paired.Agent, "from_path", fail)
    result = paired.evaluate(**options(experiment), secrets=("secret-value",))
    terminal = next(Path(result["run_dir"]).glob("patient_intake/*/terminal.json")).read_text()
    assert "secret-value" not in terminal and "[REDACTED]" in terminal
    assert result["completed"] == 0


def test_scoped_config_preserves_original(experiment, tmp_path):
    data, _ = experiment
    config = ProcedaConfig()
    config.decisions.fallback_llm = LLMConfig(model="fallback")
    before = dataclasses.asdict(config)
    scoped = paired.scoped_config(config, tmp_path / "case", "email_intent", data)
    assert dataclasses.asdict(config) == before
    assert scoped.decisions.fallback_llm.audit_dir == str(tmp_path / "case/providers")
    assert scoped.apps[0].env["PYTHONPATH"] == str(data.parents[2])


def test_credential_helper_restores_environment(monkeypatch):
    monkeypatch.setenv("EXPERIMENT_TEST_KEY", "before")
    with (
        pytest.raises(RuntimeError),
        paired.credential_environment({"EXPERIMENT_TEST_KEY": "private"}),
    ):
        assert os.environ["EXPERIMENT_TEST_KEY"] == "private"
        raise RuntimeError("test")
    assert os.environ["EXPERIMENT_TEST_KEY"] == "before"


def test_shared_skills_parse_and_have_matching_output_fields():
    for domain in paired.DOMAINS:
        skill = load_skill(paired.DEFAULT_EXPERIMENT / "skills" / domain / "SKILL.md")
        _, metadata = paired.load_domain(paired.DEFAULT_DATA, domain)
        assert set(skill.output_fields) == set(metadata["output_columns"])
        assert skill.step_count >= 3


def test_pass_credentials_are_memory_only_and_bounded(monkeypatch):
    from benchmarks.sop_bench.experiment_credentials import load_pass_credentials

    seen = []

    def fake_run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(stdout="private-value\noptional notes\n")

    monkeypatch.setattr("benchmarks.sop_bench.experiment_credentials.subprocess.run", fake_run)
    values = load_pass_credentials({"SOME_API_KEY": "api/service"})
    assert values == {"SOME_API_KEY": "private-value"}
    command, kwargs = seen[0]
    assert command == ["/Users/Vivek.Haldar/.homebrew/bin/pass", "show", "api/service"]
    assert kwargs["timeout"] == 15
    assert kwargs["env"]["PATH"].startswith("/Users/Vivek.Haldar/.homebrew/bin:")


def test_email_bridge_imports_from_preserved_source(monkeypatch):
    import sys

    from benchmarks.sop_bench.mcp_bridge import load_domain

    monkeypatch.setattr(sys, "path", [str(paired.DEFAULT_DATA.parents[2]), *sys.path])
    manager, specs = load_domain("email_intent", str(paired.DEFAULT_DATA))
    assert type(manager).__name__ == "ProductListingManager"
    assert len(specs) == 5


def test_public_event_stream_cleanup_finishes_before_return():
    order = []

    async def events():
        order.append("terminal_event")
        yield event(EventType.RUN_COMPLETED)
        await asyncio.sleep(0)
        order.append("transport_closed")
        await asyncio.sleep(0)
        order.append("log_closed")

    def to_result():
        assert order == ["terminal_event", "transport_closed", "log_closed"]
        return "result"

    async def create_session(**kwargs):
        return SimpleNamespace(events=events, to_result=to_result)

    result = asyncio.run(
        paired.run_until_closed(
            SimpleNamespace(create_session=create_session),
            {},
            SimpleNamespace(),
        )
    )
    assert result == "result"


def test_four_jobs_complete_all_frozen_holdout_cases(experiment, fake_agent):
    args = options(experiment)
    args.update(split="holdout", jobs=4)
    result = paired.evaluate(**args)
    assert result["tasks"] == 4 and result["completed"] == 4
    assert result["full_frozen_holdout"] is True
    assert len(fake_agent) == 4
    terminals = list(Path(result["run_dir"]).glob("patient_intake/*/terminal.json"))
    assert len(terminals) == 4
    assert len({json.loads(p.read_text())["task_id"] for p in terminals}) == 4


@pytest.mark.parametrize("jobs", [0, 3, 5])
def test_unsupported_worker_counts_are_rejected(experiment, jobs):
    with pytest.raises(ValueError, match="one, two, or four"):
        paired.evaluate(**options(experiment), jobs=jobs)
