"""Evidence repairs replace the original call's cache across client restarts."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from ree.evaluator import evaluate
from ree.evidence import validate_evidence
from ree.judge import OpenAIJudge
from ree.models import Case, Judgement
from ree.pack import load_pack


def judgement(quote):
    return Judgement(
        status="ok",
        value=True,
        reason="The greeting was delivered.",
        evidence_refs=["response"],
        annotations=[
            {"ref": "response", "kind": "support", "quote": quote, "reason": "Observed greeting"}
        ],
    )


def transport_client(cache, handler, **kwargs):
    client = OpenAIJudge(model=kwargs.pop("model", "mock"), cache=cache, retries=0, **kwargs)
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def response(value):
    return httpx.Response(
        200, json={"choices": [{"message": {"content": value.model_dump_json()}}]}
    )


def no_requests(_):
    pytest.fail("A validated cache hit must not call the provider")


def test_cache_hook_replaces_exact_original_key_and_survives_restart(tmp_path):
    case = Case(request="A greeting.", response="**Hello**")
    payload, prompt = case.model_dump(mode="json"), "Greeting policy"
    original, repaired = judgement("Not verbatim"), judgement("**Hello**")
    client = transport_client(tmp_path, lambda _: response(original))
    try:
        assert client.judge("original", prompt, payload, Judgement) == original
        with pytest.raises(ValueError, match="quote"):
            validate_evidence(original, case)
        assert validate_evidence(repaired, case) == repaired
        client.cache_validated(prompt, payload, Judgement, repaired)
        assert client.judge("same", prompt, payload, Judgement) == repaired
        assert client._calls == 1
        key = hashlib.sha256(
            json.dumps(
                [
                    client.base_url,
                    client.model,
                    prompt,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    Judgement.model_json_schema(),
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        assert json.loads((tmp_path / f"{key}.json").read_text()) == repaired.model_dump(
            mode="json"
        )
        assert len(list(tmp_path.glob("*.json"))) == 1
        assert not list(tmp_path.glob("*.tmp"))
    finally:
        client.close()
    restarted = transport_client(tmp_path, no_requests)
    try:
        assert restarted.judge("regrade", prompt, payload, Judgement) == repaired
        assert restarted._calls == 0 and restarted.usage[0]["status"] == "cache_hit"
    finally:
        restarted.close()


def test_evaluator_repaired_cache_regrades_without_another_repair_request(tmp_path):
    pack = load_pack()
    case = Case(request="A greeting.", response="**Hello**", subjects=[])
    calls = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        calls.append(payload)
        return response(
            judgement("**Hello**" if "original_judgement" in payload else "Not verbatim")
        )

    plugins = {
        "gate.explicit_prohibition": lambda _: Judgement(
            status="not_applicable", reason="Synthetic greeting has no hard safety boundary"
        ),
        **{
            rule.name: lambda _: Judgement(status="ok", value=True, reason="Synthetic verifier")
            for rule in pack.manifest.criteria
        },
    }
    client = transport_client(tmp_path, handler)
    try:
        first = evaluate(case, client, pack, workers=1, plugins=plugins)
        assert first["status"] == "ok" and len(calls) == 2
        assert first["metadata"]["evidence_repairs"][0]["status"] == "repaired"
    finally:
        client.close()
    restarted = transport_client(tmp_path, no_requests)
    try:
        second = evaluate(case, restarted, pack, workers=1, plugins=plugins)
        assert second["status"] == "ok" and second["reward"] == first["reward"]
        assert second["conversation"] == first["conversation"]
        assert second["metadata"]["evidence_repairs"] == []
        assert [entry["status"] for entry in second["metadata"]["usage"]] == ["cache_hit"]
    finally:
        restarted.close()


@pytest.mark.parametrize("changed", ["model", "endpoint", "prompt", "payload", "schema"])
def test_validated_cache_does_not_cross_call_identity(tmp_path, changed):
    repaired = judgement("Hello")
    client = transport_client(tmp_path, no_requests)
    try:
        client.cache_validated("Policy", {"case": 1}, Judgement, repaired)
    finally:
        client.close()

    class OtherJudgement(Judgement):
        pass

    calls = []

    def handler(_):
        calls.append(True)
        return response(repaired)

    restarted = transport_client(
        tmp_path,
        handler,
        model="other" if changed == "model" else "mock",
        base_url="https://other.invalid/v1"
        if changed == "endpoint"
        else "https://api.openai.com/v1",
    )
    try:
        restarted.judge(
            "identity",
            "Other policy" if changed == "prompt" else "Policy",
            {"case": 2} if changed == "payload" else {"case": 1},
            OtherJudgement if changed == "schema" else Judgement,
        )
        assert calls == [True]
    finally:
        restarted.close()


def test_cache_hook_revalidates_model_construct_and_rejects_wrong_type(tmp_path):
    original = judgement("Original quote")
    client = transport_client(tmp_path, no_requests)
    try:
        client.cache_validated("Policy", {}, Judgement, original)
        with pytest.raises(TypeError):
            client.cache_validated("Policy", {}, Judgement, original.model_dump())
        malformed = Judgement.model_construct(status="ok", value="yes", reason="Malformed")
        with pytest.raises(ValidationError):
            client.cache_validated("Policy", {}, Judgement, malformed)
        assert client.judge("unchanged", "Policy", {}, Judgement) == original
    finally:
        client.close()


def test_cache_hook_does_not_retain_mutable_caller_references(tmp_path):
    repaired = judgement("Hello")
    client = transport_client(tmp_path, no_requests)
    try:
        client.cache_validated("Policy", {}, Judgement, repaired)
        repaired.annotations[0].quote = "Mutated after caching"
        assert client.judge("cached", "Policy", {}, Judgement).annotations[0].quote == "Hello"
        assert client._calls == 0
    finally:
        client.close()


def test_atomic_cache_write_failure_preserves_prior_memory_and_file(tmp_path, monkeypatch):
    original = judgement("Original quote")
    client = transport_client(tmp_path, no_requests)
    try:
        client.cache_validated("Policy", {}, Judgement, original)
        file = next(tmp_path.glob("*.json"))
        prior = file.read_bytes()

        def fail_replace(_source, _target):
            raise OSError("Synthetic atomic write failure")

        monkeypatch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError):
            client.cache_validated("Policy", {}, Judgement, judgement("Replacement quote"))
        assert file.read_bytes() == prior
        assert not list(tmp_path.glob("*.tmp"))
        assert client.judge("cached", "Policy", {}, Judgement) == original
    finally:
        client.close()
