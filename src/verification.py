"""Deterministic checks over explicitly supplied JSON artifacts.

These checks inspect evidence already in a Case. They never read local paths,
fetch URLs, execute tools, infer state, or compute the final reward.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Case, Event, Judgement


class ArtifactCheck(BaseModel):
    """One check at an RFC 6901 JSON pointer in supplied artifact content."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    artifact_ref: str = Field(min_length=1)
    pointer: str
    operation: Literal["equals", "exists", "absent", "changed"]
    expected: Any = None
    baseline_ref: str | None = Field(default=None, min_length=1)

    @field_validator("pointer")
    @classmethod
    def pointer_syntax(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("A JSON pointer must be empty or begin with /")
        if re.search(r"~(?![01])", value):
            raise ValueError("JSON pointer escapes must be ~0 or ~1")
        return value

    @field_validator("expected")
    @classmethod
    def json_expected(cls, value: Any) -> Any:
        def validate(item: Any) -> None:
            if item is None or type(item) in {bool, str, int}:
                return
            if type(item) is float and math.isfinite(item):
                return
            if type(item) is list:
                for element in item:
                    validate(element)
                return
            if type(item) is dict and all(type(key) is str for key in item):
                for element in item.values():
                    validate(element)
                return
            raise ValueError("Expected must be a finite JSON value")

        validate(value)
        return value


class _MissingPath(ValueError):
    pass


class _UnresolvablePointer(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are ambiguous")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("Non-finite values are not JSON")


def _resolve(value: Any, pointer: str) -> Any:
    if not pointer:
        return value
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if type(value) is dict:
            if token not in value:
                raise _MissingPath("Object member is absent")
            value = value[token]
        elif type(value) is list:
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                raise _UnresolvablePointer("Array pointer needs a canonical nonnegative index")
            try:
                index = int(token)
            except ValueError as exc:
                raise _UnresolvablePointer("Array index is not resolvable") from exc
            if index >= len(value):
                raise _MissingPath("Array index is outside the supplied array")
            value = value[index]
        else:
            raise _UnresolvablePointer("Pointer traverses a scalar rather than a container")
    return value


def _json_equal(left: Any, right: Any) -> bool:
    # Python considers False == 0. JSON booleans and numbers are different types.
    if type(left) is bool or type(right) is bool:
        return type(left) is bool and type(right) is bool and left == right
    if type(left) in {int, float} and type(right) in {int, float}:
        return left == right
    if type(left) is not type(right):
        return False
    if type(left) is list:
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if type(left) is dict:
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return left == right


def evaluate_artifact(case: Case, check: ArtifactCheck) -> Judgement:
    """Check existing artifacts, distinguishing contradicted from missing evidence.

    `equals` and `changed` require the target value to resolve. `changed` compares
    the same pointer in the current and baseline artifacts. `exists` and `absent`
    can decide a genuinely missing member/index in a present, valid JSON artifact.
    A malformed pointer traversal or absent artifact never becomes a passing check.
    """
    check = ArtifactCheck.model_validate(check.model_dump())
    refs: list[str] = []
    artifacts = {artifact.ref: artifact for artifact in case.artifacts}

    def missing(reason: str) -> Judgement:
        return Judgement(status="missing_evidence", reason=reason, evidence_refs=refs.copy())

    def read(reference: str) -> Any:
        if reference not in artifacts:
            raise ValueError("Required artifact was not supplied")
        if reference not in refs:
            refs.append(reference)
        document = json.loads(
            artifacts[reference].content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        ArtifactCheck.json_expected(document)  # Reject numeric overflow during JSON parsing.
        return document

    try:
        document = read(check.artifact_ref)
    except (ValueError, RecursionError):
        return missing("Required artifact is missing or is not unambiguous valid JSON")
    try:
        current = _resolve(document, check.pointer)
        present = True
    except _MissingPath:
        if check.operation not in {"exists", "absent"}:
            return missing("Required value is absent at the declared JSON pointer")
        present = False
        current = None
    except _UnresolvablePointer:
        return missing("JSON pointer cannot be resolved against the supplied structure")

    if check.operation == "exists":
        value, reason = (
            present,
            "Declared JSON pointer exists" if present else "Declared JSON pointer is absent",
        )
    elif check.operation == "absent":
        value, reason = (
            not present,
            "Declared JSON pointer is absent" if not present else "Declared JSON pointer exists",
        )
    elif check.operation == "equals":
        value = _json_equal(current, check.expected)
        reason = (
            "Supplied value matches the expected JSON value"
            if value
            else "Supplied value contradicts the expected JSON value"
        )
    else:
        if check.baseline_ref is None:
            return missing("changed requires an explicitly supplied baseline artifact reference")
        try:
            before = _resolve(read(check.baseline_ref), check.pointer)
        except (ValueError, RecursionError):
            return missing(
                "Baseline artifact or required baseline value is missing or unresolvable"
            )
        value = not _json_equal(current, before)
        reason = (
            "Supplied value changed from baseline"
            if value
            else "Supplied value is unchanged from baseline"
        )
    return Judgement(status="ok", value=value, reason=reason, evidence_refs=refs)


def verification_event(name: str, judgement: Judgement) -> Event:
    """Attach a verifier result as evidence using the caller's unique event ref.

    The caller should revalidate its Case after appending this Event so duplicate
    or reserved evidence references are rejected before evaluating Base.
    """
    judgement = Judgement.model_validate(judgement.model_dump())
    return Event(ref=name, kind="verification", output=judgement.model_dump(mode="json"))
