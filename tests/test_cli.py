import json
from pathlib import Path

import pytest
from ree.cli import main, recompute
from ree.models import BonusRule, RewardProfile

ROOT = Path(__file__).resolve().parents[1]


def test_score_exports_both_languages_from_one_evaluation(tmp_path, capsys):
    record_path = tmp_path / "score.json"
    assert (
        main(
            [
                "score",
                str(ROOT / "examples/config-case.json"),
                "--replay",
                str(ROOT / "examples/config-replay.json"),
                "--output",
                str(record_path),
                "--html",
                str(tmp_path / "report.html"),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    record = json.loads(record_path.read_text())
    assert summary["reward"] == record["reward"] == 0.5
    assert set(summary["reports"]) == {"en", "zh"}
    for language, path in summary["reports"].items():
        report = Path(path)
        assert report.name == f"report.{language}.html"
        assert report.stat().st_size > 1000


@pytest.mark.parametrize("language", ["en", "zh"])
def test_inspect_single_language_uses_requested_path(tmp_path, capsys, language):
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps({"status": "invalid", "reward": None, "errors": ["No evidence"]}))
    output = tmp_path / "inspection.html"
    assert main(["inspect", str(source), "--output", str(output), "--locale", language]) == 0
    assert json.loads(capsys.readouterr().out) == {language: str(output)}
    assert output.is_file()
    assert not (tmp_path / f"inspection.{language}.html").exists()


def test_inspect_accepts_multiple_jsonl_records(tmp_path):
    source = tmp_path / "batch.jsonl"
    source.write_text(
        "\n".join(json.dumps({"status": "invalid", "errors": [message]}) for message in ["A", "B"])
    )
    output = tmp_path / "batch.html"
    assert main(["inspect", str(source), "--output", str(output)]) == 0
    assert (tmp_path / "batch.en.html").is_file()
    assert (tmp_path / "batch.zh.html").is_file()


def test_recompute_keeps_gate_reward_outside_normal_clip_interval():
    record = {
        "status": "ok",
        "reward": 0,
        "gate": {"status": "routed", "reward": 0},
        "metadata": {"pack_fingerprint": "fixture"},
    }
    result = recompute(record, RewardProfile(reward_min=0.8))
    assert result["reward"] == 0
    assert result["composition"]["path"] == "gate"


def test_recompute_uses_bonus_facts_and_new_cap():
    rule = BonusRule(
        name="bonus.example",
        description="Fixture",
        prompt="bonus/example.md",
        enabled=True,
        max_value=0.2,
        min_base=0.1,
        min_quality=1,
        require_all_subjects=False,
    )
    record = {
        "status": "ok",
        "reward": 0.8,
        "base": 0.7,
        "bonus": 0.1,
        "subjects": [],
        "criteria": [],
        "bonus_details": {"checks": [{"name": rule.name, "status": "ok", "value": 0.5}]},
        "metadata": {"pack_fingerprint": "fixture", "bonuses": [rule.model_dump()]},
    }
    result = recompute(record, RewardProfile(bonus_max=0.05))
    assert result["bonus"] == 0.05
    assert result["reward"] == 0.75
