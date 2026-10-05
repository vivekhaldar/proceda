"""ABOUTME: Test label-free shared business preparation and fail-closed frozen cache loading.
ABOUTME: All compiler/reviewer calls are mocked and written to synthetic provider audits.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
import yaml

from benchmarks.sop_bench import prepare_business_plans as prep
from benchmarks.sop_bench import run_compiled_schedule as driver
from benchmarks.sop_bench.choice_probe import ProbeSource
from proceda.config import ProcedaConfig
from proceda.llm.runtime import LLMResponse
from proceda.skills.parser import parse_skill


@pytest.fixture
def setup(tmp_path, monkeypatch):
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    (experiment / "source-manifest.json").write_text('{"frozen":true}')
    data = tmp_path / "data"
    data.mkdir()
    (data / "labels.csv").write_text("CASE_AND_LABEL_MUST_NEVER_BE_READ")
    profile = {
        "llm": {
            "model": "openai/Qwen/Qwen3.8-27B",
            "max_tokens": 2048,
            "temperature": 0,
            "reasoning_effort": "low",
        },
        "planning": {
            "enabled": True,
            "mode": "business_decisions",
            "cache_epoch": "fixture",
            "native_decisions": False,
            "compiler_llm": {
                "model": "openai/Qwen/Qwen3.8-27B",
                "max_tokens": 2048,
                "temperature": 0,
                "reasoning_effort": "low",
            },
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(profile))
    source_text = "Classify message as yes or no. Treat Δ as ordinary text."

    def source(_experiment, _data, domain):
        skill = parse_skill(
            f"---\nname: {domain}\ndescription: Synthetic choice\n---\n"
            f"### Step 1: Choose\n{source_text}\n"
        )
        return ProbeSource(skill, [], ["message"], ["outcome"], {"domain": domain}, ["D1"])

    monkeypatch.setattr(prep, "load_source", source)
    monkeypatch.setattr(driver, "code_identity", lambda: {"commit": "fixture", "source_hashes": {}})
    state = {"messages": [], "active": 0, "max_active": 0, "mode": "accepted"}

    class FakeLLM:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, **_):
            assert self.config.max_tokens == 2048
            assert self.config.temperature == 0
            assert self.config.reasoning_effort == "low"
            assert "CASE_AND_LABEL_MUST_NEVER_BE_READ" not in json.dumps(messages)
            state["messages"].append(messages)
            number = len(state["messages"])
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            await asyncio.sleep(0)
            state["active"] -= 1
            if messages[0]["content"] == driver.BUSINESS_COMPILER_PROMPT:
                if state["mode"] == "unsupported":
                    step = {"index": 1, "kind": "llm"}
                else:
                    step = {
                        "index": 1,
                        "kind": "decision",
                        "decision": {
                            "field": "outcome",
                            "criteria": {"yes": "yes applies", "no": "no applies"},
                            "inputs": {
                                "message": {
                                    "kind": "input",
                                    "key": "message",
                                    "source_quote": source_text,
                                }
                            },
                            "source_quote": source_text,
                        },
                    }
                value = {"ir_version": "1", "steps": [step]}
            else:
                approved = state["mode"] != "rejected"
                value = {"approved": approved, "issues": [] if approved else ["coverage uncertain"]}
            usage = {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 0},
            }
            prep.paired.write_json_exclusive(
                Path(self.config.audit_dir) / f"{number}.json",
                {
                    "attempt": 1,
                    "status": "success",
                    "request": {"messages": messages},
                    "requested_model": self.config.model,
                    "usage": usage,
                },
            )
            return LLMResponse(
                json.dumps(value),
                [],
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                raw_response={"choices": [{"finish_reason": "stop"}]},
            )

    monkeypatch.setattr(prep, "LLMRuntime", FakeLLM)
    kwargs = dict(
        config_path=path,
        output_dir=tmp_path / "preparation",
        domains=list(prep.paired.DOMAINS),
        data_dir=data,
        experiment_dir=experiment,
    )
    return kwargs, state


def load(kwargs):
    config = ProcedaConfig.load(kwargs["config_path"])
    native = copy.deepcopy(config)
    native.planning.native_decisions = True
    return prep.load_preparation(
        kwargs["output_dir"] / "manifest.json",
        domains=kwargs["domains"],
        configs={"business-qwen": config, "business-jev": native},
        data_dir=kwargs["data_dir"],
        experiment_dir=kwargs["experiment_dir"],
        source_identity={"commit": "new-artifact-only-commit", "source_hashes": {}},
    )


@pytest.mark.parametrize("mode", ["accepted", "rejected", "unsupported"])
async def test_shared_source_only_preparation_accepts_terminal_fallbacks_without_recompile(
    setup, mode
):
    kwargs, state = setup
    state["mode"] = mode
    result = await prep.prepare(**kwargs)
    assert len(state["messages"]) == 6
    assert state["max_active"] == 3
    assert all(item["status"] == mode for item in result["domains"].values())
    assert result["accounting"]["llm_requests"] == 6
    assert result["accounting"]["cost_usd"] == pytest.approx(6 * (100 * 0.45 + 20 * 3) / 1e6)
    frozen = load(kwargs)
    assert len(frozen["artifacts"]) == 3
    assert frozen["accounting"] == result["accounting"]
    assert len(state["messages"]) == 6  # Loader must never call either model.
    assert frozen["preparation_performed_in_this_schedule"] is False


async def test_reusing_preparation_directory_never_spends_or_overwrites(setup):
    kwargs, state = setup
    await prep.prepare(**kwargs)
    with pytest.raises(FileExistsError):
        await prep.prepare(**kwargs)
    assert len(state["messages"]) == 6


async def test_changed_cache_or_provider_audit_rejected_before_execution(setup):
    kwargs, _ = setup
    await prep.prepare(**kwargs)
    artifact = next((kwargs["output_dir"] / "cache").glob("*.json"))
    artifact.write_text("{}")
    with pytest.raises(ValueError, match="artifacts changed"):
        load(kwargs)


async def test_different_compiler_budget_rejected_before_execution(setup):
    kwargs, _ = setup
    await prep.prepare(**kwargs)
    config = yaml.safe_load(kwargs["config_path"].read_text())
    config["planning"]["compiler_llm"]["max_tokens"] = 8192
    kwargs["config_path"].write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="runtime compiler identity"):
        load(kwargs)


async def test_different_source_hash_rejected_before_execution(setup, monkeypatch):
    kwargs, _ = setup
    await prep.prepare(**kwargs)
    original = prep.load_source

    def changed(*args):
        source = original(*args)
        source.identity["changed"] = True
        return source

    monkeypatch.setattr(prep, "load_source", changed)
    with pytest.raises(ValueError, match="schema identity"):
        load(kwargs)


def test_explicit_business_compiler_required():
    with pytest.raises(ValueError, match="business_decisions"):
        prep.compiler_config_identity(ProcedaConfig())


def test_four_profiles_isolate_native_route_and_share_main_compiler_configuration():
    root = (
        Path(__file__).resolve().parents[2]
        / "experiments/decision-cascades-2026-10-04/business-decisions-080"
    )
    configs = {
        name: ProcedaConfig.load(root / "configs" / f"{name}.yaml")
        for name in ("baseline", "completion", "business-qwen", "business-jev")
    }
    c, d = configs["business-qwen"], configs["business-jev"]
    assert c.llm == d.llm == c.planning.compiler_llm == d.planning.compiler_llm
    assert prep.compiler_config_identity(c) == prep.compiler_config_identity(d)
    assert not c.planning.native_decisions and d.planning.native_decisions
    c.planning.native_decisions = True
    assert c == d
    assert not configs["baseline"].decisions.enabled
    assert configs["completion"].decisions == d.decisions
    ids = json.loads((root / "pilot-case-ids.json").read_text())
    assert sum(map(len, ids.values())) * len(configs) == 48
