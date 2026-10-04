"""ABOUTME: Strict paired SOP-Bench scoring and all-attempt provider-cost accounting.
ABOUTME: Reads immutable experiment artifacts; never calls a model or infers missing usage.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from benchmarks.sop_bench.paired_experiment import strict_score

# USD per million tokens; list rates verified during experiment discovery.
LLM_PRICES = {
    "zai-org/GLM-5.3-Flash": (0.15, 0.50),
    "zai-org/GLM-5.3": (1.40, 4.40),
    "Qwen/Qwen3-30B-A3B-Instruct-2507": (0.10, 0.30),
    "Qwen/Qwen3-235B-A22B-Instruct-2507": (0.20, 0.60),
}
JEV_INPUT_PRICE = 0.042


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def latency(values: list[float]) -> dict[str, Any]:
    """Linear-interpolated percentiles of terminal case wall-clock durations."""
    ordered = sorted(values)

    def percentile(fraction: float) -> float | None:
        if not ordered:
            return None
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "n": len(ordered),
        "mean": statistics.mean(ordered) if ordered else None,
        "p50": percentile(0.50),
        "p95": percentile(0.95),
    }


def new_accounting() -> dict[str, Any]:
    return {
        "audit_records": 0,
        "llm_requests": 0,
        "decision_requests": 0,
        "decision_preflight_records": 0,
        "unknown_request_status": 0,
        "known_usage_requests": 0,
        "unknown_usage_requests": 0,
        "known_input_usage_requests": 0,
        "known_output_usage_requests": 0,
        "input_tokens_known": 0,
        "output_tokens_known": 0,
        "cost_usd_lower_bound": 0.0,
        "unknown_cost_requests": 0,
        "audit_errors": [],
        "providers": {},
    }


def account_audits(case_dir: Path, terminal: dict | None) -> dict[str, Any]:
    """Count each audit once; transport errors still count, preflight skips do not."""
    result = new_accounting()
    paths = sorted((case_dir / "providers").rglob("*.json"))
    for path in paths:
        try:
            record = read_json(path)
            if not isinstance(record, dict):
                raise ValueError("audit must be an object")
        except (ValueError, OSError) as error:
            result["audit_errors"].append(f"{path}: {type(error).__name__}")
            continue
        result["audit_records"] += 1
        is_decision = record.get("kind") in {"typesafe", "ollaya"}
        if is_decision:
            sent = record.get("request_sent")
            if sent is False or record.get("outcome") == "skipped":
                result["decision_preflight_records"] += 1
                continue
            if sent is not True:
                # Older audits prove a request when they contain a response status.
                if "http_status" not in record:
                    result["unknown_request_status"] += 1
                    continue
            result["decision_requests"] += 1
            raw = record.get("response")
            usage = raw.get("usage") if isinstance(raw, dict) else None
            if not isinstance(usage, dict):
                usage = record.get("usage")
            model = record.get("model", "unknown")
            provider = f"decision:{record['kind']}:{model}"
            rates = (0.0, 0.0) if record["kind"] == "ollaya" else (JEV_INPUT_PRICE, 0.0)
            in_field, out_field = "input_tokens", "output_tokens"
        elif "attempt" in record and "request" in record:
            result["llm_requests"] += 1
            usage = record.get("usage")
            model = record.get("response_model") or record.get("requested_model", "unknown")
            provider = f"llm:{model}"
            rates = LLM_PRICES.get(model) or LLM_PRICES.get(model.removeprefix("openai/"))
            in_field, out_field = "prompt_tokens", "completion_tokens"
        else:
            result["audit_errors"].append(f"{path}: unrecognized audit schema")
            continue
        usage = usage if isinstance(usage, dict) else {}
        input_tokens, output_tokens = count(usage.get(in_field)), count(usage.get(out_field))
        # completion_tokens already includes reasoning_tokens: never add its details.
        values = result["providers"].setdefault(
            provider,
            {
                "requests": 0,
                "known_usage_requests": 0,
                "unknown_usage_requests": 0,
                "input_tokens_known": 0,
                "output_tokens_known": 0,
                "cost_usd_lower_bound": 0.0,
                "unknown_cost_requests": 0,
            },
        )
        values["requests"] += 1
        known_usage = input_tokens is not None and output_tokens is not None
        usage_key = "known_usage_requests" if known_usage else "unknown_usage_requests"
        result[usage_key] += 1
        values[usage_key] += 1
        result["known_input_usage_requests"] += input_tokens is not None
        result["known_output_usage_requests"] += output_tokens is not None
        cost = 0.0
        cost_known = rates is not None
        for tokens, rate, field in (
            (input_tokens, rates[0] if rates else None, "input_tokens_known"),
            (output_tokens, rates[1] if rates else None, "output_tokens_known"),
        ):
            if tokens is not None:
                result[field] += tokens
                values[field] += tokens
                if rate is not None:
                    cost += tokens * rate / 1_000_000
            elif rate != 0:
                cost_known = False
        result["cost_usd_lower_bound"] += cost
        values["cost_usd_lower_bound"] += cost
        if not cost_known:
            result["unknown_cost_requests"] += 1
            values["unknown_cost_requests"] += 1
    if terminal:
        for relative in terminal.get("provider_audit_files", []):
            if not (case_dir / relative).is_file():
                result["audit_errors"].append(f"Missing listed audit: {relative}")
        reported = terminal.get("reported_tokens", {}).get("total_tokens", 0)
        if not paths and (terminal.get("completed") or reported):
            result["audit_errors"].append("Terminal case has no provider audits")
    finish_accounting(result)
    return result


def finish_accounting(result: dict[str, Any]) -> None:
    result["cost_complete"] = not (
        result["unknown_cost_requests"]
        or result["unknown_request_status"]
        or result["audit_errors"]
    )
    result["cost_usd"] = result["cost_usd_lower_bound"] if result["cost_complete"] else None


def sum_accounting(items: list[dict[str, Any]]) -> dict[str, Any]:
    result = new_accounting()
    for item in items:
        for key in result:
            if key == "providers":
                for provider, metrics in item[key].items():
                    target = result[key].setdefault(provider, {name: 0 for name in metrics})
                    for name, value in metrics.items():
                        target[name] += value
            elif key == "audit_errors":
                result[key].extend(item[key])
            else:
                result[key] += item[key]
    finish_accounting(result)
    return result


def load_arm(directory: Path) -> tuple[dict[str, Any], dict[tuple[str, str], dict]]:
    manifest = read_json(directory / "run.json")
    cases = {}
    for domain, identifiers in manifest["task_ids"].items():
        for task_id in identifiers:
            key = (domain, task_id)
            if key in cases:
                raise ValueError(f"Duplicate planned task: {key}")
            case_dir = directory / domain / task_id
            terminal_path = case_dir / "terminal.json"
            terminal = read_json(terminal_path) if terminal_path.exists() else None
            fields = {}
            warnings = []
            if terminal:
                if (terminal["domain"], terminal["task_id"]) != key:
                    raise ValueError(f"Terminal identity mismatch: {terminal_path}")
                fields = strict_score(terminal["predicted"], read_json(case_dir / "expected.json"))
            completed = terminal is not None and terminal["status"] == "completed"
            correct = completed and bool(fields) and all(fields.values())
            if terminal and correct != terminal.get("correct"):
                warnings.append("Recomputed strict score differs from recorded correct flag")
            seconds = terminal.get("elapsed_seconds") if terminal else None
            if seconds is not None and (
                not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0
            ):
                raise ValueError(f"Invalid case latency: {terminal_path}")
            cases[key] = {
                "domain": domain,
                "task_id": task_id,
                "terminal": terminal is not None,
                "status": terminal["status"]
                if terminal
                else ("partial" if case_dir.exists() else "not_started"),
                "completed": completed,
                "correct": correct,
                "field_correct": fields,
                "elapsed_seconds": seconds,
                "warnings": warnings,
                "accounting": account_audits(case_dir, terminal),
            }
    return manifest, cases


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    terminal = [case for case in cases if case["terminal"]]
    correct = sum(case["correct"] for case in terminal)
    return {
        "planned_cases": len(cases),
        "terminal_cases": len(terminal),
        "completed_cases": sum(case["completed"] for case in cases),
        "correct_cases": correct,
        "strict_accuracy_terminal": correct / len(terminal) if terminal else None,
        "all_planned_terminal": len(terminal) == len(cases),
        "status_counts": dict(Counter(case["status"] for case in cases)),
        "latency_seconds": latency(
            [case["elapsed_seconds"] for case in terminal if case["elapsed_seconds"] is not None]
        ),
        "accounting": sum_accounting([case["accounting"] for case in cases]),
    }


def compare(baseline: dict, candidate: dict) -> dict[str, Any]:
    keys = sorted(set(baseline) & set(candidate))
    paired = [key for key in keys if baseline[key]["terminal"] and candidate[key]["terminal"]]
    outcome_counts = Counter()
    comparisons = []
    deltas = []
    for key in paired:
        left, right = baseline[key], candidate[key]
        if left["correct"] and right["correct"]:
            outcome = "both_correct"
        elif right["correct"]:
            outcome = "improved"
        elif left["correct"]:
            outcome = "regressed"
        else:
            outcome = "both_incorrect"
        outcome_counts[outcome] += 1
        delta = None
        if left["elapsed_seconds"] is not None and right["elapsed_seconds"] is not None:
            delta = right["elapsed_seconds"] - left["elapsed_seconds"]
            deltas.append(delta)
        comparisons.append(
            {
                "domain": key[0],
                "task_id": key[1],
                "outcome": outcome,
                "baseline_status": left["status"],
                "candidate_status": right["status"],
                "candidate_minus_baseline_seconds": delta,
            }
        )
    return {
        "planned_overlap": len(keys),
        "terminal_pairs": len(paired),
        "unpaired_baseline": [list(key) for key in sorted(set(baseline) - set(candidate))],
        "unpaired_candidate": [list(key) for key in sorted(set(candidate) - set(baseline))],
        "pending_pairs": [list(key) for key in keys if key not in paired],
        "discordance_counts": {
            name: outcome_counts[name]
            for name in ("both_correct", "both_incorrect", "improved", "regressed")
        },
        "accuracy_delta": (outcome_counts["improved"] - outcome_counts["regressed"]) / len(paired)
        if paired
        else None,
        "candidate_minus_baseline_latency_seconds": latency(deltas),
        "baseline_paired": summarize([baseline[key] for key in paired]),
        "candidate_paired": summarize([candidate[key] for key in paired]),
        "cases": comparisons,
    }


def analyze(baseline_dir: Path, candidate_dir: Path) -> dict[str, Any]:
    baseline_manifest, baseline = load_arm(baseline_dir)
    candidate_manifest, candidate = load_arm(candidate_dir)
    for name in ("manifest_sha256", "split"):
        if baseline_manifest[name] != candidate_manifest[name]:
            raise ValueError(f"Cannot pair runs with different {name}")
    domains = sorted({key[0] for key in baseline} | {key[0] for key in candidate})
    return {
        "schema_version": 1,
        "baseline_dir": str(baseline_dir.resolve()),
        "candidate_dir": str(candidate_dir.resolve()),
        "split": baseline_manifest["split"],
        "source_manifest_sha256": baseline_manifest["manifest_sha256"],
        "prices_usd_per_million_tokens": {
            "llm_input_output": LLM_PRICES,
            "jev_input": JEV_INPUT_PRICE,
            "local_marginal_api_fee": 0.0,
        },
        "notes": [
            "All provider attempts, including failures and retries, come from audits only.",
            "LLM request means acompletion attempt; decision request means client.post attempted.",
            "Unknown usage/cost is not zero: cost_usd is null when accounting is incomplete.",
            "Local compute, electricity and hosting overhead are excluded from marginal API fees.",
            "Output tokens include reasoning; reasoning detail is never added again.",
            "Latency covers whole-case execution, including failures; percentiles interpolate.",
            "Paired comparisons use terminal records in both arms; pending cases remain explicit.",
            "Discordance counts are descriptive; no population guarantee or significance claimed.",
        ],
        "baseline": summarize(list(baseline.values())),
        "candidate": summarize(list(candidate.values())),
        "paired": compare(baseline, candidate),
        "by_domain": {
            domain: compare(
                {key: case for key, case in baseline.items() if key[0] == domain},
                {key: case for key, case in candidate.items() if key[0] == domain},
            )
            for domain in domains
        },
        "baseline_cases": list(baseline.values()),
        "candidate_cases": list(candidate.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(args.baseline, args.candidate)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "terminal_pairs": report["paired"]["terminal_pairs"],
                    "discordance_counts": report["paired"]["discordance_counts"],
                    "baseline": report["baseline"],
                    "candidate": report["candidate"],
                },
                indent=2,
            )
        )
    else:
        print(serialized, end="")


if __name__ == "__main__":
    main()
