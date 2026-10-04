"""ABOUTME: Diagnose source-compiled finite choices on explicit development cases.
ABOUTME: Separates label-free compilation/inference from scoring; never runs SOP tools.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import dataclasses
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.sop_bench.experiment_credentials import load_pass_credentials
from benchmarks.sop_bench.paired_experiment import (
    DEFAULT_DATA,
    DEFAULT_EXPERIMENT,
    DOMAINS,
    ID_COLUMNS,
    canonical_hash,
    credential_environment,
    scrub,
    sha256_file,
    source_code_identity,
    strict_score,
    write_json_exclusive,
)
from proceda.config import ProcedaConfig
from proceda.decision import DecisionService
from proceda.llm.runtime import LLMRuntime
from proceda.skill import Skill
from proceda.skills.parser import parse_skill

PASS_ENTRIES = {
    "NEBIUS_API_KEY": "NEBIUS/TOKEN_FACTORY_API_KEY",
    "TYPESAFE_API_KEY": "API_KEYS/TYPESAFE_AI_JEV_API_KEY",
}


@dataclass(frozen=True)
class ProbeSource:
    """Compiler-visible source structure, with no task values or reference answers."""

    skill: Skill
    tools: list[dict[str, Any]]
    input_fields: list[str]
    output_fields: list[str]
    identity: dict[str, Any]
    development_ids: list[str]


def load_source(experiment_dir: Path, data_dir: Path, domain: str) -> ProbeSource:
    """Check frozen hashes without parsing answer-bearing CSV contents."""
    if domain not in DOMAINS:
        raise ValueError("Unsupported source domain")
    manifest_path = experiment_dir / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    recorded = manifest["domains"][domain]
    if set(recorded["input_columns"]) & set(recorded["output_columns"]):
        raise ValueError("Source input and reference output fields overlap")
    for name, digest in recorded["files"].items():
        if sha256_file(data_dir / domain / name) != digest:
            raise ValueError(f"Frozen source hash differs: {domain}/{name}")
    skill_path = experiment_dir / "skills" / domain / "SKILL.md"
    if sha256_file(skill_path) != recorded["skill_sha256"]:
        raise ValueError("Frozen skill hash differs")
    skill = parse_skill(skill_path.read_text(), path=skill_path)
    tools = []
    for entry in json.loads((data_dir / domain / "toolspecs.json").read_text()):
        spec = entry["toolSpec"]
        if skill.required_tools and spec["name"] not in skill.required_tools:
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": f"sop-bench__{spec['name']}",
                    "description": spec.get("description", ""),
                    "parameters": spec["inputSchema"]["json"],
                },
            }
        )
    return ProbeSource(
        skill=skill,
        tools=tools,
        input_fields=list(recorded["input_columns"]),
        output_fields=list(recorded["output_columns"]),
        identity={
            "domain": domain,
            "source_commit": manifest["source_commit"],
            "manifest_sha256": sha256_file(manifest_path),
            "source_files": recorded["files"],
            "skill_sha256": recorded["skill_sha256"],
            "tool_schemas_sha256": canonical_hash(tools),
            "input_fields_sha256": canonical_hash(recorded["input_columns"]),
        },
        development_ids=list(recorded["splits"]["dev"]),
    )


def select_inputs(
    source: ProbeSource, data_dir: Path, domain: str, case_ids: Sequence[str]
) -> list[tuple[str, dict[str, str]]]:
    """Only explicit, unique development IDs; read the input-only source file."""
    if not case_ids or len(set(case_ids)) != len(case_ids):
        raise ValueError("Supply nonempty, unique case IDs")
    if set(case_ids) - set(source.development_ids):
        raise ValueError("Choice probes accept only frozen development case IDs")
    with (data_dir / domain / "test_set_without_outputs.csv").open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not set(source.input_fields) <= set(reader.fieldnames or []):
            raise ValueError("Input-only source is missing required columns")
        rows = {row[ID_COLUMNS[domain]]: row for row in reader}
    return [(key, {name: rows[key][name] for name in source.input_fields}) for key in case_ids]


def reference_answers(
    source: ProbeSource, data_dir: Path, domain: str, case_ids: Sequence[str], field: str
) -> dict[str, dict[str, str]]:
    """Offline scorer boundary; invoke only after all predictions are saved."""
    if field not in source.output_fields:
        raise ValueError("Selected choice has no matching source reference field")
    wanted = set(case_ids)
    with (data_dir / domain / "test_set_with_outputs.csv").open(newline="") as handle:
        return {
            row[ID_COLUMNS[domain]]: {field: row[field]}
            for row in csv.DictReader(handle)
            if row[ID_COLUMNS[domain]] in wanted
        }


def plain(value: Any) -> Any:
    """Serialize dataclass plans and result metadata without implicit model calls."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def probe_code_identity() -> dict[str, Any]:
    identity = source_code_identity()
    identity["source_hashes"]["benchmarks/sop_bench/choice_probe.py"] = sha256_file(Path(__file__))
    return identity


async def compile_probe(
    *,
    source: ProbeSource,
    compiler_config: ProcedaConfig,
    reviewer_config: ProcedaConfig,
    out_dir: Path,
    cache_dir: Path,
    secrets: Sequence[str] = (),
    compile_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Compile source structure once; this command never selects or reads task rows."""
    if compile_fn is None:
        from proceda.planning.compiler import get_or_compile

        compile_fn = get_or_compile
    out_dir.mkdir(parents=True, exist_ok=False)
    compiler_llm, reviewer_llm = (
        copy.deepcopy(compiler_config.llm),
        copy.deepcopy(reviewer_config.llm),
    )
    compiler_llm.audit_dir = str(out_dir / "providers" / "compiler")
    reviewer_llm.audit_dir = str(out_dir / "providers" / "reviewer")
    compiler_input = {
        "skill": source.skill.raw_content,
        "tool_schemas": source.tools,
        "input_fields": source.input_fields,
    }
    metadata = {
        "schema_version": 1,
        "kind": "source_compilation_only",
        "created_at": datetime.now(UTC).isoformat(),
        "source": source.identity,
        "compiler_input_sha256": canonical_hash(compiler_input),
        "compiler_config": scrub(plain(compiler_llm), secrets),
        "reviewer_config": scrub(plain(reviewer_llm), secrets),
        "code": probe_code_identity(),
        "compiler_cost_included_in_choice_metrics": False,
    }
    metadata["compiler_config_sha256"] = canonical_hash(metadata["compiler_config"])
    metadata["reviewer_config_sha256"] = canonical_hash(metadata["reviewer_config"])
    write_json_exclusive(out_dir / "compile.json", metadata)
    write_json_exclusive(out_dir / "compiler-input.json", compiler_input)
    try:
        result = await compile_fn(
            source.skill,
            source.tools,
            source.input_fields,
            compiler=LLMRuntime(compiler_llm),
            reviewer=LLMRuntime(reviewer_llm),
            cache_dir=cache_dir,
            compiler_config={
                key: value for key, value in plain(compiler_llm).items() if key != "audit_dir"
            },
            reviewer_config={
                key: value for key, value in plain(reviewer_llm).items() if key != "audit_dir"
            },
        )
    except Exception as exc:
        write_json_exclusive(
            out_dir / "compilation-failed.json",
            {"error": scrub(f"{type(exc).__name__}: {exc}", secrets)},
        )
        raise
    record = plain(result)
    write_json_exclusive(out_dir / "compilation-result.json", scrub(record, secrets))
    bundle = {
        "schema_version": 1,
        "source": source.identity,
        "plan": plain(result.plan),
        "plan_sha256": canonical_hash(plain(result.plan)),
        "compilation_result": str(out_dir / "compilation-result.json"),
        "compiler_cache_key": result.cache_key,
        "compiler_artifact_path": str(result.artifact_path),
        "compiler_artifact_sha256": getattr(result, "artifact_sha256", ""),
    }
    write_json_exclusive(out_dir / "plan.json", bundle)
    return bundle


def eligible_choice_steps(plan: Any, source: ProbeSource) -> list[Any]:
    """Expose only input/extractor-derived choices that have a scorable output field."""
    eligible = []
    for step in plan.steps:
        spec = step.decision
        if step.kind != "decision" or spec is None or spec.field not in source.output_fields:
            continue
        extractor_fields = {extractor.field for extractor in step.extractors}
        if any(extractor.source.kind != "input" for extractor in step.extractors):
            continue
        if any(
            ref.kind not in {"input", "literal", "fact"}
            or (ref.kind == "fact" and (ref.key not in extractor_fields or ref.step != step.index))
            for ref in spec.inputs.values()
        ):
            continue
        eligible.append(step)
    return eligible


def load_reviewed_plan(bundle: dict[str, Any], source: ProbeSource) -> Any:
    """Require the original successful review; this loader cannot trigger inference."""
    from proceda.planning.compiler import load_cached_plan

    result = load_cached_plan(
        Path(bundle["compiler_artifact_path"]),
        bundle["compiler_cache_key"],
        source.skill,
        source.tools,
        source.input_fields,
    )
    if (
        result.plan is None
        or result.status != "accepted"
        or result.artifact_sha256 != bundle["compiler_artifact_sha256"]
    ):
        raise ValueError("Compiled artifact is missing, changed, or lacks successful review")
    if (
        canonical_hash(plain(result.plan)) != bundle["plan_sha256"]
        or canonical_hash(bundle["plan"]) != bundle["plan_sha256"]
    ):
        raise ValueError("Plan bundle differs from the reviewed compiler artifact")
    return result.plan


def select_step(plan: Any, source: ProbeSource, index: int | None) -> Any:
    candidates = eligible_choice_steps(plan, source)
    if index is not None:
        candidates = [step for step in candidates if step.index == index]
    if len(candidates) != 1:
        raise ValueError("Select exactly one eligible, input-only finite-choice step with --step")
    return candidates[0]


def collect_provider_audits(
    out_dir: Path, records: Sequence[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Include write-ahead/orphan attempts even if a case never returned a result."""
    details = [detail for record in records for detail in record.get("details", [])]
    combined = {detail.get("call_id", f"result-{i}"): detail for i, detail in enumerate(details)}
    paths = sorted(out_dir.glob("*/providers/decisions/*.json"))
    unreadable = []
    seen = set()
    for path in paths:
        try:
            raw = json.loads(path.read_text())
            call_id = raw["call_id"]
            # Result detail can record audit-finalization failure; keep that newer status.
            combined[call_id] = {**raw, **combined.get(call_id, {})}
            seen.add(call_id)
        except (ValueError, KeyError, TypeError):
            unreadable.append(str(path))
    return list(combined.values()), {
        "provider_audit_files": len(paths),
        "unreadable_provider_audits": unreadable,
        "result_attempts_missing_audit": sum(
            detail.get("call_id") not in seen for detail in details
        ),
        "incomplete_provider_attempts": sum(
            detail.get("outcome") == "started" for detail in combined.values()
        ),
    }


def summarize_predictions(
    records: Sequence[dict[str, Any]], provider_details: Sequence[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Keep abstentions, provider errors, unknown charges, and native local time explicit."""
    details = (
        list(provider_details)
        if provider_details is not None
        else [detail for record in records for detail in record.get("details", [])]
    )
    sent = [detail for detail in details if detail.get("request_sent")]
    accepted = [record for record in records if record.get("value") is not None]
    correct = [record for record in accepted if record.get("correct") is True]
    local = [detail for detail in sent if detail.get("kind") == "ollaya"]
    native = [
        detail.get("response", {}).get("total_duration")
        for detail in local
        if isinstance(detail.get("response"), dict)
    ]
    by_backend = {}
    for detail in details:
        key = detail.get("backend", "unknown")
        counts = by_backend.setdefault(key, {"attempts": 0, "requests": 0, "accepted": 0})
        counts["attempts"] += 1
        counts["requests"] += bool(detail.get("request_sent"))
        counts["accepted"] += detail.get("outcome") == "accepted"
    return {
        "scope": "finite_choice_diagnostic_not_end_to_end_SOP_success",
        "cases": len(records),
        "accepted": len(accepted),
        "abstained": len(records) - len(accepted),
        "coverage": len(accepted) / len(records) if records else None,
        "correct_accepted": len(correct),
        "accepted_accuracy": len(correct) / len(accepted) if accepted else None,
        "correct_over_all_cases": len(correct) / len(records) if records else None,
        "case_errors": sum(record.get("status") == "error" for record in records),
        "provider_requests": len(sent),
        "by_backend": by_backend,
        "provider_errors": sum(detail.get("outcome") == "error" for detail in sent),
        "configured_thresholds": sorted(
            {detail["threshold"] for detail in details if "threshold" in detail}
        ),
        "known_provider_cost_usd": sum(detail.get("cost_usd", 0.0) for detail in sent),
        "unknown_provider_cost_requests": sum(not detail.get("cost_known") for detail in sent),
        "unknown_usage_requests": sum(not detail.get("usage_reported") for detail in sent),
        "decision_elapsed_seconds": sum(detail.get("elapsed_seconds", 0.0) for detail in details),
        "local_requests": len(local),
        "local_native_seconds": sum(value / 1e9 for value in native if type(value) in (int, float)),
        "local_native_duration_unknown": len(local)
        - sum(type(value) in (int, float) for value in native),
        "local_compute_cost_included": False,
        "compiler_cost_included": False,
        "input_tokens": sum(detail.get("input_tokens", 0) for detail in sent),
        "output_tokens": sum(detail.get("output_tokens", 0) for detail in sent),
    }


async def evaluate_probe(
    *,
    source: ProbeSource,
    plan: Any,
    plan_bundle: dict[str, Any],
    config: ProcedaConfig,
    step_index: int | None,
    case_ids: Sequence[str],
    data_dir: Path,
    domain: str,
    out_dir: Path,
    state_builder: Callable[[Any, dict[str, str]], Any],
    service_factory: Callable[..., Any] = DecisionService,
    secrets: Sequence[str] = (),
) -> dict[str, Any]:
    """Infer sequentially, save predictions, then separately load reference answers."""
    step = select_step(plan, source, step_index)
    cases = select_inputs(source, data_dir, domain, case_ids)
    if not config.decisions.backends:
        raise ValueError("Diagnostic requires at least one configured decision backend")
    if plan_bundle["source"] != source.identity:
        raise ValueError("Compiled plan source identity no longer matches")
    if plan_bundle["plan_sha256"] != canonical_hash(plain(plan)):
        raise ValueError("Compiled plan hash differs")
    out_dir.mkdir(parents=True, exist_ok=False)
    config_record = scrub(plain(config), secrets)
    write_json_exclusive(
        out_dir / "run.json",
        {
            "schema_version": 1,
            "kind": "finite_choice_development_diagnostic",
            "created_at": datetime.now(UTC).isoformat(),
            "domain": domain,
            "split": "dev",
            "case_ids": list(case_ids),
            "step_index": step.index,
            "source": source.identity,
            "plan_sha256": plan_bundle["plan_sha256"],
            "config": config_record,
            "config_sha256": canonical_hash(config_record),
            "code": probe_code_identity(),
            "tools_executed": False,
            "llm_fallback": False,
            "case_concurrency": 1,
        },
    )
    write_json_exclusive(out_dir / "plan.json", plan_bundle)
    records = []
    for case_id, inputs in cases:
        case_dir = out_dir / case_id
        write_json_exclusive(case_dir / "inputs.json", inputs)
        record: dict[str, Any] = {"case_id": case_id, "field": step.decision.field, "value": None}
        started = time.perf_counter()
        try:
            prepared = state_builder(step, inputs)
            state = prepared.state
            write_json_exclusive(
                case_dir / "choice-binding.json",
                {"extracted": plain(prepared.extracted), "provenance": plain(prepared.provenance)},
            )
            write_json_exclusive(case_dir / "choice-state.json", state)
            service = service_factory(
                config.decisions.backends,
                threshold=config.decisions.threshold,
                audit_dir=case_dir / "providers",
            )
            result = await service.choose(
                state=state,
                instructions=step.decision.instructions,
                criteria=step.decision.criteria,
                question_id=step.decision.field,
            )
            record.update(plain(result))
            record["status"] = "accepted" if result.value is not None else "abstained"
        except Exception as exc:
            record.update(status="error", error=scrub(f"{type(exc).__name__}: {exc}", secrets))
        record["elapsed_seconds"] = time.perf_counter() - started
        record = scrub(record, secrets)
        write_json_exclusive(case_dir / "prediction.json", record)
        records.append(record)
        with (out_dir / "predictions.jsonl").open("a") as handle:
            handle.write(json.dumps(record) + "\n")
    # This is the first answer-bearing file parse in the inference path.
    references = reference_answers(source, data_dir, domain, case_ids, step.decision.field)
    scored = []
    for record in records:
        expected = references[record["case_id"]]
        flags = strict_score({record["field"]: record["value"]}, expected)
        scored_record = {**record, "correct": all(flags.values())}
        write_json_exclusive(out_dir / record["case_id"] / "expected.json", expected)
        write_json_exclusive(out_dir / record["case_id"] / "score.json", flags)
        scored.append(scored_record)
    audits, integrity = collect_provider_audits(out_dir, records)
    summary = {**summarize_predictions(scored, audits), **integrity}
    summary["completed_at"] = datetime.now(UTC).isoformat()
    write_json_exclusive(out_dir / "summary.json", summary)
    return summary


async def main_async(
    argv: Sequence[str] | None = None, credentials: Mapping[str, str] | None = None
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--load-pass", action="store_true", help="Load known entries into memory")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser("compile", help="Compile only; no task inference")
    compile_parser.add_argument("--config", type=Path, required=True)
    compile_parser.add_argument("--reviewer-config", type=Path)
    compile_parser.add_argument("--domain", choices=DOMAINS, required=True)
    compile_parser.add_argument("--name", required=True)
    evaluate_parser = commands.add_parser("evaluate", help="Evaluate explicit development IDs")
    evaluate_parser.add_argument("--config", type=Path, required=True)
    evaluate_parser.add_argument("--domain", choices=DOMAINS, required=True)
    evaluate_parser.add_argument("--plan", type=Path, required=True)
    evaluate_parser.add_argument("--run-id", required=True)
    evaluate_parser.add_argument("--step", type=int)
    evaluate_parser.add_argument("--case-ids", nargs="+", required=True)
    args = parser.parse_args(argv)
    identifier = args.name if args.command == "compile" else args.run_id
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        parser.error("name/run-id must contain only letters, numbers, _ or -")
    source = load_source(args.experiment_dir, args.data_dir, args.domain)
    phase2 = args.experiment_dir / "phase2"
    loaded = dict(credentials or {})
    if args.load_pass:
        loaded.update(load_pass_credentials(PASS_ENTRIES))
    with credential_environment(loaded):
        config = ProcedaConfig.load(args.config)
        if args.command == "compile":
            reviewer_config = ProcedaConfig.load(args.reviewer_config or args.config)
            result = await compile_probe(
                source=source,
                compiler_config=config,
                reviewer_config=reviewer_config,
                out_dir=phase2 / "plans" / args.name,
                cache_dir=phase2 / "cache",
                secrets=tuple(loaded.values()),
            )
            print(
                json.dumps({"plan_sha256": result["plan_sha256"], "compiled": bool(result["plan"])})
            )
            return 0 if result["plan"] is not None else 1
        # Plan validation and evidence binding use the same implementation as runtime.
        from proceda.planning.runner import prepare_choice

        bundle = json.loads(args.plan.read_text())
        plan = load_reviewed_plan(bundle, source)
        summary = await evaluate_probe(
            source=source,
            plan=plan,
            plan_bundle=bundle,
            config=config,
            step_index=args.step,
            case_ids=args.case_ids,
            data_dir=args.data_dir,
            domain=args.domain,
            out_dir=phase2 / "runs" / args.run_id,
            state_builder=prepare_choice,
            secrets=tuple(loaded.values()),
        )
    print(json.dumps(summary))
    return 0


def main(argv: Sequence[str] | None = None, credentials: Mapping[str, str] | None = None) -> int:
    return asyncio.run(main_async(argv, credentials))


if __name__ == "__main__":
    raise SystemExit(main())
