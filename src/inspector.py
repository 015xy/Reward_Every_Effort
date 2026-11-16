"""Generate a portable, offline Inspector for saved evaluation records.

The trajectory is untrusted. It is transported as escaped JSON and rendered only
with textContent; neither prompts nor event payloads become executable markup.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

_REQUIRED_LISTS = ("subjects", "tasks", "criteria", "groups", "errors")


def _validated_record(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("Inspector expects a JSON object evaluation record")
    if record.get("schema_version", 1) != 1:
        raise ValueError("Unsupported evaluation record schema_version")
    if record.get("status") not in {"ok", "invalid"}:
        raise ValueError("Evaluation record status must be 'ok' or 'invalid'")
    # Sparse invalid records are useful when evaluation failed before scoring.
    result = dict(record)
    for name in _REQUIRED_LISTS:
        value = result.setdefault(name, [])
        if not isinstance(value, list):
            raise ValueError(f"Evaluation record {name} must be a list")
    for name in ("case", "metadata"):
        if not isinstance(result.setdefault(name, {}), dict):
            raise ValueError(f"Evaluation record {name} must be an object")
    for name in ("subjects", "tasks", "criteria", "groups"):
        if any(not isinstance(item, dict) for item in result[name]):
            raise ValueError(f"Evaluation record {name} entries must be objects")
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
    locale_data = {"locale": locale, "strings": strings}
    if alternate:
        locale_data["alternate"] = {
            "label": "中文" if locale == "en" else "English",
            "href": quote(alternate, safe=""),
        }
    template = (web / "inspector.html").read_text(encoding="utf-8")
    css = (web / "inspector.css").read_text(encoding="utf-8")
    script = (web / "inspector.js").read_text(encoding="utf-8")
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
