import json

import pytest
from pydantic import ValidationError
from ree.models import Artifact, Case
from ree.verification import ArtifactCheck, evaluate_artifact, verification_event


def case_with(**documents):
    return Case(
        request="Check the supplied result.",
        artifacts=[
            Artifact(ref=ref, name=ref, content=json.dumps(value))
            for ref, value in documents.items()
        ],
    )


def check(
    case, pointer, operation="equals", expected=None, baseline_ref=None, artifact_ref="result"
):
    return evaluate_artifact(
        case,
        ArtifactCheck(
            artifact_ref=artifact_ref,
            pointer=pointer,
            operation=operation,
            expected=expected,
            baseline_ref=baseline_ref,
        ),
    )


def test_missing_artifact_is_not_verified_absence():
    absent = check(case_with(result={}), "/item", "absent")
    assert (absent.status, absent.value, absent.evidence_refs) == ("ok", True, ["result"])
    missing = check(Case(request="Check result"), "/item", "absent")
    assert missing.status == "missing_evidence"
    assert missing.value is None
    assert missing.evidence_refs == []
    assert check(case_with(result={}), "/item", "exists").value is False


def test_required_missing_field_does_not_become_false_outcome():
    result = check(case_with(result={}), "/item", expected=1)
    assert result.status == "missing_evidence"
    assert result.value is None
    assert result.evidence_refs == ["result"]


def test_json_equality_distinguishes_boolean_and_number_recursively():
    evidence = case_with(result={"flag": False, "nested": {"list": [False, 1.0]}})
    assert check(evidence, "/flag", expected=False).value is True
    assert check(evidence, "/flag", expected=0).value is False
    assert check(evidence, "/nested", expected={"list": [0, 1]}).value is False
    assert check(evidence, "/nested", expected={"list": [False, 1]}).value is True


def test_rfc6901_escapes_empty_key_root_and_arrays():
    evidence = case_with(result={"a/b": {"~key": [5, {"": None}]}, "password": "opaque field"})
    assert check(evidence, "/a~1b/~0key/0", expected=5).value is True
    assert check(evidence, "/a~1b/~0key/1/", expected=None).value is True
    assert check(evidence, "/a~1b/~0key/2", "absent").value is True
    assert check(evidence, "/password", "exists").value is True
    assert check(case_with(result=[1, 2]), "", expected=[1, 2]).value is True


def test_malformed_pointer_rejected_and_invalid_array_traversal_not_passed():
    for pointer in ["item", "/a~2b", "/trailing~"]:
        with pytest.raises(ValidationError):
            ArtifactCheck(artifact_ref="result", pointer=pointer, operation="exists")
    evidence = case_with(result=[1])
    for pointer in ["/01", "/-1", "/-", "/not_an_index", "/0/child"]:
        result = check(evidence, pointer, "absent")
        assert result.status == "missing_evidence"
        assert result.value is None


def test_changed_requires_real_resolvable_baseline():
    evidence = case_with(result={"count": 2}, baseline={"count": 1})
    result = check(evidence, "/count", "changed", baseline_ref="baseline")
    assert (result.status, result.value) == ("ok", True)
    assert result.evidence_refs == ["result", "baseline"]
    assert (
        check(
            case_with(result={"count": 2}, baseline={"count": 2}),
            "/count",
            "changed",
            baseline_ref="baseline",
        ).value
        is False
    )
    assert check(evidence, "/count", "changed").status == "missing_evidence"
    assert (
        check(evidence, "/count", "changed", baseline_ref="unobserved").status == "missing_evidence"
    )
    assert (
        check(
            case_with(result={"count": 2}, baseline={}),
            "/count",
            "changed",
            baseline_ref="baseline",
        ).status
        == "missing_evidence"
    )


@pytest.mark.parametrize("content", ["not JSON", '{"x":NaN}', '{"x":1e999}', '{"x":1,"x":2}'])
def test_malformed_or_ambiguous_artifacts_never_pass(content):
    evidence = Case(
        request="Check result", artifacts=[Artifact(ref="result", name="result", content=content)]
    )
    result = check(evidence, "/missing", "absent")
    assert result.status == "missing_evidence"
    assert result.value is None


def test_verification_event_preserves_status_and_real_evidence_refs():
    evidence = case_with(result={"count": 2})
    judgement = check(evidence, "/count", expected=2)
    event = verification_event("verify/count", judgement)
    assert event.kind == "verification"
    assert event.ref == "verify/count"
    assert event.output == judgement.model_dump()
    enriched = Case.model_validate({**evidence.model_dump(), "events": [event.model_dump()]})
    assert enriched.events[0].output["evidence_refs"] == ["result"]
    with pytest.raises(ValidationError):
        Case.model_validate(
            {
                **evidence.model_dump(),
                "events": [verification_event("result", judgement).model_dump()],
            }
        )


def test_expected_only_accepts_finite_json_values():
    for expected in [float("nan"), {1: "non-string-key"}, object()]:
        with pytest.raises(ValidationError):
            ArtifactCheck(artifact_ref="result", pointer="", operation="equals", expected=expected)


def test_verifier_composes_as_registered_plugin_and_base_evidence():
    from ree.evaluator import evaluate
    from ree.models import Subject

    evidence = case_with(result={"timeout": 20})
    evidence.subjects = [Subject(subject="Update the configuration")]
    condition = ArtifactCheck(
        artifact_ref="result", pointer="/timeout", operation="equals", expected=30
    )
    event = verification_event("verify/timeout", evaluate_artifact(evidence, condition))
    evidence = Case.model_validate({**evidence.model_dump(), "events": [event.model_dump()]})

    class FixtureJudge:
        model = "test-fixture"

        def judge(self, name, prompt, payload, schema):
            if name == "gate.explicit_prohibition":
                return schema.model_validate(
                    {
                        "status": "not_applicable",
                        "value": None,
                        "reason": "Fixture contains no hard safety boundary",
                        "evidence_refs": [],
                    }
                )
            if name.startswith("bonus."):
                return schema.model_validate(
                    {
                        "status": "ok",
                        "value": 0.0,
                        "reason": "Fixture supplies only the required configuration",
                        "evidence_refs": [],
                    }
                )
            if name.startswith("base/"):
                assert any(item["ref"] == "verify/timeout" for item in payload["events"])
            return schema.model_validate(
                {
                    "status": "ok",
                    "value": True,
                    "reason": "Fixture judgement",
                    "evidence_refs": ["response"],
                }
            )

    record = evaluate(
        evidence,
        FixtureJudge(),
        plugins={
            "fact.attribute_consistency": lambda case: evaluate_artifact(case, condition),
        },
    )
    assert record["status"] == "ok"
    result = next(
        item for item in record["criteria"] if item["name"] == "fact.attribute_consistency"
    )
    assert result["value"] is False
    assert result["evidence_refs"] == ["result"]
    assert record["reward"] < 1
    unavailable = condition.model_copy(update={"artifact_ref": "unobserved"})
    missing = evaluate(
        evidence,
        FixtureJudge(),
        plugins={
            "fact.attribute_consistency": lambda case: evaluate_artifact(case, unavailable),
        },
    )
    assert missing["status"] == "invalid"
    assert missing["reward"] is None
