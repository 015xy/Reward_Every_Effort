"""Generic deterministic checks; extensions consume normalized case evidence."""

from __future__ import annotations

import json
import math

import jsonschema
from referencing import Registry

from .models import Case, EvidenceAnnotation, Judgement


def validate_local_schema(value, schema):
    """Keep deterministic checks offline; unresolved external references are invalid."""
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema, registry=Registry()).validate(value)


def _reject_constant(value):
    raise ValueError(f"Nonstandard JSON constant: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are ambiguous")
        result[key] = value
    return result


def _finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON number overflows the supported representation")
    if isinstance(value, dict):
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _finite_json(item)


def check_json(case: Case) -> Judgement:
    ctx = case.context
    if not ctx or (ctx.output_format != "json" and ctx.output_schema is None):
        return Judgement(status="not_applicable", reason="No declared JSON output contract")
    if not case.evidence_complete and not case.response:
        return Judgement(status="missing_evidence", reason="Final response not provided")
    try:
        result = json.loads(
            case.response, parse_constant=_reject_constant, object_pairs_hook=_unique_object
        )
        _finite_json(result)
        if ctx.output_schema is not None:
            validate_local_schema(result, ctx.output_schema)
    except jsonschema.SchemaError as exc:
        raise ValueError("Invalid task output_schema") from exc
    except (json.JSONDecodeError, jsonschema.ValidationError, ValueError) as exc:
        return Judgement(
            status="ok",
            value=False,
            reason=f"Invalid JSON output: {exc}",
            evidence_refs=["response"],
            annotations=[EvidenceAnnotation(ref="response", kind="issue", reason=str(exc))],
        )
    return Judgement(
        status="ok",
        value=True,
        reason="JSON satisfies declared output contract",
        evidence_refs=["response"],
    )


def check_tools(case: Case) -> Judgement:
    calls = [e for e in case.events if e.kind == "tool_call"]
    if not calls:
        if not case.evidence_complete:
            return Judgement(status="missing_evidence", reason="Tool trace is incomplete")
        return Judgement(status="not_applicable", reason="No tool calls")
    declared = {t.name: t for t in case.harness.tools}
    # Task context may supplement the harness without requiring a separate actor integration.
    if case.context:
        for tool in case.context.harness.get("tools", []):
            from .models import Tool

            parsed = Tool.model_validate(tool)
            declared.setdefault(parsed.name, parsed)
    if not declared and not any(call.harness and call.harness.tools for call in calls):
        return Judgement(status="missing_evidence", reason="Tool schemas were not supplied")
    violations, refs, annotations = [], [], []
    for call in calls:
        available = {t.name: t for t in call.harness.tools} if call.harness else declared
        refs.append(call.ref)
        if not available:
            return Judgement(
                status="missing_evidence",
                reason="Tool schemas for this call were not supplied",
                evidence_refs=[call.ref],
            )
        if call.tool not in available:
            violations.append(f"{call.ref}: undeclared tool")
            annotations.append(
                EvidenceAnnotation(ref=call.ref, kind="issue", reason="Tool was not declared")
            )
            continue
        schema = available[call.tool].parameters
        if schema is None:
            return Judgement(
                status="missing_evidence",
                reason=f"No schema for {call.tool}",
                evidence_refs=[call.ref],
            )
        try:
            _finite_json(call.arguments)
            validate_local_schema(call.arguments, schema)
        except jsonschema.SchemaError as exc:
            raise ValueError(f"Invalid declared schema for {call.tool}") from exc
        except jsonschema.ValidationError as exc:
            violations.append(f"{call.ref}: {exc.message}")
            annotations.append(EvidenceAnnotation(ref=call.ref, kind="issue", reason=exc.message))
    return Judgement(
        status="ok",
        value=not violations,
        reason="; ".join(violations) or "All observed calls satisfy declared schemas",
        evidence_refs=refs,
        annotations=annotations,
    )
