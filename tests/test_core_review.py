import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from pydantic import ValidationError
from ree.adaptation import adapt
from ree.aggregation import aggregate_quality, aggregate_subjects, lookup
from ree.checkers import check_json, check_tools
from ree.evaluator import evaluate, training_reward
from ree.judge import OpenAIJudge, ReplayJudge
from ree.models import Case, Event, Harness, Judgement, RewardProfile, Subject, TaskContext, Tool
from ree.pack import load_pack


def complete_responses(pack):
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
            f"criterion/{criterion.name}": {
                "status": "ok",
                "value": True,
                "reason": "Fixture",
                "evidence_refs": [],
            }
            for criterion in pack.manifest.criteria
        },
    }


def test_evaluation_usage_is_per_case_and_immutable():
    class RecordingReplay(ReplayJudge):
        def judge(self, name, prompt, payload, schema):
            self.usage.append({"name": name, "request": payload["request"]})
            return super().judge(name, prompt, payload, schema)

    pack = load_pack()
    client = RecordingReplay(complete_responses(pack))
    first = evaluate(Case(request="A", response="A", subjects=[Subject(subject="A")]), client, pack)
    expected = json.loads(json.dumps(first["metadata"]["usage"]))
    second = evaluate(
        Case(request="B", response="B", subjects=[Subject(subject="B")]), client, pack
    )
    assert first["metadata"]["usage"] == expected
    assert {item["request"] for item in first["metadata"]["usage"]} == {"A"}
    assert {item["request"] for item in second["metadata"]["usage"]} == {"B"}
    client.usage[0]["request"] = "mutated"
    assert first["metadata"]["usage"] == expected


def test_shared_openai_client_isolates_concurrent_case_usage():
    barrier = Barrier(2)

    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "status": "ok",
                                    "value": True,
                                    "reason": payload["request"],
                                    "evidence_refs": [],
                                }
                            )
                        }
                    }
                ],
                "usage": {"fixture_case": payload["request"]},
            },
        )

    client = OpenAIJudge(model="fixture", retries=0, max_calls=100)
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    try:

        def run(name):
            barrier.wait()
            return evaluate(
                Case(request=name, response=name, subjects=[Subject(subject=name)]), client
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            records = list(pool.map(run, ["A", "B"]))
        for record in records:
            assert record["status"] == "ok"
            assert {item["usage"]["fixture_case"] for item in record["metadata"]["usage"]} == {
                record["case"]["request"]
            }
    finally:
        client.close()


@pytest.mark.parametrize("resource", ["manifest.json", "task_context.json"])
def test_pack_resources_cannot_follow_external_symlinks(tmp_path, resource):
    root = tmp_path / "pack"
    shutil.copytree(load_pack().root, root)
    outside = tmp_path / "external.json"
    if resource == "manifest.json":
        outside.write_text((root / resource).read_text())
        (root / resource).unlink()
    else:
        outside.write_text(TaskContext(task_description="External context").model_dump_json())
    (root / resource).symlink_to(outside)
    with pytest.raises(ValueError, match="[Uu]nsafe|[Ii]nvalid"):
        load_pack(root)


def test_dangling_context_symlink_is_not_silently_absent(tmp_path):
    root = tmp_path / "pack"
    shutil.copytree(load_pack().root, root)
    (root / "task_context.json").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError):
        load_pack(root)


def test_adaptation_preserves_schema_property_names_that_are_not_credentials(tmp_path):
    schema = {
        "type": "object",
        "properties": {"password": {"type": "string"}, "api_key": {"type": "string"}},
    }
    context = TaskContext(
        task_description="Describe the schema.",
        output_schema=schema,
        harness={"tools": [{"name": "inspect", "parameters": schema}]},
    )
    adapt(context, tmp_path / "task", load_pack())
    assert load_pack(tmp_path / "task").context().output_schema == schema


def test_dynamic_missing_tool_schema_is_missing_evidence_not_undeclared():
    case = Case(
        request="Call the declared tools",
        events=[
            Event(
                ref="first",
                kind="tool_call",
                tool="A",
                harness=Harness(tools=[Tool(name="A", parameters={"type": "object"})]),
            ),
            Event(ref="second", kind="tool_call", tool="B", harness=Harness()),
        ],
    )
    judgement = check_tools(case)
    assert judgement.status == "missing_evidence"
    assert judgement.value is None
    assert judgement.evidence_refs == ["second"]
    case.events[1].harness = Harness(tools=[Tool(name="A", parameters={"type": "object"})])
    assert check_tools(case).value is False


def test_explicit_empty_schema_is_valid_but_omitted_schema_is_missing():
    case = Case(
        request="Call A",
        harness=Harness(tools=[Tool(name="A", parameters={})]),
        events=[Event(ref="call", kind="tool_call", tool="A")],
    )
    assert check_tools(case).value is True
    case.harness = Harness(tools=[Tool(name="A")])
    assert check_tools(case).status == "missing_evidence"


def test_conflicting_tool_declarations_rejected():
    with pytest.raises(ValidationError):
        Harness(
            tools=[
                Tool(name="A", parameters={"type": "object"}),
                Tool(name="A", parameters={"type": "array"}),
            ]
        )


@pytest.mark.parametrize("response", ['{"count":1,"count":2}', '{"count":1e999}'])
def test_ambiguous_or_overflow_json_does_not_pass(response):
    assert (
        check_json(
            Case(request="JSON", response=response, context=TaskContext(output_format="json"))
        ).value
        is False
    )


@pytest.mark.parametrize("value", ["false", 1, None])
def test_base_lookup_never_coerces_invalid_booleans(value):
    with pytest.raises(ValueError):
        lookup(value, True, True, True, RewardProfile())


@pytest.mark.parametrize(
    "criterion",
    [
        {"group": "unknown", "status": "ok", "value": True, "weight": 2},
        {"group": "safety", "status": "unresolved", "value": None, "weight": 2},
        {"group": "safety", "status": "ok", "value": float("nan"), "weight": 2},
        {"group": "safety", "status": "ok", "value": True, "weight": -1},
    ],
)
def test_invalid_quality_input_never_becomes_neutral_pass(criterion):
    with pytest.raises(ValueError):
        aggregate_quality([criterion], RewardProfile())


def test_extreme_finite_group_weights_produce_finite_quality():
    profile = RewardProfile(
        group_weights={name: 1e308 for name in ["safety", "tool_use", "hallucination", "format"]}
    )
    criteria = [
        {"group": name, "status": "ok", "value": value, "weight": 2}
        for name, value in [
            ("safety", False),
            ("tool_use", True),
            ("hallucination", False),
            ("format", True),
        ]
    ]
    assert 0 <= aggregate_quality(criteria, profile)["quality"] <= 1


@pytest.mark.parametrize(
    "score", [{"raw_score": 1, "row1": "false"}, {"raw_score": True, "row1": True}]
)
def test_subject_aggregation_rejects_invalid_score_labels(score):
    with pytest.raises(ValueError):
        aggregate_subjects([Subject(subject="Do it")], [score])


@pytest.mark.parametrize("value", [True, "1"])
def test_training_reward_is_a_numeric_scalar_not_coerced_input(value):
    with pytest.raises(ValueError):
        training_reward({"status": "ok", "reward": value})


def test_unknown_tool_schema_survives_record_regrade_roundtrip():
    case = Case(
        request="Call A",
        harness=Harness(tools=[Tool(name="A")]),
        events=[Event(ref="call", kind="tool_call", tool="A")],
    )
    assert check_tools(case).status == "missing_evidence"
    restored = Case.model_validate_json(case.model_dump_json())
    assert restored.harness.tools[0].parameters is None
    assert check_tools(restored).status == "missing_evidence"


def test_invalid_mutated_plugin_result_is_recorded_instead_of_scored():
    pack = load_pack()
    case = Case(request="Answer", response="Done", subjects=[Subject(subject="Answer")])
    judgement = Judgement(status="ok", value=True, reason="Fixture")
    judgement.value = 2
    record = evaluate(
        case,
        ReplayJudge(complete_responses(pack)),
        pack,
        plugins={"fact.attribute_consistency": lambda _: judgement},
    )
    assert record["status"] == "invalid"
    assert record["reward"] is None
    assert (
        next(item for item in record["criteria"] if item["name"] == "fact.attribute_consistency")[
            "status"
        ]
        == "error"
    )


def test_conversation_missing_evidence_details_are_retained():
    pack = load_pack()
    data = complete_responses(pack)
    data["base/conversation"] = {
        "status": "missing_evidence",
        "value": None,
        "reason": "Final reply unavailable",
        "evidence_refs": ["response"],
    }
    record = evaluate(Case(request="Hello", subjects=[]), ReplayJudge(data), pack)
    assert record["status"] == "invalid"
    assert (
        record["conversation"] == Judgement.model_validate(data["base/conversation"]).model_dump()
    )


@pytest.mark.parametrize(
    "events",
    [
        [
            {"ref": "call", "kind": "tool_call", "tool": "A"},
            {"ref": "result", "kind": "tool_result", "call_ref": "call", "tool": "B"},
        ],
        [
            {"ref": "result", "kind": "tool_result", "call_ref": "call", "tool": "A"},
            {"ref": "call", "kind": "tool_call", "tool": "A"},
        ],
    ],
)
def test_tool_result_association_and_order_are_validated(events):
    with pytest.raises(ValidationError):
        Case(request="Call", events=[Event.model_validate(item) for item in events])


def test_nonfinite_tool_arguments_never_pass_schema_check():
    case = Case(
        request="Call A",
        harness=Harness(tools=[Tool(name="A", parameters={"type": "object"})]),
        events=[Event(ref="call", kind="tool_call", tool="A", arguments={"x": float("inf")})],
    )
    with pytest.raises(ValueError):
        check_tools(case)
