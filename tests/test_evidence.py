import pytest
from ree.evidence import validate_evidence
from ree.models import Case, Event, EvidenceAnnotation, Judgement


def sample():
    return Case(
        request="Update the configuration.",
        response="Updated successfully.",
        events=[Event(ref="failure", kind="message", content="Write denied.")],
    )


def test_distinguishes_issue_from_support_and_preserves_exact_quotes():
    judgement = Judgement(
        status="ok",
        value=False,
        reason="The claimed write contradicts its observed result.",
        annotations=[
            EvidenceAnnotation(
                ref="response", kind="issue", quote="Updated successfully.", reason="False claim"
            ),
            EvidenceAnnotation(
                ref="failure", kind="support", quote="Write denied.", reason="Observed failure"
            ),
        ],
    )
    assert validate_evidence(judgement, sample()) == judgement


@pytest.mark.parametrize(
    "ref,quote", [("invented", None), ("response", "Write denied."), ("failure", "Write succeeded")]
)
def test_rejects_fabricated_annotation_locations(ref, quote):
    judgement = Judgement(
        status="ok",
        value=False,
        reason="Bad annotation",
        annotations=[EvidenceAnnotation(ref=ref, kind="issue", quote=quote, reason="Claim")],
    )
    with pytest.raises(ValueError, match="annotation"):
        validate_evidence(judgement, sample())


def test_citations_do_not_implicitly_become_issues():
    judgement = Judgement(
        status="ok", value=False, reason="Missing operation", evidence_refs=["request"]
    )
    assert validate_evidence(judgement, sample()).annotations == []
