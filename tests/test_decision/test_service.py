"""ABOUTME: Tests bounded decision cascades using mocked provider HTTP responses.
ABOUTME: Verifies abstention, truncation handling, credential-free audits and usage accounting.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from proceda.decision import DecisionService

QUESTIONS = {
    "complete": {"type": "noul", "instructions": "Is the step complete?"},
    "advance": {"type": "noul", "instructions": "Can the next step begin?"},
}


def backend(name="local", kind="ollaya", **changes):
    config = {
        "name": name,
        "kind": kind,
        "model": "model-v1",
        "base_url": f"http://{name}.test",
        "api_key_env": "TEST_DECISION_KEY" if kind == "typesafe" else "",
        "max_input_chars": 100_000,
        "timeout": 1.0,
        "input_cost_per_million": 0.0,
    }
    return SimpleNamespace(**(config | changes))


def body(complete=0.99, advance=0.99, **changes):
    return {
        "model": "model-v1",
        "answers": {
            "complete": {"type": "noul", "noul": complete},
            "advance": {"type": "noul", "noul": advance},
        },
        "usage": {"input_tokens": 100, "output_tokens": 0},
        "state_truncated": False,
    } | changes


async def test_uncertain_local_cascades_to_hosted(monkeypatch):
    monkeypatch.setenv("TEST_DECISION_KEY", "example-test-secret")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=body(complete=0.6 if len(requests) == 1 else 0.99))

    service = DecisionService(
        [backend(), backend("hosted", "typesafe", input_cost_per_million=0.042)],
        transport=httpx.MockTransport(handler),
    )
    result = await service.evaluate({"text": "exact evidence"}, QUESTIONS)
    assert result.value is True
    assert [item["outcome"] for item in result.details] == ["uncertain", "accepted"]
    assert [request.url.path for request in requests] == ["/api/decide", "/v1/systemone"]
    assert requests[1].headers["Authorization"] == "Bearer example-test-secret"
    assert result.input_tokens == 200
    assert result.cost_usd == pytest.approx(0.0000042)
    assert all(item["request_sent"] and item["usage_reported"] for item in result.details)
    assert all(item["cost_known"] for item in result.details)
    assert json.loads(requests[0].content)["state"] == {"text": "exact evidence"}


async def test_confident_no_is_false_not_uncertainty_or_a_second_call():
    calls = []

    def handler(request):
        calls.append(request)
        response = body(complete=0.01)
        response["answers"]["complete"]["confidence"] = 0.999
        return httpx.Response(200, json=response)

    service = DecisionService(
        [backend(), backend("unused")], transport=httpx.MockTransport(handler)
    )
    result = await service.evaluate({}, QUESTIONS)
    assert result.value is False
    assert result.reason == "obligation_rejected"
    assert len(calls) == 1


async def test_confidence_statistic_does_not_override_yes_probability():
    response = body(complete=0.7)
    response["answers"]["complete"]["confidence"] = 0.999
    service = DecisionService(
        [backend()], transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    )
    result = await service.evaluate({}, QUESTIONS)
    assert result.value is None
    assert result.details[0]["outcome"] == "uncertain"


@pytest.mark.parametrize("failure", ["truncated", "missing_flag", "http", "timeout", "bad_json"])
async def test_invalid_or_failed_backend_falls_through(failure):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(200, json=body())
        if failure == "timeout":
            raise httpx.ReadTimeout("credential-bearing messages must not be logged")
        if failure == "http":
            return httpx.Response(529, json={"error": "temporarily unavailable"})
        if failure == "bad_json":
            return httpx.Response(200, text="not json")
        response = body()
        if failure == "truncated":
            response["state_truncated"] = True
        else:
            del response["state_truncated"]
        return httpx.Response(200, json=response)

    service = DecisionService([backend(), backend("next")], transport=httpx.MockTransport(handler))
    result = await service.evaluate({}, QUESTIONS)
    assert result.value is True
    assert len(calls) == 2
    assert result.details[0]["outcome"] == "error"
    assert result.details[0]["request_sent"] is True
    if failure == "truncated":
        assert result.input_tokens == 200  # Rejected truncated inference still costs compute.
        assert result.details[0]["usage_reported"] is True
    elif failure in {"http", "timeout", "bad_json"}:
        assert result.details[0]["usage_reported"] is False
        assert result.details[0]["cost_known"] is False


async def test_oversize_backend_skipped_without_silent_truncation(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body())

    state = {"evidence": "A" * 500}
    service = DecisionService(
        [backend(max_input_chars=10), backend("next")],
        audit_dir=tmp_path,
        transport=httpx.MockTransport(handler),
    )
    result = await service.evaluate(state, QUESTIONS)
    assert result.value is True
    assert len(calls) == 1
    assert json.loads(calls[0].content)["state"] == state
    assert result.details[0]["reason"] == "input_too_large"
    assert result.details[0]["request_sent"] is False
    assert result.details[0]["usage_reported"] is False
    assert result.details[0]["cost_known"] is True
    records = [json.loads(path.read_text()) for path in (tmp_path / "decisions").glob("*.json")]
    assert len(records) == 2
    assert all(record["request"]["state"] == state for record in records)


@pytest.mark.parametrize("probability", [True, "0.99", None, -0.1, 1.1])
async def test_invalid_probabilities_never_accepted(probability):
    service = DecisionService(
        [backend()],
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=body(complete=probability))
        ),
    )
    result = await service.evaluate({}, QUESTIONS)
    assert result.value is None
    assert result.details[0]["reason"] == "invalid_probability"


async def test_missing_question_cannot_accept_or_reject_other_questions():
    response = body(complete=0.01)
    del response["answers"]["advance"]
    service = DecisionService(
        [backend()], transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    )
    result = await service.evaluate({}, QUESTIONS)
    assert result.value is None
    assert result.details[0]["reason"] == "missing_or_invalid_answer"


async def test_audit_keeps_full_sanitized_request_response_without_credentials(
    tmp_path, monkeypatch
):
    secret = "unique-credential-value-for-test"
    monkeypatch.setenv("TEST_DECISION_KEY", secret)
    callbacks = []

    async def audit_callback(record):
        callbacks.append(record)

    response = body(server_echo=secret)
    service = DecisionService(
        [backend("hosted", "typesafe")],
        audit_dir=tmp_path,
        audit_callback=audit_callback,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    )
    result = await service.evaluate({"api_key": secret, "business_text": "preserved"}, QUESTIONS)
    assert result.value is True
    path = next((tmp_path / "decisions").glob("*.json"))
    raw = path.read_text()
    assert secret not in raw
    assert secret not in json.dumps(callbacks)
    record = json.loads(raw)
    assert record["request"]["state"]["api_key"] == "[REDACTED]"
    assert record["request"]["state"]["business_text"] == "preserved"
    assert record["response"]["server_echo"] == "[REDACTED]"
    assert record["response"]["answers"] == response["answers"]
    assert result.details[0]["audit_path"] == str(path)


async def test_failed_audit_abstains_instead_of_returning_unaudited_approval():
    def reject_audit(_):
        raise OSError("disk unavailable")

    service = DecisionService(
        [backend()],
        audit_callback=reject_audit,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body())),
    )
    result = await service.evaluate({}, QUESTIONS)
    assert result.value is None
    assert result.details[0]["reason"] == "audit_failed"


async def test_pending_audit_exists_before_http_send_and_final_callback_runs_once(tmp_path):
    callbacks = []
    seen = []

    def handler(request):
        paths = list((tmp_path / "decisions").glob("*.json"))
        assert len(paths) == 1
        path = paths[0]
        record = json.loads(path.read_text())
        assert record["outcome"] == "started" and record["request_sent"] is True
        assert record["usage"] is None and record["cost_known"] is False
        assert record["response"] is None and record["usage_reported"] is False
        assert record["request"] == json.loads(request.content)
        assert callbacks == []
        seen.append((path, path.stat().st_ino))
        return httpx.Response(200, json=body())

    service = DecisionService(
        [backend()],
        audit_dir=tmp_path,
        audit_callback=callbacks.append,
        transport=httpx.MockTransport(handler),
    )
    result = await service.evaluate({"source": "actual input"}, QUESTIONS)
    assert result.value is True and len(callbacks) == 1
    path, inode = seen[0]
    assert list((tmp_path / "decisions").glob("*.json")) == [path]
    assert path.stat().st_ino != inode
    assert json.loads(path.read_text())["outcome"] == "accepted"


async def test_initial_audit_failure_prevents_decision_dispatch(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("do not overwrite")
    requests = []
    service = DecisionService(
        [backend()],
        audit_dir=blocked,
        transport=httpx.MockTransport(lambda req: requests.append(req)),
    )
    result = await service.evaluate({}, QUESTIONS)
    assert result.value is None and requests == []
    assert result.details[0]["request_sent"] is False
    assert result.details[0]["cost_known"] is True
    assert blocked.read_text() == "do not overwrite"


async def test_hosted_missing_key_abstains_without_call(monkeypatch):
    monkeypatch.delenv("TEST_DECISION_KEY", raising=False)
    calls = []
    service = DecisionService(
        [backend("hosted", "typesafe")],
        transport=httpx.MockTransport(lambda request: calls.append(request)),
    )
    result = await service.evaluate({}, QUESTIONS)
    assert result.value is None
    assert result.details[0]["reason"] == "missing_api_key"
    assert result.details[0]["request_sent"] is False
    assert result.details[0]["cost_known"] is True
    assert not calls


@pytest.mark.parametrize(
    ("usage", "reported", "cost_known", "known_cost"),
    [
        (None, False, False, 0),
        ({}, False, False, 0),
        ({"input_tokens": 0, "output_tokens": 0}, True, True, 0),
        ({"input_tokens": 100, "output_tokens": 3}, True, True, 0.0001),
        ({"output_tokens": 3}, True, False, 0),
        ({"input_tokens": True}, False, False, 0),
    ],
)
async def test_failed_request_usage_is_unknown_unless_provider_reports_it(
    tmp_path, usage, reported, cost_known, known_cost
):
    service = DecisionService(
        [backend(input_cost_per_million=1)],
        audit_dir=tmp_path,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(503, json={"error": "unavailable", "usage": usage})
        ),
    )
    result = await service.evaluate({}, QUESTIONS)
    stage = result.details[0]
    assert result.value is None
    assert stage["request_sent"] is True
    assert stage["usage_reported"] is reported
    assert stage["cost_known"] is cost_known
    assert result.cost_usd == pytest.approx(known_cost)
    record = json.loads(next((tmp_path / "decisions").glob("*.json")).read_text())
    assert record["usage_reported"] is reported
    assert record["cost_known"] is cost_known
    assert (record["usage"] is not None) is reported


async def test_helpers_preserve_step_evidence_and_build_only_bounded_questions():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json=body(
                answers={name: {"type": "noul", "noul": 0.99} for name in payload["questions"]}
            ),
        )

    service = DecisionService([backend()], transport=httpx.MockTransport(handler))
    evidence = {"tool_result": "full unmodified text", "variables": {"id": "R-1"}}
    complete = await service.check_completion(step="Follow this exact step.", evidence=evidence)
    proposed = await service.check_proposal(
        step="Follow this exact step.",
        evidence=evidence,
        proposal={"tool_calls": [{"name": "complete_step", "arguments": {"summary": "done"}}]},
        tools=[],
        final=True,
        output_fields=["status"],
    )
    assert complete.value is True and proposed.value is True
    assert all(request["state"]["observed_evidence"] == evidence for request in requests)
    assert all(
        question["type"] == "noul"
        for request in requests
        for question in request["questions"].values()
    )
    assert requests[1]["state"]["required_output_fields"] == ["status"]
    assert len(requests[0]["questions"]) == 2
    assert len(requests[1]["questions"]) == 3


async def test_backend_threshold_override_and_endpoint_suffix(monkeypatch):
    monkeypatch.setenv("TEST_DECISION_KEY", "example-key")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body(complete=0.9, advance=0.9))

    service = DecisionService(
        [backend("hosted", "typesafe", base_url="https://hosted.test/v1", threshold=0.85)],
        transport=httpx.MockTransport(handler),
    )
    result = await service.evaluate({}, QUESTIONS)
    assert result.value is True
    assert calls[0].url.path == "/v1/systemone"


@pytest.mark.parametrize("threshold", [0.5, 0.0, 1.01])
def test_invalid_threshold_rejected(threshold):
    with pytest.raises(ValueError, match="threshold"):
        DecisionService([], threshold=threshold)
