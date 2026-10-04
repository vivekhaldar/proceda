"""ABOUTME: Offline tests for strict paired scoring and all-attempt cost accounting.
ABOUTME: Verifies missing usage, retry charges, pending cases and reasoning-token treatment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.sop_bench.analyze_paired import (
    account_audits,
    analyze,
    discordant_test,
    latency,
    main,
    paired_bootstrap,
    sum_accounting,
    wilson_interval,
)


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


def make_arm(directory: Path, predictions: dict, split="dev", domain="domain"):
    write(
        directory / "run.json",
        {
            "manifest_sha256": "same-source",
            "split": split,
            "task_ids": {domain: ["A", "B", "C"]},
        },
    )
    for task_id, (prediction, status, elapsed) in predictions.items():
        case = directory / domain / task_id
        write(case / "expected.json", {"decision": "valid"})
        write(
            case / "terminal.json",
            {
                "domain": domain,
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


def test_wilson_interval_is_wide_at_small_sample_sizes():
    interval = wilson_interval(0, 10)
    assert interval["lower"] == pytest.approx(0, abs=1e-15)
    assert interval["upper"] == pytest.approx(0.2775327998628892)
    assert wilson_interval(2, 2)["lower"] == pytest.approx(0.342380227506653)
    assert wilson_interval(0, 0)["lower"] is None


def test_paired_bootstrap_is_deterministic_and_reports_excluded_pairs():
    first = paired_bootstrap([-2.0, 2.0], [["domain", "unknown"]])
    assert first == paired_bootstrap([-2.0, 2.0], [["domain", "unknown"]])
    assert first["mean_difference"] == 0
    assert first["lower"] == -2 and first["upper"] == 2
    assert first["resamples"] >= 1000
    assert first["excluded_pair_count"] == 1
    assert paired_bootstrap([2], [])["lower"] is None
    assert paired_bootstrap([], [])["mean_difference"] is None


def test_exact_discordance_probability():
    assert discordant_test(3, 0)["two_sided_p"] == 0.25
    assert discordant_test(0, 3)["two_sided_p"] == 0.25
    assert discordant_test(2, 1)["two_sided_p"] == 1.0
    assert discordant_test(0, 0)["two_sided_p"] == 1.0


def test_unknown_pair_cost_excluded_from_cost_interval_only(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    make_arm(baseline, {"A": ("valid", "completed", 1), "B": ("valid", "completed", 3)})
    make_arm(candidate, {"A": ("valid", "completed", 2), "B": ("valid", "completed", 2)})
    write(candidate / "domain/B/providers/request.json", llm())
    result = analyze(baseline, candidate)
    for pair in (result["paired"], result["by_domain"]["domain"]):
        cost = pair["mean_cost_difference_bootstrap95"]
        assert cost["n"] == 1 and cost["excluded_pair_count"] == 1
        assert cost["excluded_pairs"] == [["domain", "B"]]
        assert cost["lower"] is None and cost["insufficient_pairs"]
        assert pair["mean_latency_difference_bootstrap95"]["n"] == 2
    assert result["candidate"]["strict_accuracy_wilson95"]["n"] == 2


def test_native_duration_units_known_zero_and_missing_are_distinct(tmp_path):
    for name, response in {
        "known": {"total_duration": 500_000_000, "load_duration": 0, "eval_duration": 400_000_000},
        "missing": {},
    }.items():
        write(
            tmp_path / f"providers/{name}.json",
            {
                "kind": "ollaya",
                "model": "winnow:e4b",
                "request_sent": True,
                "response": response,
            },
        )
    accounting = account_audits(tmp_path, None)
    assert accounting["local_decision_requests"] == 2
    assert accounting["local_native_total_seconds"] == 0.5
    assert accounting["local_native_total_known_calls"] == 1
    assert accounting["local_native_load_seconds"] == 0
    assert accounting["local_native_load_known_calls"] == 1
    assert accounting["local_native_eval_seconds"] == 0.4
    empty = account_audits(tmp_path / "empty", None)
    assert empty["local_native_total_seconds"] is None
    summed = sum_accounting([empty, accounting, accounting])
    assert summed["local_native_total_seconds"] == 1
    assert summed["local_native_total_known_calls"] == 2
    assert summed["local_native_load_seconds"] == 0
    assert sum_accounting([empty])["local_native_load_seconds"] is None


def test_active_model_hour_break_even_excludes_missing_duration_and_cost(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    cases = {name: ("valid", "completed", 1) for name in ("A", "B", "C")}
    make_arm(baseline, cases)
    make_arm(candidate, cases)
    for name in cases:
        write(
            candidate / f"domain/{name}/providers/request.json",
            llm({"prompt_tokens": 0, "completion_tokens": 0}),
        )
        write(
            candidate / f"domain/{name}/providers/local.json",
            {
                "kind": "ollaya",
                "model": "winnow:e4b",
                "request_sent": True,
                "response": {"total_duration": 1_000_000_000} if name != "B" else {},
            },
        )
    write(candidate / "domain/C/providers/request.json", llm())
    result = analyze(baseline, candidate)
    for pair in (result["paired"], result["by_domain"]["domain"]):
        breakeven = pair["local_active_model_hour_break_even"]
        assert breakeven["included_pairs"] == 1
        assert breakeven["excluded_pairs"] == [["domain", "B"], ["domain", "C"]]
        assert breakeven["hosted_api_savings_usd"] == pytest.approx(0.000025)
        assert breakeven["additional_local_native_total_seconds"] == 1
        assert breakeven["usd_per_additional_active_model_hour"] == pytest.approx(0.09)


def test_active_model_hour_break_even_has_no_rate_without_extra_local_compute(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    make_arm(baseline, {"A": ("valid", "completed", 1)})
    make_arm(candidate, {"A": ("valid", "completed", 1)})
    breakeven = analyze(baseline, candidate)["paired"]["local_active_model_hour_break_even"]
    assert breakeven["included_pairs"] == 1
    assert breakeven["additional_local_native_total_seconds"] == 0
    assert breakeven["usd_per_additional_active_model_hour"] is None


def test_cli_merges_three_domains_and_preserves_source_provenance(tmp_path, monkeypatch, capsys):
    baseline, candidate = [], []
    for domain in ("email", "patient", "referral"):
        for arm, directories, seconds in (("baseline", baseline, 1), ("candidate", candidate, 2)):
            directory = tmp_path / domain / arm
            make_arm(directory, {"A": ("valid", "completed", seconds)}, domain=domain)
            directories.append(directory)
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "analyze_paired",
            "--baseline",
            *map(str, baseline),
            "--candidate",
            *map(str, candidate),
            "--output",
            str(output),
        ],
    )
    main()
    capsys.readouterr()
    report = json.loads(output.read_text())
    assert report["paired"]["terminal_pairs"] == 3
    assert report["baseline"]["planned_cases"] == 9
    assert report["baseline"]["accounting"]["llm_requests"] == 3
    assert report["baseline"]["accounting"]["cost_usd"] == pytest.approx(0.000075)
    assert report["paired"]["mean_latency_difference_bootstrap95"]["mean_difference"] == 1
    assert set(report["by_domain"]) == {"email", "patient", "referral"}
    assert all(value["terminal_pairs"] == 1 for value in report["by_domain"].values())
    for case in report["paired"]["cases"]:
        assert case["baseline_source_arm_dir"] == str(tmp_path / case["domain"] / "baseline")
        assert case["candidate_source_arm_dir"] == str(tmp_path / case["domain"] / "candidate")
    assert report["baseline_cases"][0]["source_arm_dir"] == str(baseline[0])
    assert report["baseline_dirs"] == list(map(str, baseline))
    assert report["baseline_dir"] is None


def test_merging_overlapping_runs_rejects_repeats_including_pending_cases(tmp_path):
    first, repeated, candidate = [tmp_path / name for name in ("first", "repeated", "candidate")]
    make_arm(first, {})
    make_arm(repeated, {"A": ("valid", "completed", 1)})
    make_arm(candidate, {})
    with pytest.raises(ValueError, match="Duplicate case across arm directories"):
        analyze([first, repeated], candidate)
    with pytest.raises(ValueError, match="Duplicate case across arm directories"):
        analyze(candidate, [first, first])


def test_merging_distinct_domains_rejects_different_frozen_splits(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    make_arm(first, {}, domain="one")
    make_arm(second, {}, domain="two", split="holdout")
    with pytest.raises(ValueError, match="Cannot merge runs with different split"):
        analyze([first, second], first)


def test_cached_aliases_count_once_and_reasoning_output_is_not_added(tmp_path):
    write(
        tmp_path / "providers/request.json",
        llm(
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 80},
                "prompt_cache_hit_tokens": 80,
                "prompt_cache_miss_tokens": 20,
                "completion_tokens_details": {"reasoning_tokens": 15},
            }
        ),
    )
    result = account_audits(tmp_path, None)
    assert result["llm_known_cached_usage_requests"] == 1
    assert result["llm_cached_input_tokens"] == 80
    assert result["llm_uncached_input_tokens"] == 20
    assert result["output_tokens_known"] == 20
    assert result["cost_usd"] == pytest.approx(0.000025)
    assert result["hypothetical_cache_price_costs"]["0.0"]["cost_usd"] == pytest.approx(0.000013)
    assert result["hypothetical_cache_price_costs"]["0.2"]["cost_usd"] == pytest.approx(0.0000154)
    assert result["hypothetical_cache_price_costs"]["1.0"]["cost_usd"] == result["cost_usd"]
    provider = result["providers"]["llm:zai-org/GLM-5.3-Flash"]
    assert provider["cached_input_tokens_known"] == 80
    combined = sum_accounting([result, result])
    assert combined["llm_cached_input_tokens"] == 160
    assert combined["hypothetical_cache_price_costs"]["0.0"]["cost_usd"] == pytest.approx(0.000026)
    assert "not a lower bound on an actual invoice" in result["cost_basis"]


@pytest.mark.parametrize(
    "cache_fields",
    [
        {"prompt_tokens_details": {"cached_tokens": 80}, "prompt_cache_hit_tokens": 70},
        {"prompt_cache_hit_tokens": 101},
        {"prompt_cache_hit_tokens": -1},
        {"prompt_cache_hit_tokens": True},
        {"prompt_cache_hit_tokens": 80.0},
        {"prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 21},
    ],
)
def test_inconsistent_cache_usage_does_not_invalidate_full_rate_cost(tmp_path, cache_fields):
    write(
        tmp_path / "providers/request.json",
        llm(
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                **cache_fields,
            }
        ),
    )
    result = account_audits(tmp_path, None)
    assert result["cost_complete"]
    assert result["llm_unknown_cached_usage_requests"] == 1
    assert result["llm_cached_input_tokens"] is None
    assert result["cache_usage_errors"]
    assert result["hypothetical_cache_price_costs"]["0.0"]["cost_usd"] is None
    assert result["hypothetical_cache_price_costs"]["1.0"]["cost_usd"] == pytest.approx(0.000025)


def test_missing_cache_is_unknown_and_explicit_zero_is_known(tmp_path):
    write(
        tmp_path / "missing/providers/request.json",
        llm(
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": None},
                "prompt_cache_miss_tokens": 100,
            }
        ),
    )
    write(
        tmp_path / "zero/providers/request.json",
        llm(
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 0},
            }
        ),
    )
    missing = account_audits(tmp_path / "missing", None)
    zero = account_audits(tmp_path / "zero", None)
    assert missing["llm_cached_input_tokens"] is None
    assert not missing["cache_usage_errors"]  # Missing is distinct from inconsistent.
    assert missing["llm_unknown_cached_usage_requests"] == 1
    assert zero["llm_cached_input_tokens"] == 0
    assert zero["llm_known_cached_usage_requests"] == 1
    assert zero["hypothetical_cache_price_costs"]["0.0"]["cost_usd"] == zero["cost_usd"]
    combined = sum_accounting([missing, zero])
    assert combined["llm_cached_input_tokens"] is None
    assert combined["llm_cached_input_tokens_known"] == 0
    assert combined["llm_known_cached_usage_requests"] == 1
    assert combined["llm_unknown_cached_usage_requests"] == 1


def test_paired_cache_sensitivity_can_reverse_cost_comparison(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for arm, inputs, cached in ((baseline, 100, 90), (candidate, 80, 60)):
        make_arm(arm, {"A": ("valid", "completed", 1)})
        write(
            arm / "domain/A/providers/request.json",
            llm(
                {
                    "prompt_tokens": inputs,
                    "completion_tokens": 10,
                    "prompt_cache_hit_tokens": cached,
                }
            ),
        )
    report = analyze(baseline, candidate)
    for comparison in (report["paired"], report["by_domain"]["domain"]):
        sensitivity = comparison["hypothetical_cache_price_sensitivity"]
        assert sensitivity["hypothetical_not_actual_invoice"]
        scenarios = sensitivity["scenarios"]
        assert scenarios["0.0"]["candidate_minus_baseline_cost_usd"] == pytest.approx(0.0000015)
        assert scenarios["0.2"]["candidate_minus_baseline_cost_usd"] == pytest.approx(0.0000006)
        assert scenarios["1.0"]["candidate_minus_baseline_cost_usd"] == pytest.approx(-0.000003)
        crossover = sensitivity["break_even"]
        assert crossover["cached_input_rate_fraction"] == pytest.approx(1 / 3)
        assert crossover["cached_input_rate_usd_per_million"] == pytest.approx(0.05)
        assert crossover["within_zero_to_full_rate"]
        assert "candidate cheaper above" in crossover["comparison"]


def test_pair_missing_cache_excluded_from_discount_scenarios_but_not_full_rate(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    make_arm(baseline, {"A": ("valid", "completed", 1), "B": ("valid", "completed", 1)})
    make_arm(candidate, {"A": ("valid", "completed", 1), "B": ("valid", "completed", 1)})
    for arm in (baseline, candidate):
        write(
            arm / "domain/A/providers/request.json",
            llm(
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_cache_hit_tokens": 0,
                }
            ),
        )
    report = analyze(baseline, candidate)
    sensitivity = report["paired"]["hypothetical_cache_price_sensitivity"]
    assert sensitivity["scenarios"]["0.0"]["included_pairs"] == 1
    assert sensitivity["scenarios"]["0.2"]["excluded_pairs"] == [["domain", "B"]]
    assert sensitivity["scenarios"]["1.0"]["included_pairs"] == 2
    assert sensitivity["break_even"]["included_pairs"] == 1
    assert sensitivity["break_even"]["cached_input_rate_fraction"] is None
    assert (
        sensitivity["break_even"]["comparison"]
        == "equal estimated cost at every cache-rate fraction"
    )


def test_cache_sensitivity_preserves_decision_fees(tmp_path):
    write(
        tmp_path / "providers/llm.json",
        llm(
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_cache_hit_tokens": 100,
            }
        ),
    )
    write(
        tmp_path / "providers/jev.json",
        {
            "kind": "typesafe",
            "model": "jev",
            "request_sent": True,
            "response": {"usage": {"input_tokens": 100, "output_tokens": 45}},
        },
    )
    result = account_audits(tmp_path, None)
    assert result["llm_known_cached_usage_requests"] == 1
    assert result["llm_unknown_cached_usage_requests"] == 0
    assert result["hypothetical_cache_price_costs"]["0.0"]["cost_usd"] == pytest.approx(0.0000142)
