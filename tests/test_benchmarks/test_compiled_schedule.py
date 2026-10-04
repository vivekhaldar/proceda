"""ABOUTME: Validates frozen case-major schedules, shared compilation and audit accounting.
ABOUTME: Uses mocked case execution only; no providers or benchmark tools are called.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from benchmarks.sop_bench import run_compiled_schedule as driver
from benchmarks.sop_bench.analyze_paired import load_arm
from proceda.events import EventType, RunEvent

DOMAIN = "email_intent"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    exp = tmp_path / "experiment"
    exp.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    manifest = {"domains": {DOMAIN: {"splits": {"dev": ["E1", "E2", "E3"], "holdout": ["E4"]}}}}
    driver.paired.write_json_exclusive(exp / "source-manifest.json", manifest)
    skill = exp / "skills" / DOMAIN / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Original frozen skill fixture")
    frozen_skill = skill.read_text()

    def verify_sources(*_):
        if skill.read_text() != frozen_skill:
            raise ValueError("Frozen source manifest differs from source, skill, or split")
        return manifest

    monkeypatch.setattr(driver.paired, "verify_sources", verify_sources)
    monkeypatch.setattr(
        driver.paired,
        "load_domain",
        lambda *_: (
            [
                {"email_id": f"E{i}", "text": f"input-{i}", "answer": "EXPECTED_ONLY"}
                for i in range(1, 5)
            ],
            {"input_columns": ["email_id", "text"], "output_columns": ["answer"]},
        ),
    )
    monkeypatch.setattr(driver, "code_identity", lambda: {"commit": "fixture", "source_hashes": {}})
    monkeypatch.setattr(driver, "runtime_identity", lambda: {"python": "fixture"})
    arms = {}
    for name in ("baseline", "compiled-llm", "compiled-dm"):
        path = tmp_path / f"{name}.yaml"
        path.write_text(
            "llm:\n  model: openai/zai-org/GLM-5.3-Flash\n"
            "  api_key_env: SCHEDULE_TEST_KEY\nplanning:\n"
            f"  enabled: {'false' if name == 'baseline' else 'true'}\n"
            "  cache_dir: external-prepared-cache\n"
        )
        arms[name] = path
    return dict(
        arms=arms,
        run_id="test",
        domains=[DOMAIN],
        case_ids={DOMAIN: ["E1", "E2", "E3"]},
        data_dir=data,
        experiment_dir=exp,
    )


@pytest.fixture
def cases(monkeypatch):
    calls = []
    active = 0

    async def run_case(**kwargs):
        nonlocal active
        active += 1
        assert active == 1  # Neither cases nor arms may overlap.
        await asyncio.sleep(0)
        case = kwargs["case_dir"]
        arm = case.parts[-3]
        root = case.parents[2]
        schedule = json.loads((root / "schedule.json").read_text())
        assert (root / arm / "run.json").is_file()
        assert "EXPECTED_ONLY" not in json.dumps(schedule)
        calls.append((arm, kwargs["task"]["email_id"], id(asyncio.get_running_loop())))
        case.mkdir(parents=True)
        provider = case / "providers"
        provider.mkdir()
        events = []
        prompts = ["Ordinary case inference"]
        config = kwargs["config"]
        if config.planning.enabled:
            cache = Path(config.planning.cache_dir)
            assert cache == root / "plan-cache"
            artifact = cache / "source-derived-plan.json"
            hit = artifact.exists()
            if not hit:
                cache.mkdir()
                artifact.write_text('{"fixture":"reviewed plan"}')
                prompts += [driver.COMPILER_PROMPT, driver.REVIEW_PROMPT]
            events.append(
                RunEvent.create(
                    "fixture-session",
                    EventType.PLAN_PREPARED,
                    {
                        "status": "accepted",
                        "cache_hit": hit,
                        "artifact_path": str(artifact),
                        "artifact_sha256": driver.paired.sha256_file(artifact),
                        "cache_key": "source-only-fixture",
                        "preparation_elapsed_seconds": 0.01 if hit else 0.2,
                    },
                ).to_dict()
            )
        for index, prompt in enumerate(prompts):
            driver.paired.write_json_exclusive(
                provider / f"attempt-{index}.json",
                {
                    "attempt": 1,
                    "status": "success",
                    "request": {"messages": [{"role": "system", "content": prompt}]},
                    "requested_model": "openai/zai-org/GLM-5.3-Flash",
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "prompt_tokens_details": {"cached_tokens": 0},
                    },
                },
            )
        (case / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
        driver.paired.write_json_exclusive(case / "expected.json", {"answer": "EXPECTED_ONLY"})
        record = {
            "domain": DOMAIN,
            "task_id": kwargs["task"]["email_id"],
            "status": "completed",
            "completed": True,
            "correct": True,
            "predicted": {"answer": "EXPECTED_ONLY"},
            "elapsed_seconds": 1.0,
            "reported_tokens": {"total_tokens": len(prompts) * 120},
            "provider_audit_files": [str(p.relative_to(case)) for p in provider.glob("*.json")],
        }
        driver.paired.write_json_exclusive(case / "terminal.json", record)
        active -= 1
        return record

    monkeypatch.setattr(driver.paired, "run_case_async", run_case)
    return calls


async def test_exact_rotation_no_concurrency_and_immutable_before_inference(environment, cases):
    summary = await driver.run_schedule_async(**environment)
    assert [(arm, case) for arm, case, _ in cases] == [
        ("baseline", "E1"),
        ("compiled-llm", "E1"),
        ("compiled-dm", "E1"),
        ("compiled-llm", "E2"),
        ("compiled-dm", "E2"),
        ("baseline", "E2"),
        ("compiled-dm", "E3"),
        ("baseline", "E3"),
        ("compiled-llm", "E3"),
    ]
    assert len({loop for _, _, loop in cases}) == 1
    root = Path(summary["run_dir"])
    frozen = json.loads((root / "schedule.json").read_text())
    assert frozen["jobs"] == 1 and frozen["concurrency_between_arms"] == 0
    assert frozen["task_ids"] == environment["case_ids"]
    assert frozen["compilation"]["prepared_cache"] is False
    assert summary["terminal_entries"] == 9
    assert len((root / "predictions.jsonl").read_text().splitlines()) == 9
    assert (root / "completed.json").is_file()
    assert "Starting 1/9" in (root / "driver.log").read_text()
    for arm in environment["arms"]:
        _, observed = load_arm(root / arm)
        assert {case for _, case in observed} == {"E1", "E2", "E3"}
        assert all(case["correct"] for case in observed.values())


async def test_compilation_is_counted_once_and_charged_to_actual_caller(environment, cases):
    summary = await driver.run_schedule_async(**environment)
    assert summary["accounting"]["llm_requests"] == 11
    compilation = summary["shared_compilation_once"]
    assert compilation["requests"] == 2
    assert compilation["compiler_requests"] == compilation["reviewer_requests"] == 1
    assert compilation["cost_usd"] == pytest.approx(2 * (100 * 0.15 + 20 * 0.5) / 1_000_000)
    assert summary["arms"]["compiled-llm"]["charged_compilation"]["requests"] == 2
    assert summary["arms"]["compiled-dm"]["charged_compilation"]["requests"] == 0
    assert len(summary["arms"]["compiled-dm"]["plan_links"]) == 3
    assert all(link["artifact_sha256"] for link in summary["arms"]["compiled-dm"]["plan_links"])
    assert summary["arms"]["compiled-llm"]["preparation"]["elapsed_seconds"] == pytest.approx(0.22)
    assert summary["arms"]["compiled-llm"]["case_seconds_excluding_preparation"] == pytest.approx(
        2.78
    )


def test_plan_links_preserve_recorded_hash_and_flag_mismatch(tmp_path):
    artifact = tmp_path / "plan.json"
    attempt = tmp_path / "attempt.json"
    artifact.write_text('{"source":"changed"}')
    attempt.write_text('{"response":"original"}')
    event = RunEvent.create(
        "test",
        EventType.PLAN_PREPARED,
        {
            "artifact_path": str(artifact),
            "artifact_sha256": "original-recorded-hash",
            "attempt_path": str(attempt),
            "preparation_elapsed_seconds": 3.5,
        },
    ).to_dict()
    (tmp_path / "events.jsonl").write_text(json.dumps(event) + "\n")
    links, errors = driver._plan_links(tmp_path)
    assert links[0]["artifact_sha256"] == "original-recorded-hash"
    assert links[0]["observed_artifact_sha256"] == driver.paired.sha256_file(artifact)
    assert links[0]["observed_attempt_sha256"] == driver.paired.sha256_file(attempt)
    assert links[0]["preparation_elapsed_seconds"] == 3.5
    assert len(errors) == 1 and "hash differs" in errors[0]


def test_missing_preparation_timing_is_unknown_not_zero():
    summary = driver._preparation_summary([{"cache_hit": False}])
    assert summary["elapsed_seconds"] is None
    assert summary["unknown_elapsed_events"] == 1


async def test_reusing_run_id_never_overwrites_or_reruns(environment, cases):
    summary = await driver.run_schedule_async(**environment)
    before = (Path(summary["run_dir"]) / "schedule.json").read_bytes()
    with pytest.raises(FileExistsError):
        await driver.run_schedule_async(**environment)
    assert len(cases) == 9
    assert (Path(summary["run_dir"]) / "schedule.json").read_bytes() == before


@pytest.mark.parametrize(
    "bad",
    [
        {DOMAIN: ["E1", "E1"]},
        {DOMAIN: ["E4"]},
        {DOMAIN: []},
        {DOMAIN: "E1"},
        {"patient_intake": ["E1"]},
    ],
)
async def test_explicit_selection_rejects_duplicate_missing_or_out_of_split_ids(
    environment, cases, bad
):
    environment["case_ids"] = bad
    with pytest.raises(ValueError):
        await driver.run_schedule_async(**environment)
    assert not cases


async def test_reused_holdout_cannot_be_described_as_untouched(environment, cases):
    environment.update(split="holdout", case_ids={DOMAIN: ["E4"]})
    summary = await driver.run_schedule_async(**environment)
    schedule = json.loads((Path(summary["run_dir"]) / "schedule.json").read_text())
    assert schedule["validation_status"] == summary["validation_status"] == "reused_validation"
    assert schedule["untouched_confirmation"] is False


async def test_changed_config_aborts_before_next_case_and_preserves_first(
    environment, cases, monkeypatch
):
    original = driver.paired.run_case_async

    async def mutate(**kwargs):
        result = await original(**kwargs)
        environment["arms"]["baseline"].write_text("llm:\n  model: changed\n")
        return result

    monkeypatch.setattr(driver.paired, "run_case_async", mutate)
    with pytest.raises(ValueError, match="configuration changed"):
        await driver.run_schedule_async(**environment)
    root = environment["experiment_dir"] / "phase2/runs/test"
    failure = json.loads((root / "failed.json").read_text())
    assert failure["finished_entries"] == 1 and failure["planned_entries"] == 9
    assert len(cases) == 1
    assert (root / "baseline" / DOMAIN / "E1/terminal.json").is_file()


@pytest.mark.parametrize("last_case", [False, True])
async def test_changed_frozen_skill_stops_next_inference_or_final_completion(
    environment,
    cases,
    monkeypatch,
    last_case,
):
    original = driver.paired.run_case_async
    if last_case:
        environment["arms"] = {"baseline": environment["arms"]["baseline"]}
        environment["case_ids"] = {DOMAIN: ["E1"]}

    async def mutate(**kwargs):
        result = await original(**kwargs)
        kwargs["skill_path"].write_text("Changed business obligations during schedule")
        return result

    monkeypatch.setattr(driver.paired, "run_case_async", mutate)
    with pytest.raises(ValueError, match="Frozen source manifest differs"):
        await driver.run_schedule_async(**environment)
    root = environment["experiment_dir"] / "phase2/runs/test"
    failure = json.loads((root / "failed.json").read_text())
    assert len(cases) == 1
    assert failure["finished_entries"] == 1
    assert failure["retained_prefix"]["accounting"]["llm_requests"] == 1
    assert not (root / "completed.json").exists()
    assert not (root / "summary.json").exists()


async def test_unknown_compiler_usage_and_missing_audit_are_visible(
    environment, cases, monkeypatch
):
    original = driver.paired.run_case_async

    async def lose_usage(**kwargs):
        result = await original(**kwargs)
        case = kwargs["case_dir"]
        compiler = case / "providers/attempt-1.json"
        if compiler.exists():
            record = json.loads(compiler.read_text())
            record["usage"] = None
            record["status"] = "started"
            compiler.write_text(json.dumps(record))
            (case / "providers/attempt-2.json").unlink()
        return result

    monkeypatch.setattr(driver.paired, "run_case_async", lose_usage)
    summary = await driver.run_schedule_async(**environment)
    assert summary["shared_compilation_once"]["unknown_cost_requests"] == 1
    assert summary["shared_compilation_once"]["cost_usd"] is None
    assert summary["accounting"]["cost_usd"] is None
    assert any("Missing listed audit" in error for error in summary["accounting"]["audit_errors"])


async def test_driver_redacts_provider_exception_and_preserves_abort(environment, monkeypatch):
    async def fail(**kwargs):
        kwargs["case_dir"].mkdir(parents=True)
        print("debug contains TEST_PRIVATE_VALUE")
        raise RuntimeError("error contains TEST_PRIVATE_VALUE")

    monkeypatch.setattr(driver.paired, "run_case_async", fail)
    with pytest.raises(RuntimeError):
        await driver.run_schedule_async(**environment, secrets=["TEST_PRIVATE_VALUE"])
    root = environment["experiment_dir"] / "phase2/runs/test"
    assert "TEST_PRIVATE_VALUE" not in (root / "driver.log").read_text()
    assert "TEST_PRIVATE_VALUE" not in (root / "failed.json").read_text()
    assert (root / "baseline" / DOMAIN / "E1").is_dir()


async def test_cancelled_case_retains_aborted_marker_without_retry(environment, monkeypatch):
    calls = []

    async def cancel(**kwargs):
        calls.append(kwargs["case_dir"])
        kwargs["case_dir"].mkdir(parents=True)
        driver.paired.write_json_exclusive(
            kwargs["case_dir"] / "providers/pending.json",
            {
                "attempt": 1,
                "status": "started",
                "usage": None,
                "requested_model": "openai/zai-org/GLM-5.3-Flash",
                "request": {"messages": [{"role": "system", "content": driver.COMPILER_PROMPT}]},
            },
        )
        raise asyncio.CancelledError()

    monkeypatch.setattr(driver.paired, "run_case_async", cancel)
    with pytest.raises(asyncio.CancelledError):
        await driver.run_schedule_async(**environment)
    root = environment["experiment_dir"] / "phase2/runs/test"
    assert len(calls) == 1 and calls[0].is_dir()
    aborted = json.loads((root / "aborted.json").read_text())
    assert aborted["finished_entries"] == 0
    assert aborted["retained_prefix"]["accounting"]["llm_requests"] == 1
    assert aborted["retained_prefix"]["accounting"]["cost_usd"] is None
    assert aborted["retained_prefix"]["compilation"]["unknown_cost_requests"] == 1
    assert not (root / "completed.json").exists()


async def test_cli_sanitizes_exception_outside_tee_and_restores_credentials(
    environment, monkeypatch, capsys
):
    monkeypatch.delenv("SCHEDULE_TEST_KEY", raising=False)
    monkeypatch.setattr(driver, "PASS_ENTRIES", {"SCHEDULE_TEST_KEY": "fixture-entry"})
    loaded = []

    def credentials(entries):
        loaded.append(entries)
        return {"SCHEDULE_TEST_KEY": "DO_NOT_PRINT_TOKEN"}

    async def fail(**kwargs):
        raise RuntimeError("Provider error DO_NOT_PRINT_TOKEN")

    monkeypatch.setattr(driver, "load_pass_credentials", credentials)
    monkeypatch.setattr(driver, "run_schedule_async", fail)
    status = await driver.main_async(
        [
            "--run-id",
            "test",
            "--arm",
            f"baseline={environment['arms']['baseline']}",
        ]
    )
    assert status == 1
    assert loaded == [{"SCHEDULE_TEST_KEY": "fixture-entry"}]
    assert "DO_NOT_PRINT_TOKEN" not in capsys.readouterr().err
    import os

    assert "SCHEDULE_TEST_KEY" not in os.environ


def test_arm_parser_and_single_arm_order_are_supported():
    assert set(driver._arms(["original=/tmp/profile.yaml"])) == {"original"}
    with pytest.raises(ValueError):
        driver._arms(["same=/a", "same=/b"])
    with pytest.raises(ValueError):
        driver._arms(["missing-separator"])
    assert [entry["arm"] for entry in driver.build_order({DOMAIN: ["E1", "E2"]}, ["only"])] == [
        "only",
        "only",
    ]
