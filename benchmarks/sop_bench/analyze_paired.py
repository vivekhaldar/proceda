"""ABOUTME: Strict paired SOP-Bench scoring and all-attempt provider-cost accounting.
ABOUTME: Reads immutable experiment artifacts; never calls a model or infers missing usage.
"""

from __future__ import annotations

import argparse
import json
import math
import random
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
BOOTSTRAP_SEED = 20261004
BOOTSTRAP_RESAMPLES = 2000
NATIVE_DURATION_FIELDS = ("total", "load", "eval")


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


def wilson_interval(successes: int, n: int) -> dict[str, Any]:
    """Nominal 95% Wilson interval, including failures in the observed denominator."""
    if n == 0:
        return {"n": 0, "successes": successes, "lower": None, "upper": None}
    z = 1.959963984540054
    proportion = successes / n
    denominator = 1 + z * z / n
    center = (proportion + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(proportion * (1 - proportion) / n + z * z / (4 * n * n))
    radius /= denominator
    return {
        "n": n,
        "successes": successes,
        "lower": max(0.0, center - radius),
        "upper": min(1.0, center + radius),
    }


def paired_bootstrap(differences: list[float], excluded_pairs: list[list[str]]) -> dict[str, Any]:
    """Resample matched case differences, retaining both arms of every sampled pair."""
    n = len(differences)
    result = {
        "n": n,
        "excluded_pair_count": len(excluded_pairs),
        "excluded_pairs": excluded_pairs,
        "mean_difference": statistics.mean(differences) if n else None,
        "lower": None,
        "upper": None,
        "confidence": 0.95,
        "resamples": BOOTSTRAP_RESAMPLES if n >= 2 else 0,
        "seed": BOOTSTRAP_SEED,
        "method": "paired percentile bootstrap of candidate minus baseline means",
        "insufficient_pairs": n < 2,
    }
    if n < 2:
        # A one-pair degenerate interval would imply unsupported precision.
        return result
    generator = random.Random(BOOTSTRAP_SEED)
    means = sorted(
        statistics.mean(generator.choices(differences, k=n)) for _ in range(BOOTSTRAP_RESAMPLES)
    )
    for field, quantile in (("lower", 0.025), ("upper", 0.975)):
        position = (len(means) - 1) * quantile
        lower = int(position)
        result[field] = means[lower] + (means[lower + 1] - means[lower]) * (position - lower)
    return result


def discordant_test(improved: int, regressed: int) -> dict[str, Any]:
    """Exact two-sided McNemar/binomial test; lack of significance is not equivalence."""
    n = improved + regressed
    probability = (
        min(1.0, 2 * sum(math.comb(n, k) for k in range(min(improved, regressed) + 1)) / (2**n))
        if n
        else 1.0
    )
    return {"discordant_pairs": n, "two_sided_p": probability, "method": "exact McNemar"}


def new_accounting() -> dict[str, Any]:
    return {
        "audit_records": 0,
        "llm_requests": 0,
        "decision_requests": 0,
        "local_decision_requests": 0,
        **{f"local_native_{field}_seconds": None for field in NATIVE_DURATION_FIELDS},
        **{f"local_native_{field}_known_calls": 0 for field in NATIVE_DURATION_FIELDS},
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
            if record["kind"] == "ollaya":
                result["local_decision_requests"] += 1
                for field in NATIVE_DURATION_FIELDS:
                    duration = (
                        count(raw.get(f"{field}_duration")) if isinstance(raw, dict) else None
                    )
                    if duration is not None:
                        key = f"local_native_{field}_seconds"
                        result[key] = (result[key] or 0.0) + duration / 1_000_000_000
                        result[f"local_native_{field}_known_calls"] += 1
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
            elif key.startswith("local_native_") and key.endswith("_seconds"):
                if item[key] is not None:
                    result[key] = (result[key] or 0.0) + item[key]
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
        "strict_accuracy_wilson95": wilson_interval(correct, len(terminal)),
        "all_planned_terminal": len(terminal) == len(cases),
        "status_counts": dict(Counter(case["status"] for case in cases)),
        "latency_seconds": latency(
            [case["elapsed_seconds"] for case in terminal if case["elapsed_seconds"] is not None]
        ),
        "accounting": sum_accounting([case["accounting"] for case in cases]),
    }


def local_active_seconds(accounting: dict[str, Any]) -> float | None:
    """Zero only for no local calls; missing native duration is not a zero observation."""
    calls = accounting["local_decision_requests"]
    if not calls:
        return 0.0
    if accounting["local_native_total_known_calls"] != calls:
        return None
    return accounting["local_native_total_seconds"]


def local_break_even(baseline: dict, candidate: dict, paired: list) -> dict[str, Any]:
    """Observed API savings per incremental native active hour, not a TCO estimate."""
    savings = 0.0
    additional_seconds = 0.0
    excluded = []
    for key in paired:
        left, right = baseline[key]["accounting"], candidate[key]["accounting"]
        left_seconds, right_seconds = local_active_seconds(left), local_active_seconds(right)
        if any(
            value is None
            for value in (left["cost_usd"], right["cost_usd"], left_seconds, right_seconds)
        ):
            excluded.append(list(key))
            continue
        savings += left["cost_usd"] - right["cost_usd"]
        additional_seconds += right_seconds - left_seconds
    included = len(paired) - len(excluded)
    return {
        "included_pairs": included,
        "excluded_pair_count": len(excluded),
        "excluded_pairs": excluded,
        "hosted_api_savings_usd": savings if included else None,
        "additional_local_native_total_seconds": additional_seconds if included else None,
        "usd_per_additional_active_model_hour": savings * 3600 / additional_seconds
        if included and additional_seconds > 0 and savings >= 0
        else None,
        "positive_api_savings": savings > 0 if included else None,
        "interpretation": (
            "Observed hosted list-price savings divided by additional summed local native total "
            "duration. Active time is not provisioned model-hours; idle hardware, electricity, "
            "hosting overhead and TCO are unmeasured. Total includes its components; do not add "
            "load or eval duration again. Unknown cost or local duration excludes the pair."
        ),
    }


def compare(baseline: dict, candidate: dict) -> dict[str, Any]:
    keys = sorted(set(baseline) & set(candidate))
    paired = [key for key in keys if baseline[key]["terminal"] and candidate[key]["terminal"]]
    outcome_counts = Counter()
    comparisons = []
    deltas = []
    cost_deltas = []
    excluded_latency = []
    excluded_cost = []
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
        else:
            excluded_latency.append(list(key))
        left_cost = left["accounting"]["cost_usd"]
        right_cost = right["accounting"]["cost_usd"]
        cost_delta = None
        if left_cost is not None and right_cost is not None:
            cost_delta = right_cost - left_cost
            cost_deltas.append(cost_delta)
        else:
            excluded_cost.append(list(key))
        comparisons.append(
            {
                "domain": key[0],
                "task_id": key[1],
                "outcome": outcome,
                "baseline_status": left["status"],
                "candidate_status": right["status"],
                "candidate_minus_baseline_seconds": delta,
                "candidate_minus_baseline_cost_usd": cost_delta,
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
        "paired_accuracy_test": discordant_test(
            outcome_counts["improved"], outcome_counts["regressed"]
        ),
        "mean_latency_difference_bootstrap95": paired_bootstrap(deltas, excluded_latency),
        "mean_cost_difference_bootstrap95": paired_bootstrap(cost_deltas, excluded_cost),
        "local_active_model_hour_break_even": local_break_even(baseline, candidate, paired),
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
            "Local native durations convert Ollaya nanoseconds to seconds; missing is null.",
            "Native total/load/eval known-call counts are separate; components are not summed.",
            "Output tokens include reasoning; reasoning detail is never added again.",
            "Latency covers whole-case execution, including failures; percentiles interpolate.",
            "Paired comparisons use terminal records in both arms; pending cases remain explicit.",
            "Discordance counts are descriptive; no population guarantee or significance claimed.",
            "Wilson and paired bootstrap intervals are nominal 95%, with case-level independence.",
            "Correlated cases or shared entities can make these intervals too narrow.",
            "Small accuracy differences and nonsignificant tests do not establish equivalence.",
            "Bootstrap intervals use 2000 seeded matched-pair resamples and exclude unknown costs.",
            "Bootstrap captures observed case variation, not repeat-run/provider or order effects.",
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
