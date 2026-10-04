"""ABOUTME: Render final held-out comparisons from complete, audited paired reports.

Run from any directory without changing the project environment or lockfile:
    uv run --no-project --python 3.12 --with matplotlib==3.11.2 /path/to/plot_final.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

DOMAIN_NAMES = {
    "referral_abuse_detection_v1": "Referral abuse",
    "email_intent": "Email intent",
    "patient_intake": "Patient intake",
}
EXPECTED_COUNTS = {"referral_abuse_detection_v1": 160, "email_intent": 148, "patient_intake": 52}
ARM_NAMES = {"baseline": "LLM baseline", "candidate": "Local completion gate + LLM"}
COLORS = {"baseline": "#376C9F", "candidate": "#BD6135"}


def load_complete_report(path: Path) -> dict:
    report = json.loads(path.read_text())
    if report["split"] != "holdout" or set(report["by_domain"]) != set(EXPECTED_COUNTS):
        raise ValueError("This figure requires the frozen three-domain final holdout.")
    if report["paired"]["terminal_pairs"] != sum(EXPECTED_COUNTS.values()):
        raise ValueError("Wait until all 360 pairs are terminal before rendering final figures.")
    for domain, count in EXPECTED_COUNTS.items():
        paired = report["by_domain"][domain]
        if paired["terminal_pairs"] != count or paired["pending_pairs"]:
            raise ValueError(f"Incomplete domain: {domain}")
        for arm in ARM_NAMES:
            summary = paired[f"{arm}_paired"]
            if not summary["all_planned_terminal"] or summary["terminal_cases"] != count:
                raise ValueError(f"Incomplete arm: {domain}/{arm}")
            if summary["accounting"]["cost_usd"] is None:
                raise ValueError(f"Unknown provider cost prevents a full-cost bar: {domain}/{arm}")
    return report


def save_figure(fig: plt.Figure, output: Path, name: str) -> list[str]:
    paths = []
    for suffix in ("svg", "png"):
        path = output / f"{name}.{suffix}"
        fig.savefig(path, dpi=160, facecolor="white", metadata={"Creator": "plot_final.py"})
        if suffix == "svg":
            lines = (line.rstrip() for line in path.read_text().splitlines())
            path.write_text("\n".join(lines) + "\n")
        paths.append(str(path.resolve()))
    plt.close(fig)
    return paths


def comparison_figure(report: dict, output: Path) -> list[str]:
    fig, axes = plt.subplots(1, 2, figsize=(11.4, 5.2))
    fig.subplots_adjust(left=0.075, right=0.985, bottom=0.30, top=0.75, wspace=0.25)
    domains = list(DOMAIN_NAMES)
    labels = []
    for domain in domains:
        paired = report["by_domain"][domain]
        b, c = paired["baseline_paired"], paired["candidate_paired"]
        labels.append(
            f"{DOMAIN_NAMES[domain]}\n"
            f"B {b['correct_cases']}/{b['terminal_cases']}\n"
            f"H {c['correct_cases']}/{c['terminal_cases']}"
        )
    for arm, offset in (("baseline", -0.18), ("candidate", 0.18)):
        summaries = [report["by_domain"][d][f"{arm}_paired"] for d in domains]
        costs = [100 * s["accounting"]["cost_usd"] / s["terminal_cases"] for s in summaries]
        means = [s["latency_seconds"]["mean"] for s in summaries]
        for ax, values, fmt in zip(axes, (costs, means), ("%.3f", "%.1f"), strict=True):
            bars = ax.bar(
                [i + offset for i in range(len(domains))],
                values,
                width=0.32,
                color=COLORS[arm],
                label=ARM_NAMES[arm],
            )
            ax.bar_label(bars, fmt=fmt, padding=4, fontsize=9)
            ax.set_xticks(range(len(domains)), labels, fontsize=9)
            ax.set_ylim(bottom=0)
            ax.margins(y=0.20)
            ax.grid(axis="y", alpha=0.18)
            ax.set_axisbelow(True)
            ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_title("Hosted list-price estimate per case", loc="left", fontweight="bold")
    axes[0].set_ylabel("US cents · cached input priced at full rate")
    axes[1].set_title("Mean whole-case latency", loc="left", fontweight="bold")
    axes[1].set_ylabel("Seconds")
    fig.suptitle("Final holdout: matched cases across three SOP domains", x=0.075, ha="left")
    fig.legend(
        *axes[0].get_legend_handles_labels(),
        loc="upper left",
        bbox_to_anchor=(0.067, 0.92),
        ncol=2,
        frameon=False,
    )
    fig.text(
        0.075,
        0.065,
        "All 360 cases per arm, including failures. "
        "Labels show strict accuracy: B = baseline; H = hybrid.\n"
        "Cost is a usage-based estimate, not an invoice; local compute is excluded.\n"
        "Arms ran in sequential blocks. "
        "Observed latency differences include provider and order effects.",
        fontsize=9,
        color="#555555",
        va="bottom",
    )
    return save_figure(fig, output, "holdout-domain-comparison")


def case_start(case: dict, runs_root: Path) -> datetime:
    arm = Path(case["source_arm_dir"])
    path = arm / case["domain"] / case["task_id"] / "started.json"
    if not path.exists():
        path = (
            runs_root
            / arm.parent.name
            / arm.name
            / case["domain"]
            / case["task_id"]
            / "started.json"
        )
    started = json.loads(path.read_text())
    if (started["domain"], started["task_id"]) != (case["domain"], case["task_id"]):
        raise ValueError(f"Start record identity mismatch: {path}")
    time = datetime.fromisoformat(started["started_at"])
    if time.tzinfo is None:
        raise ValueError(f"Start timestamp has no timezone: {path}")
    return time.astimezone(UTC)


def timeline_figure(report: dict, output: Path, runs_root: Path) -> list[str]:
    fig, axes = plt.subplots(3, 1, figsize=(11.4, 7.6))
    fig.subplots_adjust(left=0.085, right=0.98, top=0.83, bottom=0.14, hspace=0.64)
    for ax, domain in zip(axes, DOMAIN_NAMES, strict=True):
        for arm in ARM_NAMES:
            cases = [case for case in report[f"{arm}_cases"] if case["domain"] == domain]
            if len(cases) != EXPECTED_COUNTS[domain] or any(not c["terminal"] for c in cases):
                raise ValueError(f"Incomplete timeline cases: {domain}/{arm}")
            times = [case_start(case, runs_root) for case in cases]
            values = [case["elapsed_seconds"] for case in cases]
            ax.scatter(times, values, color=COLORS[arm], alpha=0.64, s=14, linewidths=0)
            wrong = [i for i, case in enumerate(cases) if not case["correct"]]
            if wrong:
                ax.scatter(
                    [times[i] for i in wrong],
                    [values[i] for i in wrong],
                    marker="x",
                    color="#151515",
                    s=35,
                    linewidths=1.3,
                    zorder=3,
                )
        ax.set_title(DOMAIN_NAMES[domain], loc="left", fontweight="bold", fontsize=11)
        ax.set_ylabel("Case seconds")
        ax.set_ylim(bottom=0)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=UTC))
        ax.set_xlabel("Actual case start time · UTC", fontsize=9)
        ax.grid(alpha=0.18)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
    handles = [
        Line2D([], [], marker="o", color=COLORS[a], linestyle="", label=ARM_NAMES[a])
        for a in ARM_NAMES
    ]
    handles.append(
        Line2D(
            [],
            [],
            marker="x",
            color="#151515",
            linestyle="",
            label="Strict score incorrect (any terminal status)",
        )
    )
    fig.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(0.077, 0.945),
        frameon=False,
        ncol=2,
        fontsize=9,
    )
    fig.suptitle(
        "Execution time reveals sequential arm blocks and latency variation",
        x=0.085,
        ha="left",
        y=0.98,
    )
    dates = sorted(
        {
            case_start(case, runs_root).date().isoformat()
            for arm in ARM_NAMES
            for case in report[f"{arm}_cases"]
        }
    )
    fig.text(
        0.085,
        0.035,
        f"UTC date(s): {', '.join(dates)}. Every terminal case is shown, including failures.\n"
        "Each domain has its own time scale. Four concurrent cases; arms did not overlap.\n"
        "Provider drift and execution order are confounded with arm; "
        "this is not a causal estimate.",
        fontsize=9,
        color="#555555",
    )
    return save_figure(fig, output, "holdout-latency-timeline")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    parser.add_argument("--report", type=Path, default=here / "holdout-final.json")
    parser.add_argument("--output-dir", type=Path, default=here / "figures")
    parser.add_argument("--runs-root", type=Path, default=here.parent / "runs")
    args = parser.parse_args()
    report = load_complete_report(args.report)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none"})
    paths = comparison_figure(report, args.output_dir)
    paths += timeline_figure(report, args.output_dir, args.runs_root)
    manifest = {
        "report": str(args.report.resolve()),
        "report_sha256": hashlib.sha256(args.report.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "matplotlib_version": matplotlib.__version__,
        "terminal_pairs": report["paired"]["terminal_pairs"],
        "includes_failed_cases": True,
        "cost_basis": report["cost_basis"],
        "figures": paths,
    }
    (args.output_dir / "figure-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
