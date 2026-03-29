# ABOUTME: Generates comparison report for cache evaluation runs.
# ABOUTME: Reads baseline/L1/L2 results and produces markdown with accuracy and savings tables.

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

DOMAINS = [
    "dangerous_goods",
    "video_classification",
    "customer_service",
    "traffic_spoofing_detection",
]
MODES = ["baseline", "level1", "level2"]


def load_results(eval_dir: Path, mode: str, domain: str) -> dict | None:
    """Load results.json for a given mode and domain."""
    results_path = eval_dir / mode / domain / "results.json"
    if not results_path.exists():
        return None
    with open(results_path) as f:
        return json.load(f)


def count_trace_events(eval_dir: Path, mode: str, domain: str) -> dict:
    """Count LLM calls, tokens, and cache events from traces."""
    traces_dir = eval_dir / mode / domain / "traces"
    totals = {
        "total_tokens": 0,
        "llm_calls": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_fallbacks": 0,
        "trace_count": 0,
    }

    if not traces_dir.exists():
        return totals

    for trace_file in traces_dir.glob("*.jsonl"):
        totals["trace_count"] += 1
        with open(trace_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "_meta" in evt:
                    continue
                evt_type = evt.get("type", "")
                payload = evt.get("payload", {})

                if evt_type == "llm.usage":
                    totals["llm_calls"] += 1
                    tokens = payload.get("total_tokens")
                    if tokens and tokens != "**REDACTED**":
                        try:
                            totals["total_tokens"] += int(tokens)
                        except (ValueError, TypeError):
                            pass
                elif evt_type == "cache.hit":
                    totals["cache_hits"] += 1
                elif evt_type == "cache.miss":
                    totals["cache_misses"] += 1
                elif evt_type == "cache.fallback":
                    totals["cache_fallbacks"] += 1

    return totals


def find_regressions(baseline_results: dict, cached_results: dict, mode_label: str) -> list[dict]:
    """Find tasks that were correct in baseline but wrong in cached run."""
    baseline_tasks = {t["task_id"]: t for t in baseline_results.get("tasks", [])}
    cached_tasks = {t["task_id"]: t for t in cached_results.get("tasks", [])}

    regressions = []
    for task_id, bt in baseline_tasks.items():
        ct = cached_tasks.get(task_id)
        if bt.get("is_correct") and ct and not ct.get("is_correct"):
            regressions.append(
                {
                    "task_id": task_id,
                    "mode": mode_label,
                    "baseline_predicted": bt.get("predicted"),
                    "cached_predicted": ct.get("predicted"),
                    "expected": bt.get("expected"),
                }
            )
    return regressions


def generate_report(eval_dir: Path) -> str:
    """Generate the full markdown report."""
    lines = [
        "# Cache Evaluation Report",
        "",
        f"**Date**: {eval_dir.name}",
        f"**Generated**: {datetime.now().isoformat()}",
        "",
        "## Accuracy Comparison",
        "",
        "| Domain | Mode | Tasks | TSR | ECR | C-TSR |",
        "|--------|------|-------|-----|-----|-------|",
    ]

    all_regressions = []

    for domain in DOMAINS:
        for mode in MODES:
            data = load_results(eval_dir, mode, domain)
            if data is None:
                lines.append(f"| {domain} | {mode} | - | - | - | - |")
                continue
            m = data["metrics"]
            lines.append(
                f"| {domain} | {mode} | {m['num_tasks']} | "
                f"{m['tsr']:.3f} | {m['ecr']:.3f} | {m['c_tsr']:.3f} |"
            )

            if mode != "baseline":
                baseline = load_results(eval_dir, "baseline", domain)
                if baseline:
                    regs = find_regressions(baseline, data, mode)
                    all_regressions.extend(regs)

    # Token and LLM call savings
    lines.extend(
        [
            "",
            "## Token & LLM Call Savings",
            "",
            "| Domain | Mode | Tokens | LLM Calls | Hits | Fallbacks | Tok Sav | Call Sav |",
            "|--------|------|--------|-----------|------|-----------|---------|----------|",
        ]
    )

    for domain in DOMAINS:
        baseline_stats = count_trace_events(eval_dir, "baseline", domain)
        for mode in MODES:
            stats = count_trace_events(eval_dir, mode, domain)
            token_savings = ""
            call_savings = ""
            if mode != "baseline" and baseline_stats["total_tokens"] > 0:
                ts = (
                    (baseline_stats["total_tokens"] - stats["total_tokens"])
                    / baseline_stats["total_tokens"]
                    * 100
                )
                token_savings = f"{ts:.1f}%"
            if mode != "baseline" and baseline_stats["llm_calls"] > 0:
                cs = (
                    (baseline_stats["llm_calls"] - stats["llm_calls"])
                    / baseline_stats["llm_calls"]
                    * 100
                )
                call_savings = f"{cs:.1f}%"
            lines.append(
                f"| {domain} | {mode} | {stats['total_tokens']:,} | "
                f"{stats['llm_calls']} | {stats['cache_hits']} | "
                f"{stats['cache_fallbacks']} | {token_savings} | {call_savings} |"
            )

    # Regression analysis
    lines.extend(
        [
            "",
            "## Regression Analysis",
            "",
        ]
    )

    if all_regressions:
        lines.append(f"**{len(all_regressions)} regressions found:**")
        lines.append("")
        lines.append("| Task ID | Mode | Expected | Baseline Predicted | Cached Predicted |")
        lines.append("|---------|------|----------|-------------------|-----------------|")
        for r in all_regressions:
            lines.append(
                f"| {r['task_id']} | {r['mode']} | {r['expected']} | "
                f"{r['baseline_predicted']} | {r['cached_predicted']} |"
            )
    else:
        lines.append(
            "No regressions found. All tasks correct in baseline remain correct with caching."
        )

    # Recommendations
    lines.extend(
        [
            "",
            "## Recommendations",
            "",
        ]
    )

    # Check hard gate
    hard_gate_failed = False
    for domain in DOMAINS:
        baseline = load_results(eval_dir, "baseline", domain)
        if not baseline:
            continue
        for mode in ["level1", "level2"]:
            cached = load_results(eval_dir, mode, domain)
            if not cached:
                continue
            baseline_tsr = baseline["metrics"]["tsr"]
            cached_tsr = cached["metrics"]["tsr"]
            if baseline_tsr - cached_tsr > 0.02:
                lines.append(
                    f"- **HARD GATE FAILED**: {domain} {mode} TSR dropped by "
                    f"{(baseline_tsr - cached_tsr) * 100:.1f}% (>{2}% threshold)"
                )
                hard_gate_failed = True

    if not hard_gate_failed:
        lines.append("- All domains pass the 2% TSR regression hard gate.")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare cache evaluation results")
    parser.add_argument(
        "eval_dir",
        help="Path to evaluation directory (e.g., results/cache_eval/2026-03-29)",
    )
    args = parser.parse_args()

    eval_dir = Path(args.eval_dir)
    if not eval_dir.exists():
        print(f"Error: {eval_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    report = generate_report(eval_dir)

    report_path = eval_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"Report written to {report_path}")
    print()
    print(report)


if __name__ == "__main__":
    main()
