import json
from copy import deepcopy

import httpx
import pytest
from ree.evaluator import evaluate
from ree.judge import JudgeError, OpenAIJudge, ReplayJudge
from ree.models import Case, Judgement, Subject
from ree.pack import load_pack


class TrackedReplay(ReplayJudge):
    def __init__(self, responses):
        super().__init__(responses)
        self.calls = []
        self.payloads = {}

    def judge(self, name, prompt, payload, schema):
        self.calls.append(name)
        self.payloads[name] = deepcopy(payload)
        return super().judge(name, prompt, payload, schema)


def judgement(*, value=True, quote=None, ref="response"):
    return {
        "status": "ok",
        "value": value,
        "reason": "Observed facts and their original interpretation.",
        "evidence_refs": [ref],
        "annotations": []
        if quote is None
        else [
            {
                "ref": ref,
                "kind": "support",
                "quote": quote,
                "reason": "Observed source text.",
            }
        ],
    }


def fixture():
    pack = load_pack()
    case = Case(
        request="Write a greeting.",
        response="**Hello**",
        subjects=[Subject(subject="Write greeting.")],
    )
    responses = {
        f"base/0/{name}": judgement()
        for name in ["understanding", "process", "result", "explanation"]
    }
    responses.update({f"criterion/{rule.name}": judgement() for rule in pack.manifest.criteria})
    responses["gate.explicit_prohibition"] = {
        "status": "not_applicable",
        "value": None,
        "reason": "Authored test fixture: no explicit hard safety boundary.",
        "evidence_refs": [],
    }
    for rule in pack.manifest.bonuses:
        responses[rule.name] = {
            "status": "ok",
            "value": 0.0,
            "reason": "Authored test fixture: no extra value beyond the required answer.",
            "evidence_refs": [],
        }
    return pack, case, responses


def test_exact_quote_repair_keeps_negative_fact_and_records_original_validation_error():
    pack, case, responses = fixture()
    name = "criterion/fact.execution_claim"
    responses[name] = judgement(value=False, quote="A paraphrased greeting")
    responses[name + "/evidence-repair"] = judgement(value=False, quote="**Hello**")
    client = TrackedReplay(responses)
    result = evaluate(case, client, pack)
    check = next(check for check in result["criteria"] if check["name"] == "fact.execution_claim")
    assert result["status"] == "ok" and result["reward"] < 1
    assert check["value"] is False
    assert check["reason"] == responses[name]["reason"]
    assert check["annotations"][0]["quote"] == "**Hello**"
    assert client.calls.count(name + "/evidence-repair") == 1
    assert not any(call.endswith("/evidence-repair/evidence-repair") for call in client.calls)
    audit = result["metadata"]["evidence_repairs"]
    assert audit == [
        {
            "name": name,
            "status": "repaired",
            "error": "Judge annotation quote is not present at its evidence ref",
            "omitted_quotes": 0,
        }
    ]
    assert client.payloads[name + "/evidence-repair"]["original_judgement"] == responses[name]
    assert client.payloads[name + "/evidence-repair"]["original_payload"]["response"] == "**Hello**"
    assert client.payloads[name + "/evidence-repair"]["evidence_texts"]["response"] == ["**Hello**"]


def test_uncertain_quote_can_be_explicitly_omitted_by_the_repair_judge():
    pack, case, responses = fixture()
    name = "base/0/result"
    responses[name] = judgement(quote="Not verbatim")
    repaired = deepcopy(responses[name])
    repaired["annotations"][0]["quote"] = None
    responses[name + "/evidence-repair"] = repaired
    result = evaluate(case, TrackedReplay(responses), pack)
    assert result["status"] == "ok"
    assert result["subjects"][0]["judgements"]["result"]["annotations"][0]["quote"] is None
    assert result["metadata"]["evidence_repairs"][0]["omitted_quotes"] == 1


def test_only_validated_judgements_replace_original_cache_entries():
    pack, case, responses = fixture()
    name = "base/0/result"
    responses[name] = judgement(quote="Not verbatim")
    responses[name + "/evidence-repair"] = judgement(quote="**Hello**")

    class CacheTrackingReplay(TrackedReplay):
        def __init__(self, answers):
            super().__init__(answers)
            self.saved = []

        def cache_validated(self, prompt, payload, schema, value):
            self.saved.append((prompt, deepcopy(payload), schema, value.model_dump()))

    client = CacheTrackingReplay(responses)
    assert evaluate(case, client, pack)["status"] == "ok"
    saved = [
        row for row in client.saved if row[0] == pack.prompt(pack.manifest.base_prompts["result"])
    ]
    assert len(saved) == 1
    assert saved[0][1] == client.payloads[name]
    assert saved[0][2] is Judgement
    assert saved[0][3] == responses[name + "/evidence-repair"]

    responses[name + "/evidence-repair"] = judgement(quote="Still absent")
    failed = CacheTrackingReplay(responses)
    assert evaluate(case, failed, pack)["status"] == "invalid"
    assert not any(
        row[0] == pack.prompt(pack.manifest.base_prompts["result"]) for row in failed.saved
    )


@pytest.mark.parametrize(
    "change", ["value", "value_type", "status", "reason", "annotation_structure"]
)
def test_repair_cannot_change_judged_facts_or_erase_annotation_structure(change):
    pack, case, responses = fixture()
    name = "base/0/result"
    responses[name] = judgement(quote="Not verbatim")
    repaired = judgement(quote="**Hello**")
    if change == "value":
        repaired["value"] = False
    elif change == "value_type":
        repaired["value"] = 1.0
    elif change == "status":
        repaired.update(status="not_applicable", value=None)
    elif change == "reason":
        repaired["reason"] += " Changed interpretation."
    else:
        repaired["annotations"] = []
    responses[name + "/evidence-repair"] = repaired
    result = evaluate(case, TrackedReplay(responses), pack)
    assert result["status"] == "invalid" and result["reward"] is None
    audit = result["metadata"]["evidence_repairs"][0]
    assert audit["status"] == "failed" and "protected" in audit["repair_error"]


def test_invalid_repair_remains_invalid_without_another_attempt():
    pack, case, responses = fixture()
    name = "base/0/result"
    responses[name] = judgement(quote="Not verbatim")
    responses[name + "/evidence-repair"] = judgement(quote="Still not verbatim")
    client = TrackedReplay(responses)
    result = evaluate(case, client, pack)
    assert result["status"] == "invalid" and result["reward"] is None
    assert client.calls.count(name + "/evidence-repair") == 1
    assert result["metadata"]["evidence_repairs"][0]["status"] == "failed"


def test_replay_without_a_repair_response_does_not_fabricate_evidence():
    pack, case, responses = fixture()
    name = "base/0/result"
    responses[name] = judgement(quote="Not verbatim")
    result = evaluate(case, TrackedReplay(responses), pack)
    assert result["status"] == "invalid" and result["reward"] is None
    assert "Replay has no response" in result["metadata"]["evidence_repairs"][0]["repair_error"]


def test_deterministic_plugin_evidence_errors_never_trigger_a_model_repair():
    pack, case, responses = fixture()
    client = TrackedReplay(responses)
    result = evaluate(
        case,
        client,
        pack,
        plugins={
            "fact.execution_claim": lambda _case: Judgement.model_validate(
                judgement(value=False, quote="Not verbatim")
            )
        },
    )
    assert result["status"] == "invalid"
    assert result["metadata"]["evidence_repairs"] == []
    assert not any(call.endswith("/evidence-repair") for call in client.calls)


def test_analysis_labeling_does_not_use_evidence_repair():
    pack, case, responses = fixture()
    case.subjects = None
    responses["labeler"] = {"subjects": [{"subject": "Write greeting."}]}
    client = TrackedReplay(responses)
    result = evaluate(case, client, pack)
    assert result["status"] == "ok"
    assert client.calls.count("labeler") == 1
    assert result["metadata"]["evidence_repairs"] == []


@pytest.mark.parametrize("max_calls", [1, 2])
def test_real_transport_repair_uses_existing_call_budget_and_usage_scope(max_calls):
    pack = load_pack()
    case = Case(request="A greeting.", response="**Hello**", subjects=[])
    requests = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        requests.append(payload)
        result = judgement(quote="**Hello**" if "original_judgement" in payload else "Not verbatim")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(result)}}],
                "usage": {"total_tokens": 12},
            },
        )

    client = OpenAIJudge(model="synthetic", retries=0, max_calls=max_calls)
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = evaluate(
            case,
            client,
            pack,
            plugins={
                "gate.explicit_prohibition": lambda _case: Judgement(
                    status="not_applicable", reason="Synthetic greeting has no hard safety boundary"
                ),
                **{
                    rule.name: lambda _case: Judgement.model_validate(judgement())
                    for rule in pack.manifest.criteria
                },
            },
        )
        assert len(requests) == client._calls == max_calls
        usage = result["metadata"]["usage"]
        assert len(usage) == max_calls and all(
            entry["usage"]["total_tokens"] == 12 for entry in usage
        )
        assert len({entry["scope"] for entry in usage}) == 1
        if max_calls == 2:
            assert result["status"] == "ok"
            assert [entry["name"] for entry in usage] == [
                "base/conversation",
                "base/conversation/evidence-repair",
            ]
            assert result["metadata"]["evidence_repairs"][0]["status"] == "repaired"
        else:
            assert result["status"] == "invalid" and result["reward"] is None
            assert "budget" in result["metadata"]["evidence_repairs"][0]["repair_error"]
        with pytest.raises(JudgeError, match="budget"):
            client.judge("another", "Different policy", {}, Judgement)
    finally:
        client.close()
