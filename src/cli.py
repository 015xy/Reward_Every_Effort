"""CLI for scoring, frozen adaptation and offline reward diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean, pvariance

import yaml

from .adaptation import adapt
from .aggregation import (
    aggregate_bonus,
    aggregate_quality,
    aggregate_subjects,
    compose_reward,
    lookup,
)
from .evaluator import evaluate, training_reward
from .judge import OpenAIJudge, ReplayJudge
from .models import BonusRule, Case, RewardProfile, Subject, TaskContext
from .pack import load_pack


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _invalid_json_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def _read_json(text):
    return json.loads(text, object_pairs_hook=_json_object, parse_constant=_invalid_json_constant)


def read_data(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    # Large conversation exports should use the JSON parser, not YAML's loader.
    return _read_json(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)


def read_records(path):
    path = Path(path)
    if path.suffix.lower() != ".jsonl":
        return read_data(path)
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                records.append(_read_json(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {number}: {exc.msg}") from exc
    return records


def write_data(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_reports(record: dict | list[dict], output: str, locale: str) -> dict[str, str]:
    from .inspector import render_report, render_reports

    if locale == "both":
        paths = render_reports(record, Path(output))
    else:
        paths = {locale: render_report(record, Path(output), locale=locale)}
    return {language: str(path) for language, path in paths.items()}


def recompute(record: dict, profile: RewardProfile) -> dict:
    if record.get("status") != "ok":
        raise ValueError("Cannot recompute an invalid evaluation")
    training_reward(record)
    result = json.loads(json.dumps(record))
    result["metadata"]["recomputed_from"] = record["metadata"]["pack_fingerprint"]
    result["metadata"]["profile"] = profile.model_dump()
    if record.get("gate", {}).get("status") == "routed":
        result.update(
            compose_reward(None, None, None, profile, gate_reward=record["gate"]["reward"])
        )
        return result
    if record["subjects"]:
        subjects, scored = [], []
        for s in record["subjects"]:
            subjects.append(
                Subject(
                    subject=s["subject"], constraints=s["constraints"], depends_on=s["depends_on"]
                )
            )
            copy = dict(s)
            js = s["judgements"]
            if "clarification" not in js:
                copy["raw_score"], copy["row"], copy["row1"] = lookup(
                    *(
                        js[n]["value"]
                        for n in ["understanding", "process", "result", "explanation"]
                    ),
                    profile,
                )
            scored.append(copy)
        result.update(aggregate_subjects(subjects, scored))
    result.update(aggregate_quality(record["criteria"], profile))
    rules = [BonusRule.model_validate(rule) for rule in record["metadata"].get("bonuses", [])]
    if record.get("bonus") and not rules:
        raise ValueError("Cannot recompute a bonus without its recorded rules")
    result.update(
        aggregate_bonus(
            record.get("bonus_details", {}).get("checks", []),
            rules,
            profile,
            base=result["base"],
            quality=result["quality"],
            subjects=result["subjects"],
        )
    )
    result.update(compose_reward(result["base"], result["deduction"], result["bonus"], profile))
    return result


def diagnostics(records: list[dict]) -> dict:
    groups = {
        json.dumps(
            {k: r.get("case", {}).get(k) for k in ["request", "history", "context", "harness"]},
            sort_keys=True,
        )
        for r in records
    }
    if len(groups) > 1:
        raise ValueError("Rollout diagnostics require identical request and task context")
    policies = {
        json.dumps(
            {
                "pack": r.get("metadata", {}).get("pack_fingerprint"),
                "profile": r.get("metadata", {}).get("profile"),
            },
            sort_keys=True,
        )
        for r in records
        if r.get("status") == "ok"
    }
    if len(policies) > 1:
        raise ValueError("Rollout diagnostics require the same scoring policy")
    values = sorted(training_reward(r) for r in records if r.get("status") == "ok")
    if not values:
        return {"valid": 0, "invalid": len(records), "mean": None, "variance": None}
    tail = values[int(0.8 * len(values)) :]
    return {
        "valid": len(values),
        "invalid": len(records) - len(values),
        "mean": mean(values),
        "variance": pvariance(values),
        "distinct_rewards": len(set(values)),
        "tail_count": len(tail),
        "tail_distinct": len(set(tail)),
        "tail_variance": pvariance(tail),
        "note": "Within-prompt rollout diagnostics; ties/variance do not prove learning benefit.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ree", description="Reward Every Effort")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Validate bundled plan without calling a model")
    p = sub.add_parser("normalize", help="Convert one messages record to an observable Case")
    p.add_argument("input")
    p.add_argument("--index", type=int, default=0, help="Zero-based record position in a dataset")
    p.add_argument("--output", required=True)
    for name in ["score", "batch", "regrade"]:
        p = sub.add_parser(name)
        p.add_argument("input")
        p.add_argument("--pack")
        p.add_argument("--replay", help="Explicit fixture responses; no novel model judgement")
        p.add_argument("--output", required=True)
        p.add_argument("--html")
        p.add_argument("--locale", choices=["en", "zh", "both"], default="both")
        p.add_argument("--workers", type=int, default=4)
        p.add_argument("--max-calls", type=int, default=200)
        p.add_argument("--timeout", type=float, default=45)
        p.add_argument("--deadline", type=float, default=600)
        p.add_argument("--retries", type=int, default=1)
        p.add_argument(
            "--min-interval", type=float, default=0, help="Seconds between judge requests"
        )
        p.add_argument("--cache", type=Path)
    p = sub.add_parser("inspect")
    p.add_argument("input")
    p.add_argument("--output", required=True)
    p.add_argument("--locale", choices=["en", "zh", "both"], default="both")
    p = sub.add_parser("adapt")
    p.add_argument("input")
    p.add_argument("--output", required=True)
    p.add_argument("--pack")
    p.add_argument("--designer", action="store_true")
    p = sub.add_parser("recompute")
    p.add_argument("input")
    p.add_argument("--profile", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("diagnostics")
    p.add_argument("input", nargs="+")
    args = parser.parse_args(argv)
    try:
        if args.command == "normalize":
            from .conversation import case_from_messages

            data = read_records(args.input)
            rows = data if isinstance(data, list) else [data]
            if not 0 <= args.index < len(rows):
                raise ValueError(f"Record index must be between 0 and {len(rows) - 1}")
            case = case_from_messages(rows[args.index])
            write_data(args.output, case.model_dump(mode="json"))
            print(
                json.dumps(
                    {
                        "record_index": args.index,
                        "events": len(case.events),
                        "evidence_complete": case.evidence_complete,
                        "output": args.output,
                    }
                )
            )
            return 0
        if args.command == "doctor":
            pack = load_pack()
            print(
                json.dumps(
                    {
                        "pack": pack.manifest.name,
                        "version": pack.manifest.version,
                        "criteria": len(pack.manifest.criteria),
                        "fingerprint": pack.fingerprint,
                    },
                    indent=2,
                )
            )
            return 0
        if args.command == "diagnostics":
            print(json.dumps(diagnostics([read_data(p) for p in args.input]), indent=2))
            return 0
        if args.command == "inspect":
            print(
                json.dumps(
                    write_reports(read_records(args.input), args.output, args.locale),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "recompute":
            write_data(
                args.output,
                recompute(
                    read_data(args.input), RewardProfile.model_validate(read_data(args.profile))
                ),
            )
            return 0
        if args.command == "adapt":
            client = OpenAIJudge.from_env(max_calls=4) if args.designer else None
            try:
                report = adapt(
                    TaskContext.model_validate(read_data(args.input)),
                    Path(args.output),
                    load_pack(args.pack),
                    client,
                )
                print(json.dumps(report, ensure_ascii=False, indent=2))
            finally:
                if client:
                    client.close()
            return 0
        client = (
            ReplayJudge.from_file(args.replay)
            if args.replay
            else OpenAIJudge.from_env(
                timeout=args.timeout,
                max_calls=args.max_calls,
                deadline=args.deadline,
                retries=args.retries,
                min_interval=args.min_interval,
                cache=args.cache,
            )
        )
        try:
            pack = load_pack(args.pack)
            if args.command == "batch":
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                failed = False
                html_records = []
                with output.open("w", encoding="utf-8") as f:
                    for line_number, line in enumerate(
                        Path(args.input).read_text().splitlines(), 1
                    ):
                        if not line.strip():
                            continue
                        try:
                            record = evaluate(
                                Case.model_validate_json(line), client, pack, workers=args.workers
                            )
                        except Exception as exc:
                            record = {"status": "invalid", "reward": None, "errors": [str(exc)]}
                        record["line"] = line_number
                        failed |= record["status"] != "ok"
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                        if args.html:
                            html_records.append(record)
                if args.html:
                    print(json.dumps(write_reports(html_records, args.html, args.locale), indent=2))
                return 2 if failed else 0
            data = read_data(args.input)
            case = Case.model_validate(data["case"] if args.command == "regrade" else data)
            record = evaluate(case, client, pack, workers=args.workers)
            write_data(args.output, record)
            reports = {}
            if args.html:
                reports = write_reports(record, args.html, args.locale)
            print(
                json.dumps(
                    {
                        "status": record["status"],
                        "reward": record["reward"],
                        "base": record["base"],
                        "output": args.output,
                        "reports": reports,
                    },
                    indent=2,
                )
            )
            return 0 if record["status"] == "ok" else 2
        finally:
            if hasattr(client, "close"):
                client.close()
    except Exception as exc:
        parser.exit(2, f"ree: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
