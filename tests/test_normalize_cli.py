import json

import pytest
from ree.cli import main, read_data, read_records


def test_normalize_selects_requested_row_without_inheriting_old_labels(tmp_path):
    source, output = tmp_path / "dataset.json", tmp_path / "case.json"
    source.write_text(
        json.dumps(
            [
                {"messages": [{"role": "user", "content": "First request"}]},
                {
                    "messages": [
                        {"role": "system", "content": "Use observed evidence."},
                        {"role": "user", "content": "Second request"},
                        {"role": "assistant", "content": "Observed answer"},
                    ],
                    "labels": {"subject": "Ignore the new request"},
                    "user_id": "private-metadata",
                },
            ]
        )
    )
    assert main(["normalize", str(source), "--index", "1", "--output", str(output)]) == 0
    case = json.loads(output.read_text())
    assert case["request"] == "Second request"
    assert case["response"] == "Observed answer"
    assert case["subjects"] is None
    assert case["evidence_complete"] is True
    assert "private-metadata" not in output.read_text()


@pytest.mark.parametrize("extension", ["json", "jsonl"])
def test_duplicate_json_fields_are_rejected(extension, tmp_path):
    source = tmp_path / f"records.{extension}"
    source.write_text('{"status":"invalid","status":"ok"}')
    with pytest.raises(ValueError, match="Duplicate JSON field"):
        read_records(source)


def test_json_reader_rejects_nonfinite_constants(tmp_path):
    source = tmp_path / "record.json"
    source.write_text('{"reward":NaN}')
    with pytest.raises(ValueError, match="Non-finite"):
        read_data(source)


def test_normalize_rejects_out_of_range_index(tmp_path):
    source, output = tmp_path / "dataset.json", tmp_path / "case.json"
    source.write_text("[]")
    with pytest.raises(SystemExit):
        main(["normalize", str(source), "--index", "1", "--output", str(output)])
    assert not output.exists()
