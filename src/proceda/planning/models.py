"""ABOUTME: Defines and validates the source-derived, data-only planning representation.
ABOUTME: Rejects unsupported operations and bindings before any plan can execute.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from proceda.skill import Skill

IR_VERSION = "1"
VALIDATION_VERSION = "3"


class PlanValidationError(ValueError):
    """A proposed plan cannot safely be interpreted by this IR version."""


@dataclass(frozen=True)
class ValueRef:
    kind: str
    key: str | None = None
    step: int | None = None
    tool: str | None = None
    path: str | None = None
    value: Any = None
    source_quote: str = ""


@dataclass(frozen=True)
class RegexExtractor:
    field: str
    source: ValueRef
    pattern: str
    group: int | str = 0
    source_quote: str = ""


@dataclass(frozen=True)
class ChoiceSpec:
    field: str
    instructions: str
    criteria: dict[str, str]
    inputs: dict[str, ValueRef]
    source_quote: str
    kind: Literal["choice", "predicate"] = "choice"


@dataclass(frozen=True)
class Condition:
    ref: ValueRef
    equals: Any
    source_quote: str


@dataclass(frozen=True)
class ToolPlan:
    tool: str
    arguments: dict[str, ValueRef]
    source_quote: str
    when: Condition | None = None


@dataclass(frozen=True)
class RenderSpec:
    format: str
    fields: dict[str, ValueRef]
    source_quote: str


@dataclass(frozen=True)
class StepPlan:
    index: int
    kind: str
    source_quote: str
    residual_obligations: tuple[str, ...] = ()
    decision: ChoiceSpec | None = None
    extractors: tuple[RegexExtractor, ...] = ()
    tools: tuple[ToolPlan, ...] = ()
    render: RenderSpec | None = None


@dataclass(frozen=True)
class ProcedurePlan:
    ir_version: str
    steps: tuple[StepPlan, ...]

    def get_step(self, index: int) -> StepPlan | None:
        return next((step for step in self.steps if step.index == index), None)

    def to_dict(self) -> dict[str, Any]:
        # JSON round-trip also removes tuples from the public wire representation.
        return json.loads(json.dumps(asdict(self), allow_nan=False))


def _object(value: Any, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise PlanValidationError(f"Invalid object keys; expected {sorted(required)}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanValidationError(f"{label} must be a nonempty string")
    return value


def _quote(value: Any, source: str) -> str:
    value = _text(value, "source_quote")
    if value not in source:
        raise PlanValidationError("source_quote is not an exact source substring")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(k, str) or not k for k in value):
        raise PlanValidationError(f"{label} must be a string-keyed object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PlanValidationError(f"{label} must be a list")
    return value


@dataclass
class _Context:
    skill: Skill
    tools: dict[str, dict[str, Any]]
    inputs: set[str]
    facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    receipt_contracts: set[tuple[int, str]] = field(default_factory=set)
    mode: str = "full"

    def ref(self, raw: Any, index: int, source: str) -> ValueRef:
        item = _object(
            raw,
            {"kind", "key", "step", "tool", "path", "value", "source_quote"},
            {"kind", "source_quote"},
        )
        kind = item["kind"]
        quote = _quote(item["source_quote"], source)
        fields = {k for k, v in item.items() if v is not None} - {"kind", "source_quote"}
        if kind == "input":
            if (
                fields != {"key"}
                or not isinstance(item.get("key"), str)
                or item["key"] not in self.inputs
            ):
                raise PlanValidationError("Input ref must name an available input field")
        elif kind == "fact":
            if (
                fields != {"step", "key"}
                or type(item.get("step")) is not int
                or not isinstance(item.get("key"), str)
            ):
                raise PlanValidationError("Fact ref requires step and key only")
            if (item["step"], item["key"]) not in self.facts or item["step"] > index:
                raise PlanValidationError("Fact ref is not a prior accepted fact or extractor")
        elif kind == "receipt":
            if fields != {"step", "tool", "path"} or type(item.get("step")) is not int:
                raise PlanValidationError("Receipt ref requires step, tool and JSON pointer")
            if (
                not 1 <= item["step"] < index
                or not isinstance(item.get("tool"), str)
                or item["tool"] not in self.tools
            ):
                raise PlanValidationError("Receipt ref requires a prior step and canonical tool")
            if (item["step"], item["tool"]) not in self.receipt_contracts:
                raise PlanValidationError("Receipt ref requires a prior admitted tool plan")
            path = item["path"]
            if not isinstance(path, str) or (path and not path.startswith("/")):
                raise PlanValidationError("Receipt path must be a JSON pointer")
            if re.search(r"~(?![01])", path):
                raise PlanValidationError("Invalid JSON pointer escape")
        elif kind == "literal":
            if fields - {"value"} or "value" not in item:
                raise PlanValidationError("Literal ref requires only an explicit value")
            value = item["value"]
            if isinstance(value, (dict, list)):
                raise PlanValidationError("Only scalar source literals are supported")
            try:
                literal = value if isinstance(value, str) else json.dumps(value, allow_nan=False)
            except (ValueError, TypeError) as exc:
                raise PlanValidationError("Literal must be finite JSON") from exc
            if not literal or literal not in quote:
                raise PlanValidationError("Literal must appear exactly in its source quote")
        else:
            raise PlanValidationError(f"Unsupported value ref kind: {kind}")
        return ValueRef(
            kind=kind,
            key=item.get("key"),
            step=item.get("step"),
            tool=item.get("tool"),
            path=item.get("path"),
            value=item.get("value"),
            source_quote=quote,
        )


def _validation_context(
    raw: Any,
    skill: Skill,
    tool_schemas: list[dict[str, Any]],
    input_fields: list[str],
    mode: str,
) -> tuple[_Context, list[Any]]:
    """Root/schema defects cannot be recovered by replacing an individual step."""
    if mode not in {"full", "business_decisions"}:
        raise PlanValidationError("Unsupported planning mode")
    item = _object(raw, {"ir_version", "steps"}, {"ir_version", "steps"})
    if item["ir_version"] != IR_VERSION:
        raise PlanValidationError("Unsupported IR version")
    tools: dict[str, dict[str, Any]] = {}
    for schema in tool_schemas:
        function = schema.get("function", {})
        name = function.get("name")
        if not isinstance(name, str) or name in tools:
            raise PlanValidationError("Tool schemas need unique canonical names")
        tools[name] = function.get("parameters", {})
    context = _Context(skill, tools, set(input_fields), mode=mode)
    raw_steps = _list(item["steps"], "steps")
    if len(raw_steps) != skill.step_count:
        raise PlanValidationError("Every SOP step must be covered exactly once")
    return context, raw_steps


def validate_plan(
    raw: Any,
    skill: Skill,
    tool_schemas: list[dict[str, Any]],
    input_fields: list[str],
    *,
    mode: str = "full",
) -> ProcedurePlan:
    """Validate structural/source boundaries, not prove semantic equivalence to prose."""
    context, raw_steps = _validation_context(raw, skill, tool_schemas, input_fields, mode)
    steps = tuple(
        _step(raw_step, source.index, context)
        for raw_step, source in zip(raw_steps, skill.steps, strict=True)
    )
    return ProcedurePlan(IR_VERSION, steps)


def validate_partial_plan(
    raw: Any,
    skill: Skill,
    tool_schemas: list[dict[str, Any]],
    input_fields: list[str],
    *,
    mode: str = "full",
) -> tuple[ProcedurePlan, list[dict[str, Any]]]:
    """Lose optimization conservatively; never repair or admit an invalid operation.

    Validate in source order. A rejected step loses every tentative fact and tool
    contract, then becomes an LLM step containing its entire original instructions.
    Dependent steps must validate against only the surviving evidence contracts.
    The caller must independently review the resulting plan before executing it.
    """
    context, raw_steps = _validation_context(raw, skill, tool_schemas, input_fields, mode)
    steps, downgrades = [], []
    for raw_step, source in zip(raw_steps, skill.steps, strict=True):
        tentative = _Context(
            skill,
            context.tools,
            context.inputs,
            dict(context.facts),
            set(context.receipt_contracts),
            mode,
        )
        try:
            step = _step(raw_step, source.index, tentative)
        except PlanValidationError as exc:
            downgrades.append({"step_index": source.index, "reason": str(exc)})
            step = _step(
                {
                    "index": source.index,
                    "kind": "llm",
                    "source_quote": source.content,
                    "residual_obligations": [source.content],
                },
                source.index,
                context,
            )
        else:
            context = tentative
        steps.append(step)
    recovered = ProcedurePlan(IR_VERSION, tuple(steps))
    # The ordinary strict validator remains the final execution boundary.
    return validate_plan(
        recovered.to_dict(), skill, tool_schemas, input_fields, mode=mode
    ), downgrades


def _step(raw: Any, index: int, ctx: _Context) -> StepPlan:
    item = _object(
        raw,
        {
            "index",
            "kind",
            "source_quote",
            "residual_obligations",
            "decision",
            "extractors",
            "tools",
            "render",
        },
        {"index", "kind", "source_quote", "residual_obligations"},
    )
    source = ctx.skill.get_step(index).content
    if type(item["index"]) is not int or item["index"] != index:
        raise PlanValidationError("Steps must match the original order and indices")
    if item["source_quote"] != source:
        raise PlanValidationError("Step source_quote must preserve the entire original step")
    residual = tuple(_quote(q, source) for q in _list(item["residual_obligations"], "residual"))
    kind = item["kind"]
    decision = None
    render = None
    calls: list[ToolPlan] = []
    extractors: list[RegexExtractor] = []
    if not isinstance(kind, str) or kind not in {"decision", "tool", "render", "llm"}:
        raise PlanValidationError(f"Unsupported step kind: {kind}")
    if ctx.mode == "business_decisions" and kind == "render":
        raise PlanValidationError("Business mode does not admit rendering operations")
    for collection in ("tools", "extractors"):
        if collection in item:
            _list(item[collection], collection)
    for optional in ("decision", "render"):
        if item.get(optional) is not None and not isinstance(item[optional], dict):
            raise PlanValidationError(f"{optional} must be an object or null")
    if kind != "decision" and (item.get("decision") is not None or item.get("extractors")):
        raise PlanValidationError("Only decision steps may contain choices or extractors")
    if kind != "tool" and item.get("tools"):
        raise PlanValidationError("Only tool steps may contain calls")
    if kind != "render" and item.get("render") is not None:
        raise PlanValidationError("Only render steps may contain rendering")
    if kind in {"decision", "render"} and residual:
        raise PlanValidationError("Whole-step completion cannot leave residual obligations")
    if kind == "llm" and residual != (source,):
        raise PlanValidationError("LLM steps must retain their entire original instructions")
    if ctx.mode == "business_decisions" and kind == "tool" and residual != (source,):
        raise PlanValidationError("Observation contracts must retain the entire original step")
    if kind == "decision":
        for extraction in _list(item.get("extractors", []), "extractors"):
            ex = _object(
                extraction,
                {"field", "source", "pattern", "group", "source_quote"},
                {"field", "source", "pattern", "source_quote"},
            )
            field_name = _text(ex["field"], "extractor field")
            if (index, field_name) in ctx.facts:
                raise PlanValidationError("Duplicate extracted field")
            quote = _quote(ex["source_quote"], source)
            pattern = _text(ex["pattern"], "regex pattern")
            if len(pattern) > 512:
                raise PlanValidationError("Regex pattern exceeds the 512-character limit")
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise PlanValidationError("Invalid regex") from exc
            group = ex.get("group", 0)
            if not (
                (type(group) is int and 0 <= group <= compiled.groups)
                or (isinstance(group, str) and group in compiled.groupindex)
            ):
                raise PlanValidationError("Regex group does not exist")
            ref = ctx.ref(ex["source"], index, source)
            extractors.append(RegexExtractor(field_name, ref, pattern, group, quote))
            ctx.facts[index, field_name] = True
        choice = _object(
            item.get("decision"),
            {"field", "instructions", "criteria", "inputs", "source_quote", "kind"},
            {"field", "instructions", "criteria", "inputs", "source_quote"},
        )
        field_name = _text(choice["field"], "choice field")
        if (index, field_name) in ctx.facts:
            raise PlanValidationError("Choice field collides with extractor")
        criteria = _mapping(choice["criteria"], "criteria")
        decision_kind = choice.get("kind", "choice")
        if decision_kind == "predicate":
            if set(criteria) != {"true"}:
                raise PlanValidationError("Predicate requires exactly one true criterion")
            _quote(criteria["true"], source)
        elif decision_kind == "choice":
            if not 2 <= len(criteria) <= 64:
                raise PlanValidationError("Choice requires 2–64 labels")
            for label, criterion in criteria.items():
                if not re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", source):
                    raise PlanValidationError(
                        "Choice labels must appear as whole outcomes in the original step"
                    )
                _text(criterion, "choice criterion")
        else:
            raise PlanValidationError("Unsupported decision kind")
        instructions = _text(choice["instructions"], "choice instructions")
        if ctx.mode == "business_decisions" and instructions != source:
            raise PlanValidationError(
                "Business decisions require the entire original step as policy"
            )
        inputs = {
            k: ctx.ref(v, index, source)
            for k, v in _mapping(choice["inputs"], "choice inputs").items()
        }
        if not inputs:
            raise PlanValidationError("Choice must bind evidence")
        decision = ChoiceSpec(
            field_name,
            instructions,
            dict(criteria),
            inputs,
            _quote(choice["source_quote"], source),
            decision_kind,
        )
        ctx.facts[index, field_name] = True
    elif kind == "tool":
        for call_raw in _list(item.get("tools"), "tools"):
            call = _object(
                call_raw,
                {"tool", "arguments", "source_quote", "when"},
                {"tool", "arguments", "source_quote"},
            )
            name = call["tool"]
            if not isinstance(name, str) or name not in ctx.tools:
                raise PlanValidationError("Call must use an allowed canonical tool name")
            args = _mapping(call["arguments"], "arguments")
            schema = ctx.tools[name]
            properties = schema.get("properties", {})
            if set(args) - set(properties) or set(schema.get("required", [])) - set(args):
                raise PlanValidationError("Tool argument names do not match its schema")
            when = None
            if call.get("when") is not None:
                condition = _object(
                    call["when"],
                    {"ref", "equals", "source_quote"},
                    {"ref", "equals", "source_quote"},
                )
                quote = _quote(condition["source_quote"], source)
                # The comparison constant has exactly the same source restrictions as a literal.
                literal = ctx.ref(
                    {"kind": "literal", "value": condition["equals"], "source_quote": quote},
                    index,
                    source,
                )
                when = Condition(ctx.ref(condition["ref"], index, source), literal.value, quote)
            calls.append(
                ToolPlan(
                    name,
                    {k: ctx.ref(v, index, source) for k, v in args.items()},
                    _quote(call["source_quote"], source),
                    when,
                )
            )
        if not calls or len(calls) > 16:
            raise PlanValidationError("Tool step requires 1–16 independent calls")
        ctx.receipt_contracts.update((index, call.tool) for call in calls)
    elif kind == "render":
        if index != ctx.skill.step_count:
            raise PlanValidationError("Only the final step may be rendered")
        rr = _object(
            item.get("render"),
            {"format", "fields", "source_quote"},
            {"format", "fields", "source_quote"},
        )
        if not isinstance(rr["format"], str) or rr["format"] not in {"json", "xml", "json_xml"}:
            raise PlanValidationError("Only JSON/XML rendering is supported")
        fields = {
            k: ctx.ref(v, index, source) for k, v in _mapping(rr["fields"], "render fields").items()
        }
        if not fields or not ctx.skill.output_fields or set(fields) != set(ctx.skill.output_fields):
            raise PlanValidationError("Render fields must exactly match declared output_fields")
        if any(ref.kind not in {"receipt", "fact"} for ref in fields.values()):
            raise PlanValidationError(
                "Rendered values require accepted facts or successful receipts"
            )
        if rr["format"] in {"xml", "json_xml"} and any(
            not re.fullmatch(r"[A-Za-z_][\w.-]*", k) for k in fields
        ):
            raise PlanValidationError("Render fields must be valid XML tag names")
        render = RenderSpec(rr["format"], fields, _quote(rr["source_quote"], source))
    return StepPlan(
        index, kind, source, residual, decision, tuple(extractors), tuple(calls), render
    )
