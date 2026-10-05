"""ABOUTME: Independently rescan completed Qwen/Jev schedules without inference or mutations.
ABOUTME: Check frozen identities, original inputs, deterministic scoring, and all provider audits.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
EXPERIMENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from benchmarks.sop_bench import paired_experiment as paired  # noqa: E402
from benchmarks.sop_bench.analyze_paired import account_audits, sum_accounting  # noqa: E402
from proceda.events import RunEvent  # noqa: E402
from proceda.skills.loader import load_skill  # noqa: E402

GROUPS = [
    "qwen-jev-080-pilot",
    "qwen-jev-080-holdout--email_intent",
    "qwen-jev-080-holdout--patient_intake",
    "qwen-jev-080-holdout--referral_abuse_detection_v1",
]


def read(path: Path):
    return json.loads(path.read_text())


def rows(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def key(item):
    return item["arm"], item["domain"], item["task_id"]


def verify(groups: list[str]) -> dict:
    source = read(EXPERIMENT / "source-manifest.json")
    errors, reports, all_accounting = [], [], []
    frozen_sources, frozen_runtime, frozen_profiles = set(), set(), collections.defaultdict(set)
    loaded_domains = {}

    def check(condition, message, where):
        if not condition:
            errors.append({"where": where, "issue": message})

    def code_hash(value):
        return paired.canonical_hash(value)

    for group in groups:
        folder = EXPERIMENT / "phase2/runs" / group
        # A live or aborted group is ineligible for this final integrity report.
        marker = read(folder / "completed.json")
        schedule = read(folder / "schedule.json")
        summary = read(folder / "summary.json")
        predictions = rows(folder / "predictions.jsonl")
        by_key = {key(item): item for item in predictions}
        entries = schedule["entries"]
        planned = {key(entry) for entry in entries}
        check(len(planned) == len(entries), "Duplicate planned execution", group)
        check(len(by_key) == len(predictions), "Duplicate prediction execution", group)
        check(set(by_key) == planned, "Prediction/plan identities differ", group)
        check(marker["entries"] == len(entries), "Completion count differs", group)
        check(
            marker["summary_sha256"] == paired.sha256_file(folder / "summary.json"),
            "Completion summary hash differs",
            group,
        )
        check(
            summary["planned_entries"] == summary["terminal_entries"] == len(entries),
            "Summary does not cover every execution",
            group,
        )
        check(
            not any((folder / name).exists() for name in ("aborted.json", "failed.json")),
            "Conflicting terminal schedule marker",
            group,
        )
        check(
            schedule["manifest_sha256"] == code_hash(source),
            "Source manifest identity differs",
            group,
        )
        check(
            schedule["manifest_file_sha256"]
            == paired.sha256_file(EXPERIMENT / "source-manifest.json"),
            "Source manifest file hash differs",
            group,
        )
        paired.verify_sources(Path(schedule["data_dir"]), EXPERIMENT)
        frozen_sources.add(code_hash(schedule["source"]))
        frozen_runtime.add(code_hash(schedule["runtime"]))
        for relative, expected_hash in schedule["source"]["source_hashes"].items():
            check(
                paired.sha256_file(REPO / relative) == expected_hash,
                "Current source bytes differ from frozen schedule",
                relative,
            )
        for arm, profile in schedule["configurations"].items():
            config = profile["effective"]
            frozen_profiles[arm].add(profile["effective_sha256"])
            check(
                code_hash(config) == profile["effective_sha256"],
                "Effective config hash differs",
                group,
            )
            check(
                paired.sha256_file(Path(profile["path"])) == profile["file_sha256"],
                "Current profile file hash differs",
                group,
            )
            check(not config["planning"]["enabled"], "Unexpected compiled planning", group)
            check(
                config["llm"]["model"] == "openai/Qwen/Qwen3.8-27B",
                "Unexpected primary model",
                group,
            )
            manifest = read(folder / arm / "run.json")
            check(
                manifest["schedule_sha256"] == paired.sha256_file(folder / "schedule.json"),
                "Arm schedule hash differs",
                group,
            )
            check(manifest["configuration"] == config, "Arm config differs from schedule", group)
            check(manifest["source"] == schedule["source"], "Arm source identity differs", group)
            arm_rows = rows(folder / arm / "predictions.jsonl")
            check(
                {key(item) for item in arm_rows} == {k for k in planned if k[0] == arm},
                "Arm prediction identities differ",
                group,
            )
            check(
                len(arm_rows) == len([k for k in planned if k[0] == arm]),
                "Arm prediction count differs",
                group,
            )
            check(
                all(item == by_key.get(key(item)) for item in arm_rows),
                "Arm/global prediction bytes differ",
                group,
            )
        baseline = schedule["configurations"]["baseline"]["effective"]
        hybrid = schedule["configurations"]["hybrid"]["effective"]
        check(
            {k: v for k, v in baseline.items() if k != "decisions"}
            == {k: v for k, v in hybrid.items() if k != "decisions"},
            "Non-decision configuration differs between arms",
            group,
        )
        check(not baseline["decisions"]["enabled"], "Baseline decisions enabled", group)
        check(
            hybrid["decisions"]["enabled"] and hybrid["decisions"]["threshold"] == 0.8,
            "Hybrid decision threshold differs",
            group,
        )
        check(
            hybrid["decisions"]["fast_completion"] and not hybrid["decisions"]["verify_proposals"],
            "Unexpected hybrid decision modes",
            group,
        )
        check(
            len(hybrid["decisions"]["backends"]) == 1
            and hybrid["decisions"]["backends"][0]["model"] == "jev-1.13.0"
            and hybrid["decisions"]["backends"][0]["kind"] == "typesafe",
            "Unexpected checker backend",
            group,
        )

        accounting_by_case = {}
        counters = collections.Counter()
        llm_models, decision_models, audit_statuses, decision_reasons = (
            collections.Counter() for _ in range(4)
        )
        provider_issues = []
        starts, finishes, event_ids = [], [], set()
        per_arm = collections.defaultdict(list)
        for entry in entries:
            arm, domain, task_id = key(entry)
            where = f"{group}/{arm}/{domain}/{task_id}"
            case = folder / arm / domain / task_id
            terminal = read(case / "terminal.json")
            observation = read(case / "phase2-observation.json")
            check(
                observation == by_key.get(key(entry)),
                "Observation/global prediction differs",
                where,
            )
            check(
                all(observation.get(k) == v for k, v in terminal.items()),
                "Terminal/observation differs",
                where,
            )
            check(
                terminal["task_id"] == task_id and terminal["domain"] == domain,
                "Terminal identity differs",
                where,
            )
            check(
                terminal["status"] in {"completed", "failed", "cancelled"},
                "Nonterminal case status",
                where,
            )
            if domain not in loaded_domains:
                values, metadata = paired.load_domain(Path(schedule["data_dir"]), domain)
                loaded_domains[domain] = (
                    {row[paired.ID_COLUMNS[domain]]: row for row in values},
                    metadata,
                    load_skill(Path(schedule["skill_paths"][domain])),
                )
            source_rows, metadata, skill = loaded_domains[domain]
            row = source_rows[task_id]
            inputs = read(case / "inputs.json")
            expected = read(case / "expected.json")
            check(
                inputs == {k: str(row.get(k, "") or "") for k in metadata["input_columns"]},
                "Inputs differ from frozen benchmark",
                where,
            )
            check(
                expected == {k: row.get(k, "") for k in metadata["output_columns"]},
                "Expected outputs differ from frozen benchmark",
                where,
            )
            check(
                task_id in source["domains"][domain]["splits"][schedule["split"]],
                "Case is outside its recorded split",
                where,
            )
            events = rows(case / "events.jsonl")
            prediction = paired.extract_deterministic(
                [RunEvent.from_dict(e) for e in events], metadata["output_columns"]
            )
            scores = paired.strict_score(prediction, expected)
            check(prediction == terminal["predicted"], "Offline output extraction differs", where)
            check(scores == terminal["field_correct"], "Offline field scores differ", where)
            check(
                terminal["correct"]
                == (terminal["status"] == "completed" and bool(scores) and all(scores.values())),
                "Offline case score differs",
                where,
            )
            check(len({e["id"] for e in events}) == len(events), "Repeated event identity", where)
            check(
                not event_ids.intersection(e["id"] for e in events),
                "Event identity reused across cases",
                where,
            )
            event_ids.update(e["id"] for e in events)
            check(
                all(e["run_id"] == events[0]["run_id"] for e in events),
                "Mixed run identities",
                where,
            )
            finished_steps = [
                e["payload"]["step_index"]
                for e in events
                if e["type"] in {"step.completed", "step.skipped"}
            ]
            if terminal["completed"]:
                check(
                    sorted(finished_steps) == list(range(1, len(skill.steps) + 1)),
                    "Completed case lacks exact complete step sequence",
                    where,
                )
                check(
                    sum(e["type"] == "run.completed" for e in events) == 1,
                    "Completed case lacks one run.completed event",
                    where,
                )
            tool_events = [e for e in events if e["type"].startswith("tool.")]
            check(
                tool_events == rows(case / "tools.jsonl"), "Tool trace differs from events", where
            )
            calls = [
                e["payload"]["tool_call_id"] for e in tool_events if e["type"] == "tool.called"
            ]
            results = [
                e["payload"]["tool_call_id"]
                for e in tool_events
                if e["type"] in {"tool.completed", "tool.failed"}
            ]
            check(
                collections.Counter(calls) == collections.Counter(results),
                "Tool call/result IDs differ",
                where,
            )
            check(
                not any(e["type"].startswith("planning.") for e in events),
                "Unexpected planning event",
                where,
            )
            counters.update(
                {
                    "terminal_cases": 1,
                    "events": len(events),
                    "finished_steps": len(finished_steps),
                    "tool_calls": len(calls),
                    "correct_cases": int(terminal["correct"]),
                    "completed_cases": int(terminal["completed"]),
                }
            )
            counters[f"terminal_status:{terminal['status']}"] += 1
            counters["tool_failures"] += sum(e["type"] == "tool.failed" for e in tool_events)
            raw_accounting = account_audits(case, terminal)
            check(
                raw_accounting == observation["accounting"],
                "Saved versus rescanned audit accounting differs",
                where,
            )
            check(
                not raw_accounting["audit_errors"],
                "Unreadable, missing, or malformed provider audit",
                where,
            )
            check(
                not observation["compiler_attempts"] and not observation["plan_links"],
                "Unexpected compiler or plan artifacts",
                where,
            )
            accounting_by_case[key(entry)] = raw_accounting
            per_arm[arm].append(raw_accounting)
            audit_paths = sorted((case / "providers").rglob("*.json"))
            check(
                sorted(str(p.relative_to(case)) for p in audit_paths)
                == sorted(terminal["provider_audit_files"]),
                "Raw audit files differ from terminal listing",
                where,
            )
            for path in audit_paths:
                audit = read(path)
                if "outcome" in audit:
                    decision_models[audit["model"]] += 1
                    decision_reasons[audit.get("reason", "missing")] += 1
                    state = audit["outcome"]
                    check(
                        arm == "hybrid" and audit["model"] == "jev-1.13.0",
                        "Unexpected decision audit model/arm",
                        where,
                    )
                    check(audit["threshold"] == 0.8, "Decision audit threshold differs", where)
                    check(
                        state in {"accepted", "rejected", "uncertain", "error", "skipped"},
                        "Incomplete decision audit",
                        where,
                    )
                else:
                    model = audit.get("response_model") or audit.get("requested_model")
                    llm_models[model] += 1
                    state = audit["status"]
                    check(
                        model.removeprefix("openai/") == "Qwen/Qwen3.8-27B",
                        "Unexpected LLM audit model",
                        where,
                    )
                    check(
                        state in {"success", "error"} and audit.get("finished_at"),
                        "Incomplete LLM audit",
                        where,
                    )
                audit_statuses[state] += 1
                if state in {"error", "started"}:
                    provider_issues.append(
                        {
                            "path": str(path.relative_to(EXPERIMENT)),
                            "state": state,
                            "reason": audit.get("reason"),
                            "usage_reported": audit.get("usage_reported"),
                        }
                    )
            starts.append(observation["actual_started_at"])
            finishes.append(observation["actual_finished_at"])
        # Preserve the recorded completion order to avoid float summation-order noise.
        accounting = [accounting_by_case[key(item)] for item in predictions]
        summed = sum_accounting(accounting)
        check(summed == summary["accounting"], "Summary versus rescanned accounting differs", group)
        check(
            len(list(folder.glob("*/*/*/terminal.json"))) == len(entries),
            "Extra or missing terminal files",
            group,
        )
        execution = rows(folder / "execution-order.jsonl")
        started = [e for e in execution if e["event"] == "entry.started"]
        ended = [e for e in execution if e["event"] == "entry.finished"]
        check(
            collections.Counter(key(e) for e in started) == collections.Counter(planned),
            "Execution start records differ",
            group,
        )
        check(
            collections.Counter(key(e) for e in ended) == collections.Counter(planned),
            "Execution finish records differ",
            group,
        )
        check(
            all(e.get("runtime_cleanup_confirmed") for e in ended),
            "Cleanup is not confirmed",
            group,
        )
        for domain, ids in schedule["task_ids"].items():
            for task_id in ids:
                pair = sorted(
                    [by_key[(arm, domain, task_id)] for arm in schedule["arms"]],
                    key=lambda x: x["arm_position"],
                )
                check(
                    pair[0]["actual_finished_at"] <= pair[1]["actual_started_at"],
                    "Paired arms overlapped within a case",
                    f"{group}/{domain}/{task_id}",
                )
        all_accounting.extend(accounting)
        reports.append(
            {
                "group": group,
                "planned_executions": len(entries),
                "counts": dict(counters),
                "created_at": schedule["created_at"],
                "first_started_at": min(starts),
                "last_finished_at": max(finishes),
                "completed_at": marker["completed_at"],
                "source_commit": schedule["source"]["commit"],
                "source_hash_count": len(schedule["source"]["source_hashes"]),
                "source_identity_sha256": code_hash(schedule["source"]),
                "runtime_sha256": code_hash(schedule["runtime"]),
                "config_effective_sha256": {
                    arm: value["effective_sha256"]
                    for arm, value in schedule["configurations"].items()
                },
                "schedule_sha256": paired.sha256_file(folder / "schedule.json"),
                "summary_sha256": marker["summary_sha256"],
                "predictions_sha256": paired.sha256_file(folder / "predictions.jsonl"),
                "split": schedule["split"],
                "validation_status": schedule["validation_status"],
                "untouched_confirmation": schedule["untouched_confirmation"],
                "jobs": schedule["jobs"],
                "llm_models": dict(llm_models),
                "decision_models": dict(decision_models),
                "audit_statuses": dict(audit_statuses),
                "decision_reasons": dict(decision_reasons),
                "provider_issues": provider_issues,
                "accounting": summed,
                "accounting_by_arm": {
                    arm: sum_accounting(values) for arm, values in per_arm.items()
                },
            }
        )
    check(len(frozen_sources) == 1, "Source identities differ across schedules", "all_groups")
    check(len(frozen_runtime) == 1, "Runtime identities differ across schedules", "all_groups")
    check(
        all(len(values) == 1 for values in frozen_profiles.values()),
        "Arm profiles differ across schedules",
        "all_groups",
    )
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "schema_version": 1,
        "scope": (
            "Offline evidence integrity; completed schedules only; "
            "no semantic gate-quality verdict"
        ),
        "integrity_passed": not errors,
        "errors": errors,
        "groups": reports,
        "accounting_all_groups": sum_accounting(all_accounting),
        "shared_source_identity_count": len(frozen_sources),
        "shared_runtime_identity_count": len(frozen_runtime),
        "source_manifest_sha256": paired.sha256_file(EXPERIMENT / "source-manifest.json"),
        "source_manifest_canonical_sha256": code_hash(source),
        "protocol_sha256": paired.sha256_file(EXPERIMENT / "qwen-jev-080/protocol.json"),
        "integrity_script_sha256": paired.sha256_file(Path(__file__)),
        "inference_performed": False,
        "raw_files_changed": False,
        "limitations": [
            "The original holdout is reused validation, not unseen confirmation.",
            "Final-field scores and complete runtime traces do not establish every "
            "prose SOP obligation.",
            "Provider costs are list-price usage estimates, with cached input priced at "
            "full input rate; actual cache billing is unverified.",
            "The pilot is retained separately from the 720 holdout executions; "
            "do not pool its accuracy into the holdout comparison.",
            "Three domain processes each allow four cases; latency includes shared "
            "hosted-service contention.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", action="append", choices=GROUPS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.group or GROUPS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(
        json.dumps(
            {
                "integrity_passed": report["integrity_passed"],
                "errors": report["errors"],
                "groups": len(report["groups"]),
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return int(not report["integrity_passed"])


if __name__ == "__main__":
    raise SystemExit(main())
