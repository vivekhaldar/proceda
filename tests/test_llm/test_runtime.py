"""ABOUTME: Tests for LLM runtime: retries, Gemini sanitization, formatting, thinking tags.
ABOUTME: Covers Items 1-4 of the LLM runtime backport."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from proceda.config import LLMConfig
from proceda.exceptions import LLMAPIError, LLMRateLimitError, LLMTimeoutError
from proceda.llm.runtime import (
    LLMRuntime,
    _sanitize_schema_for_gemini,
    format_assistant_tool_calls,
    format_tool_result,
    parse_thinking_tags,
)
from proceda.session import ToolCall


def _make_litellm_response(
    content: str = "ok",
    tool_calls: list | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
):
    """Build a fake LiteLLM response object."""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    response = SimpleNamespace(choices=[choice], usage=usage)
    response.model_dump = lambda: {"choices": [{"message": {"content": content}}]}
    return response


# ---------------------------------------------------------------------------
# Item 1: Retry with exponential backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_on_rate_limit():
    import litellm

    config = LLMConfig(max_retries=3)
    runtime = LLMRuntime(config)

    mock_acompletion = AsyncMock(
        side_effect=[
            litellm.RateLimitError("rate limited", "provider", "model", None),
            litellm.RateLimitError("rate limited", "provider", "model", None),
            _make_litellm_response("success"),
        ]
    )

    sleep_waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_waits.append(seconds)

    with (
        patch("litellm.acompletion", mock_acompletion),
        patch("asyncio.sleep", fake_sleep),
    ):
        result = await runtime.complete([{"role": "user", "content": "hi"}])

    assert result.content == "success"
    assert mock_acompletion.call_count == 3
    assert sleep_waits == [1, 2]  # 2**0, 2**1


@pytest.mark.asyncio
async def test_timeout_fails_immediately():
    import litellm

    config = LLMConfig(max_retries=3)
    runtime = LLMRuntime(config)

    mock_acompletion = AsyncMock(side_effect=litellm.Timeout("timed out", "provider", "model"))

    with patch("litellm.acompletion", mock_acompletion):
        with pytest.raises(LLMTimeoutError):
            await runtime.complete([{"role": "user", "content": "hi"}])

    assert mock_acompletion.call_count == 1


@pytest.mark.asyncio
async def test_api_error_retry():
    import litellm

    config = LLMConfig(max_retries=3)
    runtime = LLMRuntime(config)

    mock_acompletion = AsyncMock(
        side_effect=[
            litellm.APIError("server error", "provider", "model", None, 500),
            litellm.APIError("server error", "provider", "model", None, 500),
            _make_litellm_response("recovered"),
        ]
    )

    with (
        patch("litellm.acompletion", mock_acompletion),
        patch("asyncio.sleep", AsyncMock()),
    ):
        result = await runtime.complete([{"role": "user", "content": "hi"}])

    assert result.content == "recovered"
    assert mock_acompletion.call_count == 3


@pytest.mark.asyncio
async def test_retries_exhausted_raises_last_error():
    import litellm

    config = LLMConfig(max_retries=2)
    runtime = LLMRuntime(config)

    mock_acompletion = AsyncMock(
        side_effect=litellm.RateLimitError("rate limited", "provider", "model", None)
    )

    with (
        patch("litellm.acompletion", mock_acompletion),
        patch("asyncio.sleep", AsyncMock()),
    ):
        with pytest.raises(LLMRateLimitError):
            await runtime.complete([{"role": "user", "content": "hi"}])

    assert mock_acompletion.call_count == 3  # initial + 2 retries


# ---------------------------------------------------------------------------
# Item 2: Gemini schema sanitization
# ---------------------------------------------------------------------------


def test_sanitize_anyof():
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    result = _sanitize_schema_for_gemini(schema)
    assert result == {"type": "string"}


def test_sanitize_oneof():
    schema = {"oneOf": [{"type": "number"}, {"type": "string"}]}
    result = _sanitize_schema_for_gemini(schema)
    assert result == {"type": "number"}


def test_sanitize_allof():
    schema = {
        "allOf": [
            {"type": "object", "properties": {"a": {"type": "string"}}},
            {"properties": {"b": {"type": "integer"}}},
        ]
    }
    result = _sanitize_schema_for_gemini(schema)
    assert result["properties"]["a"] == {"type": "string"}
    assert result["properties"]["b"] == {"type": "integer"}


def test_sanitize_array_without_items():
    schema = {"type": "array"}
    result = _sanitize_schema_for_gemini(schema)
    assert result["items"] == {"type": "string"}


def test_sanitize_nested():
    schema = {
        "type": "object",
        "properties": {
            "field1": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "field2": {
                "type": "array",
                "items": {"oneOf": [{"type": "integer"}, {"type": "string"}]},
            },
        },
    }
    result = _sanitize_schema_for_gemini(schema)
    assert result["properties"]["field1"] == {"type": "string"}
    assert result["properties"]["field2"]["items"] == {"type": "integer"}


# ---------------------------------------------------------------------------
# Item 3: Message formatting helpers
# ---------------------------------------------------------------------------


def test_format_tool_result():
    result = format_tool_result("tc_123", "some output")
    assert result == {
        "role": "tool",
        "tool_call_id": "tc_123",
        "content": "some output",
    }


def test_format_tool_result_error():
    result = format_tool_result("tc_123", "not found", is_error=True)
    assert result == {
        "role": "tool",
        "tool_call_id": "tc_123",
        "content": "Error: not found",
    }


def test_format_assistant_tool_calls():
    tcs = [ToolCall(id="tc_1", name="read_file", arguments={"path": "/tmp/x"})]
    result = format_assistant_tool_calls(tcs)
    assert result["role"] == "assistant"
    assert result["content"] == ""
    assert len(result["tool_calls"]) == 1
    tc = result["tool_calls"][0]
    assert tc["id"] == "tc_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "read_file"
    assert tc["function"]["arguments"] == '{"path": "/tmp/x"}'


def test_format_assistant_tool_calls_with_text():
    tcs = [ToolCall(id="tc_1", name="do_thing", arguments={})]
    result = format_assistant_tool_calls(tcs, text="Let me do that.")
    assert result["content"] == "Let me do that."


# ---------------------------------------------------------------------------
# Item 4: Multi-block thinking tag parsing
# ---------------------------------------------------------------------------


def test_parse_no_thinking_blocks():
    blocks, cleaned = parse_thinking_tags("Hello world")
    assert blocks == []
    assert cleaned == "Hello world"


def test_parse_single_thinking_block():
    text = "Before <thinking>reasoning here</thinking> after"
    blocks, cleaned = parse_thinking_tags(text)
    assert blocks == ["reasoning here"]
    assert cleaned == "Before  after"


def test_parse_multiple_thinking_blocks():
    text = "<thinking>first</thinking> middle <thinking>second</thinking> end"
    blocks, cleaned = parse_thinking_tags(text)
    assert blocks == ["first", "second"]
    assert cleaned == "middle  end"


def test_parse_multiline_thinking():
    text = "<thinking>\nline 1\nline 2\n</thinking> rest"
    blocks, cleaned = parse_thinking_tags(text)
    assert blocks == ["line 1\nline 2"]
    assert cleaned == "rest"


# ---------------------------------------------------------------------------
# Item 5: Token usage extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_response_extracts_token_usage():
    config = LLMConfig()
    runtime = LLMRuntime(config)

    response = _make_litellm_response(
        "hello", prompt_tokens=100, completion_tokens=50, total_tokens=150
    )

    result = runtime._parse_response(response)
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 50
    assert result.total_tokens == 150


@pytest.mark.asyncio
async def test_parse_response_handles_missing_usage():
    config = LLMConfig()
    runtime = LLMRuntime(config)

    # Response with no usage attribute
    message = SimpleNamespace(content="hi", tool_calls=None)
    choice = SimpleNamespace(message=message)
    response = SimpleNamespace(choices=[choice])
    response.model_dump = lambda: {}

    result = runtime._parse_response(response)
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
    assert result.total_tokens == 0


def test_empty_choices_preserve_usage_and_raw_response():
    response = _make_litellm_response(prompt_tokens=10, completion_tokens=4, total_tokens=14)
    response.choices = []
    response.model_dump = lambda: {"choices": [], "usage": {"total_tokens": 14}}
    result = LLMRuntime(LLMConfig())._parse_response(response)
    assert result.content is None and result.tool_calls == []
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (10, 4, 14)
    assert result.raw_response == {"choices": [], "usage": {"total_tokens": 14}}


@pytest.mark.asyncio
async def test_provider_configuration_is_forwarded(monkeypatch):
    monkeypatch.setenv("PROCEDA_TEST_KEY", "test-provider-key")
    runtime = LLMRuntime(
        LLMConfig(
            model="openai/test-model",
            api_key_env="PROCEDA_TEST_KEY",
            api_base="https://example.test/v1",
            timeout=12.5,
        )
    )
    completion = AsyncMock(return_value=_make_litellm_response())
    with patch("litellm.acompletion", completion):
        await runtime.complete([{"role": "user", "content": "hi"}])
    kwargs = completion.call_args.kwargs
    assert kwargs["api_key"] == "test-provider-key"
    assert kwargs["api_base"] == "https://example.test/v1"
    assert kwargs["timeout"] == 12.5
    assert kwargs["num_retries"] == 0


@pytest.mark.asyncio
async def test_unset_provider_fields_are_omitted(monkeypatch):
    monkeypatch.delenv("PROCEDA_MISSING_TEST_KEY", raising=False)
    runtime = LLMRuntime(LLMConfig(api_key_env="PROCEDA_MISSING_TEST_KEY"))
    completion = AsyncMock(return_value=_make_litellm_response())
    with patch("litellm.acompletion", completion):
        await runtime.complete([{"role": "user", "content": "hi"}])
    assert "api_key" not in completion.call_args.kwargs
    assert "api_base" not in completion.call_args.kwargs


@pytest.mark.asyncio
async def test_audit_success_preserves_payload_and_redacts_credentials(tmp_path, monkeypatch):
    secret = "a-private-key-that-must-not-be-logged"
    monkeypatch.setenv("PROCEDA_TEST_KEY", secret)
    runtime = LLMRuntime(LLMConfig(api_key_env="PROCEDA_TEST_KEY", audit_dir=str(tmp_path)))
    response = _make_litellm_response(
        "done", prompt_tokens=10, completion_tokens=2, total_tokens=12
    )
    response.model_dump = lambda: {
        "model": "resolved-model",
        "choices": [{"message": {"content": "done"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        "metadata": {"headers": {"secret-header": "must-not-be-logged"}, "api_key": secret},
    }
    tools = [{"type": "function", "function": {"name": "read_order", "parameters": {}}}]
    messages = [{"role": "user", "content": f"look up {secret}"}]
    with patch("litellm.acompletion", AsyncMock(return_value=response)):
        result = await runtime.complete(messages, tools)
    text = next(tmp_path.glob("*.json")).read_text()
    record = json.loads(text)
    assert secret not in text
    assert "must-not-be-logged" not in text
    assert record["request"]["messages"][0]["content"] == "look up [REDACTED]"
    assert record["request"]["tools"] == tools
    assert "api_key" not in record["request"]
    assert record["response_model"] == "resolved-model"
    assert record["usage"] == {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    assert record["usage_reported"] is True
    assert record["elapsed_ms"] >= 0
    assert record["status"] == "success"
    assert result.total_tokens == 12
    assert messages[0]["content"] == f"look up {secret}"


@pytest.mark.asyncio
async def test_audit_records_each_retry_and_unknown_failure_usage(tmp_path, monkeypatch):
    import litellm

    secret = "private-retry-key"
    monkeypatch.setenv("PROCEDA_TEST_KEY", secret)
    runtime = LLMRuntime(
        LLMConfig(
            api_key_env="PROCEDA_TEST_KEY",
            audit_dir=str(tmp_path),
            max_retries=1,
        )
    )
    completion = AsyncMock(
        side_effect=[
            litellm.APIError(f"failed with {secret}", "provider", "model", None, 500),
            _make_litellm_response("ok", prompt_tokens=4, completion_tokens=2, total_tokens=6),
        ]
    )
    with patch("litellm.acompletion", completion), patch("asyncio.sleep", AsyncMock()):
        await runtime.complete([{"role": "user", "content": "hi"}])
    records = [json.loads(path.read_text()) for path in sorted(tmp_path.glob("*.json"))]
    assert len(records) == 2
    assert records[0]["call_id"] == records[1]["call_id"]
    assert [record["attempt"] for record in records] == [1, 2]
    assert [record["status"] for record in records] == ["error", "success"]
    assert records[0]["usage"] is None
    assert records[0]["usage_reported"] is False
    assert records[1]["usage"]["total_tokens"] == 6
    assert secret not in json.dumps(records)


@pytest.mark.asyncio
async def test_failed_call_redacts_audit_and_raised_error(tmp_path, monkeypatch):
    secret = "private-failed-call-key"
    monkeypatch.setenv("PROCEDA_TEST_KEY", secret)
    runtime = LLMRuntime(LLMConfig(api_key_env="PROCEDA_TEST_KEY", audit_dir=str(tmp_path)))
    completion = AsyncMock(side_effect=RuntimeError(f"Authorization: Bearer {secret}"))
    with patch("litellm.acompletion", completion), pytest.raises(LLMAPIError) as exc:
        await runtime.complete([{"role": "user", "content": "hi"}])
    assert secret not in str(exc.value)
    assert exc.value.__suppress_context__
    record = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert secret not in json.dumps(record)
    assert record["error"]["type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_concurrent_audit_calls_have_distinct_files(tmp_path):
    runtime = LLMRuntime(LLMConfig(audit_dir=str(tmp_path)))
    completion = AsyncMock(return_value=_make_litellm_response())
    with patch("litellm.acompletion", completion):
        await asyncio.gather(
            *[runtime.complete([{"role": "user", "content": str(i)}]) for i in range(8)]
        )
    records = [json.loads(path.read_text()) for path in tmp_path.glob("*.json")]
    assert len(records) == 8
    assert len({record["call_id"] for record in records}) == 8


@pytest.mark.asyncio
async def test_audit_retains_reported_usage_on_failed_attempt(tmp_path):
    import litellm

    error = litellm.APIError("provider error", "provider", "model", None, 500)
    error.response = SimpleNamespace(
        json=lambda: {
            "error": {"message": "provider error"},
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
    )
    runtime = LLMRuntime(LLMConfig(audit_dir=str(tmp_path), max_retries=1))
    completion = AsyncMock(
        side_effect=[
            error,
            _make_litellm_response(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        ]
    )
    with patch("litellm.acompletion", completion), patch("asyncio.sleep", AsyncMock()):
        await runtime.complete([{"role": "user", "content": "hi"}])
    records = [json.loads(path.read_text()) for path in tmp_path.glob("*.json")]
    assert len(records) == 2
    assert all(record["usage_reported"] for record in records)
    assert sum(record["usage"]["total_tokens"] for record in records) == 18


@pytest.mark.asyncio
async def test_overall_timeout_bounds_provider_setup_and_is_audited(tmp_path):
    async def never_finishes(**kwargs):
        await asyncio.Event().wait()

    runtime = LLMRuntime(LLMConfig(timeout=0.01, audit_dir=str(tmp_path)))
    with patch("litellm.acompletion", never_finishes), pytest.raises(LLMTimeoutError):
        await runtime.complete([{"role": "user", "content": "hi"}])
    records = [json.loads(path.read_text()) for path in tmp_path.glob("*.json")]
    assert len(records) == 1
    assert records[0]["error"]["type"] == "TimeoutError"
    assert records[0]["usage_reported"] is False
