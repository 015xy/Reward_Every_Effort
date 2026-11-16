import json

import pytest
from ree.adaptation import AdaptationError, Coverage, Increment, Overlay, adapt
from ree.models import TaskContext
from ree.pack import load_pack


def context():
    return TaskContext(
        task_description="Modify the configuration.", success_criteria=["Preserve other keys."]
    )


class Designer:
    def __init__(self, factory):
        self.factory = factory
        self.calls = 0

    def judge(self, name, prompt, payload, schema):
        self.calls += 1
        return self.factory(payload)


def proposal(payload, name="task.consistency"):
    return Overlay(
        coverage=[
            Coverage(requirement=x, covered_by=[name], action="increment")
            for x in payload["requirements"]
        ],
        increments=[
            Increment(
                name=name,
                group="hallucination",
                description="Explicit local consistency condition.",
                prompt="Compare the explicitly requested configuration invariant with evidence. "
                "Return missing_evidence when needed; include positive and negative cases.",
            )
        ],
    )


def test_context_only_freezes_baseline_and_covers_all_requirements(tmp_path):
    baseline = load_pack()
    before = {x: baseline.prompt(x) for x in baseline.manifest.base_prompts.values()}
    target = tmp_path / "task"
    result = adapt(context(), target, baseline)
    frozen = load_pack(target)
    assert result["fingerprint"] == frozen.fingerprint
    assert result["validation"] == {"structure": "passed", "semantic_calibration": "not_run"}
    assert result["mode"] == "context_only"
    assert {x["requirement"] for x in result["coverage"]} == {
        "Modify the configuration.",
        "Preserve other keys.",
    }
    assert frozen.manifest.profile == baseline.manifest.profile
    assert frozen.manifest.criteria == baseline.manifest.criteria
    assert {x: frozen.prompt(x) for x in before} == before
    assert json.loads((target / "task_context.json").read_text())["success_criteria"] == [
        "Preserve other keys."
    ]
    with pytest.raises(AdaptationError, match="already exists"):
        adapt(context(), target, baseline)


def test_designer_cannot_choose_paths_and_is_bounded(tmp_path):
    client = Designer(lambda p: proposal(p, "../escape"))
    target = tmp_path / "task"
    with pytest.raises(AdaptationError, match="Unsafe increment name"):
        adapt(context(), target, load_pack(), client)
    assert client.calls == 2
    assert not target.exists()
    assert not (tmp_path / "escape").exists()


def test_duplicate_or_unjustified_increment_rejected(tmp_path):
    def duplicate(p):
        result = proposal(p)
        result.increments.append(result.increments[0])
        return result

    client = Designer(duplicate)
    with pytest.raises(AdaptationError, match="Duplicate increment names"):
        adapt(context(), tmp_path / "task", load_pack(), client)
    assert client.calls == 2


def test_proposal_repairs_once_and_preserves_existing_policy(tmp_path):
    def factory(p):
        result = proposal(p)
        if not p["previous_validation_errors"]:
            result.coverage.pop()
        return result

    client = Designer(factory)
    baseline = load_pack()
    target = tmp_path / "task"
    result = adapt(context(), target, baseline, client)
    assert client.calls == 2
    assert result["attempts"][0]["status"] == "rejected"
    assert result["attempts"][1]["status"] == "valid"
    frozen = load_pack(target)
    assert len(frozen.manifest.criteria) == len(baseline.manifest.criteria) + 1
    assert frozen.manifest.criteria[-1].weight == 2
    assert frozen.manifest.profile == baseline.manifest.profile
    for criterion in baseline.manifest.criteria:
        assert frozen.prompt(criterion.prompt) == baseline.prompt(criterion.prompt)


def test_context_change_changes_pack_fingerprint(tmp_path):
    a = adapt(context(), tmp_path / "a", load_pack())
    b = adapt(
        TaskContext(task_description="Modify a different configuration."),
        tmp_path / "b",
        load_pack(),
    )
    assert a["fingerprint"] != b["fingerprint"]


def test_credentials_stay_outside_frozen_context(tmp_path):
    task = TaskContext(
        task_description="Modify a configuration.",
        harness={"connection": {"api_key": "do-not-store"}},
    )
    target = tmp_path / "task"
    with pytest.raises(AdaptationError, match="credential field"):
        adapt(task, target, load_pack())
    assert not target.exists()


def test_context_coverage_routes_by_explicit_fields_not_word_guessing(tmp_path):
    task = TaskContext(
        task_description="Generate a report.",
        success_criteria=["The totals are correct."],
        constraints=["Mention JSON in the report.", "The totals are correct."],
        output_format="json",
        output_schema={"type": "object"},
    )
    report = adapt(task, tmp_path / "task", load_pack())
    coverage = {entry["requirement"]: entry["covered_by"] for entry in report["coverage"]}
    assert coverage["Output format: json"] == ["format.syntax"]
    assert coverage['Output schema: {"type":"object"}'] == ["format.syntax"]
    assert coverage["Mention JSON in the report."] == ["base.process", "base.result"]
    assert set(coverage["The totals are correct."]) == {"base.process", "base.result"}
    assert report["requirement_sources"]["The totals are correct."] == [
        "success_criteria[0]",
        "constraints[1]",
    ]


def test_non_json_output_contract_does_not_claim_deterministic_parser(tmp_path):
    report = adapt(
        TaskContext(task_description="Write a report.", output_format="markdown"),
        tmp_path / "task",
        load_pack(),
    )
    entry = next(x for x in report["coverage"] if x["requirement"] == "Output format: markdown")
    assert entry["covered_by"] == ["format.presentation"]
