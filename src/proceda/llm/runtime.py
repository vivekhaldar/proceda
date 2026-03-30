"""ABOUTME: LLM runtime wrapping LiteLLM for model calls with tool support.
ABOUTME: Handles retries, Gemini schema sanitization, and message formatting."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from proceda.config import LLMConfig
from proceda.exceptions import LLMAPIError, LLMError, LLMRateLimitError, LLMTimeoutError
from proceda.session import RunMessage, ToolCall

logger = logging.getLogger(__name__)


def parse_thinking_tags(text: str) -> tuple[list[str], str]:
    """Extract all <thinking> blocks from text. Returns (blocks, cleaned_text)."""
    blocks: list[str] = []

    def _collect(match: re.Match[str]) -> str:
        blocks.append(match.group(1).strip())
        return ""

    cleaned = re.sub(r"<thinking>(.*?)</thinking>", _collect, text, flags=re.DOTALL)
    return blocks, cleaned.strip()


def format_tool_result(tool_call_id: str, result: str, is_error: bool = False) -> dict[str, Any]:
    """Format a tool result message for the LLM conversation."""
    content = f"Error: {result}" if is_error else result
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def format_assistant_tool_calls(
    tool_calls: list[ToolCall], text: str | None = None
) -> dict[str, Any]:
    """Format an assistant message containing tool calls."""
    return {
        "role": "assistant",
        "content": text or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            for tc in tool_calls
        ],
    }


@dataclass
class LLMResponse:
    """Parsed response from an LLM call."""

    content: str | None
    tool_calls: list[ToolCall]
    reasoning: str | None = None
    raw_response: dict[str, Any] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMRuntime:
    """Wraps LiteLLM for making LLM API calls."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._model = config.model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Make an LLM completion call with retries."""
        return await self._complete_with_retry(messages, tools, temperature)

    async def _complete_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Call the LLM with exponential backoff on retryable errors."""
        import litellm

        max_retries = self._config.max_retries
        last_error: Exception | None = None

        effective_tools = tools
        if tools and (self._model.startswith("gemini/") or self._model.startswith("vertex_ai/")):
            effective_tools = self._sanitize_tools_for_gemini(tools)

        for attempt in range(max_retries + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                    "temperature": temperature
                    if temperature is not None
                    else self._config.temperature,
                    "max_tokens": self._config.max_tokens,
                }

                # Gemini 2.5 Pro requires thinking mode
                if "gemini-2.5-pro" in self._model:
                    kwargs["thinking"] = {"type": "enabled", "budget_tokens": 1024}

                if effective_tools:
                    kwargs["tools"] = effective_tools
                    kwargs["tool_choice"] = "auto"

                if self._config.thinking:
                    kwargs["thinking"] = {"type": self._config.thinking}

                response = await litellm.acompletion(**kwargs)
                return self._parse_response(response)

            except litellm.RateLimitError as e:
                last_error = e
                if attempt < max_retries:
                    wait = 2**attempt
                    logger.warning(
                        "Rate limited (attempt %d/%d), retrying in %ds",
                        attempt + 1,
                        max_retries + 1,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    continue

            except litellm.Timeout as e:
                raise LLMTimeoutError(f"LLM call timed out: {e}") from e

            except litellm.APIError as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        "API error (attempt %d/%d), retrying in 1s",
                        attempt + 1,
                        max_retries + 1,
                    )
                    await asyncio.sleep(1)
                    continue

            except ImportError:
                raise LLMError(
                    "litellm is required but not installed. Install with: pip install litellm"
                )

            except Exception as e:
                raise LLMAPIError(f"LLM call failed: {e}") from e

        # All retries exhausted
        attempts = max_retries + 1
        if isinstance(last_error, litellm.RateLimitError):
            raise LLMRateLimitError(
                f"Rate limit exceeded after {attempts} attempts"
            ) from last_error
        raise LLMAPIError(f"LLM API error after {attempts} attempts: {last_error}") from last_error

    def _sanitize_tools_for_gemini(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deep-copy and sanitize tool schemas for Gemini compatibility."""
        sanitized = copy.deepcopy(tools)
        for tool in sanitized:
            if "function" in tool and "parameters" in tool["function"]:
                tool["function"]["parameters"] = _sanitize_schema_for_gemini(
                    tool["function"]["parameters"]
                )
        return sanitized

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse a LiteLLM response into our internal model."""
        if not response.choices:
            return LLMResponse(content=None, tool_calls=[])
        choice = response.choices[0]
        message = choice.message

        content = message.content
        reasoning = None
        tool_calls: list[ToolCall] = []

        # Extract reasoning from <thinking> tags
        if content and "<thinking>" in content:
            blocks, content = parse_thinking_tags(content)
            if blocks:
                reasoning = "\n\n".join(blocks)

        # Parse tool calls
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {"raw": tc.function.arguments}

                tool_calls.append(
                    ToolCall(
                        id=tc.id or ToolCall.generate_id(),
                        name=tc.function.name,
                        arguments=args,
                    )
                )

        # Extract token usage
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        if hasattr(response, "usage") and response.usage:
            prompt_tokens = getattr(response.usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(response.usage, "completion_tokens", 0) or 0
            total_tokens = getattr(response.usage, "total_tokens", 0) or 0

        return LLMResponse(
            content=content if content else None,
            tool_calls=tool_calls,
            reasoning=reasoning,
            raw_response=response.model_dump() if hasattr(response, "model_dump") else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    def format_messages(self, run_messages: list[RunMessage]) -> list[dict[str, Any]]:
        """Convert internal RunMessages to LiteLLM message format."""
        formatted: list[dict[str, Any]] = []

        for msg in run_messages:
            entry: dict[str, Any] = {
                "role": msg.role,
                "content": msg.content,
            }

            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id

            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]

            formatted.append(entry)

        return formatted


def _sanitize_schema_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize a JSON schema for Gemini compatibility."""
    if "anyOf" in schema:
        replacement = schema.pop("anyOf")[0]
        schema.update(_sanitize_schema_for_gemini(replacement))
        return schema

    if "oneOf" in schema:
        replacement = schema.pop("oneOf")[0]
        schema.update(_sanitize_schema_for_gemini(replacement))
        return schema

    if "allOf" in schema:
        parts = schema.pop("allOf")
        merged: dict[str, Any] = {}
        for part in parts:
            sanitized_part = _sanitize_schema_for_gemini(part)
            for k, v in sanitized_part.items():
                if k == "properties" and k in merged:
                    merged[k].update(v)
                else:
                    merged[k] = v
        schema.update(merged)
        return schema

    if schema.get("type") == "array" and "items" not in schema:
        schema["items"] = {"type": "string"}

    if "properties" in schema:
        for key, prop in schema["properties"].items():
            schema["properties"][key] = _sanitize_schema_for_gemini(prop)

    if "items" in schema and isinstance(schema["items"], dict):
        schema["items"] = _sanitize_schema_for_gemini(schema["items"])

    return schema
