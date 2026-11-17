"""Validate saved score records before either Inspector can replace its data."""

import json
import math
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from ree.evaluator import evaluate
from ree.inspector import _validated_record, render_reports
from ree.judge import ReplayJudge
from ree.models import Case

ROOT = Path(__file__).parents[1]


def browser_results(sources):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for optional frontend regression")
    script = (ROOT / "src/web/inspector-data.js").read_text().split("function importError")[0]
    runner = r"""
const fs = require('node:fs'), vm = require('node:vm');
const {script, sources} = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = vm.createContext({TextEncoder, $: () => ({}),
  t: (key, values = {}) => key + ' ' + JSON.stringify(values)});
vm.runInContext(script, context);
console.log(JSON.stringify(sources.map(source => {
  try { return {records: context.parseRecords(source)}; }
  catch (error) { return {error: error.message}; }
})));
"""
    result = subprocess.run(
        [node, "-e", runner],
        input=json.dumps({"script": script, "sources": sources}),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


BAD_RECORDS = [
    {"status": "ok", "request": "Unscored trajectory", "response": "Done"},
    {"status": "ok", "reward": "1"},
    {"status": "invalid", "base": True},
    {"status": "invalid", "schema_version": None},
    {"status": "invalid", "schema_version": True},
    {"status": "invalid", "tasks": [{"hops": [None]}]},
    {"status": "invalid", "tasks": [{"hops": [{"subjects": {}}]}]},
    {"status": "invalid", "tasks": [{"hops": [{"score": "0.8"}]}]},
    {"status": "invalid", "subjects": [{"raw_score": "0.5"}]},
    {"status": "invalid", "subjects": [{"judgements": {"result": None}}]},
    {"status": "invalid", "subjects": [{"constraints": [None]}]},
    {"status": "invalid", "gate": {"checks": [None]}},
    {"status": "invalid", "gate": {"checks": [{"configured_reward": "0"}]}},
    {"status": "invalid", "bonus_details": {"checks": [None]}},
    {"status": "invalid", "bonus_details": {"checks": [{"judge_fact": None}]}},
    {"status": "invalid", "criteria": [{"annotations": [None]}]},
    {"status": "invalid", "criteria": [{"value": "1"}]},
    {"status": "invalid", "criteria": [{"evidence_refs": {}}]},
    {"status": "invalid", "composition": {"bounds": []}},
    {"status": "invalid", "metadata": {"profile": {"table": ["1"]}}},
    {"status": "invalid", "metadata": {"profile": {"group_weights": {"safety": True}}}},
    {"status": "invalid", "metadata": {"bonuses": [None]}},
    {"status": "invalid", "case": {"events": [None]}},
    {"status": "invalid", "case": {"events": [{"arguments": []}]}},
    {"status": "invalid", "case": {"events": [{"harness": {"tools": [None]}}]}},
    {"status": "invalid", "case": {"subjects": [None]}},
    {"status": "invalid", "case": {"harness": {"tools": [{"parameters": []}]}}},
    {"status": "invalid", "case": {"context": {"examples": [None]}}},
    {"status": "invalid", "metadata": {"display_query": True}},
    {"status": "invalid", "metadata": {"display_query": 3}},
    {"status": "invalid", "metadata": {"display_query": []}},
    {"status": "invalid", "metadata": {"display_query": {}}},
    {"status": "invalid", "metadata": {"display_reasoning": None}},
    {"status": "invalid", "metadata": {"display_reasoning": []}},
    {"status": "invalid", "metadata": {"display_reasoning": "Unmapped text"}},
    {"status": "invalid", "metadata": {"display_reasoning": {"request": "Not an assistant"}}},
    {"status": "invalid", "metadata": {"display_reasoning": {"history": "Not an assistant"}}},
    {"status": "invalid", "metadata": {"display_reasoning": {"absent": "Missing ref"}}},
    {"status": "invalid", "metadata": {"display_reasoning": {"response": None}}},
    {"status": "invalid", "metadata": {"display_reasoning": {"response": True}}},
    {"status": "invalid", "metadata": {"display_reasoning": {"response": {}}}},
    {
        "status": "invalid",
        "case": {"events": [{"ref": "system-note", "kind": "context"}]},
        "metadata": {"display_reasoning": {"system-note": "Not an assistant"}},
    },
    {
        "status": "invalid",
        "case": {"events": [{"ref": "tool-output", "kind": "tool_result"}]},
        "metadata": {"display_reasoning": {"tool-output": "Not an assistant"}},
    },
    {
        "status": "invalid",
        "case": {"artifacts": [{"ref": "file", "name": "File", "content": "Content"}]},
        "metadata": {"display_reasoning": {"file": "Not an assistant"}},
    },
]


@pytest.mark.parametrize("record", BAD_RECORDS)
def test_python_rejects_malformed_known_record_fields(record):
    with pytest.raises(ValueError):
        _validated_record(record)


def test_browser_rejects_same_malformed_records_before_installation():
    results = browser_results([json.dumps(record) for record in BAD_RECORDS])
    assert all("error" in result for result in results)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, 10**1000])
def test_python_rejects_nonfinite_or_overflowing_score(value):
    with pytest.raises(ValueError, match="finite number"):
        _validated_record({"status": "invalid", "reward": value})


def test_generic_actual_evaluation_and_sparse_records_survive_validation():
    case = Case.model_validate_json((ROOT / "examples/config-case.json").read_text())
    record = evaluate(
        case, ReplayJudge(json.loads((ROOT / "examples/config-replay.json").read_text()))
    )
    assert record["status"] == "ok"
    assert _validated_record(record) == record
    sparse = {"status": "invalid"}
    # Inspector displays configured scalar rewards without imposing default bounds.
    configured = {
        "status": "ok",
        "reward": -2.5,
        "base": None,
        "gate": {"reward": -2.5, "checks": [{"value": True, "configured_reward": -2.5}]},
        "case": {"subjects": None, "context": None, "events": [{"harness": None}]},
    }
    assert _validated_record(configured)["reward"] == -2.5
    results = browser_results([json.dumps(value) for value in [record, sparse, configured]])
    assert all("records" in result for result in results)
    assert results[0]["records"][0] == record
    assert results[2]["records"][0]["reward"] == -2.5


def test_duplicate_keys_rejected_with_decoded_unicode_in_json_and_jsonl():
    sources = [
        '{"status":"invalid","reward":0,"reward":1}',
        '{"status":"invalid","reward":0,"rewar\\u0064":1}',
        '{"status":"invalid","case":{"request":"a","request":"b"}}',
        '{"status":"invalid"}\n{"status":"invalid","reward":0,"reward":1}',
        '{"status":"invalid","metadata":{"\\ud83d\\ude00":1,"😀":2}}',
    ]
    results = browser_results(sources)
    assert all("import_duplicate" in result["error"] for result in results)


def test_duplicate_scanner_ignores_strings_and_independent_objects():
    records = [
        {"status": "invalid", "case": {"request": 'Braces {} [] and ":" and backslash \\'}},
        {"status": "invalid", "case": {"request": "Same key in a separate record"}},
    ]
    sources = [json.dumps(records), "\n".join(json.dumps(record) for record in records)]
    results = browser_results(sources)
    assert all(len(result["records"]) == 2 for result in results)
    assert results[0]["records"][0]["case"]["request"] == records[0]["case"]["request"]


def test_valid_jsonl_preserves_nonfinite_error_instead_of_reporting_syntax():
    result = browser_results(['{"status":"invalid"}\n{"status":"ok","reward":1e999}'])[0]
    assert "import_number" in result["error"]


@pytest.mark.parametrize("query", [None, "", " \n\t ", "A complete user query"])
def test_display_metadata_preserves_originals_and_accepts_blank_fallbacks(query):
    record = {
        "status": "invalid",
        "case": {
            "request": "Complete request plus its original context",
            "response": "A recorded answer",
            "events": [
                {"ref": "assistant-plan", "kind": "message", "content": "A recorded message"},
                {"ref": "assistant-call", "kind": "tool_call", "tool": "read", "arguments": {}},
            ],
        },
        "metadata": {
            "display_query": query,
            "display_reasoning": {
                "assistant-plan": "An existing recorded thought",
                "assistant-call": "An existing tool decision",
                "response": "",
            },
        },
    }
    original = deepcopy(record)
    validated = _validated_record(record)
    assert record == original
    assert validated["metadata"] == original["metadata"]
    assert validated["case"] == original["case"]
    result = browser_results([json.dumps(record)])[0]["records"][0]
    assert result["metadata"] == original["metadata"]
    assert result["case"] == original["case"]


def test_display_metadata_has_no_implicit_defaults():
    record = {"status": "invalid", "case": {"request": "Original request"}}
    original = deepcopy(record)
    result = _validated_record(record)
    assert record == original
    assert result["metadata"] == {}
    assert "display_query" not in result["metadata"]
    assert "display_reasoning" not in result["metadata"]
    assert browser_results([json.dumps(record)])[0]["records"][0]["metadata"] == {}


def test_html_exports_complete_display_text_without_changing_record(tmp_path):
    text = 'Recorded text </script><script>alert("text")</script>\n__RECORD_JSON__ 你好'
    record = {
        "status": "invalid",
        "case": {"request": "Original scoring request", "response": "Original response"},
        "metadata": {"display_query": text, "display_reasoning": {"response": text}},
    }
    original = deepcopy(record)
    paths = render_reports(record, tmp_path / "display.html")
    assert record == original
    for path in paths.values():
        html = path.read_text()
        embedded = re.search(
            r'<script type="application/json" id="evaluation-record">(.*?)</script>',
            html,
            re.DOTALL,
        )[1]
        exported = json.loads(embedded)
        assert exported["metadata"] == original["metadata"]
        assert exported["case"] == original["case"]
        assert '</script><script>alert("text")</script>' not in embedded
        assert browser_results([embedded])[0]["records"][0]["metadata"] == original["metadata"]
