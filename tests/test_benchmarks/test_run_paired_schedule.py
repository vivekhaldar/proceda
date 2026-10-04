"""ABOUTME: Test balanced sequential schedules and secret-safe driver bookkeeping.
ABOUTME: Stub both password-store access and model-running entrypoints.
"""

from __future__ import annotations

import asyncio
import json
import sys

from benchmarks.sop_bench import run_paired_schedule as driver


def test_balanced_order_and_unique_domain_blocks():
    schedule = driver.build_schedule("smoke", driver.DOMAINS, driver.PROFILES, "dev", 2)
    blocks = schedule["blocks"]
    assert [b["profile"] for b in blocks[:3]] == list(driver.PROFILES)
    assert [b["profile"] for b in blocks[3:6]] == [*driver.PROFILES[1:], driver.PROFILES[0]]
    assert [b["profile"] for b in blocks[6:]] == [driver.PROFILES[2], *driver.PROFILES[:2]]
    assert len({(b["run_id"], b["profile"]) for b in blocks}) == 9


def test_driver_loads_once_and_defaults_to_two_dev_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "DEFAULT_EXPERIMENT", tmp_path)
    credential_calls = []
    experiment_calls = []
    loops = []
    active = []

    def credentials(entries):
        credential_calls.append(entries)
        return {"NEBIUS_API_KEY": "private-test-token"}

    async def run(arguments, credentials):
        assert (tmp_path / "runs/test/schedule.json").exists()
        assert not active
        active.append(arguments)
        loops.append(asyncio.get_running_loop())
        await asyncio.sleep(0)
        experiment_calls.append(arguments)
        print(credentials["NEBIUS_API_KEY"], file=sys.stderr)
        active.pop()
        return 0

    monkeypatch.setattr(driver, "load_pass_credentials", credentials)
    monkeypatch.setattr(driver, "run_experiment", run)
    assert driver.main(["--run-id", "test"]) == 0
    assert len(credential_calls) == 1 and len(experiment_calls) == 9
    assert len(set(loops)) == 1
    assert all(command[-2:] == ["--limit", "2"] for command in experiment_calls)
    log = (tmp_path / "runs/test/driver.log").read_text()
    assert "private-test-token" not in log and "[REDACTED]" in log
    assert (tmp_path / "runs/test/completed.json").exists()


def test_full_holdout_omits_limit_and_failure_is_retained(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "DEFAULT_EXPERIMENT", tmp_path)
    monkeypatch.setattr(driver, "load_pass_credentials", lambda entries: {})
    calls = []

    async def fail(arguments, credentials):
        calls.append(arguments)
        raise RuntimeError("provider failed")

    monkeypatch.setattr(driver, "run_experiment", fail)
    assert driver.main(["--run-id", "holdout", "--split", "holdout"]) == 1
    assert "--limit" not in calls[0]
    schedule = json.loads((tmp_path / "runs/holdout/schedule.json").read_text())
    assert schedule["limit_per_domain"] is None
    assert (tmp_path / "runs/holdout/failed.json").exists()
    assert not (tmp_path / "runs/holdout/completed.json").exists()


def test_driver_passes_two_jobs_without_cross_arm_concurrency(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "DEFAULT_EXPERIMENT", tmp_path)
    monkeypatch.setattr(driver, "load_pass_credentials", lambda entries: {})
    calls = []

    async def run(args, credentials):
        calls.append(args)
        return 0

    monkeypatch.setattr(driver, "run_experiment", run)
    assert driver.main(["--run-id", "parallel-cases", "--jobs", "2"]) == 0
    assert all(args[args.index("--jobs") + 1] == "2" for args in calls)
    schedule = json.loads((tmp_path / "runs/parallel-cases/schedule.json").read_text())
    assert schedule["jobs"] == 2 and schedule["concurrency_between_arms"] == 0


def test_local_only_profile_keeps_initial_defaults_and_primary_model():
    from proceda.config import ProcedaConfig

    base = driver.DEFAULT_EXPERIMENT / "configs"
    local = ProcedaConfig.load(base / "hybrid-local.yaml")
    original = ProcedaConfig.load(base / "hybrid-local-jev.yaml")
    assert local.llm == original.llm
    assert local.decisions.threshold == 0.97
    assert local.decisions.backends == original.decisions.backends[:1]
    assert driver.PROFILES == ("baseline", "hybrid-jev", "hybrid-local-jev")
    assert "hybrid-local" in driver.PROFILE_CHOICES


def test_four_job_local_only_development_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "DEFAULT_EXPERIMENT", tmp_path)
    monkeypatch.setattr(driver, "load_pass_credentials", lambda entries: {})
    calls = []

    async def run(args, credentials):
        calls.append(args)
        return 0

    monkeypatch.setattr(driver, "run_experiment", run)
    assert (
        driver.main(
            [
                "--run-id",
                "dev-next",
                "--profiles",
                "baseline",
                "hybrid-local",
                "--limit",
                "8",
                "--jobs",
                "4",
            ]
        )
        == 0
    )
    assert len(calls) == 6
    assert all(args[args.index("--jobs") + 1] == "4" for args in calls)
    assert all(args[args.index("--limit") + 1] == "8" for args in calls)
    schedule = json.loads((tmp_path / "runs/dev-next/schedule.json").read_text())
    assert schedule["jobs"] == 4 and schedule["concurrency_between_arms"] == 0
    assert schedule["profiles"] == ["baseline", "hybrid-local"]


def test_twelve_billion_profile_changes_only_model_and_timeout():
    from dataclasses import replace

    from proceda.config import ProcedaConfig

    base = driver.DEFAULT_EXPERIMENT / "configs"
    smaller = ProcedaConfig.load(base / "hybrid-local.yaml")
    larger = ProcedaConfig.load(base / "hybrid-local-12b.yaml")
    assert larger.llm == smaller.llm
    expected_backend = replace(smaller.decisions.backends[0], model="winnow:12b", timeout=90)
    assert larger.decisions == replace(smaller.decisions, backends=[expected_backend])
    assert "hybrid-local-12b" in driver.PROFILE_CHOICES
