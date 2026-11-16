import shutil

import pytest
from pydantic import ValidationError
from ree.aggregation import aggregate_bonus, bonus_eligibility, compose_reward
from ree.evaluator import evaluate, training_reward
from ree.judge import ReplayJudge
from ree.models import BonusRule, Case, GateRule, Judgement, Pack, RewardProfile, Subject
from ree.pack import load_pack


def customized_pack(tmp_path, *, gate=False, bonus=False, table=None):
    original = load_pack()
    root = tmp_path / "pack"
    shutil.copytree(original.root, root)
    manifest = original.manifest
    manifest.gates[0].enabled = gate
    manifest.bonuses[0].enabled = bonus
    if table is not None:
        manifest.profile.table = table
    (root / "manifest.json").write_text(manifest.model_dump_json())
    return load_pack(root)


def case():
    return Case(
        request="Update a configuration.",
        response="Updated.",
        subjects=[Subject(subject="Update configuration")],
    )


def responses(pack):
    return {
        **{
            f"base/0/{name}": {
                "status": "ok",
                "value": True,
                "reason": "Fixture",
                "evidence_refs": [],
            }
            for name in ["understanding", "process", "result", "explanation"]
        },
        **{
            f"criterion/{rule.name}": {
                "status": "ok",
                "value": True,
                "reason": "Fixture",
                "evidence_refs": [],
            }
            for rule in pack.manifest.criteria
        },
    }


class TrackedReplay(ReplayJudge):
    def __init__(self, data):
        super().__init__(data)
        self.calls = []

    def judge(self, name, prompt, payload, schema):
        self.calls.append(name)
        return super().judge(name, prompt, payload, schema)


def bonus_rule(**kwargs):
    return BonusRule.model_validate(
        {
            "name": "bonus.test",
            "prompt": "bonus/reusable_artifact.md",
            "description": "Test bonus",
            "enabled": True,
            **kwargs,
        }
    )


def test_default_reference_policies_are_real_but_disabled():
    pack = load_pack()
    client = TrackedReplay(responses(pack))
    record = evaluate(case(), client, pack)
    assert record["status"] == "ok"
    assert record["gate"]["status"] == "admitted"
    assert record["gate"]["checks"][0]["status"] == "disabled"
    assert record["bonus"] == 0
    assert record["bonus_details"]["status"] == "disabled"
    assert record["composition"]["path"] == "normal"
    assert record["composition"]["before_clip"] == 1
    assert not any(name.startswith(("gate.", "bonus.")) for name in client.calls)
    assert pack.prompt(pack.manifest.gates[0].prompt)
    assert pack.prompt(pack.manifest.bonuses[0].prompt)


def test_gate_priority_routes_directly_without_running_downstream(tmp_path):
    pack = customized_pack(tmp_path, gate=True, bonus=True)
    manifest = pack.manifest
    manifest.gates.append(
        GateRule(
            name="gate.second",
            description="Second route",
            prompt=manifest.gates[0].prompt,
            priority=1,
            reward=0.2,
            enabled=True,
        )
    )
    (pack.root / "manifest.json").write_text(manifest.model_dump_json())
    pack = load_pack(pack.root)
    order = []

    def gate(name, hit):
        def check(_):
            order.append(name)
            return Judgement(status="ok", value=hit, reason="Fixture")

        return check

    client = TrackedReplay({})
    record = evaluate(
        case(),
        client,
        pack,
        plugins={
            "gate.explicit_prohibition": gate("first", False),
            "gate.second": gate("second", True),
        },
    )
    assert order == ["first", "second"] and client.calls == []
    assert record["gate"]["matched_rule"] == "gate.second"
    assert record["composition"]["path"] == "gate"
    assert record["reward"] == 0.2
    assert record["base"] is None and record["deduction"] is None and record["bonus"] is None
    assert {entry["status"] for entry in record["criteria"]} == {"not_run"}
    assert record["bonus_details"]["status"] == "not_run"


def test_gate_zero_reward_bypasses_normal_minimum(tmp_path):
    pack = customized_pack(tmp_path, gate=True)
    record = evaluate(
        case(),
        TrackedReplay({}),
        pack,
        plugins={
            "gate.explicit_prohibition": lambda _: Judgement(
                status="ok", value=True, reason="Observed forbidden action"
            )
        },
    )
    assert record["reward"] == 0
    assert training_reward(record) == 0
    assert record["composition"]["clipped"] is False


@pytest.mark.parametrize(
    "judgement",
    [
        Judgement(status="missing_evidence", reason="Required policy unavailable"),
        Judgement(status="ok", value=0.5, reason="Wrong gate output type"),
    ],
)
def test_enabled_gate_unresolved_or_invalid_is_not_admitted(tmp_path, judgement):
    pack = customized_pack(tmp_path, gate=True)
    record = evaluate(
        case(), TrackedReplay({}), pack, plugins={"gate.explicit_prohibition": lambda _: judgement}
    )
    assert record["status"] == "invalid" and record["reward"] is None
    assert record["gate"]["status"] == "invalid"
    assert record["composition"]["path"] == "invalid"
    assert record["bonus_details"]["status"] == "not_run"


def test_gate_verifier_error_is_not_admitted(tmp_path):
    pack = customized_pack(tmp_path, gate=True)

    def failed(_):
        raise RuntimeError("Verifier failed")

    record = evaluate(
        case(), TrackedReplay({}), pack, plugins={"gate.explicit_prohibition": failed}
    )
    assert record["status"] == "invalid" and record["reward"] is None
    assert record["gate"]["checks"][0]["status"] == "error"


def test_gate_not_applicable_admits_normal_scoring(tmp_path):
    pack = customized_pack(tmp_path, gate=True)
    record = evaluate(
        case(),
        TrackedReplay(responses(pack)),
        pack,
        plugins={
            "gate.explicit_prohibition": lambda _: Judgement(
                status="not_applicable", reason="No explicit hard prohibition"
            )
        },
    )
    assert record["status"] == "ok" and record["gate"]["status"] == "admitted"
    assert record["reward"] == 1


def test_bonus_is_real_reward_contribution_after_eligibility(tmp_path):
    pack = customized_pack(tmp_path, bonus=True, table=[0.8, 0.7, 0.8, 0.3, 0.5, 0.0])
    manifest = pack.manifest
    manifest.bonuses[0].min_base = 0.8
    (pack.root / "manifest.json").write_text(manifest.model_dump_json())
    pack = load_pack(pack.root)
    data = responses(pack)
    data["bonus.reusable_artifact"] = {
        "status": "ok",
        "value": 0.5,
        "reason": "Reusable extra artifact",
        "evidence_refs": [],
    }
    client = TrackedReplay(data)
    record = evaluate(case(), client, pack)
    assert record["status"] == "ok"
    assert record["base"] == 0.8 and record["bonus"] == pytest.approx(0.05)
    assert record["reward"] == pytest.approx(0.85)
    assert record["bonus_details"]["checks"][0]["value"] == 0.5
    assert record["composition"]["before_clip"] == pytest.approx(0.85)
    assert client.calls[-1] == "bonus.reusable_artifact"


def test_honest_failure_base_one_does_not_qualify_for_bonus(tmp_path):
    pack = customized_pack(tmp_path, bonus=True)
    data = responses(pack)
    data["base/0/result"]["value"] = False
    client = TrackedReplay(data)
    record = evaluate(case(), client, pack)
    assert record["base"] == 1 and record["subjects"][0]["row"] == 3
    assert record["bonus"] == 0 and record["bonus_details"]["status"] == "ineligible"
    assert "bonus.reusable_artifact" not in client.calls


def test_enabled_bonus_missing_evidence_invalidates_reward(tmp_path):
    pack = customized_pack(tmp_path, bonus=True)
    data = responses(pack)
    data["bonus.reusable_artifact"] = {
        "status": "missing_evidence",
        "value": None,
        "reason": "Artifact not supplied",
        "evidence_refs": [],
    }
    record = evaluate(case(), TrackedReplay(data), pack)
    assert record["base"] == 1 and record["quality"] == 1
    assert record["bonus"] is None and record["reward"] is None
    assert record["bonus_details"]["status"] == "invalid"
    assert record["composition"]["path"] == "invalid"
    assert record["composition"]["base"] == 1
    assert record["composition"]["deduction"] == 0


def test_labeler_failure_preserves_declared_downstream_checks():
    pack = load_pack()
    record = evaluate(
        Case(request="Update a configuration.", response="Updated."), ReplayJudge({}), pack
    )
    assert record["status"] == "invalid" and record["reward"] is None
    assert len(record["criteria"]) == len(pack.manifest.criteria)
    assert all(item["status"] == "not_run" for item in record["criteria"])
    assert len(record["bonus_details"]["checks"]) == len(pack.manifest.bonuses)
    assert record["bonus_details"]["checks"][0]["status"] == "disabled"


def test_gate_short_circuit_explains_later_unexecuted_gates(tmp_path):
    pack = customized_pack(tmp_path, gate=True)
    manifest = pack.manifest
    manifest.gates.append(
        manifest.gates[0].model_copy(update={"name": "gate.later", "priority": 1})
    )
    (pack.root / "manifest.json").write_text(manifest.model_dump_json())
    pack = load_pack(pack.root)
    record = evaluate(
        case(),
        TrackedReplay({}),
        pack,
        plugins={"gate.explicit_prohibition": lambda _: Judgement(status="ok", value=True)},
    )
    later = record["gate"]["checks"][1]
    assert later["status"] == "not_run" and "prior Gate" in later["reason"]


def test_bonus_plugin_is_explicitly_registered_and_used(tmp_path):
    pack = customized_pack(tmp_path, bonus=True)
    record = evaluate(
        case(),
        TrackedReplay(responses(pack)),
        pack,
        plugins={
            "bonus.reusable_artifact": lambda _: Judgement(
                status="ok", value=1.0, reason="Trusted reusable artifact verifier"
            )
        },
    )
    assert record["bonus"] == 0.1
    assert record["composition"]["before_clip"] == 1.1
    assert record["reward"] == 1 and record["composition"]["clipped"]


def test_pure_bonus_aggregation_applies_cap_without_discarding_facts():
    rules = [bonus_rule(), bonus_rule(name="bonus.other")]
    facts = [
        {"name": rule.name, "status": "ok", "value": 1.0, "reason": "Fixture", "evidence_refs": []}
        for rule in rules
    ]
    result = aggregate_bonus(
        facts,
        rules,
        RewardProfile(bonus_max=0.1),
        base=1,
        quality=1,
        subjects=[{"final_row1": True}],
    )
    assert result["bonus"] == 0.1 and result["bonus_details"]["uncapped"] == 0.2
    assert all(item["judge_fact"]["value"] == 1 for item in result["bonus_details"]["checks"])


def test_recompute_cannot_invent_newly_eligible_bonus_facts():
    rule = bonus_rule()
    facts = [
        {
            "name": rule.name,
            "status": "ineligible",
            "value": None,
            "reason": "Old zero cap",
            "evidence_refs": [],
        }
    ]
    with pytest.raises(ValueError, match="requires regrade"):
        aggregate_bonus(
            facts, [rule], RewardProfile(), base=1, quality=1, subjects=[{"final_row1": True}]
        )


def test_recompute_preserves_previous_facts_across_ineligible_profile():
    rule = bonus_rule()
    original = [
        {"name": rule.name, "status": "ok", "value": 0.5, "reason": "Fixture", "evidence_refs": []}
    ]
    skipped = aggregate_bonus(
        original,
        [rule],
        RewardProfile(bonus_max=0),
        base=1,
        quality=1,
        subjects=[{"final_row1": True}],
    )
    assert skipped["bonus"] == 0
    restored = aggregate_bonus(
        skipped["bonus_details"]["checks"],
        [rule],
        RewardProfile(),
        base=1,
        quality=1,
        subjects=[{"final_row1": True}],
    )
    assert restored["bonus"] == 0.05


def test_bonus_empty_subjects_and_quality_threshold_do_not_call_judge():
    rule = bonus_rule()
    assert not bonus_eligibility(rule, 1, 1, [], RewardProfile())[0]
    assert not bonus_eligibility(rule, 1, 0.5, [{"final_row1": True}], RewardProfile())[0]


def test_manifest_enforces_priority_and_component_names():
    manifest = load_pack().manifest
    manifest.gates.append(manifest.gates[0].model_copy(update={"name": "gate.second"}))
    with pytest.raises(ValidationError, match="priorities"):
        Pack.model_validate_json(manifest.model_dump_json())
    manifest = load_pack().manifest
    manifest.bonuses[0].name = manifest.criteria[0].name
    with pytest.raises(ValidationError, match="Duplicate"):
        Pack.model_validate_json(manifest.model_dump_json())


def test_compose_invalid_and_direct_gate_paths_are_explicit():
    profile = RewardProfile()
    assert compose_reward(None, None, None, profile)["composition"]["path"] == "invalid"
    assert compose_reward(None, None, None, profile, gate_reward=0)["reward"] == 0
    assert compose_reward(0.8, 0.2, 0.1, profile)["reward"] == pytest.approx(0.7)
