"""Offline strict paired metrics and receipt-backed completion-gate evidence.

Never performs inference, edits frozen inputs, or overwrites an analysis snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
EXPERIMENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from benchmarks.sop_bench.analyze_paired import analyze  # noqa: E402


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def inspect_case(case, skill):
    terminal, inputs = read(case / "terminal.json"), read(case / "inputs.json")
    events = rows(case / "events.jsonl")
    parts = re.split(r"^### Step (\d+):[^\n]*\n", skill.read_text(), flags=re.MULTILINE)
    instructions = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts), 2)}
    step, batch = None, 0
    calls, observed, gates, autos, stages, issues = [], [], [], [], [], []
    immutable_receipts, llm_by_step, summaries = {}, Counter(), []
    for index, event in enumerate(events):
        kind, payload = event["type"], event["payload"]
        if kind == "step.started":
            step = payload["step_index"]
        elif kind == "llm.usage":
            batch += 1
            llm_by_step[str(payload["step_index"])] += 1
        elif kind == "tool.called":
            calls.append({**payload, "step_index": step, "batch": batch, "line": index + 1})
        elif kind == "tool.completed":
            matches = [
                c
                for c in calls
                if c["tool_call_id"] == payload["tool_call_id"]
                and c["tool_name"] == payload["tool_name"]
                and c["step_index"] == step
            ]
            if matches:
                observed.append({**matches[-1], "result": decoded(payload["result"])})
            else:
                issues.append(f"Unmatched tool completion at events line {index + 1}")
        elif kind == "summary.generated":
            summaries.append(
                {
                    "step_index": payload["step_index"],
                    "summary": payload["summary"],
                    "event_line": index + 1,
                }
            )
        elif kind == "decision.evaluated" and payload.get("purpose") == "completion":
            gate = {
                "step_index": step,
                "event_line": index + 1,
                "value": payload["value"],
                "reason": payload["reason"],
                "probabilities": payload.get("probabilities"),
                "stage_audits": [],
                "receipt_checks": [],
                "current_receipts": [],
                "fresh_receipt_ids": [],
                "issues": [],
            }
            gates.append(gate)
            if payload["step_index"] != step:
                gate["issues"].append("Event step differs from active step")
            for stage in payload.get("stages", []):
                path = case / "providers" / "decisions" / f"decision-{stage['call_id']}.json"
                gate["stage_audits"].append(str(path))
                audit = read(path) if path.is_file() else None
                if audit is None:
                    gate["issues"].append(f"Missing decision audit {path}")
                    continue
                response = audit.get("response")
                truncated = response.get("state_truncated") if isinstance(response, dict) else None
                stages.append(
                    {
                        "step_index": step,
                        "audit": str(path),
                        "outcome": audit.get("outcome"),
                        "reason": audit.get("reason"),
                        "model": audit.get("model"),
                        "kind": audit.get("kind"),
                        "threshold": audit.get("threshold"),
                        "state_truncated": truncated,
                        "request_sent": audit.get("request_sent"),
                        "input_chars": audit.get("input_chars"),
                        "input_tokens": (audit.get("usage") or {}).get("input_tokens"),
                    }
                )
                for key in ("outcome", "reason", "threshold", "call_id"):
                    if audit.get(key) != stage.get(key):
                        gate["issues"].append(f"Stage/audit {key} mismatch: {path.name}")
                if payload["value"] is not True or audit.get("outcome") != "accepted":
                    continue
                threshold, probabilities = audit["threshold"], audit.get("probabilities", {})
                if not probabilities or any(p < threshold for p in probabilities.values()):
                    gate["issues"].append("Accepted probabilities fail the recorded threshold")
                if truncated is True or (audit.get("kind") == "ollaya" and truncated is not False):
                    gate["issues"].append("Accepted response violates provider truncation guard")
                state = audit["request"]["state"]
                evidence = state["observed_evidence"]
                if evidence["inputs"] != inputs:
                    gate["issues"].append("Gate inputs differ from saved inputs")
                if state["step_instructions"] != instructions.get(step):
                    gate["issues"].append("Gate instructions differ from frozen SOP")
                receipts = evidence["receipts"]
                if len({r["receipt_id"] for r in receipts}) != len(receipts):
                    gate["issues"].append("Duplicate receipt identity")
                for receipt in receipts:
                    rid = receipt["receipt_id"]
                    core = {k: v for k, v in receipt.items() if k != "current_step"}
                    if rid in immutable_receipts and immutable_receipts[rid] != core:
                        gate["issues"].append(f"Immutable receipt changed: {rid}")
                    immutable_receipts[rid] = core
                    matches = [
                        o
                        for o in observed
                        if o["tool_call_id"] == receipt["tool_call_id"]
                        and o["tool_name"] == receipt["qualified_tool_name"]
                        and o["arguments"] == receipt["arguments"]
                        and o["result"] == receipt["result"]
                        and (o["step_index"] == step) == receipt["current_step"]
                    ]
                    valid = (
                        bool(matches)
                        and receipt["persisted"] is True
                        and receipt["is_error"] is False
                    )
                    gate["receipt_checks"].append(
                        {
                            "receipt_id": rid,
                            "backed_by_prior_tool_event": valid,
                            "tool_event_lines": [o["line"] for o in matches],
                        }
                    )
                    if not valid:
                        gate["issues"].append(
                            f"Receipt not backed by successful prior event: {rid}"
                        )
                    if receipt["current_step"]:
                        gate["current_receipts"].append(receipt)
                        if any(o["batch"] == batch for o in matches):
                            gate["fresh_receipt_ids"].append(rid)
                gate["semantic_review_scope"] = (
                    "Receipt checks establish executed tools, arguments and returned data. "
                    "They do not certify every prose obligation; delegated risk calculation "
                    "need not expose its "
                    "internal weights unless the SOP requires that artifact."
                )
            if payload["value"] is True and not gate["current_receipts"]:
                gate["issues"].append("Accepted gate lacks current-step receipt evidence")
        elif kind == "decision.step_auto_completed":
            candidates = [
                g
                for g in gates
                if g["step_index"] == payload["step_index"]
                and g["value"] is True
                and "auto_event_line" not in g
            ]
            auto = {
                "step_index": payload["step_index"],
                "event_line": index + 1,
                "receipt_ids": payload["receipt_ids"],
                "direct_continuation_bypass_verified": False,
            }
            autos.append(auto)
            if not candidates:
                issues.append(f"Auto completion has no accepted gate at line {index + 1}")
                continue
            gate = candidates[-1]
            gate["auto_event_line"] = index + 1
            between = events[gate["event_line"] : index]
            if any(e["type"] in {"llm.usage", "step.started"} for e in between):
                gate["issues"].append(
                    "Generation or next-step start between acceptance and auto completion"
                )
            else:
                auto["direct_continuation_bypass_verified"] = True
            if set(payload["receipt_ids"]) != set(gate["fresh_receipt_ids"]):
                gate["issues"].append(
                    "Auto-completion receipt links differ from the fresh tool batch"
                )
    for gate in gates:
        if gate["value"] is True and "auto_event_line" not in gate:
            gate["issues"].append("Accepted gate has no auto-completion event")
    authority = []
    for path in sorted((case / "providers").glob("*.json")):
        messages = read(path).get("request", {}).get("messages", [])
        if re.search(
            r"configured decision gate|Original tool receipts:|receipt_[0-9a-f]{16,}",
            json.dumps(messages),
        ):
            authority.append(str(path))
    if authority:
        issues.append("Synthetic gate authority or receipt identity found in LLM context")
    return {
        "domain": terminal["domain"],
        "task_id": terminal["task_id"],
        "recorded_correct": terminal["correct"],
        "predicted": terminal["predicted"],
        "case": str(case),
        "terminal": str(case / "terminal.json"),
        "events": str(case / "events.jsonl"),
        "skill": str(skill),
        "gates": gates,
        "stages": stages,
        "auto_completions": autos,
        "llm_success_usage_events_by_step": dict(llm_by_step),
        "observed_tools": observed,
        "step_summaries": summaries,
        "authority_contexts": authority,
        "issues": issues,
    }


def supplemental(cases, metrics):
    result = {}
    for domain, comparison in metrics["by_domain"].items():
        selected = [c for c in cases if c["domain"] == domain]
        gates = [g for c in selected for g in c["gates"]]
        stages = [s for c in selected for s in c["stages"]]
        steps = sorted({g["step_index"] for g in gates})
        paired_ids = {c["task_id"] for c in comparison["cases"]}
        paired_cases = [c for c in selected if c["task_id"] in paired_ids]
        bypasses = sum(
            a["direct_continuation_bypass_verified"]
            for c in paired_cases
            for a in c["auto_completions"]
        )
        baseline = comparison["baseline_paired"]["accounting"]["llm_requests"]
        candidate = comparison["candidate_paired"]["accounting"]["llm_requests"]
        result[domain] = {
            "reviewed_terminal_candidate_cases": len(selected),
            "accepted_gates": sum(g["value"] is True for g in gates),
            "by_step": {
                str(step): {
                    "outcomes": dict(
                        Counter(
                            "accepted"
                            if g["value"] is True
                            else "rejected"
                            if g["value"] is False
                            else "unsettled"
                            for g in gates
                            if g["step_index"] == step
                        )
                    ),
                    "stage_reasons": dict(
                        Counter(s["reason"] for s in stages if s["step_index"] == step)
                    ),
                    "state_truncated_true": sum(
                        s["state_truncated"] is True for s in stages if s["step_index"] == step
                    ),
                    "state_truncated_absent_or_null": sum(
                        s["state_truncated"] is None for s in stages if s["step_index"] == step
                    ),
                }
                for step in steps
            },
            "stage_reasons": dict(Counter(s["reason"] for s in stages)),
            "stage_outcomes": dict(Counter(s["outcome"] for s in stages)),
            "observed_probability_threshold_coverage": {
                "interpretation": (
                    "Score-only counts for the checks actually observed at threshold 0.8. "
                    "This is not a rerun or a prediction of accuracy, latency, cost, or "
                    "future check coverage under another threshold."
                ),
                "checks_with_probabilities": sum(bool(g["probabilities"]) for g in gates),
                "would_clear_both_probabilities": {
                    str(threshold): sum(
                        bool(g["probabilities"])
                        and all(p >= threshold for p in g["probabilities"].values())
                        for g in gates
                    )
                    for threshold in (0.8, 0.9, 0.95, 0.97)
                },
            },
            "truncated_stage_evidence": [
                s["audit"] for s in stages if s["state_truncated"] is True
            ],
            "paired_call_comparison": {
                "terminal_pairs": comparison["terminal_pairs"],
                "baseline_llm_attempts": baseline,
                "candidate_llm_attempts": candidate,
                "net_fewer_llm_attempts": baseline - candidate,
                "direct_continuation_bypasses": bypasses,
                "net_fewer_attempts_minus_direct_bypasses": baseline - candidate - bypasses,
                "interpretation": (
                    "Each verified auto completion bypasses the ordinary next LLM continuation "
                    "at that point. Net arm request differences also include different generation "
                    "trajectories, failures and retries; the two quantities need not agree."
                ),
            },
            "cases_with_structural_flags": sum(
                bool(c["issues"] or any(g["issues"] for g in c["gates"])) for c in selected
            ),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dirs", type=Path, nargs="+", required=True)
    parser.add_argument("--baseline-arm", default="baseline")
    parser.add_argument("--candidate-arm", default="hybrid")
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    outputs = {name: Path(f"{args.output_prefix}-{name}.json") for name in ("paired", "completion")}
    if any(p.exists() for p in outputs.values()):
        raise SystemExit("Refusing to overwrite an analysis snapshot; choose a new output prefix")
    run_dirs = [p.resolve() for p in args.run_dirs]
    markers = {
        str(p): {
            name: (p / name).is_file() for name in ("completed.json", "failed.json", "aborted.json")
        }
        for p in run_dirs
    }
    completed_markers = all(
        m["completed.json"] and not m["failed.json"] and not m["aborted.json"]
        for m in markers.values()
    )
    if args.require_complete and not completed_markers:
        raise SystemExit("All clean completion markers are required for a final report")
    metrics = analyze(
        [p / args.baseline_arm for p in run_dirs], [p / args.candidate_arm for p in run_dirs]
    )
    complete = (
        completed_markers
        and metrics["baseline"]["all_planned_terminal"]
        and metrics["candidate"]["all_planned_terminal"]
    )
    if args.require_complete and not complete:
        raise SystemExit("All planned terminal cases are required for a final report")
    cases = []
    for run in run_dirs:
        schedule = read(run / "schedule.json")
        arm = run / args.candidate_arm
        for domain, ids in read(arm / "run.json")["task_ids"].items():
            skill = Path(schedule["skill_paths"][domain])
            for task_id in ids:
                case = arm / domain / task_id
                if (case / "terminal.json").is_file():
                    cases.append(inspect_case(case, skill))
    # Primary metrics set the snapshot population; later-finishing cases wait for another snapshot.
    population = {(c["domain"], c["task_id"]) for c in metrics["candidate_cases"] if c["terminal"]}
    cases = [c for c in cases if (c["domain"], c["task_id"]) in population]
    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "analysis_status": "complete" if complete else "partial_snapshot_not_full_comparison",
        "run_markers": markers,
        "paired_metrics": str(outputs["paired"].resolve()),
        "analyzer_sha256": digest(Path(__file__)),
        "strict_metrics_analyzer_sha256": digest(REPO / "benchmarks/sop_bench/analyze_paired.py"),
        "schedule_evidence": [
            {"path": str(p / "schedule.json"), "sha256": digest(p / "schedule.json")}
            for p in run_dirs
        ],
        "notes": [
            "Strict output scoring and all-attempt accounting are reused unchanged "
            "from analyze_paired.py.",
            "Only immutable terminal candidate cases are inspected; live snapshots "
            "can have pending or unpaired cases.",
            "Provider contract: state_truncated=true disallows acceptance. "
            "Ollaya requires explicit false; Jev permits an absent flag.",
            "Absent Jev truncation metadata is not evidence of zero server-side truncation.",
            "Tool receipts establish observable execution, not fulfillment of "
            "every prose documentation obligation.",
            "Planning is disabled in these runs; the phase2 directory name "
            "identifies the reused schedule driver.",
            "The original holdout was reused and is not an untouched confirmation set.",
        ],
        "domains": supplemental(cases, metrics),
        "cases": cases,
    }
    report["structural_flags"] = [
        {
            "domain": c["domain"],
            "task_id": c["task_id"],
            "case": c["case"],
            "issues": c["issues"],
            "gate_issues": [
                {"step": g["step_index"], "issues": g["issues"]} for g in c["gates"] if g["issues"]
            ],
        }
        for c in cases
        if c["issues"] or any(g["issues"] for g in c["gates"])
    ]
    for name, value in (("paired", metrics), ("completion", report)):
        outputs[name].parent.mkdir(parents=True, exist_ok=True)
        with outputs[name].open("x") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
    print(
        json.dumps(
            {
                "outputs": {k: str(v) for k, v in outputs.items()},
                "analysis_status": report["analysis_status"],
                "terminal_pairs": metrics["paired"]["terminal_pairs"],
                "domains": report["domains"],
                "structural_flag_count": len(report["structural_flags"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
