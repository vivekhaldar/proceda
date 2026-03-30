"""ABOUTME: Defines control tool schemas injected into LLM calls.
ABOUTME: Lets the LLM signal step completion and request user input."""

from __future__ import annotations

from typing import Any

CONTROL_TOOLS = {"complete_step", "request_clarification", "skip_remaining_steps"}


def get_control_tool_schemas() -> list[dict[str, Any]]:
    """Return the tool schemas for runtime control tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": "complete_step",
                "description": (
                    "Signal that the current step is complete. Call this when you have "
                    "finished all actions required for the current step."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Brief summary of what was accomplished in this step.",
                        }
                    },
                    "required": ["summary"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "skip_remaining_steps",
                "description": (
                    "Skip all remaining steps and end the procedure immediately. "
                    "Use this when the final answer is already determined and no "
                    "further steps are needed (e.g. invalid input detected, terminal "
                    "condition reached, or early exit per the SOP)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": (
                                "Summary explaining why remaining steps are being skipped "
                                "and what the final answer is."
                            ),
                        },
                    },
                    "required": ["summary"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "request_clarification",
                "description": (
                    "Ask the user a question and wait for their response. Use this "
                    "whenever a step requires user input, feedback, or a decision. "
                    "This is the ONLY way to get input from the user — printing text "
                    "does not pause for a response."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The clarification question to ask the user.",
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of suggested options.",
                        },
                    },
                    "required": ["question"],
                },
            },
        },
    ]


def is_control_tool(tool_name: str) -> bool:
    """Check if a tool name is a runtime control tool."""
    return tool_name in CONTROL_TOOLS
