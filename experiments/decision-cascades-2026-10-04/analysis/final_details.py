"""ABOUTME: Summarize per-field outcomes and recorded execution spans after all 720 runs finish."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

EXPECTED_COUNTS = {"referral_abuse_detection_v1": 160, "email_intent": 148, "patient_intake": 52}


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Recorded execution timestamp has no timezone")
    return result.astimezone(UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    parser.add_argument("--report", type=Path, default=here / "holdout-final.json")
    parser.add_argument("--output", type=Path, default=here / "holdout-final-details.json")
    parser.add_argument("--runs-root", type=Path, default=here.parent / "runs")
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    if report["split"] != "holdout" or report["paired"]["terminal_pairs"] != 360:
        raise ValueError("All 720 frozen final-holdout runs must be terminal first")
    fields, blocks = {}, []
    for domain, count in EXPECTED_COUNTS.items():
        if report["by_domain"][domain]["terminal_pairs"] != count:
            raise ValueError(f"Incomplete paired domain: {domain}")
        fields[domain] = {}
        for arm in ("baseline", "candidate"):
            cases = [c for c in report[f"{arm}_cases"] if c["domain"] == domain]
            if len(cases) != count or any(not c["terminal"] for c in cases):
                raise ValueError(f"Incomplete terminal cases: {domain}/{arm}")
            names = sorted({name for case in cases for name in case["field_correct"]})
            fields[domain][arm] = {
                name: {
                    "completed_and_field_correct": sum(
                        c["completed"] and c["field_correct"].get(name, False) for c in cases
                    ),
                    "field_matches_regardless_of_status": sum(
                        c["field_correct"].get(name, False) for c in cases
                    ),
                    "total_terminal_cases": count,
                    "missing_field_score_cases": sum(name not in c["field_correct"] for c in cases),
                }
                for name in names
            }
            starts, finishes = [], []
            for case in cases:
                source = Path(case["source_arm_dir"])
                path = source / domain / case["task_id"]
                if not path.exists():
                    path = (
                        args.runs_root / source.parent.name / source.name / domain / case["task_id"]
                    )
                started = json.loads((path / "started.json").read_text())
                terminal = json.loads((path / "terminal.json").read_text())
                for record in (started, terminal):
                    if (record["domain"], record["task_id"]) != (domain, case["task_id"]):
                        raise ValueError(f"Case timing identity mismatch: {path}")
                starts.append(timestamp(started["started_at"]))
                finishes.append(timestamp(terminal["finished_at"]))
            start, finish = min(starts), max(finishes)
            span = (finish - start).total_seconds()
            blocks.append(
                {
                    "domain": domain,
                    "arm": arm,
                    "terminal_cases": count,
                    "first_case_started_at_utc": start.isoformat(),
                    "last_case_finished_at_utc": finish.isoformat(),
                    "recorded_case_wall_span_seconds": span,
                    "terminal_cases_per_minute_of_block_span": count * 60 / span,
                    "sum_case_elapsed_seconds": sum(c["elapsed_seconds"] for c in cases),
                }
            )
    blocks.sort(key=lambda b: b["first_case_started_at_utc"])
    first = timestamp(blocks[0]["first_case_started_at_utc"])
    last = max(timestamp(b["last_case_finished_at_utc"]) for b in blocks)
    schedule = json.loads((args.runs_root / "holdout-final" / "schedule.json").read_text())
    created = timestamp(schedule["created_at"])
    span = (last - first).total_seconds()
    sum_blocks = sum(b["recorded_case_wall_span_seconds"] for b in blocks)
    result = {
        "all_720_terminal_prerequisite_satisfied": True,
        "canonical_report": str(args.report.resolve()),
        "field_success_counts": fields,
        "execution_blocks_in_recorded_order": blocks,
        "whole_schedule": {
            "schedule_created_at_utc": created.isoformat(),
            "first_case_started_at_utc": first.isoformat(),
            "last_case_finished_at_utc": last.isoformat(),
            "first_start_to_last_finish_seconds": span,
            "schedule_created_to_last_finish_seconds": (last - created).total_seconds(),
            "sum_block_case_wall_spans_seconds": sum_blocks,
            "interblock_gaps_seconds": span - sum_blocks,
            "terminal_runs": 720,
            "terminal_runs_per_minute_of_recorded_span": 720 * 60 / span,
        },
        "notes": [
            "Every terminal case is included, including incorrect and failed cases.",
            "Field success requires a completed run and a correct field, "
            "as in strict task scoring.",
            "Raw field matches regardless of status are also reported "
            "to expose partial-run matches.",
            "Block wall span is earliest recorded case start to latest recorded case finish.",
            "Block spans include four-case concurrency and the final tail, not pre-case setup.",
            "Schedule-created span includes startup and gaps, "
            "but excludes unrecorded final cleanup.",
            "Case latency, block wall time and throughput are different quantities.",
            "Sequential blocks and provider variation prevent causal throughput attribution.",
        ],
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
