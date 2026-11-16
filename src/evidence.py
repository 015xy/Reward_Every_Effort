"""Validate trace locations without inferring an error from a citation alone."""

from __future__ import annotations

import json

from .models import Case, Judgement


def evidence_texts(case: Case) -> dict[str, list[str]]:
    """Observable text fields for exact-quote validation, keyed by evidence ref."""

    def text(value) -> str:
        return (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        )

    result = {
        "request": [case.request],
        "response": [case.response],
        "history": [message.content for message in case.history],
    }
    for event in case.events:
        snippets = [event.content]
        if event.kind == "tool_call":
            snippets.append(text(event.arguments))
        if event.output is not None:
            snippets.append(text(event.output))
        if event.error is not None:
            snippets.append(event.error)
        result[event.ref] = snippets
    result.update({artifact.ref: [artifact.content] for artifact in case.artifacts})
    return result


def validate_evidence(judgement: Judgement, case: Case) -> Judgement:
    texts = evidence_texts(case)
    if not set(judgement.evidence_refs) <= texts.keys():
        raise ValueError("Judge referenced evidence absent from the case")
    for annotation in judgement.annotations:
        if annotation.ref not in texts:
            raise ValueError("Judge annotation referenced evidence absent from the case")
        if annotation.quote is not None and not any(
            annotation.quote in snippet for snippet in texts[annotation.ref]
        ):
            raise ValueError("Judge annotation quote is not present at its evidence ref")
    return judgement
