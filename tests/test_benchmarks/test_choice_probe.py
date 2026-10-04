"""ABOUTME: Verify label isolation, immutable diagnostics, and honest choice accounting.
ABOUTME: All compiler and decision inference is mocked; these tests need no credentials.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.sop_bench import choice_probe as probe
from proceda.config import DecisionBackendConfig, ProcedaConfig
from proceda.decision import ChoiceResult


@dataclass
class Ref:
    kind: str = "input"
    key: str = "email_body"
    step: int | None = None


@dataclass
class Choice:
    field: str = "intent"
    instructions: str = "Choose the declared category."
    criteria: dict = dc_field(default_factory=lambda: {"route_a": "A", "route_b": "B"})
    inputs: dict = dc_field(default_factory=lambda: {"message": Ref()})


@dataclass
class Step:
    index: int = 1
    kind: str = "decision"
    decision: Choice | None = dc_field(default_factory=Choice)
    extractors: list = dc_field(default_factory=list)


@dataclass
class Plan:
    steps: list = dc_field(default_factory=lambda: [Step()])


@dataclass
class Compilation:
    plan: Plan | None = dc_field(default_factory=Plan)
    cache_key: str = "cache-key"
    artifact_path: Path = Path("cache/artifact.json")
    cache_hit: bool = False
    status: str = "reviewed"
    errors: list = dc_field(default_factory=list)
    prompt_tokens: int = 10
    completion_tokens: int = 5
    artifact_sha256: str = "artifact-bytes-hash"


@pytest.fixture
def fixture_source(tmp_path):
    experiment, data = tmp_path / "experiment", tmp_path / "data"
    domain = "email_intent"
    source_dir = data / domain
    source_dir.mkdir(parents=True)
    skill_path = experiment / "skills" / domain / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: source-choice\ndescription: Classify input.\n"
        "required_tools:\n  - lookup\noutput_fields:\n  - intent\n---\n\n"
        "### Step 1: Choose\nClassify email_body as route_a or route_b.\n"
    )
    spec = lambda name: {  # noqa: E731
        "toolSpec": {"name": name, "inputSchema": {"json": {"type": "object"}}}
    }
    (source_dir / "toolspecs.json").write_text(json.dumps([spec("lookup"), spec("unused")]))
    columns = ["email_id", "email_body", "product_id"]
    rows = [
        {"email_id": "D1", "email_body": "CASE_VALUE_1", "product_id": "P00001"},
        {"email_id": "D2", "email_body": "CASE_VALUE_2", "product_id": "P00002"},
        {"email_id": "H1", "email_body": "HOLDOUT_VALUE", "product_id": "P00003"},
    ]
    for suffix, fields in [("without", columns), ("with", [*columns, "intent"])]:
        with (source_dir / f"test_set_{suffix}_outputs.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({**row, "intent": "route_a"} if suffix == "with" else row)
    manifest = {
        "source_commit": "frozen-revision",
        "domains": {
            domain: {
                "files": {path.name: probe.sha256_file(path) for path in source_dir.iterdir()},
                "skill_sha256": probe.sha256_file(skill_path),
                "input_columns": columns,
                "output_columns": ["intent"],
                "splits": {"dev": ["D1", "D2"], "holdout": ["H1"]},
            }
        },
    }
    (experiment / "source-manifest.json").write_text(json.dumps(manifest))
    source = probe.load_source(experiment, data, domain)
    return SimpleNamespace(experiment=experiment, data=data, domain=domain, source=source)


def test_source_payload_has_structure_only_and_filters_tools(fixture_source, monkeypatch):
    def reject_csv_parse(*args, **kwargs):
        raise AssertionError("Compilation must not parse task rows")

    monkeypatch.setattr(probe.csv, "DictReader", reject_csv_parse)
    f = fixture_source
    source = probe.load_source(f.experiment, f.data, f.domain)
    assert source.input_fields == ["email_id", "email_body", "product_id"]
    assert [tool["function"]["name"] for tool in source.tools] == ["sop-bench__lookup"]
    assert "CASE_VALUE" not in json.dumps(source.identity)


@pytest.mark.parametrize("ids", [[], ["D1", "D1"], ["H1"], ["missing"]])
def test_rejects_invalid_or_holdout_selection(fixture_source, ids):
    f = fixture_source
    with pytest.raises(ValueError):
        probe.select_inputs(f.source, f.data, f.domain, ids)


def test_selection_preserves_requested_order_and_excludes_answers(fixture_source):
    f = fixture_source
    rows = probe.select_inputs(f.source, f.data, f.domain, ["D2", "D1"])
    assert [key for key, _ in rows] == ["D2", "D1"]
    assert all("intent" not in inputs for _, inputs in rows)


def test_source_tampering_rejected(fixture_source):
    f = fixture_source
    (f.data / f.domain / "toolspecs.json").write_text("[]")
    with pytest.raises(ValueError, match="hash differs"):
        probe.load_source(f.experiment, f.data, f.domain)


async def test_compiler_never_receives_case_values_or_labels(fixture_source, tmp_path):
    f = fixture_source
    calls = []

    async def compiler(skill, tools, fields, **kwargs):
        calls.append((skill, tools, fields, kwargs))
        assert fields == f.source.input_fields
        assert "CASE_VALUE" not in skill.raw_content
        assert "case_ids" not in kwargs and "output_fields" not in kwargs
        assert isinstance(kwargs["compiler_config"], dict)
        assert "audit_dir" not in kwargs["compiler_config"]
        return Compilation()

    out = tmp_path / "compiled"
    result = await probe.compile_probe(
        source=f.source,
        compiler_config=ProcedaConfig(),
        reviewer_config=ProcedaConfig(),
        out_dir=out,
        cache_dir=tmp_path / "cache",
        compile_fn=compiler,
    )
    assert len(calls) == 1
    assert result["plan_sha256"] == probe.canonical_hash(probe.plain(Plan()))
    assert "CASE_VALUE" not in (out / "compiler-input.json").read_text()
    assert (out / "compilation-result.json").exists()
    with pytest.raises(FileExistsError):
        await probe.compile_probe(
            source=f.source,
            compiler_config=ProcedaConfig(),
            reviewer_config=ProcedaConfig(),
            out_dir=out,
            cache_dir=tmp_path / "cache",
            compile_fn=compiler,
        )
    assert len(calls) == 1


def test_eligible_choice_rejects_receipt_dependencies_and_ambiguous_steps(fixture_source):
    source = fixture_source.source
    dependent = Step(index=2, decision=Choice(inputs={"result": Ref(kind="receipt")}))
    assert probe.eligible_choice_steps(Plan([dependent]), source) == []
    assert probe.select_step(Plan([Step(), dependent]), source, None).index == 1
    with pytest.raises(ValueError, match="exactly one"):
        probe.select_step(Plan([Step(), Step(index=2)]), source, None)
    assert probe.select_step(Plan([Step(), Step(index=2)]), source, 2).index == 2


def test_fact_choice_requires_current_step_extractor(fixture_source):
    extractor = SimpleNamespace(field="extracted", source=Ref())
    step = Step(
        decision=Choice(inputs={"value": Ref(kind="fact", key="extracted", step=1)}),
        extractors=[extractor],
    )
    assert probe.eligible_choice_steps(Plan([step]), fixture_source.source) == [step]
    step.decision.inputs["value"].step = 0
    assert probe.eligible_choice_steps(Plan([step]), fixture_source.source) == []


def test_shared_plan_validator_and_runner_bind_input_only(fixture_source):
    from proceda.planning.models import validate_plan
    from proceda.planning.runner import PlanBindingError, prepare_choice

    source = fixture_source.source
    text = source.skill.steps[0].content
    raw = {
        "ir_version": "1",
        "steps": [
            {
                "index": 1,
                "kind": "decision",
                "source_quote": text,
                "residual_obligations": [],
                "decision": {
                    "field": "intent",
                    "instructions": text,
                    "criteria": {"route_a": "A", "route_b": "B"},
                    "inputs": {
                        "message": {"kind": "input", "key": "email_body", "source_quote": text}
                    },
                    "source_quote": text,
                },
            }
        ],
    }
    plan = validate_plan(raw, source.skill, source.tools, source.input_fields)
    selected = probe.select_step(plan, source, None)
    bound = prepare_choice(selected, {"email_body": "observed", "intent": "forbidden answer"})
    assert bound.state == {"message": "observed"}
    assert "forbidden answer" not in json.dumps(probe.plain(bound))
    with pytest.raises(PlanBindingError):
        prepare_choice(selected, {})


def test_plan_loading_requires_original_successful_review(fixture_source, tmp_path):
    bundle = {
        "compiler_artifact_path": str(tmp_path / "missing.json"),
        "compiler_cache_key": "key",
        "compiler_artifact_sha256": "invented",
        "plan": probe.plain(Plan()),
        "plan_sha256": probe.canonical_hash(probe.plain(Plan())),
    }
    with pytest.raises(ValueError, match="lacks successful review"):
        probe.load_reviewed_plan(bundle, fixture_source.source)


def test_plan_bundle_must_match_reviewed_artifact(fixture_source, monkeypatch):
    from proceda.planning import compiler

    result = Compilation(status="accepted")
    monkeypatch.setattr(compiler, "load_cached_plan", lambda *args: result)
    bundle = {
        "compiler_artifact_path": "artifact.json",
        "compiler_cache_key": "key",
        "compiler_artifact_sha256": result.artifact_sha256,
        "plan": probe.plain(result.plan),
        "plan_sha256": probe.canonical_hash(probe.plain(result.plan)),
    }
    assert probe.load_reviewed_plan(bundle, fixture_source.source) == result.plan
    bundle["plan"]["steps"][0]["decision"]["instructions"] = "tampered"
    with pytest.raises(ValueError, match="differs"):
        probe.load_reviewed_plan(bundle, fixture_source.source)


def configured():
    config = ProcedaConfig()
    config.decisions.backends = [DecisionBackendConfig(kind="ollaya", model="fixture")]
    return config


def prepared(step, inputs):
    return SimpleNamespace(state={"inputs": inputs}, extracted={}, provenance={"input": "source"})


async def test_evaluation_saves_predictions_before_offline_scoring(
    fixture_source, tmp_path, monkeypatch
):
    f = fixture_source
    out = tmp_path / "run"
    requests = []

    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        async def choose(self, **kwargs):
            requests.append(kwargs)
            assert "intent" not in kwargs["state"]["inputs"]
            return ChoiceResult("route_a" if len(requests) == 1 else None, reason="fixture")

    original = probe.reference_answers

    def scoring_boundary(*args, **kwargs):
        assert len(requests) == 2
        assert all((out / key / "prediction.json").exists() for key in ["D1", "D2"])
        return original(*args, **kwargs)

    monkeypatch.setattr(probe, "reference_answers", scoring_boundary)
    plan = Plan()
    summary = await probe.evaluate_probe(
        source=f.source,
        plan=plan,
        plan_bundle={
            "source": f.source.identity,
            "plan_sha256": probe.canonical_hash(probe.plain(plan)),
        },
        config=configured(),
        step_index=None,
        case_ids=["D1", "D2"],
        data_dir=f.data,
        domain=f.domain,
        out_dir=out,
        state_builder=prepared,
        service_factory=FakeService,
    )
    assert summary["accepted"] == 1 and summary["abstained"] == 1
    assert summary["coverage"] == 0.5 and summary["accepted_accuracy"] == 1
    assert summary["correct_over_all_cases"] == 0.5
    assert "not_end_to_end" in summary["scope"]
    assert (out / "D1" / "choice-binding.json").exists()
    assert json.loads((out / "D2" / "score.json").read_text()) == {"intent": False}


async def test_error_preserved_and_later_cases_continue(fixture_source, tmp_path):
    f = fixture_source
    calls = []

    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        async def choose(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("provider broke with SECRET")
            return ChoiceResult("route_a")

    plan = Plan()
    out = tmp_path / "errors"
    summary = await probe.evaluate_probe(
        source=f.source,
        plan=plan,
        plan_bundle={
            "source": f.source.identity,
            "plan_sha256": probe.canonical_hash(probe.plain(plan)),
        },
        config=configured(),
        step_index=None,
        case_ids=["D1", "D2"],
        data_dir=f.data,
        domain=f.domain,
        out_dir=out,
        state_builder=prepared,
        service_factory=FakeService,
        secrets=["SECRET"],
    )
    assert len(calls) == 2 and summary["case_errors"] == 1
    assert "SECRET" not in (out / "D1" / "prediction.json").read_text()
    assert "[REDACTED]" in (out / "D1" / "prediction.json").read_text()


def test_metrics_keep_business_unknown_separate_from_abstention_and_cost_unknown():
    summary = probe.summarize_predictions(
        [
            {
                "value": "unable to decide",
                "correct": True,
                "details": [{"request_sent": True, "cost_known": False, "threshold": 0.97}],
            },
            {
                "value": None,
                "details": [
                    {
                        "request_sent": True,
                        "kind": "ollaya",
                        "usage_reported": True,
                        "cost_known": True,
                        "cost_usd": 0,
                        "response": {"total_duration": 2_000_000_000},
                    }
                ],
            },
        ]
    )
    assert summary["accepted"] == 1 and summary["abstained"] == 1
    assert summary["unknown_provider_cost_requests"] == 1
    assert summary["unknown_usage_requests"] == 1
    assert summary["local_native_seconds"] == 2
    assert not summary["local_compute_cost_included"]


def test_orphan_write_ahead_audits_are_included_and_unknown(tmp_path):
    audit = {
        "call_id": "orphan",
        "request_sent": True,
        "outcome": "started",
        "cost_known": False,
        "usage_reported": False,
    }
    probe.write_json_exclusive(tmp_path / "D1/providers/decisions/orphan.json", audit)
    details, integrity = probe.collect_provider_audits(tmp_path, [{"value": None}])
    summary = probe.summarize_predictions([{"value": None}], details)
    assert integrity["provider_audit_files"] == 1
    assert integrity["incomplete_provider_attempts"] == 1
    assert summary["unknown_provider_cost_requests"] == 1


def test_audit_response_native_timing_keeps_newer_finalization_error(tmp_path):
    raw = {
        "call_id": "call",
        "kind": "ollaya",
        "request_sent": True,
        "outcome": "accepted",
        "response": {"total_duration": 3_000_000_000},
    }
    probe.write_json_exclusive(tmp_path / "D1/providers/decisions/call.json", raw)
    record = {"value": None, "details": [{"call_id": "call", "outcome": "error"}]}
    details, integrity = probe.collect_provider_audits(tmp_path, [record])
    summary = probe.summarize_predictions([record], details)
    assert summary["local_native_seconds"] == 3
    assert summary["provider_errors"] == 1
    assert integrity["result_attempts_missing_audit"] == 0
