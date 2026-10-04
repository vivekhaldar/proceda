"""ABOUTME: Strict, immutable paired SOP-Bench experiments with deterministic splits.
ABOUTME: Keeps reference labels outside inference and preserves every case's audit files.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import csv
import dataclasses
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.sop_bench.output_extractor import (
    _extract_bare_json,
    _extract_final_output_json,
    _extract_xml_tags,
)
from proceda.agent import Agent
from proceda.config import AppConfig, ProcedaConfig
from proceda.events import EventType, RunEvent

REPO = Path(__file__).resolve().parents[2]
DEFAULT_EXPERIMENT = REPO / "experiments/decision-cascades-2026-10-04"
DEFAULT_DATA = DEFAULT_EXPERIMENT / "source/SOP-Bench/src/amazon_sop_bench/benchmarks/data"
DOMAINS = ("referral_abuse_detection_v1", "email_intent", "patient_intake")
ID_COLUMNS = {
    "referral_abuse_detection_v1": "account_id",
    "email_intent": "email_id",
    "patient_intake": "patient_id",
}
SOURCE_COMMIT = "f6d43858b94cf439854038101bce0eecd3e16eae"
BASELINE_COMMIT = "5dfc2b9"
SPLIT_SEED = "proceda-decision-cascades-2026-10-04-v1"
KNOWN_DEV = {
    "referral_abuse_detection_v1": {"ACC100000", "ACC100001", "ACC100040", "ACC100075"},
    "patient_intake": {"P100012", "P100029", "P100057", "P100066"},
    "email_intent": {"E1117", "E1219", "E1143", "E1083"},
}
_SOURCE_FILES = (
    "sop.txt",
    "tools.py",
    "toolspecs.json",
    "metadata.json",
    "test_set_with_outputs.csv",
    "test_set_without_outputs.csv",
)
_JSONL_LOCK = threading.Lock()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_domain(data_dir: Path, domain: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    path = data_dir / domain
    metadata = json.loads((path / "metadata.json").read_text())
    with (path / "test_set_with_outputs.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    ids = [row[ID_COLUMNS[domain]] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate task identifiers: {domain}")
    if set(metadata["input_columns"]) & set(metadata["output_columns"]):
        raise ValueError(f"Input/output schema overlap: {domain}")
    return rows, metadata


def split_rows(rows: list[dict[str, str]], domain: str) -> dict[str, list[str]]:
    """Select ~20% development without labels, grouping email product duplicates."""
    id_column = ID_COLUMNS[domain]
    groups: dict[str, list[str]] = {}
    for row in rows:
        group = row["product_id"] if domain == "email_intent" else row[id_column]
        groups.setdefault(group, []).append(row[id_column])
    dev_groups = {group for group, ids in groups.items() if set(ids) & KNOWN_DEV[domain]}
    target = math.ceil(len(rows) * 0.2)
    ordered = sorted(groups, key=lambda group: canonical_hash([SPLIT_SEED, domain, group]))
    for group in ordered:
        if sum(len(groups[g]) for g in dev_groups) >= target:
            break
        dev_groups.add(group)
    dev = sorted(task for group in dev_groups for task in groups[group])
    holdout = sorted(
        task for group, ids in groups.items() if group not in dev_groups for task in ids
    )
    return {"dev": dev, "holdout": holdout}


def source_revision(data_dir: Path) -> str:
    marker = data_dir.parents[3] / "UPSTREAM_REVISION"
    if marker.exists():
        return marker.read_text().strip()
    return subprocess.check_output(
        ["git", "-C", str(data_dir), "rev-parse", "HEAD"], text=True
    ).strip()


def source_snapshot(data_dir: Path, experiment_dir: Path) -> dict[str, Any]:
    if source_revision(data_dir) != SOURCE_COMMIT:
        raise ValueError("SOP-Bench checkout does not match the pinned source commit")
    domains = {}
    for domain in DOMAINS:
        rows, metadata = load_domain(data_dir, domain)
        files = {
            name: sha256_file(data_dir / domain / name)
            for name in _SOURCE_FILES
            if (data_dir / domain / name).exists()
        }
        skill_path = experiment_dir / "skills" / domain / "SKILL.md"
        domains[domain] = {
            "row_count": len(rows),
            "files": files,
            "skill_sha256": sha256_file(skill_path),
            "input_columns": metadata["input_columns"],
            "output_columns": metadata["output_columns"],
            "splits": split_rows(rows, domain),
        }
    return {
        "schema_version": 1,
        "source_repo": "https://github.com/amazon-science/SOP-Bench",
        "source_commit": SOURCE_COMMIT,
        "baseline_commit": BASELINE_COMMIT,
        "split_seed": SPLIT_SEED,
        "split_method": "label-blind group hash; forced inspected IDs dev",
        "known_development_ids": {k: sorted(v) for k, v in KNOWN_DEV.items()},
        "domains": domains,
    }


def freeze_sources(data_dir: Path, experiment_dir: Path) -> dict[str, Any]:
    manifest = source_snapshot(data_dir, experiment_dir)
    path = experiment_dir / "source-manifest.json"
    if path.exists():
        if json.loads(path.read_text()) != manifest:
            raise ValueError("Frozen source manifest differs from source, skill, or split")
    else:
        write_json_exclusive(path, manifest)
    return manifest


def verify_sources(data_dir: Path, experiment_dir: Path) -> dict[str, Any]:
    path = experiment_dir / "source-manifest.json"
    if not path.exists():
        raise ValueError("Freeze source-manifest.json with --prepare before inference")
    recorded = json.loads(path.read_text())
    if recorded != source_snapshot(data_dir, experiment_dir):
        raise ValueError("Frozen source manifest differs from source, skill, or split")
    return recorded


def normalize_value(value: Any) -> str:
    """Enum normalization only: never accept substrings or semantic paraphrases."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return re.sub(r"[\s-]+", "_", str(value).strip().casefold())


def strict_score(predicted: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, bool]:
    return {
        key: key in predicted
        and predicted[key] is not None
        and normalize_value(predicted[key]) == normalize_value(value)
        for key, value in expected.items()
    }


def extract_deterministic(events: Sequence[RunEvent], columns: Sequence[str]) -> dict[str, Any]:
    """Final assistant/summary text only; no tool-label shortcut and no model fallback."""
    wanted = set(columns)
    contents = []
    for event in events:
        if event.type == EventType.MESSAGE_ASSISTANT:
            contents.append(event.payload.get("content", ""))
        elif event.type == EventType.SUMMARY_GENERATED:
            contents.append(event.payload.get("summary", ""))
    for content in reversed(contents):
        if not isinstance(content, str):
            continue
        for extractor in (_extract_xml_tags, _extract_final_output_json, _extract_bare_json):
            found = extractor(content, wanted)
            if found:
                return found
    return {}


def write_json_exclusive(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def scrub(value: Any, secrets: Sequence[str] = ()) -> Any:
    """Remove credential-bearing fields and known secret values before disk writes."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key.lower() in ("api_key", "authorization", "password", "secret")
            else scrub(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


class CaseSink:
    """Append and flush observed events immediately, preserving partial executions."""

    def __init__(self, case_dir: Path, secrets: Sequence[str] = ()) -> None:
        self.case_dir = case_dir
        self.secrets = secrets
        self.events: list[RunEvent] = []

    async def handle(self, event: RunEvent) -> None:
        self.events.append(event)
        record = scrub(event.to_dict(), self.secrets)
        destinations = ["events.jsonl"]
        if event.type.value.startswith("message.") or event.type == EventType.SUMMARY_GENERATED:
            destinations.append("messages.jsonl")
        if event.type.value.startswith("tool."):
            destinations.append("tools.jsonl")
        for name in destinations:
            with (self.case_dir / name).open("a") as handle:
                handle.write(json.dumps(record) + "\n")
                handle.flush()


def source_code_identity() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()

    paths = sorted((REPO / "src/proceda").rglob("*.py"))
    paths += [
        Path(__file__),
        REPO / "benchmarks/sop_bench/mcp_bridge.py",
        REPO / "benchmarks/sop_bench/output_extractor.py",
    ]
    return {
        "commit": git("rev-parse", "HEAD"),
        "source_hashes": {str(p.relative_to(REPO)): sha256_file(p) for p in paths},
    }


def scoped_config(
    config: ProcedaConfig, case_dir: Path, domain: str, data_dir: Path
) -> ProcedaConfig:
    result = copy.deepcopy(config)
    result.logging.run_dir = str(case_dir / "runtime")
    if hasattr(result.llm, "audit_dir"):
        result.llm.audit_dir = str(case_dir / "providers")
    decisions = getattr(result, "decisions", None)
    if decisions is not None and hasattr(decisions, "audit_dir"):
        decisions.audit_dir = str(case_dir / "providers")
    if decisions is not None:
        for backend in getattr(decisions, "backends", []):
            if hasattr(backend, "audit_dir"):
                backend.audit_dir = str(case_dir / "providers")
        stronger = getattr(decisions, "fallback_llm", None)
        if stronger is not None and hasattr(stronger, "audit_dir"):
            stronger.audit_dir = str(case_dir / "providers")
    result.apps = [
        AppConfig(
            name="sop-bench",
            transport="stdio",
            command=[
                sys.executable,
                str(REPO / "benchmarks/sop_bench/mcp_bridge.py"),
                "--domain",
                domain,
                "--data-dir",
                str(data_dir),
            ],
            env={"PYTHONPATH": str(data_dir.parents[2])},
        )
    ]
    return result


async def run_until_closed(agent: Agent, variables: dict[str, str], collector: CaseSink) -> Any:
    """Wait for the public event stream to finish, including runtime/MCP cleanup."""
    handle = await agent.create_session(variables=variables, event_sinks=[collector])
    async for _event in handle.events():
        pass
    return handle.to_result()


def run_case(
    *,
    task: dict[str, str],
    domain: str,
    metadata: dict[str, Any],
    config: ProcedaConfig,
    skill_path: Path,
    case_dir: Path,
    data_dir: Path,
    secrets: Sequence[str] = (),
) -> dict[str, Any]:
    """One actual Agent execution; provider retries remain visible in its audit directory."""
    case_dir.mkdir(parents=True, exist_ok=False)
    task_id = task[ID_COLUMNS[domain]]
    variables = {key: str(task.get(key, "") or "") for key in metadata["input_columns"]}
    expected = {key: task.get(key, "") for key in metadata["output_columns"]}
    write_json_exclusive(case_dir / "inputs.json", variables)
    write_json_exclusive(case_dir / "expected.json", expected)  # never passed to the Agent
    write_json_exclusive(
        case_dir / "started.json",
        {"task_id": task_id, "domain": domain, "started_at": datetime.now(UTC).isoformat()},
    )
    for name in ("events.jsonl", "messages.jsonl", "tools.jsonl"):
        (case_dir / name).touch(exist_ok=False)
    collector = CaseSink(case_dir, secrets)
    start = time.monotonic()
    result = None
    error = None
    try:
        case_config = scoped_config(config, case_dir, domain, data_dir)
        agent = Agent.from_path(skill_path, config=case_config)
        result = asyncio.run(run_until_closed(agent, variables, collector))
        status = result.status.value
    except Exception as exc:
        status = "failed"
        error = scrub(f"{type(exc).__name__}: {exc}", secrets)
    elapsed = time.monotonic() - start
    predicted = extract_deterministic(collector.events, metadata["output_columns"])
    fields = strict_score(predicted, expected)
    record = {
        "task_id": task_id,
        "domain": domain,
        "status": status,
        "completed": status == "completed",
        "correct": status == "completed" and bool(fields) and all(fields.values()),
        "field_correct": fields,
        "predicted": predicted,
        "elapsed_seconds": elapsed,
        "error": error,
        "finished_at": datetime.now(UTC).isoformat(),
        "session_id": getattr(result, "session_id", None),
        "reported_tokens": {
            key: getattr(result, key, 0)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "case_dir": str(case_dir),
        "provider_audit_dir": str(case_dir / "providers"),
        "provider_audit_files": [
            str(p.relative_to(case_dir))
            for p in sorted((case_dir / "providers").rglob("*"))
            if p.is_file()
        ],
    }
    write_json_exclusive(case_dir / "terminal.json", scrub(record, secrets))
    return record


def evaluate(
    *,
    config: ProcedaConfig,
    arm: str,
    split: str,
    domains: Sequence[str],
    run_id: str,
    data_dir: Path = DEFAULT_DATA,
    experiment_dir: Path = DEFAULT_EXPERIMENT,
    limit: int | None = None,
    jobs: int = 1,
    resume: bool = False,
    secrets: Sequence[str] = (),
) -> dict[str, Any]:
    if jobs not in (1, 2, 4):
        raise ValueError("Use one, two, or four concurrent cases")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    for value in (arm, run_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("run-id and arm must contain only letters, numbers, _ or -")
    if (
        split not in ("dev", "holdout", "all")
        or not domains
        or len(domains) != len(set(domains))
        or not set(domains) <= set(DOMAINS)
    ):
        raise ValueError("Unknown split or domain")
    if arm == "baseline" and getattr(getattr(config, "decisions", None), "enabled", False):
        raise ValueError("Baseline requires decisions.enabled=false")
    # Also redact credentials already supplied through the process environment.
    credential_envs = [config.llm.api_key_env]
    decisions = getattr(config, "decisions", None)
    if decisions is not None:
        credential_envs.extend(backend.api_key_env for backend in decisions.backends)
        if decisions.fallback_llm is not None:
            credential_envs.append(decisions.fallback_llm.api_key_env)
    secrets = tuple(
        dict.fromkeys([*secrets, *(os.environ.get(key, "") for key in credential_envs)])
    )
    manifest = verify_sources(data_dir, experiment_dir)
    tasks = []
    for domain in domains:
        rows, metadata = load_domain(data_dir, domain)
        spec = manifest["domains"][domain]
        ids = (
            sorted(spec["splits"]["dev"] + spec["splits"]["holdout"])
            if split == "all"
            else spec["splits"][split]
        )
        if limit:
            ids = ids[:limit]
        by_id = {row[ID_COLUMNS[domain]]: row for row in rows}
        tasks.extend((domain, by_id[task_id], metadata) for task_id in ids)
    arm_dir = experiment_dir / "runs" / run_id / arm
    frozen = {
        "schema_version": 1,
        "arm": arm,
        "run_id": run_id,
        "split": split,
        "domains": list(domains),
        "limit_per_domain": limit,
        "jobs": jobs,
        "task_ids": {
            domain: [task[ID_COLUMNS[domain]] for d, task, _ in tasks if d == domain]
            for domain in domains
        },
        "manifest_sha256": canonical_hash(manifest),
        "configuration": scrub(dataclasses.asdict(config), secrets),
        "source": source_code_identity(),
        "data_dir": str(data_dir.resolve()),
        "skill_paths": {
            d: str((experiment_dir / "skills" / d / "SKILL.md").resolve()) for d in domains
        },
    }
    frozen_path = arm_dir / "run.json"
    if frozen_path.exists():
        if not resume:
            raise ValueError("Run already exists; use --resume with identical frozen settings")
        if json.loads(frozen_path.read_text()) != frozen:
            raise ValueError("Resume refused: code, configuration, tasks, or provenance changed")
    else:
        write_json_exclusive(frozen_path, frozen)
    records = []
    pending = []
    predictions_path = arm_dir / "predictions.jsonl"
    existing_predictions = {}
    if predictions_path.exists():
        for line in predictions_path.read_text().splitlines():
            item = json.loads(line)
            existing_predictions[(item["domain"], item["task_id"])] = item
    for domain, task, metadata in tasks:
        task_id = task[ID_COLUMNS[domain]]
        case_dir = arm_dir / domain / task_id
        if (case_dir / "terminal.json").exists():
            record = json.loads((case_dir / "terminal.json").read_text())
            records.append(record)
            if (domain, task_id) not in existing_predictions:
                with predictions_path.open("a") as handle:
                    handle.write(json.dumps(record) + "\n")
        elif case_dir.exists():
            raise ValueError(
                f"Partial case retained; use a new run-id to retry: {domain}/{task_id}"
            )
        else:
            pending.append(
                dict(
                    task=task,
                    domain=domain,
                    metadata=metadata,
                    config=config,
                    skill_path=experiment_dir / "skills" / domain / "SKILL.md",
                    case_dir=case_dir,
                    data_dir=data_dir,
                    secrets=secrets,
                )
            )
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(run_case, **kwargs) for kwargs in pending]
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            with _JSONL_LOCK, (arm_dir / "predictions.jsonl").open("a") as handle:
                handle.write(json.dumps(scrub(record, secrets)) + "\n")
                handle.flush()
            print(
                f"{arm} {record['domain']}/{record['task_id']}: {record['status']} "
                f"{'PASS' if record['correct'] else 'FAIL'} ({record['elapsed_seconds']:.2f}s)",
                flush=True,
            )
    summary = {
        "arm": arm,
        "split": split,
        "tasks": len(records),
        "completed": sum(r["completed"] for r in records),
        "correct": sum(r["correct"] for r in records),
        "total_case_seconds": sum(r["elapsed_seconds"] for r in records),
        "limited_selection": limit is not None,
        "full_frozen_holdout": split == "holdout" and limit is None,
        "run_dir": str(arm_dir),
    }
    summary_path = arm_dir / "summary.json"
    if not summary_path.exists():
        write_json_exclusive(summary_path, summary)
    return summary


@contextlib.contextmanager
def credential_environment(credentials: Mapping[str, str] | None):
    """Apply an in-memory secret mapping for one CLI call; never serialize or print it."""
    credentials = credentials or {}
    previous = {key: os.environ.get(key) for key in credentials}
    try:
        os.environ.update(credentials)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main(argv: Sequence[str] | None = None, credentials: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--arm")
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    parser.add_argument("--domains", nargs="+", choices=DOMAINS, default=list(DOMAINS))
    parser.add_argument(
        "--limit", type=int, help="Maximum tasks per selected domain (smoke runs only)"
    )
    parser.add_argument("--jobs", type=int, choices=(1, 2, 4), default=1)
    parser.add_argument("--run-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--prepare", action="store_true", help="Freeze existing shared skills and split"
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--experiment-dir", type=Path, default=DEFAULT_EXPERIMENT)
    args = parser.parse_args(argv)
    if args.prepare:
        freeze_sources(args.data_dir, args.experiment_dir)
        print("Source manifest and development/holdout split frozen; no models called.")
        return 0
    if not args.config or not args.arm or not args.run_id:
        parser.error("--config, --arm, and --run-id are required for inference")
    with credential_environment(credentials):
        config = ProcedaConfig.load(args.config)
        summary = evaluate(
            config=config,
            arm=args.arm,
            split=args.split,
            domains=args.domains,
            run_id=args.run_id,
            data_dir=args.data_dir,
            experiment_dir=args.experiment_dir,
            limit=args.limit,
            jobs=args.jobs,
            resume=args.resume,
            secrets=tuple((credentials or {}).values()),
        )
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
