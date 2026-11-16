from ree.aggregation import aggregate_subjects
from ree.models import Subject


def test_aggregation_preserves_complete_request_side_labels():
    subject = Subject(
        subject="Explain the named component",
        clarity="unclear_open",
        expected_output="Useful information about the supplied component",
    )
    result = aggregate_subjects([subject], [{"raw_score": 0.8, "row": 2, "row1": False}])
    saved = result["subjects"][0]
    assert {name: saved[name] for name in Subject.model_fields} == subject.model_dump(mode="json")
