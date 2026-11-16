import math

import pytest
from ree.aggregation import aggregate_quality, aggregate_subjects, derive_graph, lookup
from ree.models import RewardProfile, Subject


@pytest.mark.parametrize(
    "process,outcome,explanation,row,score,qualified",
    [
        (True, True, True, 1, 1, True),
        (True, True, False, 2, 0.8, False),
        (True, False, True, 3, 1, False),
        (True, False, False, 4, 0.3, False),
        (False, True, True, 5, 0.5, False),
        (False, False, False, 6, 0, False),
    ],
)
def test_six_rows(process, outcome, explanation, row, score, qualified):
    assert lookup(True, process, outcome, explanation, RewardProfile()) == (score, row, qualified)


def test_understanding_failure_is_not_qualified_even_with_perfect_output():
    assert lookup(False, True, True, True, RewardProfile()) == (0, None, False)


def test_selective_edge_propagation_and_three_level_denominators():
    subjects = [
        Subject(subject=str(i), depends_on=deps)
        for i, deps in enumerate([[], [], [0], [1], [2, 3]])
    ]
    # A and B connect through final join: one task, three hops, five subjects.
    scores = [
        {"raw_score": s, "row1": q}
        for s, q in [(1, False), (1, True), (1, True), (1, True), (1, True)]
    ]
    result = aggregate_subjects(subjects, scores)
    assert [s["final_score"] for s in result["subjects"]] == [1, 1, 0, 1, 0]
    assert result["subjects"][2]["blocked_by"] == [0]
    assert result["subjects"][4]["blocked_by"] == [2]
    assert result["tasks"][0]["hops"][1]["denominator"] == 2
    assert result["base"] == 0.5  # mean(1, .5, 0), not flat subject mean .6


def test_independent_tasks_are_not_global_hops():
    subjects = [Subject(subject="A"), Subject(subject="B", depends_on=[0]), Subject(subject="C")]
    assert derive_graph(subjects) == [[[0], [1]], [[2]]]
    result = aggregate_subjects(
        subjects,
        [
            {"raw_score": 1, "row1": True},
            {"raw_score": 0, "row1": False},
            {"raw_score": 1, "row1": True},
        ],
    )
    assert result["base"] == 0.75


@pytest.mark.parametrize("deps", [[[1], [0]], [[2], []], [[0], []], [[1, 1], []]])
def test_invalid_graph_never_flattens(deps):
    with pytest.raises(ValueError):
        derive_graph([Subject(subject=str(i), depends_on=d) for i, d in enumerate(deps)])


def test_added_pass_does_not_dilute_cost_and_extreme_half_life_is_stable():
    criteria = [{"group": "safety", "status": "ok", "value": False, "weight": 2}]
    p = RewardProfile()
    first = aggregate_quality(criteria, p)
    assert (
        aggregate_quality(
            criteria + [{"group": "safety", "status": "ok", "value": True, "weight": 8}], p
        )
        == first
    )
    extreme = aggregate_quality(criteria, RewardProfile(half_life=1e25))
    assert math.isfinite(extreme["quality"])
    assert extreme["quality"] == pytest.approx(1 - 2 / 32)


def test_no_applicable_groups_is_explicit_neutral_quality():
    assert aggregate_quality([], RewardProfile())["quality"] == 1
