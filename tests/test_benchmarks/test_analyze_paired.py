"""ABOUTME: Offline tests for strict paired scoring and all-attempt cost accounting.
ABOUTME: Verifies missing usage, retry charges, pending cases and reasoning-token treatment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.sop_bench.analyze_paired import account_audits, analyze, latency


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def llm(usage=None, model="zai-org/GLM-5.3-Flash"):
    return {
        "call_id": "call",
        "attempt": 1,
        "request": {},
        "requested_model": model,
        "response_model": model,
        "usage": usage,
    }


def test_retry_costs_come_from_all_audits_and_reasoning_is_not_added(tmp_path):
    providers = tmp_path / "providers"
    write(
        providers / "attempt-1.json",
        llm(
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "completion_tokens_details": {"reasoning_tokens": 15},
            }
        ),
    )
    write(providers / "attempt-2.json", llm({"prompt_tokens": 200, "completion_tokens": 30}))
    result = account_audits(tmp_path, {"reported_tokens": {"total_tokens": 230}})
    assert result["llm_requests"] == 2
    assert result["input_tokens_known"] == 300
    assert result["output_tokens_known"] == 50
    assert result["cost_complete"]
    assert result["cost_usd"] == pytest.approx((300 * 0.15 + 50 * 0.50) / 1_000_000)


def test_missing_usage_is_unknown_cost_not_zero(tmp_path):
    write(tmp_path / "providers/failed.json", llm())
    write(tmp_path / "providers/success.json", llm({"prompt_tokens": 100, "completion_tokens": 20}))
    result = account_audits(tmp_path, None)
    assert result["llm_requests"] == 2
    assert result["known_usage_requests"] == 1
    assert result["unknown_usage_requests"] == 1
    assert result["unknown_cost_requests"] == 1
    assert result["cost_usd"] is None
    assert result["cost_usd_lower_bound"] == pytest.approx(0.000025)


def test_decision_preflight_local_cash_and_jev_input_pricing(tmp_path):
    providers = tmp_path / "providers"
    write(
        providers / "skip.json",
        {
            "kind": "typesafe",
            "model": "jev",
            "request_sent": False,
            "outcome": "error",
        },
    )
    write(
        providers / "local.json",
        {
            "kind": "ollaya",
            "model": "laya:en",
            "request_sent": True,
            "response": {},
        },
    )
    write(
        providers / "jev.json",
        {
            "kind": "typesafe",
            "model": "jev",
            "request_sent": True,
            "response": {"usage": {"input_tokens": 100}},
            "input_tokens": 100000,
            "cost_usd": 999,
        },
    )
    result = account_audits(tmp_path, None)
    assert result["decision_requests"] == 2
    assert result["decision_preflight_records"] == 1
    assert result["input_tokens_known"] == 100
    assert result["cost_complete"]  # No output-token fee; local API fee is zero.
    assert result["cost_usd"] == pytest.approx(0.0000042)


def test_unknown_request_status_and_missing_audit_block_exact_cost(tmp_path):
    write(
        tmp_path / "providers/old-error.json",
        {
            "kind": "typesafe",
            "model": "jev",
            "outcome": "error",
            "response": None,
        },
    )
    result = account_audits(tmp_path, {"provider_audit_files": ["providers/missing.json"]})
    assert result["unknown_request_status"] == 1
    assert result["cost_usd"] is None
    assert result["audit_errors"]


def make_arm(directory: Path, predictions: dict, split="dev"):
    write(
        directory / "run.json",
        {
            "manifest_sha256": "same-source",
            "split": split,
            "task_ids": {"domain": ["A", "B", "C"]},
        },
    )
    for task_id, (prediction, status, elapsed) in predictions.items():
        case = directory / "domain" / task_id
        write(case / "expected.json", {"decision": "valid"})
        write(
            case / "terminal.json",
            {
                "domain": "domain",
                "task_id": task_id,
                "status": status,
                "completed": status == "completed",
                "correct": True,
                "predicted": {"decision": prediction},
                "elapsed_seconds": elapsed,
                "reported_tokens": {"total_tokens": 99999},
            },
        )
        write(case / "providers/request.json", llm({"prompt_tokens": 100, "completion_tokens": 20}))


def test_pairing_regrades_strictly_and_keeps_pending_cases_visible(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    make_arm(
        baseline,
        {
            "A": ("valid", "completed", 1),
            "B": ("invalid", "completed", 3),
            "C": ("valid", "completed", 9),
        },
    )
    make_arm(candidate, {"A": ("valid", "completed", 2), "B": ("valid", "completed", 2)})
    result = analyze(baseline, candidate)
    assert result["baseline"]["correct_cases"] == 2
    assert result["candidate"]["terminal_cases"] == 2
    assert result["candidate"]["status_counts"]["not_started"] == 1
    assert result["paired"]["terminal_pairs"] == 2
    assert result["paired"]["pending_pairs"] == [["domain", "C"]]
    assert result["paired"]["discordance_counts"] == {
        "both_correct": 1,
        "both_incorrect": 0,
        "improved": 1,
        "regressed": 0,
    }
    assert result["paired"]["accuracy_delta"] == 0.5
    assert result["paired"]["baseline_paired"]["latency_seconds"]["mean"] == 2
    assert result["baseline"]["accounting"]["input_tokens_known"] == 300
    assert result["baseline_cases"][1]["warnings"]


def test_failed_terminal_with_correct_text_still_fails(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    make_arm(baseline, {"A": ("valid", "completed", 1)})
    make_arm(candidate, {"A": ("valid", "failed", 2)})
    result = analyze(baseline, candidate)
    assert result["paired"]["discordance_counts"]["regressed"] == 1
    assert result["candidate"]["correct_cases"] == 0


def test_different_frozen_splits_cannot_be_paired(tmp_path):
    make_arm(tmp_path / "baseline", {}, split="dev")
    make_arm(tmp_path / "candidate", {}, split="holdout")
    with pytest.raises(ValueError, match="different split"):
        analyze(tmp_path / "baseline", tmp_path / "candidate")


def test_latency_has_documented_linear_percentiles():
    assert latency([1, 3, 5]) == {"n": 3, "mean": 3, "p50": 3, "p95": 4.8}
    assert latency([]) == {"n": 0, "mean": None, "p50": None, "p95": None}
