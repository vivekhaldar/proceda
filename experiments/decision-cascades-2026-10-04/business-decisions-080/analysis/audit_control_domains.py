"""Offline receipt and control audit; never runs providers or changes run artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

EXPERIMENT = Path(__file__).resolve().parents[2]
ARMS = ("baseline", "completion", "business-qwen", "business-jev")
DOMAINS = ("patient_intake", "referral_abuse_detection_v1")
REFERRAL_TOOLS = [
    "investigate_account",
    "analyze_traffic_patterns",
    "determine_enforcement_action",
]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


completion = load("completion_audit", EXPERIMENT / "qwen-jev-080/analysis/analyze_completion.py")
patient = load("patient_audit", EXPERIMENT / "qwen-jev-080/analysis/review_patient_inputs.py")
business = load("business_audit", Path(__file__).with_name("analyze_business.py"))


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(case, arm, domain):
    base = business.inspect_case(case)
    receipt = completion.inspect_case(case, EXPERIMENT / "skills" / domain / "SKILL.md")
    issues = base["issues"] + receipt["issues"]
    issues.extend(issue for g in receipt["gates"] for issue in g["issues"])
    for role in ("business_native", "business_constrained_llm", "unlinked_native"):
        if base["provider_roles"].get(role, 0):
            issues.append(f"Control domain unexpectedly has provider role {role}")
    if base["actions"] or base["facts"] or base["business_decisions"]:
        issues.append("Control domain unexpectedly records planning execution")
    if arm.startswith("business-"):
        if len(base["plans"]) != 1 or any(
            p["status"] != "unsupported"
            or p["cache_hit"] is not True
            or p["prompt_tokens"]
            or p["completion_tokens"]
            for p in base["plans"]
        ):
            issues.append("Expected unsupported cached preparation without runtime compiler usage")
    elif base["plans"]:
        issues.append("Unplanned arm unexpectedly has preparation event")
    inputs = read(case / "inputs.json")
    accepted = [g for g in receipt["gates"] if g["value"] is True]
    observed = receipt["observed_tools"]
    tool_order = [o["tool_name"].removeprefix("sop-bench__") for o in observed]
    expected_order = patient.TOOLS if domain == "patient_intake" else REFERRAL_TOOLS
    if tool_order != expected_order:
        issues.append("Required successful tool order differs from original SOP")
    patient_review = None
    if domain == "patient_intake":
        patient_review = patient.inspect(case)
        for key in ("prior_result_propagation_flags", "final_result_propagation_flags"):
            if patient_review[key]:
                issues.append(key)
        for transform in patient_review["input_transformations"]:
            if transform["classification"] in {
                "blank_source_replaced",
                "other_argument_difference",
            }:
                issues.append(f"Unexplained patient input transformation: {transform['argument']}")
            if (
                transform["source"] == ""
                and transform["step_auto_completed"]
                and not transform["accepted_gate_clarification_evidence"]
            ):
                issues.append("Accepted transformed blank lacks gate clarification evidence")
        for gate in accepted:
            if gate["step_index"] in {1, 2, 5}:
                issues.append(
                    "Accepted patient documentation step requires additional semantic review"
                )
            if gate["step_index"] == 6:
                registration = [
                    r for r in gate["current_receipts"] if r["sop_tool_name"] == "registerPatient"
                ]
                if (
                    len(registration) != 1
                    or registration[0]["result"].get("user_registration") != "success"
                ):
                    issues.append("Accepted registration without successful registration receipt")
                elif any(
                    registration[0]["arguments"].get(k) != v
                    for k, v in {
                        "insurance_validation": "valid",
                        "prescription_insurance_validation": "valid",
                        "pharmacy_check": "yes",
                    }.items()
                ):
                    issues.append("Accepted registration lacks known eligibility prerequisites")
    else:
        if any(o["arguments"] != {"account_id": inputs["account_id"]} for o in observed):
            issues.append("Referral tool arguments differ from original account input")
        if any(g["step_index"] not in {1, 2, 3} for g in accepted):
            issues.append("Referral gate bypasses a non-tool scoring/report step")
        if not receipt["llm_success_usage_events_by_step"].get("4"):
            issues.append("Referral scoring step has no ordinary Qwen usage event")
    return {
        "arm": arm,
        "domain": domain,
        "task_id": base["task_id"],
        "correct": base["correct"],
        "case": str(case.resolve()),
        "events_sha256": digest(case / "events.jsonl"),
        "provider_roles": base["provider_roles"],
        "plans": base["plans"],
        "successful_tool_order": tool_order,
        "accepted_steps": [g["step_index"] for g in accepted],
        "completion_bypasses": len(receipt["auto_completions"]),
        "direct_bypasses_verified": sum(
            a["direct_continuation_bypass_verified"] for a in receipt["auto_completions"]
        ),
        "gate_outcomes_by_step": {
            str(step): dict(
                Counter(
                    "accepted"
                    if g["value"] is True
                    else "rejected"
                    if g["value"] is False
                    else "uncertain"
                    for g in receipt["gates"]
                    if g["step_index"] == step
                )
            )
            for step in sorted({g["step_index"] for g in receipt["gates"]})
        },
        "accepted_gate_evidence": [
            {
                k: g[k]
                for k in ("step_index", "event_line", "stage_audits", "receipt_checks", "issues")
            }
            for g in accepted
        ],
        "patient_input_review": patient_review,
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an existing audit snapshot")
    cases, planned, run_metadata = [], Counter(), []
    for run in args.runs:
        for arm in ARMS:
            path = run / arm / "run.json"
            metadata = read(path)
            run_metadata.append(
                {
                    "path": str(path.resolve()),
                    "sha256": digest(path),
                    "source_commit": metadata["source"]["commit"],
                    "planning": metadata["configuration"]["planning"],
                    "llm": metadata["configuration"]["llm"],
                }
            )
            for domain in DOMAINS:
                planned[(arm, domain)] += len(metadata["task_ids"].get(domain, []))
                cases.extend(
                    inspect(p.parent, arm, domain)
                    for p in sorted((run / arm / domain).glob("*/terminal.json"))
                )
    summary = {}
    for arm in ARMS:
        summary[arm] = {}
        for domain in DOMAINS:
            selected = [c for c in cases if c["arm"] == arm and c["domain"] == domain]
            transforms = [
                t
                for c in selected
                if c["patient_input_review"]
                for t in c["patient_input_review"]["input_transformations"]
            ]
            roles, accepted, plan_keys = Counter(), Counter(), Counter()
            accepted_correct, accepted_incorrect = Counter(), Counter()
            for case in selected:
                roles.update(case["provider_roles"])
                accepted.update(map(str, case["accepted_steps"]))
                by_correctness = accepted_correct if case["correct"] else accepted_incorrect
                by_correctness.update(map(str, case["accepted_steps"]))
                plan_keys.update(p["cache_key"] for p in case["plans"])
            clarifications = [
                clarification
                for case in selected
                if case["patient_input_review"]
                for clarification in case["patient_input_review"]["clarifications"]
            ]
            summary[arm][domain] = {
                "planned": planned[(arm, domain)],
                "terminal": len(selected),
                "correct": sum(c["correct"] for c in selected),
                "provider_roles": dict(roles),
                "accepted_gates_by_step": dict(accepted),
                "accepted_gates_in_correct_cases_by_step": dict(accepted_correct),
                "accepted_gates_in_incorrect_cases_by_step": dict(accepted_incorrect),
                "direct_bypasses_verified": sum(c["direct_bypasses_verified"] for c in selected),
                "plan_cache_keys": dict(plan_keys),
                "input_transformation_counts": dict(
                    Counter(t["classification"] for t in transforms)
                ),
                "blank_replacements_in_accepted_steps": sum(
                    t["source"] == "" and t["step_auto_completed"] for t in transforms
                ),
                "accepted_blank_replacements_with_gate_clarifications": sum(
                    t["source"] == ""
                    and t["step_auto_completed"]
                    and bool(t["accepted_gate_clarification_evidence"])
                    for t in transforms
                ),
                "clarification_responses": len(clarifications),
                "clarification_responses_matching_first_option": sum(
                    bool(c.get("options")) and c["answer"] == c["options"][0]
                    for c in clarifications
                ),
                "flagged_cases": sum(bool(c["issues"]) for c in selected),
            }
    output = {
        "created_at": datetime.now(UTC).isoformat(),
        "status": "complete" if len(cases) == sum(planned.values()) else "partial",
        "scope": "Patient and referral controls only; email business decisions excluded.",
        "interpretation": [
            "Unsupported prepared plans execute no business decisions. B/C/D differences here "
            "are control variation, not evidence of native business benefit.",
            "Receipt checks validate actual calls, argument/result retention, original "
            "instructions and direct continuation bypass linkage; they do not certify "
            "every prose obligation.",
            "Patient blank replacements require preceding recorded clarification answers. This "
            "benchmark automatically chooses the first offered option; these are not real "
            "patient interviews.",
            "Simulator tools use identifier lookups. Correct final fields do not independently "
            "validate argument semantics in a real service.",
            "Patient overall-risk computation is delegated to its required tool. Unreturned "
            "internal weights alone do not show premature completion.",
            "One accepted completion bypasses one continuation; net arm LLM counts may also "
            "change through different generation trajectories.",
        ],
        "run_metadata": run_metadata,
        "summary": summary,
        "cases": cases,
        "issues": [{"case": c["case"], "issues": c["issues"]} for c in cases if c["issues"]],
    }
    with args.output.open("x") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        json.dumps(
            {"status": output["status"], "summary": summary, "issues": output["issues"]}, indent=2
        )
    )


if __name__ == "__main__":
    main()
