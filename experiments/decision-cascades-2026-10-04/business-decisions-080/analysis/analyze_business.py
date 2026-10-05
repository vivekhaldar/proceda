"""Offline four-arm accounting and trace attribution for bounded business decisions.

Reads immutable completed runs; never calls providers or edits source artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from benchmarks.sop_bench.analyze_paired import analyze, load_arms, summarize  # noqa: E402
from benchmarks.sop_bench.paired_experiment import strict_score  # noqa: E402


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def inspect_case(directory):
    terminal = read(directory / "terminal.json")
    expected = read(directory / "expected.json")
    events = rows(directory / "events.jsonl")
    decisions, facts, actions, fallbacks, gates, plans, issues = [], [], [], [], [], [], []
    llm_steps, tools = Counter(), Counter()
    for line, event in enumerate(events, 1):
        payload, kind = event["payload"], event["type"]
        if kind == "llm.usage":
            llm_steps[str(payload.get("step_index"))] += 1
        elif kind == "tool.called":
            tools[payload["tool_name"]] += 1
        elif kind == "decision.evaluated":
            record = {**payload, "event_line": line}
            if payload.get("purpose") in {"planned_choice", "planned_predicate"}:
                decisions.append(record)
                for stage in payload.get("stages", []):
                    path = directory / "providers/decisions" / f"decision-{stage['call_id']}.json"
                    if not path.is_file():
                        issues.append(f"Missing native decision audit: {path}")
                        continue
                    audit = read(path)
                    for key in ("call_id", "outcome", "threshold"):
                        if audit.get(key) != stage.get(key):
                            issues.append(f"Stage/audit {key} mismatch: {path}")
            else:
                gates.append(record)
        elif kind == "planning.fact_recorded":
            fact = {**payload["fact"], "event_line": line}
            field = fact["field"]
            fact["reference_match"] = (
                strict_score({field: fact["value"]}, {field: expected[field]})[field]
                if field in expected
                else None
            )
            facts.append(fact)
        elif kind == "planning.action":
            actions.append({**payload, "event_line": line})
        elif kind == "planning.fallback":
            fallbacks.append({**payload, "event_line": line})
        elif kind == "planning.prepared":
            plans.append(payload)
    provider_roles = Counter()
    business_ids = {
        stage["call_id"] for decision in decisions for stage in decision.get("stages", [])
    }
    completion_ids = {stage["call_id"] for gate in gates for stage in gate.get("stages", [])}
    for path in sorted((directory / "providers").rglob("*.json")):
        audit = read(path)
        if "outcome" in audit:
            if audit.get("request_sent") is not True:
                provider_roles["decision_preflight"] += 1
                continue
            call_id = audit["call_id"]
            role = (
                "business_native"
                if call_id in business_ids
                else "completion_native"
                if call_id in completion_ids
                else "unlinked_native"
            )
            if role == "unlinked_native":
                issues.append(f"Unlinked native request: {path}")
        else:
            tools_requested = audit.get("request", {}).get("tools") or []
            constrained = any(
                tool.get("function", {}).get("name") == "record_decision"
                for tool in tools_requested
            )
            role = "business_constrained_llm" if constrained else "ordinary_llm"
        provider_roles[role] += 1
    native_facts = [f for f in facts if f["origin"] in {"native_choice", "native_predicate"}]
    business_actions = [a for a in actions if a["kind"] == "decision"]
    for fact in native_facts:
        if not any(
            a["step_index"] == fact["step_index"] and a.get("origin") == fact["origin"]
            for a in business_actions
        ):
            issues.append("Native fact has no matching business action")
    return {
        "domain": terminal["domain"],
        "task_id": terminal["task_id"],
        "case": str(directory.resolve()),
        "correct": terminal["correct"],
        "predicted": terminal["predicted"],
        "expected": expected,
        "business_decisions": decisions,
        "facts": facts,
        "actions": actions,
        "fallbacks": fallbacks,
        "completion_gates": gates,
        "completion_bypasses": [
            e["payload"] for e in events if e["type"] == "decision.step_auto_completed"
        ],
        "plans": plans,
        "llm_usage_events_by_step": dict(llm_steps),
        "tool_calls": dict(tools),
        "provider_roles": dict(provider_roles),
        "issues": issues,
    }


def attribution(cases):
    counts, by_step, failures = Counter(), defaultdict(Counter), []
    for case in cases:
        counts["cases"] += 1
        counts["correct"] += case["correct"]
        counts["completion_bypasses"] += len(case["completion_bypasses"])
        counts.update({f"provider:{key}": value for key, value in case["provider_roles"].items()})
        for fact in case["facts"]:
            if fact["origin"] == "regex":
                continue
            label = f"business_fact:{fact['origin']}"
            counts[label] += 1
            if fact["reference_match"] is not None:
                counts[f"{label}:reference_comparable"] += 1
                counts[f"{label}:reference_correct"] += fact["reference_match"]
        for action in case["actions"]:
            label = f"action:{action['kind']}:{action.get('origin', '')}"
            counts[label] += 1
            by_step[str(action["step_index"])][label] += 1
        for decision in case["business_decisions"]:
            label = f"business_result:{decision['purpose']}:{decision.get('reason', '')}"
            counts[label] += 1
            by_step[str(decision["step_index"])][label] += 1
            for stage in decision.get("stages", []):
                counts[f"business_stage:{stage.get('outcome')}"] += 1
        for gate in case["completion_gates"]:
            for stage in gate.get("stages", []):
                counts[f"completion_stage:{stage.get('outcome')}"] += 1
        for fallback in case["fallbacks"]:
            counts[f"fallback:{fallback['reason']}"] += 1
        if not case["correct"]:
            failures.append(
                {
                    k: case[k]
                    for k in ("domain", "task_id", "case", "predicted", "expected", "facts")
                }
            )
    return {
        "counts": dict(counts),
        "by_step": {k: dict(v) for k, v in by_step.items()},
        "failures": failures,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument(
        "--arms", nargs=4, default=["baseline", "completion", "business-qwen", "business-jev"]
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite analysis snapshot")
    output = {
        "created_at": datetime.now(UTC).isoformat(),
        "runs": [str(p.resolve()) for p in args.runs],
        "schedule_hashes": {
            str(p): hashlib.sha256((p / "schedule.json").read_bytes()).hexdigest()
            for p in args.runs
        },
        "arms": {},
        "contrasts": {},
        "trace_issues": [],
    }
    for name in args.arms:
        _, metrics = load_arms([p / name for p in args.runs])
        cases = [
            inspect_case(Path(c["source_arm_dir"]) / c["domain"] / c["task_id"])
            for c in metrics.values()
            if c["terminal"]
        ]
        output["arms"][name] = {
            "metrics": summarize(list(metrics.values())),
            "attribution": attribution(cases),
            "by_domain": {
                domain: {
                    "metrics": summarize([c for c in metrics.values() if c["domain"] == domain]),
                    "attribution": attribution([c for c in cases if c["domain"] == domain]),
                }
                for domain in sorted({c["domain"] for c in cases})
            },
            "cases": cases,
        }
        output["trace_issues"].extend(
            {"arm": name, "case": c["case"], "issue": issue} for c in cases for issue in c["issues"]
        )
    a, b, c, d = args.arms
    for left, right, label in (
        (a, b, "completion"),
        (b, c, "structured_harness"),
        (c, d, "native_business"),
        (a, d, "combined"),
    ):
        result = analyze([p / left for p in args.runs], [p / right for p in args.runs])
        output["contrasts"][label] = {
            "baseline_arm": left,
            "candidate_arm": right,
            "paired": result["paired"],
            "by_domain": result["by_domain"],
            "cost_basis": result["cost_basis"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "issues": len(output["trace_issues"]),
                "arms": {k: v["metrics"] for k, v in output["arms"].items()},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
