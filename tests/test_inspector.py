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


def _run_renderer(tmp_path, record, driver, *, dual=False):
    """Exercise exported JS and actual click handlers with a small offline DOM."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for optional frontend regression")
    output = (
        render_reports(record, tmp_path / "browser.html")["en"]
        if dual
        else render_report(record, tmp_path / "browser.html")
    )
    html = output.read_text()
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
  showModal() { this.open=true; }
  close() { this.open=false; this.callbacks.close?.(); }
}
const elements = new Map(), localized = [];
const html = fs.readFileSync(process.argv[5],"utf8");
for (const match of html.matchAll(/<([a-z][\w-]*)\b([^>]+)>/gi)) {
 const attrs=match[2], id=attrs.match(/\bid="([^"]+)"/), key=attrs.match(/\bdata-i18n="([^"]+)"/);
 if (!id && !key) continue;
 const node=new Element(match[1]);
 const className=attrs.match(/\bclass="([^"]+)"/); if(className) node.className=className[1];
 if(id) elements.set(id[1],node);
 if(key) { node.dataset={i18n:key[1]}; localized.push(node); }
 node.open=/\bopen\b/.test(attrs);
}
const document = {
 documentElement:new Element("html"),
 getElementById(id) { return elements.get(id) || null; },
 createElement(tag) { return new Element(tag); },
 createElementNS(ns,tag) { return new Element(tag); },
 querySelectorAll(selector) { return selector==="[data-i18n]" ? localized : []; }
};
const $ = id => document.getElementById(id);
let mobileViewport=false;
$("evaluation-record").textContent=fs.readFileSync(process.argv[2],"utf8");
$("ui-locale").textContent=fs.readFileSync(process.argv[4],"utf8");
const context=vm.createContext({
 document, window: {print(){},matchMedia(query){return {matches:mobileViewport && query.includes("max-width")};}}, console
});
vm.runInContext(fs.readFileSync(process.argv[3],"utf8"),context);
const run = code => vm.runInContext(code,context);
const all = node => [node,...node.children.flatMap(all)];
const click = node => node.callbacks.click({preventDefault(){}});
"""
    runner, inputs, script, ui = [
        tmp_path / name for name in ("driver.js", "input.json", "actual.js", "locale.json")
    ]
    runner.write_text(source + driver)
    inputs.write_text(json.dumps(embedded_record(html)))
    script.write_text(frontend)
    ui.write_text(json.dumps(locale_data(html)))
    result = subprocess.run(
        [node, str(runner), str(inputs), str(script), str(ui), str(output)],
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
console.log(JSON.stringify({issue:issue.textContent,support:support.textContent,issueFocused:issue.focused===true,supportFocused:support.focused===true,supportOpen:all(support).find(node=>node.tag==="details").open===true,marks:nodes.filter(node=>node.tag==="mark").map(node=>node.textContent),pane:$("workbench").className}));
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
console.log(JSON.stringify({flow:$("flow-deduction").textContent,group:$("group-detail").textContent,criterion:$("criterion-list").textContent,cells:all($("criterion-list")).find(node=>node.tag==="tr"&&node.className==="criterion-item").children.map(node=>node.textContent),reward:$("flow-reward").textContent}));
""",
    )
    assert "—" in values["flow"] and "0.0000" not in values["flow"]
    assert "Group cost total—" in values["group"]
    assert "Group score—" in values["group"]
    assert "Missing evidence" in values["criterion"]
    assert values["cells"][2:5] == ["—", "4", "—"]
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
console.log(JSON.stringify(all($("criterion-list")).filter(node=>node.className.startsWith("status")).map(node=>({text:node.textContent,style:node.className}))));
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
click(all($("criterion-list")).find(node=>node.className==="inspect-evidence"));
console.log(JSON.stringify({before,after:{pane:$("workbench").className,focused:issue.focused===true}}));
""",
    )
    assert values["before"] == {
        "pane": "workbench show-score",
        "highlighted": True,
        "focused": False,
    }
    assert values["after"] == {"pane": "workbench show-trace", "focused": True}


def test_every_unscored_group_remains_inspectable_without_invented_scores(tmp_path):
    record = _annotated_record()
    record["criteria"].extend(
        [
            {
                "name": "tool_use.schema",
                "group": "tool_use",
                "status": "not_applicable",
                "value": None,
                "weight": 2.0,
                "reason": "No tool calls to inspect.",
            },
            {
                "name": "format.syntax",
                "group": "format",
                "status": "not_applicable",
                "value": None,
                "weight": 1.0,
                "reason": "No output format was declared.",
            },
        ]
    )
    values = _run_renderer(
        tmp_path,
        record,
        r"""
click($("flow-deduction"));
const groups=$("group-list").children.map(node=>node._groupName);
click($("group-list").children.find(node=>node._groupName==="tool_use"));
console.log(JSON.stringify({groups,details:$("group-detail").textContent,checks:$("criterion-list").textContent,curve:$("curve").children.length,quality:$("quality-equations").textContent}));
""",
    )
    assert set(values["groups"]) == {"*", "hallucination", "tool_use", "format"}
    assert "Not applicable" in values["details"]
    assert "Group cost total—" in values["details"]
    assert "Group score—" in values["details"]
    assert "tool_use.schema" in values["checks"]
    assert "No tool calls to inspect." in values["checks"]
    assert values["curve"] == 0
    assert "0.5000" in values["quality"]  # Unscored groups do not change stored quality.


def test_query_filter_updates_selection_and_empty_state_then_recovers(tmp_path):
    first, second = _annotated_record(), _annotated_record()
    first["case"]["request"], second["case"]["request"] = "First query", "Second query"
    second["reward"] = 0.25
    values = _run_renderer(
        tmp_path,
        [first, second],
        r"""
$("query-search").value="Second"; $("query-search").callbacks.input();
const selected={query:$("request-title").textContent,reward:$("flow-reward").textContent,count:$("query-list").children.length,exportDisabled:$("export-record").disabled};
$("query-search").value="No matching query"; $("query-search").callbacks.input();
const empty={query:$("request-title").textContent,record:run("getSelectedRecord()"),flowDisabled:$("flow-base").disabled,exportDisabled:$("export-record").disabled,trace:$("trace-content").textContent};
$("query-search").value=""; $("query-search").callbacks.input();
console.log(JSON.stringify({selected,empty,recovered:{query:$("request-title").textContent,count:$("query-list").children.length,flowDisabled:$("flow-base").disabled,exportDisabled:$("export-record").disabled}}));
""",
    )
    assert values["selected"] == {
        "query": "Second query",
        "reward": "Reward0.2500",
        "count": 1,
        "exportDisabled": False,
    }
    assert values["empty"]["record"] is None
    assert values["empty"]["flowDisabled"] is values["empty"]["exportDisabled"] is True
    assert "Second query" not in values["empty"]["trace"]
    assert values["recovered"] == {
        "query": "First query",
        "count": 2,
        "flowDisabled": False,
        "exportDisabled": False,
    }


def test_criterion_filters_remove_hidden_duplicates_and_stale_trace_highlights(tmp_path):
    values = _run_renderer(
        tmp_path,
        _annotated_record(),
        r"""
click($("flow-deduction"));
const before=all($("trace-content")).filter(node=>node.className.includes("evidence-issue")).length;
$("search").value="no such criterion"; $("search").callbacks.input();
console.log(JSON.stringify({before,after:all($("trace-content")).filter(node=>node.className.includes("evidence-issue")).length,summary:$("group-detail").textContent,checks:$("criterion-list").textContent,count:$("check-count").textContent}));
""",
    )
    assert values["before"] == 1 and values["after"] == 0
    assert "hallucination.execution_claim" not in values["summary"]
    assert "hallucination.execution_claim" not in values["checks"]
    assert "0 of 1" in values["count"]


def test_replace_records_resets_filters_and_can_show_an_empty_workspace(tmp_path):
    values = _run_renderer(
        tmp_path,
        _annotated_record(),
        r"""
$("query-search").value="old filter";
run('replaceRecords([{status:"ok",reward:0.75,case:{request:"Imported query"}}])');
const loaded={query:$("request-title").textContent,filter:$("query-search").value,reward:$("flow-reward").textContent};
run("replaceRecords([])");
const empty={record:run("getSelectedRecord()"),exportDisabled:$("export-record").disabled};
run('replaceRecords([{status:"invalid",case:{request:"Recovered input"}}])');
console.log(JSON.stringify({loaded,empty,recovered:$("request-title").textContent}));
""",
    )
    assert values["loaded"] == {"query": "Imported query", "filter": "", "reward": "Reward0.7500"}
    assert values["empty"] == {"record": None, "exportDisabled": True}
    assert values["recovered"] == "Recovered input"


def test_replace_records_rolls_back_data_selection_filters_and_stage_on_render_error(tmp_path):
    values = _run_renderer(
        tmp_path,
        _annotated_record(),
        r"""
click($("flow-deduction"));
$("query-search").value="debug"; // Preserve the visible prior record and active view.
$("search").value="execution"; $("search").callbacks.input();
let failed=false;
try { run('replaceRecords([{status:"invalid",subjects:[null]}])'); } catch { failed=true; }
console.log(JSON.stringify({failed,query:$("request-title").textContent,queryFilter:$("query-search").value,checkFilter:$("search").value,active:$("stage-deduction").hidden,record:run("getSelectedRecord().case.request"),exportDisabled:$("export-record").disabled}));
""",
    )
    assert values == {
        "failed": True,
        "query": "Change the debug setting.",
        "queryFilter": "debug",
        "checkFilter": "execution",
        "active": False,
        "record": "Change the debug setting.",
        "exportDisabled": False,
    }


def test_language_switch_keeps_imported_records_selected_view_and_filters(tmp_path):
    values = _run_renderer(
        tmp_path,
        _annotated_record(),
        r"""
run('replaceRecords([{status:"ok",reward:0.75,case:{request:"Imported first"}},{status:"invalid",case:{request:"Imported second"}}])');
click($("query-list").children[1]); click($("flow-base"));
$("query-search").value="Imported"; $("query-search").callbacks.input();
$("search").value="kept filter";
click($("alternate-language"));
const chinese={query:$("request-title").textContent,count:$("query-list").children.length,stageHidden:$("stage-base").hidden,title:document.title,filter:$("search").value,link:$("alternate-language").textContent,href:$("alternate-language").href};
click($("alternate-language"));
console.log(JSON.stringify({chinese,english:{query:$("request-title").textContent,title:document.title,stageHidden:$("stage-base").hidden,link:$("alternate-language").textContent}}));
""",
        dual=True,
    )
    assert values["chinese"] == {
        "query": "Imported second",
        "count": 2,
        "stageHidden": False,
        "title": "Reward Every Effort | 奖赏计算",
        "filter": "kept filter",
        "link": "English",
        "href": "browser.en.html",
    }
    assert values["english"] == {
        "query": "Imported second",
        "title": "Reward Every Effort | Reward calculation",
        "stageHidden": False,
        "link": "中文",
    }


def test_all_groups_searches_across_groups_and_preserves_filters_when_switching(tmp_path):
    record = _annotated_record()
    record["criteria"].extend(
        [
            {
                "name": "tool_use.schema",
                "group": "tool_use",
                "status": "not_applicable",
                "value": None,
                "weight": 2.0,
                "reason": "No tool calls to inspect.",
            },
            {
                "name": "format.schema",
                "group": "format",
                "status": "not_applicable",
                "value": None,
                "weight": 1.0,
                "reason": "No format was declared.",
            },
        ]
    )
    values = _run_renderer(
        tmp_path,
        record,
        r"""
click($("flow-deduction"));
$("search").value="schema"; $("search").callbacks.input();
$("status-filter").value="not_applicable"; $("status-filter").callbacks.change();
const scoped=$("criterion-list").textContent;
click($("group-list").children.find(node=>node._groupName==="*"));
const allView={checks:$("criterion-list").textContent,count:$("check-count").textContent,curve:$("curve").children.length,summary:$("quality-equations").textContent};
click($("group-list").children.find(node=>node._groupName==="tool_use"));
const single={checks:$("criterion-list").textContent,search:$("search").value,status:$("status-filter").value};
click($("group-list").children.find(node=>node._groupName==="*"));
console.log(JSON.stringify({scoped,all:allView,single,again:$("criterion-list").textContent,search:$("search").value,status:$("status-filter").value}));
""",
    )
    assert "tool_use.schema" not in values["scoped"]
    assert "tool_use.schema" in values["all"]["checks"]
    assert "format.schema" in values["all"]["checks"]
    assert "2 of 3" in values["all"]["count"]
    assert "Overall quality" in values["all"]["summary"]
    assert "0.5000" in values["all"]["summary"]
    assert values["all"]["curve"] == 0
    assert "tool_use.schema" in values["single"]["checks"]
    assert "format.schema" not in values["single"]["checks"]
    assert values["single"]["search"] == values["search"] == "schema"
    assert values["single"]["status"] == values["status"] == "not_applicable"
    assert values["again"] == values["all"]["checks"]


def test_switching_from_all_groups_restores_actual_group_curve_and_equations(tmp_path):
    values = _run_renderer(
        tmp_path,
        _annotated_record(),
        r"""
click($("flow-deduction"));
click($("group-list").children.find(node=>node._groupName==="*"));
const allView={curve:$("curve").children.length,active:$("group-list").children.find(node=>node._groupName==="*").attrs["aria-pressed"]};
click($("group-list").children.find(node=>node._groupName==="hallucination"));
console.log(JSON.stringify({all:allView,single:{curve:$("curve").children.length,equations:$("group-equations").textContent,active:$("group-list").children.find(node=>node._groupName==="hallucination").attrs["aria-pressed"]}}));
""",
    )
    assert values["all"] == {"curve": 0, "active": "true"}
    assert values["single"]["curve"] == 1
    assert "C = 4.0000" in values["single"]["equations"]
    assert values["single"]["active"] == "true"


def test_display_query_controls_titles_search_and_keeps_original_request(tmp_path):
    first, second = _annotated_record(), _annotated_record()
    first["case"]["request"] = "WRAPPER: private harness context\nUSER: Short query"
    first["metadata"].update(display_query="Short query", model="hidden-model")
    second["metadata"]["display_query"] = " \n "
    second["case"]["request"] = "Fallback request"
    values = _run_renderer(
        tmp_path,
        [first, second],
        r"""
const initial={title:$("request-title").textContent,tooltip:$("request-title").title,list:$("query-list").children.map(node=>({title:node.title,text:node.textContent})),request:run("getSelectedRecord().case.request"),display:run("getSelectedRecord().metadata.display_query")};
$("query-search").value="private harness"; $("query-search").callbacks.input();
const wrapperMatches=run("matchingRecordIndices().length");
$("query-search").value="hidden-model"; $("query-search").callbacks.input();
const modelMatches=run("matchingRecordIndices().length");
$("query-search").value="Fallback"; $("query-search").callbacks.input();
console.log(JSON.stringify({initial,wrapperMatches,modelMatches,fallback:$("request-title").textContent,tooltip:$("query-list").children[0].title}));
""",
    )
    assert values["initial"]["title"] == values["initial"]["tooltip"] == "Short query"
    assert values["initial"]["list"][0]["title"] == "Short query"
    assert "0.600" in values["initial"]["list"][0]["text"]
    assert "Record 1" in values["initial"]["list"][0]["text"]
    assert values["initial"]["request"] == first["case"]["request"]
    assert values["initial"]["display"] == "Short query"
    assert values["wrapperMatches"] == values["modelMatches"] == 0
    assert values["fallback"] == values["tooltip"] == "Fallback request"


def test_full_request_evidence_opens_original_input_without_marking_query(tmp_path):
    record = _annotated_record()
    record["metadata"]["display_query"] = "Short user query"
    record["case"]["request"] = "Harness context\nOriginal constraint: debug must remain off."
    record["criteria"][0].update(
        evidence_refs=["request"],
        annotations=[{"ref": "request", "kind": "issue", "quote": "debug must remain off"}],
    )
    values = _run_renderer(
        tmp_path,
        record,
        r"""
const request=run('evidenceMap.get("request")');
const fold=request.pieces[0].node.parentElement;
const before={open:fold.open===true,text:request.pieces[0].text,query:request.target.children.find(node=>node.className==="message-body body").textContent};
click($("flow-deduction"));
console.log(JSON.stringify({before,open:fold.open===true,query:request.target.children.find(node=>node.className==="message-body body").textContent,marks:all(request.target).filter(node=>node.tag==="mark").map(node=>({text:node.textContent,kind:node.className})),pieces:request.pieces.length}));
""",
    )
    assert values["before"] == {
        "open": False,
        "text": record["case"]["request"],
        "query": "Short user query",
    }
    assert values["open"] is True
    assert values["query"] == "Short user query"
    assert values["marks"] == [{"text": "debug must remain off", "kind": "quote-issue"}]
    assert values["pieces"] == 1


def test_recorded_thinking_is_folded_and_never_registered_as_evidence(tmp_path):
    record = _annotated_record()
    record["metadata"]["display_reasoning"] = {
        "call": "Plan ⚙️",
        "response": "I considered the result.",
    }
    record["case"]["events"][0].update(
        visible_context="External context", harness={"system": "External system"}
    )
    record["criteria"][0].update(evidence_refs=["call", "response"], annotations=[])
    values = _run_renderer(
        tmp_path,
        record,
        r"""
const logs=all($("trace-content")).filter(node=>node.className.includes("recorded-reasoning"));
const before=logs.map(node=>({open:node.open===true,text:node.textContent,count:all(node).find(child=>child.className==="character-count").textContent}));
click($("flow-deduction"));
const pieces=run("evidencePieces.map(piece=>({text:piece.text,className:piece.node.className}))");
console.log(JSON.stringify({before,after:logs.map(node=>({open:node.open===true,marks:all(node).filter(child=>child.tag==="mark").length})),pieces,keys:run("[...evidenceMap.keys()]")}));
""",
    )
    assert [item["open"] for item in values["before"]] == [False, False]
    assert values["before"][0]["count"] == f"{len('Plan ⚙️')} chars"
    assert all("excluded from scoring evidence" in item["text"] for item in values["before"])
    assert values["after"] == [{"open": False, "marks": 0}, {"open": False, "marks": 0}]
    assert all(item["className"].endswith("evidence-piece") for item in values["pieces"])
    assert not any(
        item["text"] in {"Plan ⚙️", "I considered the result.", "External context"}
        for item in values["pieces"]
    )
    assert set(values["keys"]) == {"request", "call", "observed", "response"}


def test_trajectory_roles_and_folds_preserve_tool_return_strings(tmp_path):
    record = _annotated_record()
    raw = '{"debug":false, "raw":"<unsafe>"}\n'
    record["case"]["events"].insert(
        0, {"ref": "progress", "kind": "message", "content": "I will inspect the setting."}
    )
    record["case"]["events"][2]["output"] = raw
    record["case"]["events"].append(
        {"ref": "content-return", "kind": "tool_result", "tool": "configure", "content": raw}
    )
    values = _run_renderer(
        tmp_path,
        record,
        r"""
const roles=$("trace-content").children.map(node=>node.children[0].children[0].textContent);
const observed=run('evidenceMap.get("observed")');
const argumentsPiece=run('evidenceMap.get("call").pieces[0]');
const message=run('evidenceMap.get("progress").pieces[0]');
const content=run('evidenceMap.get("content-return").pieces[0]');
console.log(JSON.stringify({roles,raw:observed.pieces[0].text,toolFold:observed.pieces[0].node.parentElement.tag,toolOpen:observed.pieces[0].node.parentElement.open===true,argsFold:argumentsPiece.node.parentElement.tag,argsOpen:argumentsPiece.node.parentElement.open===true,messageFold:message.node.parentElement.tag,message:message.text,content:content.text,contentFold:content.node.parentElement.tag,contentOpen:content.node.parentElement.open===true}));
""",
    )
    assert values["roles"] == ["User", "Assistant", "Assistant", "Tool", "Tool", "Assistant"]
    assert values["raw"] == raw
    assert values["toolFold"] == values["argsFold"] == "details"
    assert values["toolOpen"] is values["argsOpen"] is False
    assert values["messageFold"] == "article"
    assert values["message"] == "I will inspect the setting."
    assert values["content"] == raw
    assert values["contentFold"] == "details"
    assert values["contentOpen"] is False


def test_single_evidence_action_opens_all_refs_and_issue_wins_quote_overlap(tmp_path):
    record = _annotated_record()
    record["case"]["response"] = "ABCDE"
    record["criteria"][0].update(
        evidence_refs=["request", "observed"],
        annotations=[
            {"ref": "response", "kind": "support", "quote": "ABCD"},
            {"ref": "response", "kind": "issue", "quote": "CDE"},
        ],
    )
    values = _run_renderer(
        tmp_path,
        record,
        r"""
click($("flow-deduction"));
const row=all($("criterion-list")).find(node=>node.className==="criterion-item");
const buttons=all(row).filter(node=>node.tag==="button");
click(buttons[0]);
const before={buttons:buttons.map(node=>node.textContent),refs:all(row).filter(node=>node.className==="ref-chip").length,targets:run('highlightedTargets.length'),opened:run('evidenceMap.get("observed").pieces[0].node.parentElement.open === true'),marks:all($("trace-content")).filter(node=>node.tag==="mark").map(node=>({text:node.textContent,kind:node.className,scrolled:node.scrolled===true})),labels:all($("trace-content")).filter(node=>node.className.startsWith("trace-evidence-label")).length};
run("clearAnnotations()");
console.log(JSON.stringify({before,after:{marks:all($("trace-content")).filter(node=>node.tag==="mark").length,labels:all($("trace-content")).filter(node=>node.className.startsWith("trace-evidence-label")).length,notes:all($("trace-content")).filter(node=>node.className.startsWith("annotation-note")).length,targets:run("highlightedTargets.length"),response:run('evidenceMap.get("response").pieces[0].node.textContent'),classes:$("trace-content").children.map(node=>node.className)}}));
""",
    )
    assert values["before"]["buttons"] == ["View evidence"]
    assert values["before"]["refs"] == 0
    assert values["before"]["targets"] == values["before"]["labels"] == 3
    assert values["before"]["opened"] is True
    assert values["before"]["marks"] == [
        {"text": "AB", "kind": "quote-support", "scrolled": False},
        {"text": "CDE", "kind": "quote-issue", "scrolled": True},
    ]
    assert values["after"]["marks"] == values["after"]["labels"] == 0
    assert values["after"]["notes"] == values["after"]["targets"] == 0
    assert values["after"]["response"] == "ABCDE"
    assert all("evidence-issue" not in name for name in values["after"]["classes"])


def test_deduction_table_distinguishes_pass_na_missing_error_and_unknown(tmp_path):
    record = _annotated_record()
    record["criteria"] = [
        {
            "name": name,
            "group": "hallucination",
            "status": status,
            "value": value,
            "weight": weight,
            "evidence_refs": ["response"],
        }
        for name, status, value, weight in [
            ("pass", "ok", True, 4),
            ("fail", "ok", False, 3),
            ("partial", "ok", 0.5, 2),
            ("na", "not_applicable", None, 1),
            ("missing", "missing_evidence", None, 5),
            ("error", "error", None, 6),
            ("unknown", "ok", None, 7),
        ]
    ]
    values = _run_renderer(
        tmp_path,
        record,
        r"""
click($("flow-deduction"));
console.log(JSON.stringify({head:all($("criterion-list")).filter(node=>node.tag==="th").map(node=>node.textContent),rows:all($("criterion-list")).filter(node=>node.className==="criterion-item").map(node=>({name:node._criterionName,cells:node.children.slice(1).map(cell=>cell.textContent)}))}));
""",
    )
    assert values["head"] == [
        "Criterion",
        "Status",
        "Score sᵢ",
        "Item weight",
        "Deduction cost",
        "Evidence",
    ]
    rows = {row["name"]: row["cells"] for row in values["rows"]}
    assert rows["pass"][:4] == ["Pass", "1", "4", "0"]
    assert rows["fail"][:4] == ["Violation", "0", "3", "3"]
    assert rows["partial"][:4] == ["Violation", "0.5", "2", "1"]
    assert rows["na"][:4] == ["Not applicable", "—", "1", "—Not counted"]
    assert rows["missing"][:4] == ["Missing evidence", "—", "5", "—"]
    assert rows["error"][:4] == ["Execution error", "—", "6", "—"]
    assert rows["unknown"][:4] == ["Unavailable", "—", "7", "—"]
    assert all(row[4] == "View evidence" for row in rows.values())


def test_aggregation_uses_recorded_weights_scores_and_excludes_na(tmp_path):
    record = _annotated_record()
    record.update(quality=0.65, deduction=0.28, reward=0.72)
    record["groups"] = [
        {"name": "hallucination", "cost": 4, "score": 0.5, "weight": 0.375, "status": "ok"},
        {"name": "tool_use", "cost": 1, "score": 0.8, "weight": 0.375, "status": "ok"},
        {"name": "safety", "cost": None, "score": None, "weight": 0.1, "status": "not_applicable"},
    ]
    values = _run_renderer(
        tmp_path,
        record,
        r"""
click($("flow-deduction"));
const before={group:$("group-detail").textContent,rows:all($("group-aggregation")).filter(node=>node.tag==="tr").slice(1).map(node=>node.children.map(cell=>cell.textContent)),metrics:all($("quality-equations")).filter(node=>node.className==="metric-row").map(node=>node.children.map(child=>child.textContent))};
$("search").value="no matches"; $("search").callbacks.input();
console.log(JSON.stringify({before,after:all($("quality-equations")).filter(node=>node.className==="metric-row").map(node=>node.children.map(child=>child.textContent))}));
""",
    )
    assert "Group cost total4" in values["before"]["group"]
    assert "Group score0.5000" in values["before"]["group"]
    assert [row[1:] for row in values["before"]["rows"]] == [
        ["4", "0.5000", "0.375", "0.1875"],
        ["1", "0.8000", "0.375", "0.3"],
        ["—", "—", "0.1", "—"],
    ]
    assert "Not counted" in values["before"]["rows"][2][0]
    assert dict(values["before"]["metrics"]) == {
        "Weighted contribution total": "0.4875",
        "Active group weight total": "0.75",
        "Overall quality": "0.6500",
        "Maximum deduction": "0.8",
        "Deduction": "0.28",
    }
    assert values["after"] == values["before"]["metrics"]


def test_unresolved_group_never_enters_quality_formula_with_stale_score(tmp_path):
    record = _annotated_record()
    record.update(status="invalid", quality=None, deduction=None, reward=None)
    record["groups"].append(
        {"name": "tool_use", "cost": 8, "score": 0.75, "weight": 9, "status": "invalid"}
    )
    values = _run_renderer(
        tmp_path,
        record,
        r"""
click($("flow-deduction"));
const initialFormula=$("group-equations").textContent;
click($("group-list").children.find(node=>node._groupName==="*"));
console.log(JSON.stringify({initialFormula,formula:$("group-equations").textContent,summary:$("quality-equations").textContent,rows:all($("group-aggregation")).filter(node=>node.tag==="tr").slice(1).map(node=>node.children.map(cell=>cell.textContent))}));
""",
    )
    assert "9.0000×0.7500" not in values["formula"]
    assert "9.0000×0.7500" not in values["initialFormula"]
    assert values["rows"][1][-1] == "—"
    assert "Overall quality—" in values["summary"]
    assert "not treated as excluded" in values["summary"]


def test_language_switch_localizes_new_trace_and_deduction_details(tmp_path):
    record = _annotated_record()
    record["metadata"].update(
        display_query="Original display Query",
        display_reasoning={"response": "Original external log"},
    )
    values = _run_renderer(
        tmp_path,
        record,
        r"""
click($("flow-deduction"));
click($("alternate-language"));
const chinese={query:$("request-title").textContent,trace:$("trace-content").textContent,head:all($("criterion-list")).filter(node=>node.tag==="th").map(node=>node.textContent),button:all($("criterion-list")).find(node=>node.className==="inspect-evidence").textContent,quality:$("quality-equations").textContent};
click($("alternate-language"));
console.log(JSON.stringify({chinese,english:{trace:$("trace-content").textContent,button:all($("criterion-list")).find(node=>node.className==="inspect-evidence").textContent,quality:$("quality-equations").textContent},request:run("getSelectedRecord().case.request"),reasoning:run("getSelectedRecord().metadata.display_reasoning.response")}));
""",
        dual=True,
    )
    assert values["chinese"]["query"] == "Original display Query"
    assert "请求上下文 / 完整输入" in values["chinese"]["trace"]
    assert "记录中的思考" in values["chinese"]["trace"]
    assert values["chinese"]["button"] == "查看证据"
    assert "子项权重" in values["chinese"]["head"]
    assert "综合质量" in values["chinese"]["quality"]
    assert "Recorded thinking" in values["english"]["trace"]
    assert values["english"]["button"] == "View evidence"
    assert "Overall quality" in values["english"]["quality"]
    assert values["request"] == record["case"]["request"]
    assert values["reasoning"] == "Original external log"
