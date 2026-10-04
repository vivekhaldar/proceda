"""ABOUTME: Replay saved development completion gates with counterfactual negative fixtures.
ABOUTME: Uses decision providers only; labels and provenance never enter model requests.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from benchmarks.sop_bench.experiment_credentials import load_pass_credentials
from proceda.config import DecisionBackendConfig
from proceda.decision import DecisionService


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases(source: Path) -> list[dict]:
    """Read only a completed dev arm and derive aliases from its real MCP toolspecs."""
    manifest = json.loads((source / "run.json").read_text())
    if manifest["split"] != "dev":
        raise ValueError("This prompt-development replay accepts dev sources only")
    cases = []
    all_aliases = {}
    for case_dir in sorted(source.glob("*/*")):
        if not case_dir.is_dir() or not (case_dir / "terminal.json").exists():
            continue
        events = [json.loads(line) for line in (case_dir / "events.jsonl").read_text().splitlines()]
        success = {
            event["payload"]["tool_call_id"]: event["type"] == "tool.completed"
            for event in events
            if event["type"] in {"tool.completed", "tool.failed"}
        }
        specs_path = Path(manifest["data_dir"]) / case_dir.parent.name / "toolspecs.json"
        specs = json.loads(specs_path.read_text())
        aliases = {
            "sop-bench__" + item.get("toolSpec", item)["name"]: item.get("toolSpec", item)["name"]
            for item in specs
        }
        all_aliases.update(aliases)
        # sop-bench is the actual app binding in paired_experiment.scoped_config.
        for audit_path in sorted((case_dir / "providers" / "decisions").glob("*.json")):
            audit = json.loads(audit_path.read_text())
            state = audit["request"]["state"]
            observed = state["observed_evidence"]
            if "step_history" not in observed:
                raise ValueError("Expected frozen v1 completion input")
            current_ids = {
                message["tool_call_id"]
                for message in observed["step_history"]
                if message.get("role") == "tool"
            }
            receipts = []
            for receipt in observed["observed_tool_receipts"]:
                result = receipt["result"]
                try:
                    result = json.loads(result)
                except (TypeError, ValueError):
                    pass
                call_id = receipt["tool_call_id"]
                receipts.append(
                    {
                        "tool_call_id": call_id,
                        "qualified_tool_name": receipt["tool_name"],
                        "sop_tool_name": aliases[receipt["tool_name"]],
                        "arguments": receipt["arguments"],
                        "result": result,
                        "persisted": True,
                        "current_step": call_id in current_ids,
                        "is_error": not success[call_id] if call_id in success else None,
                    }
                )
            cases.append(
                {
                    "id": f"positive-{case_dir.name}-{len(cases) + 1}",
                    "expected_gate": True,
                    "fixture_kind": "recorded_completed_read_step",
                    "step": state["step_instructions"],
                    "evidence": {"inputs": observed["inputs"], "receipts": receipts},
                    "provenance": {
                        "source_audit": str(audit_path),
                        "sha256": digest(audit_path),
                        "toolspecs": str(specs_path),
                        "toolspecs_sha256": digest(specs_path),
                    },
                }
            )
    if len(cases) != 6:
        raise ValueError(f"Expected exactly six frozen development gates; got {len(cases)}")
    seed = min(cases, key=lambda case: len(case["evidence"]["receipts"]))

    def negative(name: str) -> dict:
        case = copy.deepcopy(seed)
        case.update(id=name, expected_gate=False, fixture_kind="synthetic_counterfactual")
        case["provenance"]["mutation"] = name
        cases.append(case)
        return case

    negative("missing-receipt")["evidence"]["receipts"] = []
    case = negative("missing-required-result-field")
    result = case["evidence"]["receipts"][0]["result"]
    field = next(key for key in result if key not in case["evidence"]["inputs"])
    del result[field]
    case["provenance"]["removed_field"] = field
    case = negative("wrong-tool")
    case["evidence"]["receipts"][0].update(
        qualified_tool_name="test__unrelated_tool", sop_tool_name="unrelated_tool"
    )
    case = negative("wrong-argument")
    argument = next(iter(case["evidence"]["inputs"]))
    case["evidence"]["receipts"][0]["arguments"][argument] = "DIFFERENT_INPUT_VALUE"
    case = negative("failed-call")
    case["evidence"]["receipts"][0].update(is_error=True, result={"error": "call failed"})
    case = negative("remaining-user-action")
    case["step"] += (
        " Before completing, ask the user to confirm these details and wait for a reply."
    )
    case = negative("partial-two-call-batch")
    first_tool = case["evidence"]["receipts"][0]["sop_tool_name"]
    next_tool = next(name for name in all_aliases.values() if name != first_tool)
    case["step"] += f" Then call `{next_tool}` with `{argument}` and record its returned result."
    return cases


async def replay(source: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("Output exists; use a new versioned directory to retain all attempts")
    cases = load_cases(source)
    output.mkdir(parents=True)
    (output / "fixtures.json").write_text(json.dumps(cases, indent=2) + "\n")
    backends = [
        DecisionBackendConfig(name="jev", timeout=30),
        DecisionBackendConfig(
            name="local",
            kind="ollaya",
            model="winnow:e4b",
            base_url="http://127.0.0.1:11435",
            api_key_env="",
            max_input_chars=18000,
            timeout=90,
            input_cost_per_million=0,
        ),
    ]
    manifest = {
        "scope": "Decision-only development replay; not end-to-end benchmark results.",
        "threshold": 0.97,
        "source": str(source),
        "source_manifest_sha256": digest(source / "run.json"),
        "backends": [asdict(backend) for backend in backends],
        "service_sha256": digest(Path("src/proceda/decision/service.py")),
        "script_sha256": digest(Path(__file__)),
        "labels_sent_to_models": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rows = []
    for index, case in enumerate(cases):
        for backend in backends if index % 2 == 0 else reversed(backends):
            result = await DecisionService(
                [backend], threshold=0.97, audit_dir=output / case["id"] / backend.name
            ).check_completion(step=case["step"], evidence=case["evidence"])
            row = {
                "case": case["id"],
                "backend": backend.name,
                "expected_gate": case["expected_gate"],
                **asdict(result),
            }
            rows.append(row)
            with (output / "results.jsonl").open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            print(
                json.dumps(
                    {
                        key: row[key]
                        for key in ("case", "backend", "expected_gate", "value", "probabilities")
                    }
                ),
                flush=True,
            )
    summary = {}
    for backend in backends:
        selected = [row for row in rows if row["backend"] == backend.name]
        summary[backend.name] = {
            "positive_accepted": sum(
                row["expected_gate"] and row["value"] is True for row in selected
            ),
            "positive_total": sum(row["expected_gate"] for row in selected),
            "negative_rejected": sum(
                not row["expected_gate"] and row["value"] is False for row in selected
            ),
            "negative_abstained": sum(
                not row["expected_gate"] and row["value"] is None for row in selected
            ),
            "negative_false_accepted": sum(
                not row["expected_gate"] and row["value"] is True for row in selected
            ),
            "negative_total": sum(not row["expected_gate"] for row in selected),
        }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    credentials = load_pass_credentials({"TYPESAFE_API_KEY": "API_KEYS/TYPESAFE_AI_JEV_API_KEY"})
    previous = os.environ.get("TYPESAFE_API_KEY")
    os.environ.update(credentials)
    try:
        print(json.dumps(asyncio.run(replay(args.source, args.output)), indent=2))
    finally:
        if previous is None:
            os.environ.pop("TYPESAFE_API_KEY", None)
        else:
            os.environ["TYPESAFE_API_KEY"] = previous
