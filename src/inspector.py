"""Generate a portable, offline Inspector for saved evaluation records.

The trajectory is untrusted. It is transported as escaped JSON and rendered only
with textContent; neither prompts nor event payloads become executable markup.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

_REQUIRED_LISTS = ("subjects", "tasks", "criteria", "groups", "errors")


def _object_field(parent: dict, name: str, path: str, *, nullable: bool = False) -> dict:
    if name not in parent or (nullable and parent[name] is None):
        return {}
    if not isinstance(parent[name], dict):
        raise ValueError(f"Evaluation record {path} must be an object")
    return parent[name]


def _list_field(
    parent: dict, name: str, path: str, *, objects: bool = False, nullable: bool = False
) -> list:
    if name not in parent or (nullable and parent[name] is None):
        return []
    value = parent[name]
    if not isinstance(value, list):
        raise ValueError(f"Evaluation record {path} must be a list")
    if objects and any(not isinstance(item, dict) for item in value):
        raise ValueError(f"Evaluation record {path} entries must be objects")
    return value


def _scores(parent: dict, names: tuple[str, ...], path: str = "", *, boolean: bool = False):
    for name in names:
        if name not in parent or parent[name] is None or (boolean and type(parent[name]) is bool):
            continue
        value = parent[name]
        try:
            valid = type(value) in {int, float} and math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f"Evaluation record {path}{name} must be a finite number or null")


def _judgement(value: dict, path: str):
    _scores(value, ("value",), f"{path}.", boolean=True)
    _list_field(value, "evidence_refs", f"{path}.evidence_refs")
    _list_field(value, "annotations", f"{path}.annotations", objects=True)
    if "judge_fact" in value:
        _judgement(_object_field(value, "judge_fact", f"{path}.judge_fact"), f"{path}.judge_fact")


def _subject(value: dict, path: str):
    _scores(value, ("raw_score", "final_score"), f"{path}.")
    for name in ("depends_on", "blocked_by"):
        _list_field(value, name, f"{path}.{name}")
    _list_field(value, "constraints", f"{path}.constraints", objects=True)
    for name, judgement in _object_field(value, "judgements", f"{path}.judgements").items():
        if not isinstance(judgement, dict):
            raise ValueError(f"Evaluation record {path}.judgements.{name} must be an object")
        _judgement(judgement, f"{path}.judgements.{name}")


def _harness(value: dict, path: str):
    for index, tool in enumerate(_list_field(value, "tools", f"{path}.tools", objects=True)):
        _object_field(tool, "parameters", f"{path}.tools[{index}].parameters", nullable=True)
    _list_field(value, "skills", f"{path}.skills", objects=True)
    _list_field(value, "permissions", f"{path}.permissions")
    _object_field(value, "compression", f"{path}.compression")


def _validated_record(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("Inspector expects a JSON object evaluation record")
    if "request" in record:
        raise ValueError("Inspector expects an evaluation record, not an unscored trajectory")
    if type(record.get("schema_version", 1)) is not int or record.get("schema_version", 1) != 1:
        raise ValueError("Unsupported evaluation record schema_version")
    if record.get("status") not in {"ok", "invalid"}:
        raise ValueError("Evaluation record status must be 'ok' or 'invalid'")
    # Sparse invalid records are useful when evaluation failed before scoring.
    result = dict(record)
    for name in _REQUIRED_LISTS:
        result.setdefault(name, [])
        _list_field(result, name, name, objects=name != "errors")
    for name in ("case", "metadata"):
        if not isinstance(result.setdefault(name, {}), dict):
            raise ValueError(f"Evaluation record {name} must be an object")
    _scores(result, ("reward", "base", "quality", "deduction", "bonus"))
    for index, subject in enumerate(result["subjects"]):
        _subject(subject, f"subjects[{index}]")
    for index, task in enumerate(result["tasks"]):
        path = f"tasks[{index}]"
        _scores(task, ("score",), f"{path}.")
        for hop_index, hop in enumerate(_list_field(task, "hops", f"{path}.hops", objects=True)):
            hop_path = f"{path}.hops[{hop_index}]"
            _list_field(hop, "subjects", f"{hop_path}.subjects")
            _scores(hop, ("score",), f"{hop_path}.")
    for name in ("criteria", "groups"):
        for index, value in enumerate(result[name]):
            _scores(value, ("score", "cost", "weight"), f"{name}[{index}].")
            if name == "criteria":
                _judgement(value, f"{name}[{index}]")
    for name in ("gate", "bonus_details"):
        value = _object_field(result, name, name)
        _scores(value, ("reward", "cap", "uncapped"), f"{name}.")
        for index, check in enumerate(_list_field(value, "checks", f"{name}.checks", objects=True)):
            path = f"{name}.checks[{index}]"
            _scores(check, ("granted", "max_value", "configured_reward"), f"{path}.")
            _judgement(check, path)
    if "conversation" in result:
        _judgement(_object_field(result, "conversation", "conversation"), "conversation")
    composition = _object_field(result, "composition", "composition")
    _scores(
        composition, ("before_clip", "base", "deduction", "bonus", "gate_reward"), "composition."
    )
    _scores(
        _object_field(composition, "bounds", "composition.bounds"),
        ("min", "max"),
        "composition.bounds.",
    )
    profile = _object_field(result["metadata"], "profile", "metadata.profile")
    _scores(
        profile,
        ("half_life", "max_cost", "deduction_max", "bonus_max", "reward_min", "reward_max"),
        "metadata.profile.",
    )
    table = _list_field(profile, "table", "metadata.profile.table")
    _scores(dict(enumerate(table)), tuple(range(len(table))), "metadata.profile.table.")
    weights = _object_field(profile, "group_weights", "metadata.profile.group_weights")
    _scores(weights, tuple(weights), "metadata.profile.group_weights.")
    for name in ("gates", "bonuses"):
        for index, rule in enumerate(
            _list_field(result["metadata"], name, f"metadata.{name}", objects=True)
        ):
            _scores(
                rule,
                ("reward", "max_value", "min_base", "min_quality"),
                f"metadata.{name}[{index}].",
            )
    case = result["case"]
    for name in ("history", "events", "artifacts"):
        _list_field(case, name, f"case.{name}", objects=True)
    for index, subject in enumerate(
        _list_field(case, "subjects", "case.subjects", objects=True, nullable=True)
    ):
        _subject(subject, f"case.subjects[{index}]")
    _harness(_object_field(case, "harness", "case.harness"), "case.harness")
    for index, event in enumerate(case.get("events", [])):
        _object_field(event, "arguments", f"case.events[{index}].arguments")
        _harness(
            _object_field(event, "harness", f"case.events[{index}].harness", nullable=True),
            f"case.events[{index}].harness",
        )
    context = _object_field(case, "context", "case.context", nullable=True)
    for name in ("success_criteria", "constraints", "references"):
        _list_field(context, name, f"case.context.{name}")
    _list_field(context, "examples", "case.context.examples", objects=True)
    _object_field(context, "harness", "case.context.harness")
    _object_field(context, "output_schema", "case.context.output_schema", nullable=True)
    return result


def _safe_json(value: Any) -> str:
    # A JSON script element is still parsed by the HTML parser. Escaping every
    # '<' protects against closing script tags regardless of letter case.
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _render(
    record: dict[str, Any] | list[dict[str, Any]], output: Path, locale: str, alternate: str | None
) -> Path:
    if locale not in {"en", "zh"}:
        raise ValueError("Inspector locale must be 'en' or 'zh'")
    if isinstance(record, list):
        if not record:
            raise ValueError("Inspector requires at least one evaluation record")
        data = _safe_json([_validated_record(item) for item in record])
    else:
        data = _safe_json(_validated_record(record))
    web = Path(__file__).with_name("web")
    strings = json.loads((web / f"inspector.{locale}.json").read_text(encoding="utf-8"))
    locale_data = {
        "locale": locale,
        "strings": strings,
        "current_href": quote(output.name, safe=""),
    }
    if alternate:
        other_language = "zh" if locale == "en" else "en"
        locale_data["other"] = {
            "locale": other_language,
            "strings": json.loads(
                (web / f"inspector.{other_language}.json").read_text(encoding="utf-8")
            ),
        }
        locale_data["alternate"] = {
            "label": "中文" if locale == "en" else "English",
            "href": quote(alternate, safe=""),
        }
    template = (web / "inspector.html").read_text(encoding="utf-8")
    css = (web / "inspector.css").read_text(encoding="utf-8")
    script = "\n".join(
        (web / name).read_text(encoding="utf-8") for name in ("inspector.js", "inspector-data.js")
    )
    html = template.replace("__DOCUMENT_LANG__", "en" if locale == "en" else "zh-CN")
    html = html.replace("__REPORT_CSS__", css).replace("__REPORT_JS__", script)
    # Data is inserted last: template-like strings inside the record stay inert.
    replacements = {"__LOCALE_JSON__": _safe_json(locale_data), "__RECORD_JSON__": data}
    html = re.sub(r"__LOCALE_JSON__|__RECORD_JSON__", lambda match: replacements[match[0]], html)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output


def render_report(
    record: dict[str, Any] | list[dict[str, Any]],
    output: Path,
    *,
    locale: Literal["en", "zh"] = "en",
) -> Path:
    """Write one complete-language, offline workspace for one or more records.

    Invalid evaluations remain inspectable without inventing intermediate scores.
    Original requests, judge reasons, and evidence are never translated. A single
    report does not link to an alternate file that has not been generated.
    """
    return _render(record, Path(output), locale, None)


def render_reports(record: dict[str, Any] | list[dict[str, Any]], output: Path) -> dict[str, Path]:
    """Write English and Chinese workspaces with local sibling links.

    ``result.html`` yields ``result.en.html`` and ``result.zh.html``. A directory
    path yields ``report.en.html`` and ``report.zh.html`` inside that directory.
    """
    output = Path(output)
    if output.suffix.lower() == ".html":
        directory, stem = output.parent, output.stem
    else:
        directory, stem = output, "report"
    paths = {language: directory / f"{stem}.{language}.html" for language in ("en", "zh")}
    for language, path in paths.items():
        other = paths["zh" if language == "en" else "en"]
        _render(record, path, language, other.name)
    return paths
