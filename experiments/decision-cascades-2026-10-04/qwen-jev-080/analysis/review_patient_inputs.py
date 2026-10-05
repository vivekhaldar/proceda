"""Read-only patient tool order, output propagation, and input-transform evidence."""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

TOOLS = [
    "validateInsurance",
    "validatePrescriptionBenefits",
    "calculateLifestyleRisk",
    "calculateOverallRisk",
    "verifyPharmacy",
    "registerPatient",
]


def read(path):
    return json.loads(path.read_text())


def transform_kind(key, source, supplied):
    if source == supplied:
        return None
    if source == "":
        return "blank_source_replaced"
    if key in {"previous_surgeries", "chronic_conditions"}:
        try:
            if ast.literal_eval(source) == supplied:
                return "matching_list_parse"
        except (ValueError, SyntaxError):
            pass
    if key == "coverage_start_date":
        for source_format in ("%m/%d/%y", "%Y-%m-%d"):
            try:
                if datetime.strptime(source, source_format).strftime("%Y-%m-%d") == supplied:
                    return "matching_iso_date"
            except ValueError:
                pass
    return "other_argument_difference"


def inspect(case):
    inputs, terminal = read(case / "inputs.json"), read(case / "terminal.json")
    events = [json.loads(line) for line in (case / "events.jsonl").read_text().splitlines()]
    calls, observations, transformations, propagation_flags = [], {}, [], []
    step = None
    accepted_steps = []
    clarifications, pending, accepted_gate_clarifications = [], None, []
    for index, event in enumerate(events):
        kind, payload = event["type"], event["payload"]
        if kind == "step.started":
            step = payload["step_index"]
        elif kind == "clarification.requested":
            pending = {**payload, "step": step, "requested_event_line": index + 1}
        elif kind == "clarification.responded" and pending is not None:
            clarifications.append(
                {**pending, "answer": payload["answer"], "responded_event_line": index + 1}
            )
            pending = None
        elif kind == "decision.evaluated" and payload.get("value") is True:
            for stage in payload.get("stages", []):
                audit = case / "providers" / "decisions" / f"decision-{stage['call_id']}.json"
                state = read(audit)["request"]["state"]
                for clarification in state.get("observed_evidence", {}).get("clarifications", []):
                    accepted_gate_clarifications.append(
                        {**clarification, "audit": str(audit), "accepted_step": step}
                    )
        elif kind == "decision.step_auto_completed":
            accepted_steps.append(payload["step_index"])
        elif kind == "tool.called":
            calls.append(payload["tool_name"].removeprefix("sop-bench__"))
            for key, supplied in payload["arguments"].items():
                if key in inputs:
                    transform = transform_kind(key, inputs[key], supplied)
                    if transform:
                        matching = [
                            c
                            for c in clarifications
                            if c["step"] == step
                            and c["answer"] == supplied
                            and key.split("_")[0] in c["question"].lower()
                        ]
                        if transform == "blank_source_replaced" and matching:
                            transform = "clarification_grounded_blank_source"
                        transformations.append(
                            {
                                "step": step,
                                "argument": key,
                                "source": inputs[key],
                                "supplied": supplied,
                                "classification": transform,
                                "tool_event_line": index + 1,
                                "clarification_evidence": matching,
                            }
                        )
                elif key in observations and observations[key] != supplied:
                    propagation_flags.append(
                        {
                            "step": step,
                            "field": key,
                            "observed": observations[key],
                            "supplied": supplied,
                        }
                    )
        elif kind == "tool.completed":
            result = payload["result"]
            if isinstance(result, str):
                result = json.loads(result)
            if isinstance(result, dict):
                observations.update(result)
    final_flags = {
        key: {"observed": value, "predicted": terminal["predicted"].get(key)}
        for key, value in observations.items()
        if terminal["predicted"].get(key) != value
    }
    for transformation in transformations:
        transformation["step_auto_completed"] = transformation["step"] in accepted_steps
        transformation["accepted_gate_clarification_evidence"] = [
            evidence
            for evidence in accepted_gate_clarifications
            if evidence["accepted_step"] == transformation["step"]
            and any(
                evidence["question"] == c["question"] and evidence["answer"] == c["answer"]
                for c in transformation["clarification_evidence"]
            )
        ]
    return {
        "task_id": terminal["task_id"],
        "case": str(case),
        "tool_order_matches": calls == TOOLS,
        "tool_order": calls,
        "accepted_steps": accepted_steps,
        "clarifications": clarifications,
        "input_transformations": transformations,
        "prior_result_propagation_flags": propagation_flags,
        "final_result_propagation_flags": final_flags,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an existing review artifact")
    report = {
        "review_version": 2,
        "supersedes": "patient-input-review.json",
        "correction": (
            "The earlier snapshot compared original inputs without tracing clarification answers. "
            "Blank-to-None replacements grounded in recorded clarification replies are not "
            "unsupported inferred substitutions. This review retains their request/reply lines "
            "and, when accepted, their presence in the decision gate evidence."
        ),
        "scope": "Patient tool execution and source-input transformations; no rescoring.",
        "notes": [
            "Matching list/date conversions preserve supplied values.",
            "Blank-to-None is classified as clarification-grounded only with a matching "
            "preceding question and response. AutoApproveHumanInterface supplies the first "
            "offered option in this benchmark; this is not a real patient interview.",
            "Simulator tools use patient_id lookups. Correct returned/final values cannot "
            "validate the generated supporting arguments for a real service.",
            "Risk calculation is delegated to the required tool; hidden intermediate weights "
            "alone do not prove failure of a prose obligation.",
        ],
        "arms": {},
    }
    for arm in ("baseline", "hybrid"):
        cases = [
            inspect(p.parent)
            for p in sorted((args.run_dir / arm / "patient_intake").glob("*/terminal.json"))
        ]
        transformations = [t for c in cases for t in c["input_transformations"]]
        report["arms"][arm] = {
            "terminal_cases": len(cases),
            "tool_order_mismatch_cases": sum(not c["tool_order_matches"] for c in cases),
            "prior_result_propagation_flag_cases": sum(
                bool(c["prior_result_propagation_flags"]) for c in cases
            ),
            "final_result_propagation_flag_cases": sum(
                bool(c["final_result_propagation_flags"]) for c in cases
            ),
            "transformation_counts": dict(Counter(t["classification"] for t in transformations)),
            "blank_replacements": dict(
                Counter(
                    f"{t['argument']} -> {t['supplied']}"
                    for t in transformations
                    if t["source"] == ""
                )
            ),
            "blank_replacement_in_auto_completed_step_count": sum(
                t["source"] == "" and t["step_auto_completed"] for t in transformations
            ),
            "accepted_blank_replacements_with_gate_clarification_evidence": sum(
                t["source"] == ""
                and t["step_auto_completed"]
                and bool(t["accepted_gate_clarification_evidence"])
                for t in transformations
            ),
            "other_argument_difference_count": sum(
                t["classification"] == "other_argument_difference" for t in transformations
            ),
            "cases": cases,
        }
    with args.output.open("x") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        json.dumps(
            {
                arm: {k: v for k, v in value.items() if k != "cases"}
                for arm, value in report["arms"].items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
