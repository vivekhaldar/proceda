"""ABOUTME: Prepare source-only business plans once, preserving unsuccessful paid attempts.
ABOUTME: Validate frozen preparation for shared cache-only execution without case-label access.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.sop_bench import paired_experiment as paired
from benchmarks.sop_bench.analyze_paired import account_audits, sum_accounting
from benchmarks.sop_bench.choice_probe import load_source, plain
from benchmarks.sop_bench.experiment_credentials import load_pass_credentials
from benchmarks.sop_bench.run_paired_schedule import PASS_ENTRIES
from proceda.config import ProcedaConfig
from proceda.llm.runtime import LLMRuntime
from proceda.planning import compiler as planning_compiler

TERMINAL_STATUSES = {"accepted", "rejected", "invalid", "unsupported", "error"}


def compiler_config_identity(config: ProcedaConfig) -> dict[str, Any]:
    if not config.planning.enabled or config.planning.mode != "business_decisions":
        raise ValueError("Preparation requires business_decisions planning")
    if config.planning.compiler_llm is None:
        raise ValueError("Set an explicit compiler_llm for reproducible preparation")
    return {
        key: value
        for key, value in dataclasses.asdict(config.planning.compiler_llm).items()
        if key != "audit_dir"
    }


def preparation_files(directory: Path) -> dict[str, str]:
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Preparation may contain only ordinary files and directories")
        if path.is_file() and path.name not in {"manifest.json", "completed.json"}:
            result[str(path.relative_to(directory))] = paired.sha256_file(path)
    return result


async def prepare(
    *,
    config_path: Path,
    output_dir: Path,
    domains: Sequence[str],
    data_dir: Path = paired.DEFAULT_DATA,
    experiment_dir: Path = paired.DEFAULT_EXPERIMENT,
    secrets: Sequence[str] = (),
) -> dict[str, Any]:
    """Never select or parse case rows; rejected plans remain executable fallbacks."""
    from benchmarks.sop_bench.run_compiled_schedule import _compiler_attempts, code_identity

    if not domains or len(set(domains)) != len(domains) or not set(domains) <= set(paired.DOMAINS):
        raise ValueError("Select unique supported domains")
    config = ProcedaConfig.load(config_path)
    identity = compiler_config_identity(config)
    sources = {domain: load_source(experiment_dir, data_dir, domain) for domain in domains}
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    frozen = code_identity()
    profile_hash = paired.sha256_file(config_path)
    start = time.monotonic()
    record = {
        "schema_version": 1,
        "kind": "source_only_business_preparation",
        "created_at": datetime.now(UTC).isoformat(),
        "code": frozen,
        "config_path": str(config_path.resolve()),
        "config_file_sha256": profile_hash,
        "compiler_config": identity,
        "reviewer_config": identity,
        "cache_epoch": config.planning.cache_epoch,
        "mode": config.planning.mode,
        "domains": {},
        "source_manifest_sha256": paired.sha256_file(experiment_dir / "source-manifest.json"),
        "case_values_or_labels_provided": False,
    }
    paired.write_json_exclusive(output_dir / "started.json", paired.scrub(record, secrets))
    accounted = []

    async def prepare_domain(domain, source):
        folder = output_dir / domain
        folder.mkdir()
        paired.write_json_exclusive(
            folder / "compiler-input.json",
            {
                "skill": source.skill.raw_content,
                "tool_schemas": source.tools,
                "input_fields": source.input_fields,
            },
        )
        llm = dataclasses.replace(config.planning.compiler_llm, audit_dir=str(folder / "providers"))
        before = time.monotonic()
        result = await planning_compiler.get_or_compile(
            source.skill,
            source.tools,
            source.input_fields,
            compiler=LLMRuntime(llm),
            reviewer=LLMRuntime(llm),
            compiler_config=identity,
            reviewer_config=identity,
            cache_dir=output_dir / "cache",
            cache_epoch=config.planning.cache_epoch,
            mode=config.planning.mode,
        )
        elapsed = time.monotonic() - before
        paired.write_json_exclusive(
            folder / "compilation-result.json", paired.scrub(plain(result), secrets)
        )
        if result.status not in TERMINAL_STATUSES or not Path(result.artifact_path).is_file():
            raise ValueError("Preparation did not produce a reusable terminal cache artifact")
        accounting = account_audits(folder, None)
        attempts, errors = _compiler_attempts(folder)
        accounted.append(accounting)
        record["domains"][domain] = {
            "source": source.identity,
            "status": result.status,
            "cache_key": result.cache_key,
            "artifact": str(Path(result.artifact_path).relative_to(output_dir)),
            "artifact_sha256": paired.sha256_file(Path(result.artifact_path)),
            "plan_sha256": paired.canonical_hash(plain(result.plan)),
            "step_kinds": [step.kind for step in result.plan.steps] if result.plan else [],
            "elapsed_seconds": elapsed,
            "accounting": accounting,
            "attempts": attempts,
            "audit_errors": errors,
        }
        if code_identity() != frozen or paired.sha256_file(config_path) != profile_hash:
            raise ValueError("Code, HEAD, or preparation profile changed during compilation")
        if load_source(experiment_dir, data_dir, domain).identity != source.identity:
            raise ValueError("Source changed during compilation")

    try:
        # Domains compile concurrently; each compiler implementation preserves source-step order.
        async with asyncio.TaskGroup() as group:
            for domain, source in sources.items():
                group.create_task(prepare_domain(domain, source))
        record.update(
            completed_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=time.monotonic() - start,
            accounting=sum_accounting(accounted),
            files=preparation_files(output_dir),
            cost_policy=(
                "Paid once physically. Each independently deployed compiled alternative is "
                "charged the same full relevant preparation cost; never divide it by two arms."
            ),
        )
        paired.write_json_exclusive(output_dir / "manifest.json", paired.scrub(record, secrets))
        paired.write_json_exclusive(
            output_dir / "completed.json",
            {
                "completed_at": record["completed_at"],
                "manifest_sha256": paired.sha256_file(output_dir / "manifest.json"),
                "domains": list(domains),
            },
        )
    except BaseException as exc:
        paired.write_json_exclusive(
            output_dir / "failed.json",
            {
                "at": datetime.now(UTC).isoformat(),
                "error_type": type(exc).__name__,
                "partial_preparation_retained": True,
                "elapsed_seconds": time.monotonic() - start,
            },
        )
        raise
    return record


def load_preparation(
    path: Path,
    *,
    domains: Sequence[str],
    configs: Mapping[str, ProcedaConfig],
    data_dir: Path,
    experiment_dir: Path,
    source_identity: dict[str, Any],
) -> dict[str, Any]:
    """Validate bytes, scope and runtime cache identities without invoking providers."""
    path = path.resolve()
    root = path.parent
    manifest = json.loads(path.read_text())
    marker = json.loads((root / "completed.json").read_text())
    if marker["manifest_sha256"] != paired.sha256_file(path) or (root / "failed.json").exists():
        raise ValueError("Preparation lacks a consistent successful completion marker")
    if manifest["kind"] != "source_only_business_preparation":
        raise ValueError("Unrecognized preparation manifest")
    if manifest["code"]["source_hashes"] != source_identity["source_hashes"]:
        raise ValueError("Preparation used different source code")
    if manifest["source_manifest_sha256"] != paired.sha256_file(
        experiment_dir / "source-manifest.json"
    ):
        raise ValueError("Preparation used a different benchmark source manifest")
    if preparation_files(root) != manifest["files"]:
        raise ValueError("Preparation artifacts changed")
    enabled = {arm: config for arm, config in configs.items() if config.planning.enabled}
    if not enabled:
        raise ValueError("Prepared plans require at least one planning-enabled arm")
    artifacts, selected = {}, {}
    for domain in domains:
        if domain not in manifest["domains"]:
            raise ValueError("Preparation does not cover every selected domain")
        item = manifest["domains"][domain]
        source = load_source(experiment_dir, data_dir, domain)
        if source.identity != item["source"]:
            raise ValueError("Prepared source/tool schema identity differs")
        artifact = root / item["artifact"]
        if (
            not artifact.resolve().is_relative_to(root)
            or artifact.name != f"{item['cache_key']}.json"
        ):
            raise ValueError("Unsafe prepared artifact path")
        if paired.sha256_file(artifact) != item["artifact_sha256"]:
            raise ValueError("Prepared plan bytes changed")
        for config in enabled.values():
            compiler_config = compiler_config_identity(config)
            identity = planning_compiler.compiler_identity(
                source.skill,
                source.tools,
                source.input_fields,
                compiler_config=compiler_config,
                reviewer_config=compiler_config,
                cache_epoch=config.planning.cache_epoch,
                mode=config.planning.mode,
            )
            key = hashlib.sha256(
                json.dumps(
                    identity,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            if key != item["cache_key"]:
                raise ValueError("Prepared artifact does not match runtime compiler identity")
            result = planning_compiler.load_cached_plan(
                artifact,
                item["cache_key"],
                source.skill,
                source.tools,
                source.input_fields,
                mode=config.planning.mode,
            )
            if result.status != item["status"] or result.status not in TERMINAL_STATUSES:
                raise ValueError("Prepared artifact failed source/review revalidation")
        artifacts[artifact.name] = {
            "path": str(artifact),
            "sha256": item["artifact_sha256"],
            "domain": domain,
        }
        selected[domain] = item
    return {
        "manifest_path": str(path),
        "manifest_sha256": paired.sha256_file(path),
        "preparation_code": manifest["code"],
        "artifacts": artifacts,
        "domains": selected,
        "accounting": sum_accounting([item["accounting"] for item in selected.values()]),
        "elapsed_seconds": sum(item["elapsed_seconds"] for item in selected.values()),
        "cost_policy": manifest["cost_policy"],
        "physical_preparation_identity": paired.sha256_file(path),
        "preparation_performed_in_this_schedule": False,
    }


async def main_async(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--domains", nargs="+", choices=paired.DOMAINS, default=list(paired.DOMAINS)
    )
    parser.add_argument("--data-dir", type=Path, default=paired.DEFAULT_DATA)
    parser.add_argument("--experiment-dir", type=Path, default=paired.DEFAULT_EXPERIMENT)
    parser.add_argument("--environment-credentials", action="store_true")
    args = parser.parse_args(argv)
    config = ProcedaConfig.load(args.config)
    compiler_config_identity(config)
    name = config.planning.compiler_llm.api_key_env
    entries = (
        {name: PASS_ENTRIES[name]} if name in PASS_ENTRIES and not os.environ.get(name) else {}
    )
    credentials = {} if args.environment_credentials else load_pass_credentials(entries)
    with paired.credential_environment(credentials):
        await prepare(
            config_path=args.config,
            output_dir=args.output_dir,
            domains=args.domains,
            data_dir=args.data_dir,
            experiment_dir=args.experiment_dir,
            secrets=tuple(credentials.values()) + (os.environ.get(name, ""),),
        )
    print(json.dumps({"manifest": str(args.output_dir / "manifest.json"), "status": "completed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
