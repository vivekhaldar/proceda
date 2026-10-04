"""ABOUTME: Read-only evidence checks for completed holdout decision-gate traces.

ABOUTME: Domain-specific rules are offline review criteria, never runtime policy.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERRAL = {
    1: (
        "investigate_account",
        "address_validity email_pattern_suspicious website_verified "
        "business_description account_status connected_accounts login_geographic_consistency",
    ),
    2: (
        "analyze_traffic_patterns",
        "revenue_amount click_through_rate page_views "
        "device_distribution referral_source_quality payment_method_shared "
        "order_patterns_suspicious",
    ),
    3: ("determine_enforcement_action", "enforcement_guidelines"),
}
AUTHORITY = re.compile(r"configured decision gate|Original tool receipts:|receipt_[0-9a-f]{16,}")


def read(path: Path):
    return json.loads(path.read_text())


def rows(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def relative(path: Path):
    return str(path.relative_to(ROOT))


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def referral_scores(results):
    a, t = results["investigate_account"], results["analyze_traffic_patterns"]
    scores = {
        "Abusive Account Creation": sum(
            (
                not a["address_validity"],
                a["email_pattern_suspicious"],
                not a["website_verified"],
                a["connected_accounts"] >= 15,
                not a["login_geographic_consistency"],
            )
        ),
        "Misleading Ad Copy": sum(
            (
                not a["website_verified"],
                t["referral_source_quality"] in ("Low", "Medium"),
                t["order_patterns_suspicious"],
                t["click_through_rate"] > 0.4,
            )
        ),
        "Personal Orders (Related)": sum(
            (
                t["payment_method_shared"],
                0 < a["connected_accounts"] < 15,
                t["order_patterns_suspicious"],
                t["referral_source_quality"] == "High",
            )
        ),
        "No Violation": sum(
            (
                a["address_validity"],
                not a["email_pattern_suspicious"],
                a["website_verified"],
                a["login_geographic_consistency"],
                not t["payment_method_shared"],
                not t["order_patterns_suspicious"],
            )
        ),
    }
    action = (
        "Account Closure"
        if any(scores[k] >= 3 for k in list(scores)[:2])
        else "No Action"
        if scores["Personal Orders (Related)"] >= 3 or scores["No Violation"] >= 4
        else "Inconclusive"
    )
    return {"scores": scores, "action": action}


def inspect_case(case: Path):
    terminal = read(case / "terminal.json")  # Caller selects terminal cases only.
    inputs = read(case / "inputs.json")
    skill = (ROOT / "skills" / terminal["domain"] / "SKILL.md").read_text()
    parts = re.split(r"^### Step (\d+):[^\n]*\n", skill, flags=re.MULTILINE)
    instructions = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts), 2)}
    events = rows(case / "events.jsonl")
    checks = [
        e["payload"]
        for e in events
        if e["type"] == "decision.evaluated" and e["payload"]["purpose"] == "completion"
    ]
    issues, accepted, observed, calls, gate_steps = [], [], [], [], []
    receipt_cores, llm_context_count = {}, 0
    step = None
    auto = {
        e["payload"]["step_index"]: e["payload"]["receipt_ids"]
        for e in events
        if e["type"] == "decision.step_auto_completed"
    }
    for event in events:
        payload = event["payload"]
        if event["type"] == "step.started":
            step = payload["step_index"]
        elif event["type"] == "tool.called":
            calls.append({**payload, "step_index": step})
        elif event["type"] == "tool.completed":
            # Match an executed event at its position, not a global provider-ID join.
            candidates = [
                c
                for c in calls
                if c["step_index"] == step
                and c["tool_call_id"] == payload["tool_call_id"]
                and c["tool_name"] == payload["tool_name"]
            ]
            if not candidates:
                issues.append(f"tool.completed without preceding call in step {step}")
            else:
                observed.append({**candidates[-1], "result": decoded(payload["result"])})
        elif event["type"] == "tool.failed":
            issues.append(f"tool.failed in step {step}")
        elif event["type"] == "decision.evaluated" and payload["value"] is True:
            gate_steps.append(step)
            stage = payload["stages"][-1]
            audit_path = case / "providers" / "decisions" / f"decision-{stage['call_id']}.json"
            audit = read(audit_path)
            evidence = audit["request"]["state"]["observed_evidence"]
            receipts = evidence["receipts"]
            current = [r for r in receipts if r["current_step"]]
            if evidence["inputs"] != inputs:
                issues.append(f"step {step}: evidence inputs mismatch")
            if payload["step_index"] != step or audit["outcome"] != "accepted":
                issues.append(f"step {step}: gate/event disagreement")
            if audit["request"]["state"]["step_instructions"] != instructions[step]:
                issues.append(f"step {step}: gate instructions differ from the SOP")
            if audit["response"].get("state_truncated") is not False or any(
                p < audit["threshold"] for p in audit["probabilities"].values()
            ):
                issues.append(f"step {step}: truncated or below-threshold accepted response")
            if len({r["receipt_id"] for r in receipts}) != len(receipts):
                issues.append(f"step {step}: duplicate receipt identity")
            if set(auto.get(step, [])) != {r["receipt_id"] for r in current}:
                issues.append(f"step {step}: completion receipt links mismatch")
            for r in receipts:
                core = {k: v for k, v in r.items() if k != "current_step"}
                if r["receipt_id"] in receipt_cores and receipt_cores[r["receipt_id"]] != core:
                    issues.append(f"step {step}: changed immutable receipt")
                receipt_cores[r["receipt_id"]] = core
                matches = [
                    o
                    for o in observed
                    if o["tool_call_id"] == r["tool_call_id"]
                    and o["tool_name"] == r["qualified_tool_name"]
                    and o["arguments"] == r["arguments"]
                    and o["result"] == r["result"]
                    and (o["step_index"] == step) == r["current_step"]
                ]
                if not matches or r["persisted"] is not True or r["is_error"] is not False:
                    issues.append(f"step {step}: receipt not backed by successful prior event")
            if terminal["domain"] == "referral_abuse_detection_v1":
                if step not in REFERRAL or len(current) != 1:
                    issues.append(f"step {step}: unexpected referral acceptance shape")
                else:
                    tool, fields = REFERRAL[step]
                    r = current[0]
                    if (
                        r["qualified_tool_name"] != f"sop-bench__{tool}"
                        or r["sop_tool_name"] != tool
                        or r["arguments"] != {"account_id": inputs["account_id"]}
                        or r["result"].get("account_id") != inputs["account_id"]
                        or not set(fields.split()) <= r["result"].keys()
                        or any(r["result"].get(field) is None for field in fields.split())
                    ):
                        issues.append(
                            f"step {step}: tool/account/arguments/required fields mismatch"
                        )
            accepted.append(
                {
                    "step_index": step,
                    "probabilities": audit["probabilities"],
                    "audit": relative(audit_path),
                    "receipts": current,
                }
            )
    if set(auto) != set(gate_steps):
        issues.append("automatic completions do not match accepted gate steps")
    authority = []
    for audit_path in sorted((case / "providers").glob("*.json")):
        audit = read(audit_path)
        llm_context_count += 1
        matches = AUTHORITY.findall(json.dumps(audit["request"]["messages"]))
        if matches:
            authority.append({"audit": relative(audit_path), "matches": matches})
    if authority:
        issues.append("gate-authority or receipt identifiers present in LLM context")
    result = {
        "task_id": terminal["task_id"],
        "domain": terminal["domain"],
        "correct": terminal["correct"],
        "predicted": terminal["predicted"],
        "accepted": accepted,
        "issues": issues,
        "authority_contexts": authority,
        "llm_contexts_checked": llm_context_count,
        "completion_outcomes": dict(
            Counter(
                "accepted"
                if c["value"] is True
                else "rejected"
                if c["value"] is False
                else "uncertain"
                for c in checks
            )
        ),
        "completion_checks_by_step": dict(Counter(str(c["step_index"]) for c in checks)),
        "case": relative(case),
    }
    if terminal["domain"] == "patient_intake":
        expected_tools = [
            f"sop-bench__{match.group(1)}"
            for text in instructions.values()
            if (match := re.search(r"Call `([^`]+)`", text))
        ]
        if [c["tool_name"] for c in calls] != expected_tools:
            issues.append("full patient tool order mismatch")
    if terminal["domain"] == "referral_abuse_detection_v1":
        expected_tools = [f"sop-bench__{t}" for t, _ in REFERRAL.values()]
        if [c["tool_name"] for c in calls] != expected_tools or any(
            c["arguments"] != inputs for c in calls
        ):
            issues.append("full referral tool order/arguments mismatch")
        results = {o["tool_name"].split("__", 1)[1]: o["result"] for o in observed}
        result["independent_scoring"] = referral_scores(results)
        if result["independent_scoring"]["action"] != terminal["predicted"]["enforcement_action"]:
            issues.append("LLM output disagrees with offline SOP scoring of tool results")
    return result


def main():
    cases = []
    for terminal in sorted(
        (ROOT / "runs").glob("holdout-final--*/hybrid-local-low/*/*/terminal.json")
    ):
        cases.append(inspect_case(terminal.parent))
    domains = {}
    for domain in sorted({c["domain"] for c in cases}):
        subset = [c for c in cases if c["domain"] == domain]
        domains[domain] = {
            "terminal_cases": len(subset),
            "correct": sum(c["correct"] for c in subset),
            "accepted_gates": sum(len(c["accepted"]) for c in subset),
            "accepted_by_step": dict(
                Counter(str(a["step_index"]) for c in subset for a in c["accepted"])
            ),
            "llm_contexts_checked": sum(c["llm_contexts_checked"] for c in subset),
            "cases_with_flags": sum(bool(c["issues"]) for c in subset),
            "completion_outcomes": dict(
                sum((Counter(c["completion_outcomes"]) for c in subset), Counter())
            ),
            "completion_checks_by_step": dict(
                sum((Counter(c["completion_checks_by_step"]) for c in subset), Counter())
            ),
        }
    report = {
        "reviewed_at": datetime.now(UTC).isoformat(),
        "scope": "Completed hybrid cases only; offline checks, no inference or runtime changes.",
        "domains": domains,
        "cases": cases,
    }
    (ROOT / "analysis/holdout-final-gate-checks.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "reviewed_at": report["reviewed_at"],
                "domains": domains,
                "flags": [
                    {"task_id": c["task_id"], "issues": c["issues"]} for c in cases if c["issues"]
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
