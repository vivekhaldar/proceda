"""ABOUTME: Builds system and step prompts for LLM calls during skill execution.
ABOUTME: Assembles skill metadata, step instructions, variables, and tool info into prompt text."""

from __future__ import annotations

from proceda.skill import Skill, SkillStep


def build_system_prompt(skill: Skill, variables: dict[str, str] | None = None) -> str:
    """Build the system prompt for a skill execution."""
    parts = [
        f"You are executing the skill: {skill.name}",
        f"Description: {skill.description}",
        "",
        "You must follow the steps in order. For each step:",
        "1. Read the step instructions carefully",
        "2. Use the available tools to accomplish the step's goals",
        "3. Call `complete_step` with a summary when the step is done",
        "4. If you need clarification, call `request_clarification`",
        "",
        "Important rules:",
        "- Complete one step at a time",
        "- Do not skip steps unless they are marked [OPTIONAL]",
        "- Do not proceed to the next step until you call `complete_step`",
        "- If a step is marked [APPROVAL REQUIRED] or [PRE-APPROVAL REQUIRED], "
        "the system will handle approval automatically",
        "- If a step asks you to ask the user something, wait for their response, "
        "or get input from the user, you MUST call `request_clarification` with your "
        "question. Do NOT just print the question as text — that does not pause for "
        "user input. Only `request_clarification` actually reaches the user.",
        "- If a step instructs you to call a specific tool, you MUST call it — do not "
        "skip tool calls based on your own judgment about whether the result is needed.",
        "- If a step says to terminate the procedure, or you determine at any step that "
        "the final answer is already known (e.g. invalid input, terminal condition, "
        "early exit per the instructions), call `skip_remaining_steps` with a summary "
        "that includes your final answer. This ends the procedure immediately.",
        "- When a step asks you to re-run or re-evaluate using a tool you already called "
        "in an earlier step, compare the results you already have from different steps "
        "rather than re-calling the same tool. Stateless tools return identical results "
        "when called with the same arguments.",
    ]

    if skill.output_fields:
        fields = ", ".join(skill.output_fields)
        tags = ", ".join(f"<{f}>value</{f}>" for f in skill.output_fields)
        parts.append("")
        parts.append(f"Output fields: {fields}")
        parts.append(
            "CRITICAL: When completing the FINAL step (or when calling "
            "`skip_remaining_steps`), you MUST include each output field as "
            f"literal XML tags in your summary: {tags}. "
            "Do not describe the tags in prose — emit them directly. "
            "The tags must appear verbatim in your summary text."
        )

    if variables:
        parts.append("")
        parts.append("Variables provided:")
        for key, value in variables.items():
            parts.append(f"  {key} = {value}")

    if skill.required_tools:
        parts.append("")
        parts.append(f"Required tools: {', '.join(skill.required_tools)}")

    parts.append("")
    parts.append("Full skill definition:")
    parts.append("---")

    for step in skill.steps:
        markers = ""
        if step.markers:
            markers = " " + " ".join(f"[{m.value}]" for m in step.markers)
        parts.append(f"### Step {step.index}: {step.title}{markers}")
        parts.append(step.content)
        parts.append("")

    return "\n".join(parts)


def build_step_prompt(
    step: SkillStep,
    is_last_step: bool = False,
    output_fields: list[str] | None = None,
) -> str:
    """Build a user-facing prompt for starting a specific step."""
    markers_text = ""
    if step.markers:
        markers_text = " (" + ", ".join(m.value for m in step.markers) + ")"

    prompt = (
        f"Now execute Step {step.index}: {step.title}{markers_text}\n\n"
        f"{step.content}\n\n"
        f"When complete, call `complete_step` with a summary of what you did."
    )

    if is_last_step and output_fields:
        tags = "\n".join(f"  <{f}>YOUR_VALUE</{f}>" for f in output_fields)
        prompt += (
            "\n\nCRITICAL: This is the final step. Your complete_step summary "
            "MUST include these output fields as literal XML tags:\n"
            + tags
            + "\nEmit the tags directly — do not describe them in prose."
        )

    return prompt
