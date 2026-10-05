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
    active_cases = set()

    async def run_case(**kwargs):
        nonlocal active
        identity = (kwargs["domain"], kwargs["task"]["email_id"])
        assert identity not in active_cases
        active_cases.add(identity)
        active += 1
        case = kwargs["case_dir"]
        arm = case.parts[-3]
        root = case.parents[2]
        schedule = json.loads((root / "schedule.json").read_text())
        assert active <= schedule["jobs"]
        await asyncio.sleep(0)
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
                        "status": json.loads(artifact.read_text()).get("status", "accepted"),
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
        active_cases.remove(identity)
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


def prepared_fixture(environment, monkeypatch, status="accepted"):
    folder = environment["experiment_dir"] / "prepared"
    folder.mkdir()
    manifest = folder / "manifest.json"
    manifest.write_text('{"frozen":"preparation"}')
    artifact = folder / "source-derived-plan.json"
    artifact.write_text(json.dumps({"status": status}))
    prepared = {
        "manifest_path": str(manifest),
        "manifest_sha256": driver.paired.sha256_file(manifest),
        "artifacts": {
            artifact.name: {"path": str(artifact), "sha256": driver.paired.sha256_file(artifact)}
        },
        "domains": {DOMAIN: {"cache_key": "source-only-fixture", "status": status}},
        "accounting": {"cost_usd": 1.5},
    }
    monkeypatch.setattr(driver, "load_preparation", lambda *_, **__: prepared)
    environment["prepared_plans"] = manifest
    return prepared


@pytest.mark.parametrize("status", ["accepted", "rejected", "unsupported"])
async def test_prepared_outcome_shared_without_compilation_and_full_cost_each_arm(
    environment, cases, monkeypatch, status
):
    prepared_fixture(environment, monkeypatch, status)
    original = driver.paired.run_case_async

    async def check_config(**kwargs):
        if kwargs["config"].planning.enabled:
            assert kwargs["config"].planning.require_cached
        return await original(**kwargs)

    monkeypatch.setattr(driver.paired, "run_case_async", check_config)
    result = await driver.run_schedule_async(**environment)
    assert result["accounting"]["llm_requests"] == 9
    assert result["shared_compilation_once"]["requests"] == 0
    assert result["physical_experiment_cost"]["preparation_cost_usd_once"] == 1.5
    for arm in ("compiled-llm", "compiled-dm"):
        record = result["arms"][arm]
        assert record["independent_deployment_cost"]["full_preparation_cost_usd"] == 1.5
        assert not record["independent_deployment_cost"]["preparation_split_between_arms"]
        assert all(link["status"] == status and link["cache_hit"] for link in record["plan_links"])
    assert (
        result["arms"]["baseline"]["independent_deployment_cost"]["full_preparation_cost_usd"] == 0
    )


async def test_changed_prepared_cache_aborts_before_further_cases(environment, cases, monkeypatch):
    prepared_fixture(environment, monkeypatch)
    original = driver.paired.run_case_async

    async def mutate(**kwargs):
        result = await original(**kwargs)
        root = kwargs["case_dir"].parents[2]
        (root / "plan-cache/source-derived-plan.json").write_text("changed")
        return result

    monkeypatch.setattr(driver.paired, "run_case_async", mutate)
    with pytest.raises(ValueError, match="prepared cache changed"):
        await driver.run_schedule_async(**environment)
    assert len(cases) == 1


async def test_prepared_outcome_mismatch_saves_evidence_then_stops(environment, cases, monkeypatch):
    prepared = prepared_fixture(environment, monkeypatch)
    prepared["domains"][DOMAIN]["cache_key"] = "unexpected-other-identity"
    with pytest.raises(ValueError, match="Prepared plan mismatch"):
        await driver.run_schedule_async(**environment)
    assert len(cases) == 2  # Baseline, then first compiled arm; no remaining paid cases.
    root = environment["experiment_dir"] / "phase2/runs/test"
    assert (root / "compiled-llm/email_intent/E1/terminal.json").exists()
    saved = json.loads((root / "compiled-llm/email_intent/E1/phase2-observation.json").read_text())
    assert not saved["trace_integrity_valid"]
    assert len((root / "predictions.jsonl").read_text().splitlines()) == 2
    assert (root / "failed.json").exists()


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


@pytest.mark.parametrize("jobs", [2, 3, 4])
async def test_parallel_groups_are_bounded_and_keep_rotated_arm_order(
    environment,
    cases,
    monkeypatch,
    jobs,
):
    original = driver.paired.run_case_async
    active = peak = 0
    ready = asyncio.Event()

    async def overlap(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == min(jobs, 3):
            ready.set()
        await ready.wait()
        await asyncio.sleep({"E1": 0.003, "E2": 0.001, "E3": 0.002}[kwargs["task"]["email_id"]])
        try:
            return await original(**kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(driver.paired, "run_case_async", overlap)
    summary = await driver.run_schedule_async(**environment, jobs=jobs)
    assert peak == min(jobs, 3) and active == 0
    assert len(cases) == 9 and len({(a, c) for a, c, _ in cases}) == 9
    expected = {
        "E1": ["baseline", "compiled-llm", "compiled-dm"],
        "E2": ["compiled-llm", "compiled-dm", "baseline"],
        "E3": ["compiled-dm", "baseline", "compiled-llm"],
    }
    for case, order in expected.items():
        assert [arm for arm, key, _ in cases if key == case] == order
    root = Path(summary["run_dir"])
    frozen = json.loads((root / "schedule.json").read_text())
    assert frozen["entries"] == driver.build_order(
        environment["case_ids"], list(environment["arms"])
    )
    assert frozen["concurrency_between_arms"] == jobs
    assert frozen["cross_case_arm_overlap"] is True
    assert frozen["within_case_arm_concurrency"] == 1
    assert frozen["compilation"]["cache_hit_preparation_includes_singleflight_wait"] is True
    order = [json.loads(line) for line in (root / "execution-order.jsonl").read_text().splitlines()]
    starts = [event for event in order if event["event"] == "entry.started"]
    finishes = [event for event in order if event["event"] == "entry.finished"]
    assert [event["actual_start_sequence"] for event in starts] == list(range(9))
    assert len(finishes) == 9
    for case in expected:
        selected = [event for event in order if event["task_id"] == case]
        assert [event["event"] for event in selected] == ["entry.started", "entry.finished"] * 3
    predictions = [
        json.loads(line) for line in (root / "predictions.jsonl").read_text().splitlines()
    ]
    assert len(predictions) == 9
    assert all(
        record["actual_finished_at"] >= record["actual_started_at"] for record in predictions
    )
    assert summary["accounting"]["llm_requests"] == 11
    assert summary["shared_compilation_once"]["requests"] == 2
    assert summary["terminal_entries"] == summary["actual_started_entries"] == 9


async def test_source_failure_cancels_other_groups_before_provisional_cost_snapshot(
    environment,
    cases,
    monkeypatch,
):
    original = driver.paired.run_case_async
    started = set()
    all_started = asyncio.Event()
    cancelled = []

    async def pending(**kwargs):
        case_id = kwargs["task"]["email_id"]
        started.add(case_id)
        if len(started) == 3:
            all_started.set()
        if case_id == "E1":
            await all_started.wait()
            result = await original(**kwargs)
            kwargs["skill_path"].write_text("Changed original source")
            return result
        case = kwargs["case_dir"]
        case.mkdir(parents=True)
        path = case / "providers/pending.json"
        audit = {
            "attempt": 1,
            "status": "started",
            "usage": None,
            "requested_model": "openai/zai-org/GLM-5.3-Flash",
            "request": {"messages": []},
        }
        driver.paired.write_json_exclusive(path, audit)
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)  # Simulate awaited case-worker cleanup.
            audit["status"] = "error"
            path.write_text(json.dumps(audit))
            cancelled.append(case_id)

    monkeypatch.setattr(driver.paired, "run_case_async", pending)
    with pytest.raises(ValueError, match="Frozen source"):
        await driver.run_schedule_async(**environment, jobs=3)
    assert sorted(cancelled) == ["E2", "E3"]
    root = environment["experiment_dir"] / "phase2/runs/test"
    failed = json.loads((root / "failed.json").read_text())
    assert failed["finished_entries"] == 1 and failed["actual_started_entries"] == 3
    assert failed["retained_prefix"]["accounting"]["llm_requests"] == 3
    assert failed["retained_prefix"]["accounting"]["unknown_usage_requests"] == 2
    assert failed["retained_prefix"]["accounting"]["cost_usd"] is None
    assert failed["final_cost_complete"] is False
    assert failed["offline_rescan_after_process_exit_required"] is True
    assert not (root / "completed.json").exists()
    order = [json.loads(line) for line in (root / "execution-order.jsonl").read_text().splitlines()]
    assert sum(event["event"] == "entry.interrupted" for event in order) == 2


@pytest.mark.parametrize("jobs", [0, 5, True, 1.5])
async def test_invalid_concurrency_rejected_before_any_case(environment, cases, jobs):
    with pytest.raises(ValueError, match="jobs"):
        await driver.run_schedule_async(**environment, jobs=jobs)
    assert not cases
