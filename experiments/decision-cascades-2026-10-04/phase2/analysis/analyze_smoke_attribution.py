"""ABOUTME: Read-only attribution of compiled planning versus native decision-model execution.
ABOUTME: Reports only a complete frozen phase-2 schedule and makes no provider calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from benchmarks.sop_bench.analyze_paired import account_audits, sum_accounting
from benchmarks.sop_bench.paired_experiment import canonical_hash, strict_score


def read(path):
    return json.loads(path.read_text())


def events(case):
    return [json.loads(line) for line in (case / "events.jsonl").read_text().splitlines()]


def analyze(root):
    schedule = read(root / "schedule.json")
    if not (root / "completed.json").exists():
        terminals = sum((root / e["arm"] / e["domain"] / e["task_id"] / "terminal.json").exists()
                        for e in schedule["entries"])
        raise ValueError(f"Schedule incomplete: {terminals}/{len(schedule['entries'])} terminals")
    prepared_hashes = {value: key for key, value in schedule["compilation"]["prompt_hashes"].items()}
    cases = []
    for entry in schedule["entries"]:
        case = root / entry["arm"] / entry["domain"] / entry["task_id"]
        terminal = read(case / "terminal.json")
        observation = read(case / "phase2-observation.json")
        actual_events = events(case)
        actions = Counter()
        fallbacks = []
        gates = []
        app_calls = []
        for event in actual_events:
            p = event["payload"]
            if event["type"] == "planning.action":
                key = p["kind"] + (":" + p["origin"] if p.get("origin") else "")
                actions[key] += 1
            elif event["type"] == "planning.fallback":
                fallbacks.append(p)
            elif event["type"] == "decision.evaluated":
                gates.append(p)
            elif event["type"] == "decision.step_auto_completed":
                actions["completion_gate"] += 1
            elif event["type"] == "tool.called":
                app_calls.append({"name": p.get("tool_name"), "arguments": p.get("arguments")})
        audit_purposes = Counter()
        audit_errors = []
        audit_details = []
        for path in sorted((case / "providers").rglob("*.json")):
            record = read(path)
            request = record.get("request", {})
            if record.get("kind") in {"ollaya", "typesafe"}:
                questions = request.get("questions", {})
                purpose = "native_choice" if any(q.get("type") == "choice" for q in questions.values()) else "boolean_gate"
                status = record.get("outcome")
                sent = record.get("request_sent")
                if sent is not True:
                    purpose += ":preflight"
            else:
                messages = request.get("messages", [])
                system = messages[0].get("content") if messages else None
                purpose = prepared_hashes.get(canonical_hash(system))
                if purpose is None:
                    names = [tool.get("function", {}).get("name") for tool in request.get("tools", [])]
                    purpose = "generative_choice" if names == ["record_decision"] else "ordinary_llm"
                status = record.get("status")
                sent = True
            audit_purposes[purpose] += 1
            if status in {"started", "error"}:
                audit_errors.append({"path": str(path), "status": status, "reason": record.get("reason")})
            audit_details.append({"purpose": purpose, "path": str(path), "status": status,
                                  "request_sent": sent, "reason": record.get("reason"),
                                  "elapsed_ms": record.get("elapsed_ms"),
                                  "elapsed_seconds": record.get("elapsed_seconds")})
        fields = strict_score(terminal["predicted"], read(case / "expected.json"))
        prep = observation.get("preparation", {}).get("elapsed_seconds")
        cases.append({**entry, "case_dir": str(case), "status": terminal["status"],
                      "correct": terminal["completed"] and bool(fields) and all(fields.values()),
                      "predicted": terminal["predicted"], "field_correct": fields,
                      "elapsed_seconds": terminal["elapsed_seconds"], "preparation_seconds": prep,
                      "execution_seconds": terminal["elapsed_seconds"] - prep if prep is not None else None,
                      "accounting": account_audits(case, terminal), "audit_purposes": dict(audit_purposes),
                      "audit_errors": audit_errors, "audit_details": audit_details,
                      "actions": dict(actions), "fallbacks": fallbacks, "gates": gates,
                      "app_calls": app_calls, "plan_links": observation["plan_links"],
                      "compilation": observation["compilation"],
                      "trace_integrity_valid": observation["trace_integrity_valid"]})
    groups = {}
    for domain in [*schedule["domains"], "ALL"]:
        for arm in schedule["arms"]:
            selected = [c for c in cases if c["arm"] == arm and (domain == "ALL" or c["domain"] == domain)]
            purposes = Counter()
            actions = Counter()
            gate_results = Counter()
            for c in selected:
                purposes.update(c["audit_purposes"])
                actions.update(c["actions"])
                for gate in c["gates"]:
                    value = gate.get("value")
                    verdict = "abstain" if value is None else "accept" if (value is True or isinstance(value, str)) else "reject"
                    gate_results[gate["purpose"] + ":" + verdict] += 1
            total = sum_accounting([c["accounting"] for c in selected])
            compile_known = all(c["compilation"]["cost_usd"] is not None for c in selected)
            compile_cost = sum(c["compilation"]["cost_usd"] for c in selected) if compile_known else None
            groups[f"{domain}/{arm}"] = {
                "n": len(selected), "correct": sum(c["correct"] for c in selected),
                "accounting": total, "audit_purposes": dict(purposes), "actions": dict(actions),
                "gates": dict(gate_results), "actual_charged_compilation_cost_usd": compile_cost,
                "execution_cost_usd_excluding_compilation": total["cost_usd"] - compile_cost
                if total["cost_usd"] is not None and compile_cost is not None else None,
                "raw_case_seconds": sum(c["elapsed_seconds"] for c in selected),
                "preparation_seconds": sum(c["preparation_seconds"] for c in selected)
                if all(c["preparation_seconds"] is not None for c in selected) else None,
                "mean_execution_seconds": statistics.mean(c["execution_seconds"] for c in selected)
                if all(c["execution_seconds"] is not None for c in selected) else None,
                "median_execution_seconds": statistics.median(c["execution_seconds"] for c in selected)
                if all(c["execution_seconds"] is not None for c in selected) else None,
                "plan_fallbacks": dict(Counter(f["reason"] for c in selected for f in c["fallbacks"])),
            }
    plans = []
    for path in sorted((root / "plan-cache").glob("*.json")):
        artifact = read(path)
        payload = artifact["payload"]
        plans.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "key": payload["cache_key"], "status": payload["status"], "errors": payload["errors"],
                      "review": payload["review"], "plan": payload["validated_plan"],
                      "raw_skill": payload["identity"]["source"]["raw_skill"]})
    return {"schedule": str(root / "schedule.json"), "schedule_sha256": hashlib.sha256((root / "schedule.json").read_bytes()).hexdigest(),
            "scope": "Small development smoke using the original frozen manifest, not untouched confirmation or an aggregate enterprise claim.",
            "attribution": "Compiled-LLM versus baseline estimates binding/rendering/prompt-structure effects. DM versus compiled-LLM additionally changes native choices and optional completion gates; these are separated by purpose. First-caller compilation is a shared cold cost, not a unique arm property.",
            "groups": groups, "plans": plans, "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.run.resolve())
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    for name, group in result["groups"].items():
        print(name, group["correct"], "/", group["n"], group["audit_purposes"], group["actions"], group["gates"])
