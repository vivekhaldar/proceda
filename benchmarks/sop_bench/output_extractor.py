# ABOUTME: Extracts structured output fields from Proceda run events.
# ABOUTME: Tries tool results, XML tags, JSON blocks, then LLM extraction as final fallback.

from __future__ import annotations

import json
import logging
import re
from typing import Any

from proceda.events import EventType, RunEvent

logger = logging.getLogger(__name__)

# Model for LLM-based extraction fallback. Cheap and fast is fine here.
_EXTRACTION_MODEL = "gemini/gemini-2.0-flash"


def extract_output(
    events: list[RunEvent],
    expected_columns: list[str],
) -> dict[str, Any]:
    """Extract output fields from Proceda run events.

    Tries multiple strategies in order:
    1. TOOL_COMPLETED events (deterministic, from tool calls)
    2. XML tags matching expected column names from assistant messages
    3. <final_output>{JSON}</final_output> from assistant messages
    4. Bare JSON blocks from assistant messages
    5. LLM extraction from prose (final fallback for any phrasing)

    Returns the first strategy that finds all expected columns, or the
    best partial result.
    """
    tool_output = _extract_from_tool_results(events, expected_columns)
    msg_output = _extract_from_assistant_messages(events, expected_columns)

    # Message extraction (XML tags, final_output, etc.) reflects the agent's
    # deliberate final answer and takes priority over tool results.  Tool results
    # fill in any gaps not covered by message extraction.
    merged = {**tool_output, **msg_output}
    return {k: v for k, v in merged.items() if k in set(expected_columns)}


def _has_all_columns(output: dict, expected_columns: list[str]) -> bool:
    return all(col in output for col in expected_columns)


def _extract_from_tool_results(
    events: list[RunEvent],
    expected_columns: list[str],
) -> dict[str, Any]:
    """Extract output fields from TOOL_COMPLETED event payloads."""
    expected_set = set(expected_columns)
    output: dict[str, Any] = {}

    tool_completed_events = [e for e in events if e.type == EventType.TOOL_COMPLETED]

    # Use last occurrence of each key (later tool results override earlier ones)
    for event in tool_completed_events:
        result_text = event.payload.get("result", "")
        try:
            result_parsed = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(result_parsed, dict):
            for key, value in result_parsed.items():
                if key in expected_set:
                    output[key] = value
        elif isinstance(result_parsed, str | int | float) and len(expected_columns) == 1:
            # Single expected column + bare value from last tool → assign it
            output[expected_columns[0]] = result_parsed

    return output


def _extract_from_assistant_messages(
    events: list[RunEvent],
    expected_columns: list[str],
) -> dict[str, Any]:
    """Extract from assistant messages using multiple strategies."""
    expected_set = set(expected_columns)

    # Collect all text content from assistant messages and step summaries
    all_content = []
    for event in events:
        if event.type == EventType.MESSAGE_ASSISTANT:
            content = event.payload.get("content", "")
            if content:
                all_content.append(content)
        elif event.type == EventType.SUMMARY_GENERATED:
            summary = event.payload.get("summary", "")
            if summary:
                all_content.append(summary)

    if not all_content:
        return {}

    # Try deterministic strategies across all messages (last message first)
    for content in reversed(all_content):
        # Strategy 1: XML tags matching column names (e.g., <hazard_class>...</hazard_class>)
        result = _extract_xml_tags(content, expected_set)
        if result:
            return result

        # Strategy 2: <final_output>{JSON}</final_output>
        result = _extract_final_output_json(content, expected_set)
        if result:
            return result

        # Strategy 3: Bare JSON object in content
        result = _extract_bare_json(content, expected_set)
        if result:
            return result

    # Strategy 4: LLM extraction from the last few messages as fallback.
    # Concat the last messages (most likely to contain the final answer).
    combined = "\n---\n".join(reversed(all_content[:5]))
    return _extract_via_llm(combined, expected_columns)


def _extract_xml_tags(content: str, expected_set: set[str]) -> dict[str, Any]:
    """Extract values from XML tags matching expected column names."""
    output: dict[str, Any] = {}
    for col in expected_set:
        pattern = rf"<{col}>(.*?)</{col}>"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            output[col] = match.group(1).strip()
    return output


def _extract_via_llm(content: str, expected_columns: list[str]) -> dict[str, Any]:
    """Use an LLM to extract structured fields from prose content.

    Final fallback when deterministic strategies (XML, JSON) fail to find
    all expected columns. Handles any phrasing the agent might use.
    """
    import litellm

    columns_str = ", ".join(expected_columns)
    prompt = (
        f"Extract the following fields from the text below: {columns_str}\n\n"
        f"Return ONLY a JSON object with those field names as keys. "
        f"If a field's value is not present in the text, omit it.\n\n"
        f"Text:\n{content}"
    )

    try:
        response = litellm.completion(
            model=_EXTRACTION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
            response_format={"type": "json_object"},
        )
        result_text = response.choices[0].message.content or ""
        result = json.loads(result_text)
        if isinstance(result, dict):
            expected_set = set(expected_columns)
            return {k: v for k, v in result.items() if k in expected_set}
    except Exception:
        logger.debug("LLM extraction failed", exc_info=True)

    return {}


def _extract_final_output_json(content: str, expected_set: set[str]) -> dict[str, Any]:
    """Extract from <final_output>{JSON}</final_output>."""
    match = re.search(r"<final_output>(.*?)</final_output>", content, re.DOTALL)
    if not match:
        return {}
    try:
        result_dict = json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(result_dict, dict):
        return {}
    return {k: v for k, v in result_dict.items() if k in expected_set}


def _extract_bare_json(content: str, expected_set: set[str]) -> dict[str, Any]:
    """Extract from bare JSON objects in the content."""
    for match in re.finditer(r"\{[^{}]+\}", content):
        try:
            result_dict = json.loads(match.group())
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(result_dict, dict):
            continue
        extracted = {k: v for k, v in result_dict.items() if k in expected_set}
        if extracted:
            return extracted
    return {}
