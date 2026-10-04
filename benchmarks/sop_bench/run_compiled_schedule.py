"""ABOUTME: Runs bounded frozen case groups with sequential, rotated arms on one event loop.
ABOUTME: Preserves cold compilation, provider audits, plan links and strict benchmark scoring.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import dataclasses
import importlib.metadata
import json
import math
import os
import platform
import re
import sys
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.sop_bench import paired_experiment as paired
from benchmarks.sop_bench.analyze_paired import (
    COST_BASIS,
    LLM_PRICES,
    account_audits,
    count,
    finish_accounting,
    sum_accounting,
)
from benchmarks.sop_bench.experiment_credentials import load_pass_credentials
from benchmarks.sop_bench.run_paired_schedule import PASS_ENTRIES, Tee
from proceda.config import ProcedaConfig
from proceda.events import EventType
from proceda.planning.compiler import COMPILER_PROMPT, REVIEW_PROMPT


def code_identity() -> dict[str, Any]:
    """The existing recursive source snapshot includes every planning runtime module."""
    result = paired.source_code_identity()
    hashes = result.setdefault("source_hashes", {})
    for filename in (
        "run_compiled_schedule.py",
        "run_paired_schedule.py",
        "experiment_credentials.py",
        "analyze_paired.py",
    ):
        path = Path(__file__).parent / filename
        hashes[str(path.relative_to(paired.REPO))] = paired.sha256_file(path)
    return result


def runtime_identity() -> dict[str, Any]:
    versions = {}
    for package in ("proceda", "litellm", "httpx", "mcp", "regex", "jsonschema"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {"python": sys.version, "platform": platform.platform(), "packages": versions}


def select_cases(
    manifest: dict[str, Any],
    domains: Sequence[str],
    split: str,
    case_ids: Mapping[str, Sequence[str]] | None,
    limit: int | None,
) -> dict[str, list[str]]:
    """Choose only frozen IDs, preserving explicit order without looking at labels."""
    if not domains or len(set(domains)) != len(domains) or not set(domains) <= set(paired.DOMAINS):
        raise ValueError("Supply unique supported domains")
    if split not in {"dev", "holdout", "all"}:
        raise ValueError("Unknown frozen split")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be positive")
    if case_ids is not None and (limit is not None or set(case_ids) != set(domains)):
        raise ValueError("Explicit IDs must cover exactly the selected domains, without limit")
    selected = {}
    for domain in domains:
        splits = manifest["domains"][domain]["splits"]
        available = sorted(splits["dev"] + splits["holdout"]) if split == "all" else splits[split]
        if case_ids is not None and not isinstance(case_ids[domain], (list, tuple)):
            raise ValueError("Explicit case IDs must be ordered lists")
        ids = list(case_ids[domain]) if case_ids is not None else list(available[:limit])
        if (
            not ids
            or any(not isinstance(key, str) or not re.fullmatch(r"[\w-]+", key) for key in ids)
            or len(ids) != len(set(ids))
            or not set(ids) <= set(available)
        ):
            raise ValueError(f"IDs must be unique members of the frozen {split} split: {domain}")
        selected[domain] = ids
    return selected


def build_order(task_ids: Mapping[str, Sequence[str]], arms: Sequence[str]) -> list[dict[str, Any]]:
    """One case at a time; cyclic Latin rows balance arm position across consecutive cases."""
    if not arms or len(set(arms)) != len(arms):
        raise ValueError("Supply at least one unique arm")
    order = []
    ordinal = 0
    for domain, identifiers in task_ids.items():
        for task_id in identifiers:
            offset = ordinal % len(arms)
            rotated = [*arms[offset:], *arms[:offset]]
            for position, arm in enumerate(rotated):
                order.append(
                    {
                        "ordinal": len(order),
                        "case_ordinal": ordinal,
                        "domain": domain,
                        "task_id": task_id,
                        "arm": arm,
                        "arm_position": position,
                    }
                )
            ordinal += 1
    return order


def _credential_names(config: ProcedaConfig) -> set[str]:
    names = {config.llm.api_key_env}
    if config.planning.enabled and config.planning.compiler_llm:
        names.add(config.planning.compiler_llm.api_key_env)
    if config.decisions.enabled:
        names.update(backend.api_key_env for backend in config.decisions.backends)
        if config.decisions.fallback_llm:
            names.add(config.decisions.fallback_llm.api_key_env)
    return names - {""}


def _compiler_attempts(case_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Classify every compiler/reviewer retry using the frozen exact system prompts."""
    attempts, errors = [], []
    purposes = {COMPILER_PROMPT: "compiler", REVIEW_PROMPT: "reviewer"}
    for path in sorted((case_dir / "providers").rglob("*.json")):
        try:
            audit = json.loads(path.read_text())
            messages = audit.get("request", {}).get("messages", [])
            system = messages[0].get("content") if messages else None
            if system not in purposes:
                continue
            usage = audit.get("usage") or {}
            incoming, outgoing = (
                count(usage.get("prompt_tokens")),
                count(usage.get("completion_tokens")),
            )
            model = audit.get("response_model") or audit.get("requested_model", "")
            rates = LLM_PRICES.get(model) or LLM_PRICES.get(model.removeprefix("openai/"))
            cost = sum(
                tokens * rate / 1_000_000
                for tokens, rate in (
                    (incoming, rates[0] if rates else None),
                    (outgoing, rates[1] if rates else None),
                )
                if tokens is not None and rate is not None
            )
            known = incoming is not None and outgoing is not None and rates is not None
            attempts.append(
                {
                    "path": str(path),
                    "purpose": purposes[system],
                    "status": audit.get("status"),
                    "model": model,
                    "prompt_tokens": incoming,
                    "completion_tokens": outgoing,
                    "cost_usd": cost if known else None,
                    "cost_usd_lower_bound": cost,
                    "system_prompt_sha256": paired.canonical_hash(system),
                }
            )
        except (ValueError, TypeError, AttributeError, OSError) as exc:
            errors.append(f"{path}: {type(exc).__name__}")
    return attempts, errors


def _plan_links(case_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    links, errors = [], []
    events = case_dir / "events.jsonl"
    if not events.exists():
        return [], ["Missing events.jsonl"]
    for line_number, line in enumerate(events.read_text().splitlines(), 1):
        try:
            event = json.loads(line)
            if event.get("type") != EventType.PLAN_PREPARED.value:
                continue
            payload = dict(event["payload"])
            if payload.get("artifact_path"):
                artifact = Path(payload["artifact_path"])
                if artifact.is_file():
                    observed = paired.sha256_file(artifact)
                    payload["observed_artifact_sha256"] = observed
                    if payload.get("artifact_sha256") != observed:
                        errors.append(f"Recorded plan artifact hash differs from file: {artifact}")
                else:
                    errors.append(f"Missing plan artifact: {artifact}")
            if payload.get("attempt_path"):
                attempt = Path(payload["attempt_path"])
                if attempt.is_file():
                    payload["observed_attempt_sha256"] = paired.sha256_file(attempt)
                    if (
                        payload.get("attempt_sha256") is not None
                        and payload["attempt_sha256"] != payload["observed_attempt_sha256"]
                    ):
                        errors.append(
                            f"Recorded compiler attempt hash differs from file: {attempt}"
                        )
                else:
                    errors.append(f"Missing compiler attempt artifact: {attempt}")
            links.append(payload)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            errors.append(f"events.jsonl:{line_number}: {type(exc).__name__}")
    return links, errors


def _preparation_summary(links: list[dict[str, Any]]) -> dict[str, Any]:
    """Separate measured preparation wall time from case inference, without assuming zero."""
    valid = [
        (link, float(link["preparation_elapsed_seconds"]))
        for link in links
        if type(link.get("preparation_elapsed_seconds")) in {int, float}
        and math.isfinite(link["preparation_elapsed_seconds"])
        and link["preparation_elapsed_seconds"] >= 0
    ]
    total = sum(seconds for _, seconds in valid)
    return {
        "events": len(links),
        "known_elapsed_events": len(valid),
        "unknown_elapsed_events": len(links) - len(valid),
        "elapsed_seconds": total if len(valid) == len(links) else None,
        "elapsed_seconds_known": total,
        "cold_elapsed_seconds_known": sum(
            seconds for link, seconds in valid if link.get("cache_hit") is False
        ),
        "cache_hit_elapsed_seconds_known": sum(
            seconds for link, seconds in valid if link.get("cache_hit") is True
        ),
    }


def _compilation_summary(
    attempts: list[dict[str, Any]],
    errors: Sequence[str] = (),
) -> dict[str, Any]:
    known = not errors and all(attempt["cost_usd"] is not None for attempt in attempts)
    subtotal = sum(attempt["cost_usd_lower_bound"] for attempt in attempts)
    return {
        "requests": len(attempts),
        "compiler_requests": sum(a["purpose"] == "compiler" for a in attempts),
        "reviewer_requests": sum(a["purpose"] == "reviewer" for a in attempts),
        "unknown_cost_requests": sum(a["cost_usd"] is None for a in attempts),
        "audit_errors": list(errors),
        "cost_usd": subtotal if known else None,
        "cost_usd_lower_bound": subtotal,
        "cost_basis": COST_BASIS,
    }


def _append(path: Path, value: dict[str, Any], secrets: Sequence[str]) -> None:
    with path.open("a") as stream:
        stream.write(json.dumps(paired.scrub(value, secrets)) + "\n")
        stream.flush()


def _retained_prefix(driver_dir: Path, schedule: dict[str, Any]) -> dict[str, Any]:
    """Account every retained attempt, including pending calls in an interrupted case."""
    cases: list[dict[str, Any]] = []
    compiler_attempts = []
    errors = []
    for entry in schedule["entries"]:
        case = driver_dir / entry["arm"] / entry["domain"] / entry["task_id"]
        if not case.exists():
            continue
        terminal = None
        malformed = None
        try:
            if (case / "terminal.json").exists():
                terminal = json.loads((case / "terminal.json").read_text())
                if not isinstance(terminal, dict):
                    raise ValueError("Terminal record must be an object")
        except (ValueError, OSError) as exc:
            terminal = None
            malformed = f"{case}/terminal.json: {type(exc).__name__}"
        accounting = account_audits(case, terminal)
        if malformed:
            accounting["audit_errors"].append(malformed)
            finish_accounting(accounting)
        attempts, attempt_errors = _compiler_attempts(case)
        compiler_attempts.extend(attempts)
        errors.extend(accounting["audit_errors"] + attempt_errors)
        cases.append(
            {
                **entry,
                "terminal": terminal is not None,
                "case_dir": str(case),
                "accounting": accounting,
            }
        )
    return {
        "cases": cases,
        "accounting": sum_accounting([case["accounting"] for case in cases]),
        "compilation": _compilation_summary(compiler_attempts, errors),
        "compiler_attempts": compiler_attempts,
    }


async def run_schedule_async(
    *,
    arms: Mapping[str, Path],
    run_id: str,
    domains: Sequence[str],
    split: str = "dev",
    case_ids: Mapping[str, Sequence[str]] | None = None,
    limit: int | None = None,
    jobs: int = 1,
    data_dir: Path = paired.DEFAULT_DATA,
    experiment_dir: Path = paired.DEFAULT_EXPERIMENT,
    secrets: Sequence[str] = (),
) -> dict[str, Any]:
    """Run immutable case groups; never resume or overlap arms within the same case."""
    if type(jobs) is not int or not 1 <= jobs <= 4:
        raise ValueError("jobs must be an integer from one through four")
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]+", run_id)
        or not arms
        or any(not re.fullmatch(r"[A-Za-z0-9_-]+", arm) for arm in arms)
        or "plan-cache" in arms
    ):
        raise ValueError("run-id and arm names must contain letters, numbers, _ or -")
    manifest = paired.verify_sources(data_dir, experiment_dir)
    task_ids = select_cases(manifest, domains, split, case_ids, limit)
    configs = {arm: ProcedaConfig.load(Path(path)) for arm, path in arms.items()}
    known_secrets = tuple(
        dict.fromkeys(
            [
                *secrets,
                *(
                    os.environ.get(name, "")
                    for config in configs.values()
                    for name in _credential_names(config)
                ),
            ]
        )
    )
    driver_dir = (experiment_dir / "phase2" / "runs" / run_id).resolve()
    cache_dir = driver_dir / "plan-cache"
    effective = copy.deepcopy(configs)
    for config in effective.values():
        if config.planning.enabled:
            config.planning.cache_dir = str(cache_dir)
    config_snapshots = {}
    for arm, path in arms.items():
        original = paired.scrub(dataclasses.asdict(configs[arm]), known_secrets)
        execution = paired.scrub(dataclasses.asdict(effective[arm]), known_secrets)
        config_snapshots[arm] = {
            "path": str(Path(path).resolve()),
            "file_sha256": paired.sha256_file(Path(path)),
            "original": original,
            "original_sha256": paired.canonical_hash(original),
            "effective": execution,
            "effective_sha256": paired.canonical_hash(execution),
        }
    schedule: dict[str, Any] = {
        "schema_version": 1,
        "phase": 2,
        "run_id": run_id,
        "split": split,
        "validation_status": "reused_validation" if split in {"holdout", "all"} else "development",
        "untouched_confirmation": False,
        "selection": "explicit_frozen_ids" if case_ids is not None else "frozen_split_order",
        "limit_per_domain": limit,
        "domains": list(domains),
        "arms": list(arms),
        "task_ids": task_ids,
        "jobs": jobs,
        "concurrency_between_arms": 0 if jobs == 1 else jobs,
        "within_case_arm_concurrency": 1,
        "cross_case_arm_overlap": jobs > 1,
        "concurrency_model": "one_event_loop_bounded_case_groups_sequential_rotated_arms",
        "latency_interpretation": (
            "Case latency includes shared-provider contention and waits for shared compilation. "
            "Different cases can overlap arms with jobs>1; within-case arms never overlap."
        ),
        "arm_order": "cyclic Latin rotation by global case ordinal",
        "entries": build_order(task_ids, list(arms)),
        "configurations": config_snapshots,
        "source": code_identity(),
        "runtime": runtime_identity(),
        "manifest_sha256": paired.canonical_hash(manifest),
        "manifest_file_sha256": paired.sha256_file(experiment_dir / "source-manifest.json"),
        "data_dir": str(data_dir.resolve()),
        "skill_paths": {
            d: str((experiment_dir / "skills" / d / "SKILL.md").resolve()) for d in domains
        },
        "compilation": {
            "cache_policy": "new_empty_schedule_local_shared_cache",
            "cache_dir": str(cache_dir),
            "prepared_cache": False,
            "cache_hit_preparation_includes_singleflight_wait": True,
            "cost_accounting": (
                "Compiler/reviewer attempts are charged to their actual first caller. "
                "Shared one-time compilation is separately reported; no implicit arm amortization."
            ),
            "prompt_hashes": {
                name: paired.canonical_hash(prompt)
                for name, prompt in (("compiler", COMPILER_PROMPT), ("reviewer", REVIEW_PROMPT))
            },
        },
        "created_at": datetime.now(UTC).isoformat(),
    }
    rows = {}
    for domain in domains:
        values, metadata = paired.load_domain(data_dir, domain)
        by_id = {row[paired.ID_COLUMNS[domain]]: row for row in values}
        rows[domain] = (by_id, metadata)
    driver_dir.mkdir(parents=True, exist_ok=False)
    paired.write_json_exclusive(driver_dir / "schedule.json", schedule)
    for arm in arms:
        arm_dir = driver_dir / arm
        paired.write_json_exclusive(
            arm_dir / "run.json",
            {
                "schema_version": 1,
                "arm": arm,
                "run_id": run_id,
                "split": split,
                "phase": 2,
                "validation_status": schedule["validation_status"],
                "untouched_confirmation": False,
                "domains": list(domains),
                "task_ids": task_ids,
                "jobs": jobs,
                "cross_case_arm_overlap": jobs > 1,
                "manifest_sha256": schedule["manifest_sha256"],
                "configuration": config_snapshots[arm]["effective"],
                "source": schedule["source"],
                "schedule_sha256": paired.sha256_file(driver_dir / "schedule.json"),
            },
        )
        (arm_dir / "predictions.jsonl").touch(exist_ok=False)
    (driver_dir / "predictions.jsonl").touch(exist_ok=False)
    (driver_dir / "execution-order.jsonl").touch(exist_ok=False)
    records: list[dict[str, Any]] = []
    actual_starts = 0
    lock = threading.Lock()

    def verify_frozen_identity() -> None:
        if code_identity() != schedule["source"]:
            raise ValueError("Runtime source changed after schedule freeze")
        if any(
            paired.sha256_file(Path(spec["path"])) != spec["file_sha256"]
            for spec in config_snapshots.values()
        ):
            raise ValueError("Profile configuration changed after schedule freeze")
        current_manifest = paired.verify_sources(data_dir, experiment_dir)
        if paired.canonical_hash(current_manifest) != schedule["manifest_sha256"]:
            raise ValueError("Benchmark source or skill changed after schedule freeze")

    async def run_entry(entry: dict[str, Any]) -> None:
        nonlocal actual_starts
        # Abort before further inference if a running schedule's inputs/code change.
        verify_frozen_identity()
        arm, domain, task_id = entry["arm"], entry["domain"], entry["task_id"]
        by_id, metadata = rows[domain]
        case_dir = driver_dir / arm / domain / task_id
        print(
            f"Starting {entry['ordinal'] + 1}/{len(schedule['entries'])} {arm} {domain}/{task_id}",
            flush=True,
        )
        start_sequence = actual_starts
        actual_starts += 1
        started_at = datetime.now(UTC).isoformat()
        _append(
            driver_dir / "execution-order.jsonl",
            {
                **entry,
                "event": "entry.started",
                "actual_start_sequence": start_sequence,
                "started_at": started_at,
            },
            known_secrets,
        )
        try:
            record = await paired.run_case_async(
                task=by_id[task_id],
                domain=domain,
                metadata=metadata,
                config=effective[arm],
                skill_path=experiment_dir / "skills" / domain / "SKILL.md",
                case_dir=case_dir,
                data_dir=data_dir,
                secrets=known_secrets,
            )
        except BaseException as exc:
            _append(
                driver_dir / "execution-order.jsonl",
                {
                    **entry,
                    "event": "entry.interrupted",
                    "actual_start_sequence": start_sequence,
                    "started_at": started_at,
                    "finished_at": datetime.now(UTC).isoformat(),
                    "error_type": type(exc).__name__,
                    "runtime_cleanup_confirmed": False,
                },
                known_secrets,
            )
            raise
        finished_at = datetime.now(UTC).isoformat()
        _append(
            driver_dir / "execution-order.jsonl",
            {
                **entry,
                "event": "entry.finished",
                "actual_start_sequence": start_sequence,
                "started_at": started_at,
                "finished_at": finished_at,
                "status": record["status"],
                "runtime_cleanup_confirmed": True,
            },
            known_secrets,
        )
        accounting = account_audits(case_dir, record)
        attempts, compiler_errors = _compiler_attempts(case_dir)
        links, link_errors = _plan_links(case_dir)
        observation: dict[str, Any] = {
            **record,
            "arm": arm,
            "schedule_ordinal": entry["ordinal"],
            "arm_position": entry["arm_position"],
            "actual_start_sequence": start_sequence,
            "actual_started_at": started_at,
            "actual_finished_at": finished_at,
            "accounting": accounting,
            "compiler_attempts": attempts,
            "compilation": _compilation_summary(
                attempts, accounting["audit_errors"] + compiler_errors + link_errors
            ),
            "plan_links": links,
            "preparation": _preparation_summary(links),
            "trace_errors": compiler_errors + link_errors,
            "trace_integrity_valid": not (
                accounting["audit_errors"] or compiler_errors or link_errors
            ),
        }
        observation = paired.scrub(observation, known_secrets)
        paired.write_json_exclusive(case_dir / "phase2-observation.json", observation)
        records.append(observation)
        _append(driver_dir / arm / "predictions.jsonl", observation, known_secrets)
        _append(driver_dir / "predictions.jsonl", observation, known_secrets)
        audit_error_count = len(accounting["audit_errors"]) + len(observation["trace_errors"])
        print(
            f"{arm} {domain}/{task_id}: {record['status']} "
            f"{'PASS' if record['correct'] else 'FAIL'} "
            f"({record['elapsed_seconds']:.2f}s), compiler attempts={len(attempts)}, "
            f"audit errors={audit_error_count}",
            flush=True,
        )

    groups: dict[int, list[dict[str, Any]]] = {}
    for entry in schedule["entries"]:
        groups.setdefault(entry["case_ordinal"], []).append(entry)
    semaphore = asyncio.Semaphore(jobs)
    stopping = asyncio.Event()

    async def run_group(entries: list[dict[str, Any]]) -> None:
        async with semaphore:
            try:
                for entry in entries:
                    if stopping.is_set():
                        return
                    await run_entry(entry)
            except BaseException:
                stopping.set()
                raise

    with (
        (driver_dir / "driver.log").open("x") as log,
        contextlib.redirect_stdout(Tee(sys.stdout, log, list(known_secrets), lock)),
        contextlib.redirect_stderr(Tee(sys.stderr, log, list(known_secrets), lock)),
    ):
        workers: list[asyncio.Task[None]] = []
        try:
            workers = [asyncio.create_task(run_group(group)) for group in groups.values()]
            await asyncio.gather(*workers)
            # Also catch changes during the final case, before publishing completion.
            verify_frozen_identity()
        except BaseException as exc:
            stopping.set()
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            # Await case-worker cleanup. Runtime-owned background tasks can still settle
            # during asyncio.run shutdown, so this snapshot is explicitly provisional.
            await asyncio.gather(*workers, return_exceptions=True)
            failure = {
                "error_type": type(exc).__name__,
                "error": paired.scrub(str(exc), known_secrets),
                "finished_entries": len(records),
                "actual_started_entries": actual_starts,
                "planned_entries": len(schedule["entries"]),
                "partial_cases_preserved": True,
                "final_cost_complete": False,
                "offline_rescan_after_process_exit_required": True,
                "runtime_background_cleanup_may_continue": True,
                "at": datetime.now(UTC).isoformat(),
                "retained_prefix": _retained_prefix(driver_dir, schedule),
            }
            marker = (
                "aborted.json"
                if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError))
                else "failed.json"
            )
            paired.write_json_exclusive(driver_dir / marker, failure)
            print(f"Schedule stopped: {failure['error_type']}: {failure['error']}", file=sys.stderr)
            raise
        summary = {
            "run_dir": str(driver_dir),
            "planned_entries": len(schedule["entries"]),
            "terminal_entries": len(records),
            "actual_started_entries": actual_starts,
            "jobs": jobs,
            "cross_case_arm_overlap": jobs > 1,
            "execution_order_path": str(driver_dir / "execution-order.jsonl"),
            "validation_status": schedule["validation_status"],
            "untouched_confirmation": False,
            "comparison_interpretable": all(r["trace_integrity_valid"] for r in records),
            "arms": {},
            "accounting": sum_accounting([r["accounting"] for r in records]),
            "shared_compilation_once": _compilation_summary(
                [a for r in records for a in r["compiler_attempts"]],
                [error for r in records for error in r["compilation"]["audit_errors"]],
            ),
            "compilation_allocation": schedule["compilation"]["cost_accounting"],
            "preparation": _preparation_summary(
                [link for r in records for link in r["plan_links"]]
            ),
        }
        for arm in arms:
            arm_records = [r for r in records if r["arm"] == arm]
            preparation = _preparation_summary(
                [link for r in arm_records for link in r["plan_links"]]
            )
            case_seconds = sum(r["elapsed_seconds"] for r in arm_records)
            summary["arms"][arm] = {
                "planned": sum(map(len, task_ids.values())),
                "terminal": len(arm_records),
                "completed": sum(r["completed"] for r in arm_records),
                "correct": sum(r["correct"] for r in arm_records),
                "trace_integrity_valid": all(r["trace_integrity_valid"] for r in arm_records),
                "total_case_seconds": case_seconds,
                "preparation": preparation,
                "case_seconds_excluding_preparation": (
                    case_seconds - preparation["elapsed_seconds"]
                    if preparation["elapsed_seconds"] is not None
                    else None
                ),
                "accounting": sum_accounting([r["accounting"] for r in arm_records]),
                "charged_compilation": _compilation_summary(
                    [a for r in arm_records for a in r["compiler_attempts"]],
                    [error for r in arm_records for error in r["compilation"]["audit_errors"]],
                ),
                "plan_links": [link for r in arm_records for link in r["plan_links"]],
                "trace_errors": [error for r in arm_records for error in r["trace_errors"]],
            }
        paired.write_json_exclusive(driver_dir / "summary.json", summary)
        paired.write_json_exclusive(
            driver_dir / "completed.json",
            {
                "completed_at": datetime.now(UTC).isoformat(),
                "entries": len(records),
                "summary_sha256": paired.sha256_file(driver_dir / "summary.json"),
            },
        )
    return summary


def _arms(values: Sequence[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not path or name in result:
            raise ValueError("Use unique --arm NAME=CONFIG_PATH entries")
        result[name] = Path(path).expanduser().resolve()
    return result


async def main_async(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, metavar="NAME=CONFIG_PATH")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--domains", nargs="+", choices=paired.DOMAINS)
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    parser.add_argument("--case-ids", type=Path, help="JSON domain -> ordered frozen case-ID list")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--jobs", type=int, choices=(1, 2, 3, 4), default=1)
    parser.add_argument("--data-dir", type=Path, default=paired.DEFAULT_DATA)
    parser.add_argument("--experiment-dir", type=Path, default=paired.DEFAULT_EXPERIMENT)
    parser.add_argument(
        "--environment-credentials",
        action="store_true",
        help="Use existing environment only, without loading missing keys from pass",
    )
    args = parser.parse_args(argv)
    arms = _arms(args.arm)
    identifiers = json.loads(args.case_ids.read_text()) if args.case_ids else None
    if identifiers is not None and not isinstance(identifiers, dict):
        raise ValueError("--case-ids must be a JSON object mapping domains to ID lists")
    domains = args.domains or (list(identifiers) if identifiers else list(paired.DOMAINS))
    configs = [ProcedaConfig.load(path) for path in arms.values()]
    needed = set().union(*(_credential_names(config) for config in configs))
    entries = {
        name: PASS_ENTRIES[name]
        for name in needed
        if name in PASS_ENTRIES and not os.environ.get(name)
    }
    credentials = {} if args.environment_credentials else load_pass_credentials(entries)
    with paired.credential_environment(credentials):
        all_secrets = tuple(credentials.values()) + tuple(
            os.environ.get(name, "") for name in needed
        )
        try:
            summary = await run_schedule_async(
                arms=arms,
                run_id=args.run_id,
                domains=domains,
                split=args.split,
                case_ids=identifiers,
                limit=args.limit,
                jobs=args.jobs,
                data_dir=args.data_dir,
                experiment_dir=args.experiment_dir,
                secrets=all_secrets,
            )
        except Exception as exc:
            print(
                paired.scrub(f"Schedule failed: {type(exc).__name__}: {exc}", all_secrets),
                file=sys.stderr,
            )
            return 1
    print(json.dumps({"run_dir": summary["run_dir"], "status": "completed"}))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
