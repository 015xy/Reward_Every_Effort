import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from ree.checkers import check_json
from ree.cli import diagnostics
from ree.evaluator import evaluate, training_reward
from ree.judge import JudgeError, OpenAIJudge, ReplayJudge
from ree.models import Case, Judgement, Subject, TaskContext
from ree.pack import load_pack


def responses(pack):
    data = {
        f"base/0/{n}": {
            "status": "ok",
            "value": True,
            "reason": "Observed",
            "evidence_refs": ["response"],
        }
        for n in ["understanding", "process", "result", "explanation"]
    }
    for c in pack.manifest.criteria:
        data[f"criterion/{c.name}"] = {
            "status": "ok",
            "value": True,
            "reason": "No violation",
            "evidence_refs": ["response"],
        }
    return data


def test_default_works_without_task_adaptation_and_failure_has_no_scalar():
    pack = load_pack()
    case = Case(
        request="Write a greeting", response="Hello", subjects=[Subject(subject="Write greeting")]
    )
    data = responses(pack)
    result = evaluate(case, ReplayJudge(data), pack)
    assert result["status"] == "ok" and training_reward(result) == 1
    del data["base/0/process"]
    result = evaluate(case, ReplayJudge(data), pack)
    assert result["status"] == "invalid" and result["reward"] is None
    with pytest.raises(ValueError):
        training_reward(result)


def test_unrecognized_evidence_and_missing_evidence_are_invalid():
    pack = load_pack()
    data = responses(pack)
    data["base/0/result"]["evidence_refs"] = ["invented"]
    case = Case(
        request="Write a greeting", response="Hello", subjects=[Subject(subject="Write greeting")]
    )
    assert evaluate(case, ReplayJudge(data), pack)["reward"] is None
    data = responses(pack)
    data["criterion/fact.entity_existence"] = {
        "status": "missing_evidence",
        "value": None,
        "reason": "Absent source",
        "evidence_refs": [],
    }
    assert evaluate(case, ReplayJudge(data), pack)["status"] == "invalid"


def test_trusted_plugin_can_override_builtin():
    pack = load_pack()
    case = Case(
        request="Write greeting", response="hello", subjects=[Subject(subject="Write greeting")]
    )
    used = []

    def checker(case):
        used.append(True)
        return Judgement(
            status="ok", value=False, reason="Custom contract", evidence_refs=["response"]
        )

    result = evaluate(case, ReplayJudge(responses(pack)), pack, plugins={"format.syntax": checker})
    assert used and result["reward"] < 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 2])
def test_training_scalar_rejects_corrupt_records(value):
    with pytest.raises(ValueError):
        training_reward({"status": "ok", "reward": value})


def test_strict_labels_reject_boolean_dependency_and_string_judgements():
    with pytest.raises(ValidationError):
        Subject(subject="do it", depends_on=[True])
    with pytest.raises(ValidationError):
        Judgement(status="ok", value="true", reason="wrong type")


def test_json_checks_are_strict_and_schema_does_not_fetch_network():
    case = Case(request="Give JSON", response="NaN", context=TaskContext(output_format="json"))
    assert check_json(case).value is False
    case.response = "{}"
    case.context.output_schema = {"$ref": "https://unreachable.invalid/schema"}
    with pytest.raises(Exception, match="[Uu]nresolvable|[Uu]nretrievable"):
        check_json(case)


def test_fingerprint_freezes_prompt_and_context(tmp_path):
    import shutil

    root = tmp_path / "pack"
    shutil.copytree(load_pack().root, root)
    pack = load_pack(root)
    name = pack.manifest.labeler_prompt
    before = pack.prompt(name)
    (root / name).write_text("Changed evaluation policy")
    assert pack.prompt(name) == before
    assert load_pack(root).fingerprint != pack.fingerprint


def test_openai_transport_retries_schema_failure_and_preserves_budget():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        content = (
            "not JSON"
            if len(calls) == 1
            else json.dumps(
                {"status": "ok", "value": True, "reason": "Observed", "evidence_refs": []}
            )
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = OpenAIJudge(model="test", retries=1, max_calls=2)
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        assert client.judge("test", "Policy", {}, Judgement).value is True
        assert len(calls) == 2
        assert client.judge("cached", "Policy", {}, Judgement).value is True
        with pytest.raises(JudgeError, match="budget"):
            client.judge("new", "Different", {}, Judgement)
    finally:
        client.close()


def test_source_pack_matches_installed_data():
    root = Path(__file__).resolve().parent.parent
    source = root / "packs" / "general-agent"
    packaged = load_pack().root
    source_files = {
        file.relative_to(source)
        for file in source.rglob("*")
        if file.is_file() and file.suffix in {".md", ".json"}
    }
    packaged_files = {
        file.relative_to(packaged)
        for file in packaged.rglob("*")
        if file.is_file() and file.suffix in {".md", ".json"}
    }
    assert source_files == packaged_files
    for relative in source_files:
        assert (source / relative).read_bytes() == (packaged / relative).read_bytes()


def test_rl_diagnostics_rejects_mixed_requests():
    with pytest.raises(ValueError):
        diagnostics(
            [
                {"status": "ok", "reward": 0.2, "case": {"request": "A"}},
                {"status": "ok", "reward": 0.7, "case": {"request": "B"}},
            ]
        )
