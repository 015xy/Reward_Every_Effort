import json
import re

import pytest
from ree.inspector import render_report, render_reports


def embedded_record(html):
    match = re.search(
        r'<script type="application/json" id="evaluation-record">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match
    return json.loads(match.group(1))


def test_untrusted_trajectory_cannot_close_json_script(tmp_path):
    attack = '</ScRiPt><img src="https://evil.invalid" onerror="alert(1)">\u2028&'
    record = {
        "schema_version": 1,
        "status": "invalid",
        "errors": [attack],
        "case": {"request": attack, "response": attack, "events": [{"ref": attack}]},
    }
    output = render_report(record, tmp_path / "report.html")
    html = output.read_text()
    assert attack not in html
    assert "</ScRiPt>" not in html
    assert embedded_record(html)["case"]["request"] == attack
    assert embedded_record(html)["errors"] == [attack]
    assert "connect-src 'none'" in html
    assert "https://evil.invalid" in html  # Text preserved; never an HTML attribute.
    # Only trusted data, localized UI strings, and the renderer are script elements.
    assert html.count("<script") == 3


def test_preserves_skipped_missing_and_blocked_branch_scores(tmp_path):
    record = {
        "status": "invalid",
        "reward": None,
        "subjects": [
            {
                "index": 0,
                "subject": "Validate A",
                "task": 0,
                "hop": 0,
                "raw_score": 1.0,
                "final_score": 1.0,
                "row1": False,
                "final_row1": False,
                "depends_on": [],
                "blocked_by": [],
            },
            {
                "index": 1,
                "subject": "Validate B",
                "task": 0,
                "hop": 0,
                "raw_score": 1.0,
                "final_score": 1.0,
                "row1": True,
                "final_row1": True,
                "depends_on": [],
                "blocked_by": [],
            },
            {
                "index": 2,
                "subject": "Release A",
                "task": 0,
                "hop": 1,
                "raw_score": 1.0,
                "final_score": 0.0,
                "row1": True,
                "final_row1": False,
                "depends_on": [0],
                "blocked_by": [0],
            },
            {
                "index": 3,
                "subject": "Release B",
                "task": 0,
                "hop": 1,
                "raw_score": 1.0,
                "final_score": 1.0,
                "row1": True,
                "final_row1": True,
                "depends_on": [1],
                "blocked_by": [],
            },
        ],
        "tasks": [
            {
                "index": 0,
                "score": 0.75,
                "hops": [
                    {"index": 0, "subjects": [0, 1], "score": 1.0, "denominator": 2},
                    {"index": 1, "subjects": [2, 3], "score": 0.5, "denominator": 2},
                ],
            }
        ],
        "criteria": [
            {"name": "format.syntax", "status": "not_applicable", "value": None},
            {"name": "fact.execution", "status": "missing_evidence", "value": None},
        ],
    }
    parsed = embedded_record(render_report(record, tmp_path / "report.html").read_text())
    assert parsed["subjects"][2]["final_score"] == 0.0
    assert parsed["subjects"][3]["final_score"] == 1.0
    assert parsed["tasks"][0]["hops"][1]["denominator"] == 2
    assert [(c["status"], c["value"]) for c in parsed["criteria"]] == [
        ("not_applicable", None),
        ("missing_evidence", None),
    ]
    assert parsed["reward"] is None


def test_sparse_invalid_record_is_inspectable(tmp_path):
    html = render_report(
        {"status": "invalid", "errors": ["labeler failed"]}, tmp_path / "nested" / "failed.html"
    ).read_text()
    record = embedded_record(html)
    assert record["subjects"] == []
    assert record["tasks"] == []
    assert record["metadata"] == {}
    assert record["errors"] == ["labeler failed"]


@pytest.mark.parametrize(
    "record",
    [
        [],
        {"status": "pass"},
        {"status": "invalid", "schema_version": 2},
        {"status": "invalid", "subjects": {}},
        {"status": "invalid", "criteria": ["not an object"]},
        {"status": "invalid", "case": []},
        {"status": "ok", "reward": float("nan")},
    ],
)
def test_rejects_malformed_record_without_writing(record, tmp_path):
    output = tmp_path / "report.html"
    with pytest.raises((ValueError, TypeError)):
        render_report(record, output)
    assert not output.exists()


def test_assets_are_embedded_and_template_like_input_remains_text(tmp_path):
    marker = "__REPORT_CSS__ __REPORT_JS__ __RECORD_JSON__ __LOCALE_JSON__"
    record = {"status": "invalid", "case": {"request": marker}}
    html = render_report(record, tmp_path / "report.html").read_text()
    assert embedded_record(html)["case"]["request"] == marker
    assert "prefers-reduced-motion" in html
    assert ":focus-visible" in html
    assert "<script src=" not in html
    assert "<link" not in html


def test_browser_curve_remains_finite_with_extreme_profile(tmp_path):
    # Execute the actual exported JS with a minimal DOM. No browser dependency is
    # required for the renderer; skip this additional JS regression without Node.
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for optional frontend regression")
    record = {
        "status": "ok",
        "base": 1.0,
        "reward": 0.6,
        "quality": 0.5,
        "deduction": 0.4,
        "groups": [{"name": "safety", "cost": 16.0, "score": 0.5, "weight": 1.0}],
        "metadata": {"profile": {"half_life": 1e300, "max_cost": 32.0}},
        "case": {"request": "Test extreme normalization"},
    }
    html = render_report(record, tmp_path / "report.html").read_text()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    assert len(scripts) == 1
    harness = r"""
const vm = require("node:vm");
const fs = require("node:fs");
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.attrs={}; this.style={}; this.value=""; this.textContent=""; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; }
  setAttribute(name,value) { this.attrs[name]=String(value); }
  addEventListener() {}
}
const elements = new Map();
const document = {
 getElementById(id) { if(!elements.has(id)) elements.set(id,new Element("div")); return elements.get(id); },
 createElement(tag) { return new Element(tag); },
 createElementNS(ns,tag) { return new Element(tag); },
 querySelectorAll() { return []; }
};
document.getElementById("evaluation-record").textContent=fs.readFileSync(process.argv[2],"utf8");
document.getElementById("ui-locale").textContent=fs.readFileSync(process.argv[4],"utf8");
vm.runInNewContext(fs.readFileSync(process.argv[3],"utf8"), {
 document, window: {print(){},matchMedia(){return {matches:false};}}, console
});
function check(element) {
 for(const value of Object.values(element.attrs)) {
  if(/NaN|Infinity/.test(value)) throw new Error("Nonfinite SVG attribute: "+value);
 }
 for(const child of element.children) check(child);
}
const curve=elements.get("curve");
if(!curve || !curve.children.length) throw new Error("Curve not rendered");
check(curve);
"""
    runner = tmp_path / "dom-check.js"
    runner.write_text(harness)
    input_file = tmp_path / "record.json"
    input_file.write_text(json.dumps(record))
    frontend = tmp_path / "frontend.js"
    frontend.write_text(scripts[0])
    locale_file = tmp_path / "locale.json"
    locale_file.write_text(
        re.search(
            r'<script type="application/json" id="ui-locale">(.*?)</script>', html, re.DOTALL
        )[1]
    )
    result = subprocess.run(
        [node, str(runner), str(input_file), str(frontend), str(locale_file)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def locale_data(html):
    return json.loads(
        re.search(
            r'<script type="application/json" id="ui-locale">(.*?)</script>', html, re.DOTALL
        )[1]
    )


def test_two_complete_languages_link_only_generated_siblings(tmp_path):
    record = {"status": "invalid", "case": {"request": "Original request 保留原文"}}
    paths = render_reports(record, tmp_path / "analysis.html")
    assert set(paths) == {"en", "zh"}
    en, zh = [paths[language].read_text() for language in ("en", "zh")]
    assert '<html lang="en">' in en
    assert '<html lang="zh-CN">' in zh
    en_ui, zh_ui = locale_data(en), locale_data(zh)
    assert en_ui["strings"]["title"] == "Reward calculation"
    assert zh_ui["strings"]["title"] == "奖赏计算"
    assert en_ui["alternate"]["href"] == "analysis.zh.html"
    assert zh_ui["alternate"]["href"] == "analysis.en.html"
    assert embedded_record(en)["case"] == embedded_record(zh)["case"] == record["case"]
    assert set(en_ui["strings"]) == set(zh_ui["strings"])


def test_single_language_never_links_to_nonexistent_alternate(tmp_path):
    html = render_report({"status": "invalid"}, tmp_path / "single.html", locale="zh").read_text()
    assert "alternate" not in locale_data(html)
    with pytest.raises(ValueError):
        render_report({"status": "invalid"}, tmp_path / "unknown.html", locale="fr")


def test_template_markers_in_filenames_do_not_corrupt_embedded_json(tmp_path):
    record = {"status": "invalid", "case": {"request": "__LOCALE_JSON__ __RECORD_JSON__"}}
    paths = render_reports(record, tmp_path / "__RECORD_JSON__.html")
    for output in paths.values():
        assert embedded_record(output.read_text())["case"] == record["case"]
        assert locale_data(output.read_text())["alternate"]["href"].startswith("__RECORD_JSON__.")


def test_profile_table_is_preserved_without_hardcoded_reference_scores(tmp_path):
    profile = {"table": [0.93, 0.81, 0.77, 0.31, 0.52, 0.04]}
    record = {"status": "invalid", "metadata": {"profile": profile}}
    html = render_report(record, tmp_path / "profile.html").read_text()
    assert embedded_record(html)["metadata"]["profile"]["table"] == profile["table"]
    assert "list(profile.table)[index]" in html
    assert "+ bonus" not in html


def _run_renderer(tmp_path, record, driver):
    """Exercise exported JS and actual click handlers with a small offline DOM."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for optional frontend regression")
    html = render_report(record, tmp_path / "browser.html").read_text()
    frontend = re.search(r"<script>(.*?)</script>", html, re.DOTALL)[1]
    source = r"""
const vm = require("node:vm");
const fs = require("node:fs");
class Element {
  constructor(tag) { this.tag=tag; this.tagName=tag.toUpperCase(); this.children=[]; this.attrs={}; this.style={}; this.callbacks={}; this.value=""; this._text=""; this.className=""; }
  get textContent() { return this._text + this.children.map(child=>child.textContent).join(""); }
  set textContent(value) { this._text=String(value); this.children=[]; }
  append(...children) { for(const child of children) { child.parentElement=this; this.children.push(child); } }
  replaceChildren(...children) { this._text=""; this.children=[]; this.append(...children); }
  setAttribute(name,value) { this.attrs[name]=String(value); }
  addEventListener(name,callback) { this.callbacks[name]=callback; }
  remove() { if(this.parentElement) this.parentElement.children=this.parentElement.children.filter(child=>child!==this); }
  focus() { this.focused=true; }
  scrollIntoView() { this.scrolled=true; }
}
const elements = new Map();
const document = {
 getElementById(id) { if(!elements.has(id)) elements.set(id,new Element("div")); return elements.get(id); },
 createElement(tag) { return new Element(tag); },
 createElementNS(ns,tag) { return new Element(tag); },
 querySelectorAll() { return []; }
};
const $ = id => document.getElementById(id);
let mobileViewport=false;
$("evaluation-record").textContent=fs.readFileSync(process.argv[2],"utf8");
$("ui-locale").textContent=fs.readFileSync(process.argv[4],"utf8");
vm.runInNewContext(fs.readFileSync(process.argv[3],"utf8"), {
 document, window: {print(){},matchMedia(query){return {matches:mobileViewport && query.includes("max-width")};}}, console
});
const all = node => [node,...node.children.flatMap(all)];
const click = node => node.callbacks.click();
"""
    runner, inputs, script, ui = [
        tmp_path / name for name in ("driver.js", "input.json", "actual.js", "locale.json")
    ]
    runner.write_text(source + driver)
    inputs.write_text(json.dumps(embedded_record(html)))
    script.write_text(frontend)
    ui.write_text(json.dumps(locale_data(html)))
    result = subprocess.run(
        [node, str(runner), str(inputs), str(script), str(ui)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_multiple_records_select_their_own_actual_scoring_path(tmp_path):
    record = {
        "status": "ok",
        "reward": 0.9,
        "base": 1.0,
        "deduction": 0.1,
        "bonus": 0.0,
        "gate": {"status": "admitted", "enabled": False, "checks": []},
        "bonus_details": {"status": "disabled", "cap": 0.1, "uncapped": 0.0, "checks": []},
        "composition": {"path": "normal", "before_clip": 0.9, "bounds": {"min": 0.1, "max": 1.0}},
        "case": {"request": "Same query"},
    }
    routed = {
        "status": "ok",
        "reward": 0.0,
        "base": None,
        "deduction": None,
        "bonus": None,
        "gate": {
            "status": "routed",
            "enabled": True,
            "matched_rule": "policy",
            "reward": 0.0,
            "checks": [],
        },
        "bonus_details": {"status": "not_run", "cap": 0.1, "checks": []},
        "composition": {"path": "gate", "before_clip": 0.0, "bounds": {"min": 0.1, "max": 1.0}},
        "case": {"request": "Same query"},
    }
    values = _run_renderer(
        tmp_path,
        [record, routed],
        r"""
const initial={reward:$("flow-reward").textContent,bonus:$("flow-bonus").textContent,count:$("query-list").children.length};
click($("query-list").children[1]);
console.log(JSON.stringify({initial,base:$("flow-base").textContent,deduct:$("flow-deduction").textContent,bonus:$("flow-bonus").textContent,reward:$("flow-reward").textContent,route:$("flow-diagram").className,formula:$("reward-detail").textContent}));
""",
    )
    assert values["initial"]["count"] == 2
    assert "0.9000" in values["initial"]["reward"]
    assert "0.0000" in values["initial"]["bonus"]
    assert "Not run" in values["base"]
    assert "Not run" in values["deduct"]
    assert "Not run" in values["bonus"]
    assert "0.0000" in values["reward"]  # The direct Gate reward bypasses clipping.
    assert "gate-route" in values["route"]
    assert "Reward = Gate = 0.0000" in values["formula"]
    assert "clip[" not in values["formula"]


def _annotated_record():
    return {
        "status": "ok",
        "reward": 0.6,
        "base": 1.0,
        "quality": 0.5,
        "deduction": 0.4,
        "case": {
            "request": "Change the debug setting.",
            "response": "Saved: debug=true.",
            "events": [
                {
                    "ref": "call",
                    "kind": "tool_call",
                    "tool": "configure",
                    "arguments": {"debug": True},
                },
                {
                    "ref": "observed",
                    "kind": "tool_result",
                    "tool": "configure",
                    "call_ref": "call",
                    "output": {"debug": False},
                },
            ],
        },
        "metadata": {"profile": {"half_life": 8.0, "max_cost": 32.0, "deduction_max": 0.8}},
        "groups": [
            {"name": "hallucination", "cost": 4.0, "score": 0.5, "weight": 1.0, "status": "ok"}
        ],
        "criteria": [
            {
                "name": "hallucination.execution_claim",
                "group": "hallucination",
                "status": "ok",
                "value": False,
                "weight": 4.0,
                "reason": "Claim contradicts observed state.",
                "evidence_refs": ["observed", "response"],
                "annotations": [
                    {
                        "ref": "response",
                        "kind": "issue",
                        "quote": "debug=true",
                        "reason": "Unsupported completion claim",
                    },
                    {
                        "ref": "observed",
                        "kind": "support",
                        "quote": '"debug": false',
                        "reason": "Actual setting remained disabled",
                    },
                ],
            }
        ],
    }


def test_deduction_focuses_issue_before_support_and_highlights_exact_quotes(tmp_path):
    values = _run_renderer(
        tmp_path,
        _annotated_record(),
        r"""
click($("flow-deduction"));
const nodes=all($("trace-content"));
const issue=nodes.find(node=>node.className.includes("evidence-issue"));
const support=nodes.find(node=>node.className.includes("evidence-support"));
console.log(JSON.stringify({issue:issue.textContent,support:support.textContent,issueFocused:issue.focused===true,supportFocused:support.focused===true,supportOpen:support.open===true,marks:nodes.filter(node=>node.tag==="mark").map(node=>node.textContent),pane:$("workbench").className}));
""",
    )
    assert values["issueFocused"] is True
    assert values["supportFocused"] is False
    assert values["supportOpen"] is True
    assert "Unsupported completion claim" in values["issue"]
    assert "Actual setting remained disabled" in values["support"]
    assert values["marks"] == ['"debug": false', "debug=true"]
    assert "show-trace" in values["pane"]


def test_source_references_never_become_issue_annotations(tmp_path):
    record = _annotated_record()
    record["criteria"][0].pop("annotations")
    values = _run_renderer(
        tmp_path,
        record,
        r"""
click($("flow-deduction"));
const nodes=all($("trace-content"));
console.log(JSON.stringify({issues:nodes.filter(node=>node.className.includes("evidence-issue")).length,support:nodes.filter(node=>node.className.includes("evidence-support")).length,marks:nodes.filter(node=>node.tag==="mark").length,related:nodes.find(node=>node.className.includes("selected-evidence")).textContent}));
""",
    )
    assert values["issues"] == values["support"] == values["marks"] == 0
    assert "Related evidence" in values["related"]


def test_failed_and_deferred_groups_do_not_display_zero_cost_passes(tmp_path):
    record = _annotated_record()
    record.update(status="invalid", reward=None, quality=None, deduction=None)
    record["groups"][0].update(status="invalid", cost=None, score=None)
    record["criteria"][0].update(status="missing_evidence", value=None)
    values = _run_renderer(
        tmp_path,
        record,
        r"""
click($("flow-deduction"));
$("status-filter").value="missing_evidence";
$("status-filter").callbacks.change();
console.log(JSON.stringify({flow:$("flow-deduction").textContent,group:$("group-detail").textContent,criterion:$("criterion-list").textContent,reward:$("flow-reward").textContent}));
""",
    )
    assert "—" in values["flow"] and "0.0000" not in values["flow"]
    assert "C = —" in values["group"]
    assert "Missing evidence" in values["criterion"]
    assert "= —" in values["criterion"]
    assert "—" in values["reward"] and "0.0000" not in values["reward"]


@pytest.mark.parametrize("value", [False, 0.5])
def test_violations_and_partial_failures_have_failure_status_badges(tmp_path, value):
    record = _annotated_record()
    record["criteria"][0]["value"] = value
    values = _run_renderer(
        tmp_path,
        record,
        r"""
click($("flow-deduction"));
console.log(JSON.stringify(all($("group-detail")).filter(node=>node.className.startsWith("status")).map(node=>({text:node.textContent,style:node.className}))));
""",
    )
    assert {"text": "Violation", "style": "status failed"} in values


def test_mobile_stage_selection_keeps_scores_visible_until_evidence_is_requested(tmp_path):
    values = _run_renderer(
        tmp_path,
        _annotated_record(),
        r"""
mobileViewport=true;
click($("flow-deduction"));
const stagePane=$("workbench").className;
const issue=all($("trace-content")).find(node=>node.className.includes("evidence-issue"));
const before={pane:stagePane,highlighted:!!issue,focused:issue.focused===true};
click(all($("group-detail")).find(node=>node.className==="inspect-evidence"));
console.log(JSON.stringify({before,after:{pane:$("workbench").className,focused:issue.focused===true}}));
""",
    )
    assert values["before"] == {
        "pane": "workbench show-score",
        "highlighted": True,
        "focused": False,
    }
    assert values["after"] == {"pane": "workbench show-trace", "focused": True}
