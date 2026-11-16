"""Resolve an immutable plan, judge observed facts, then compute reward in code."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from uuid import uuid4

from .aggregation import (
    aggregate_bonus,
    aggregate_quality,
    aggregate_subjects,
    bonus_eligibility,
    compose_reward,
    derive_graph,
    lookup,
)
from .checkers import check_json, check_tools
from .evidence import validate_evidence
from .judge import JudgeClient
from .models import Analysis, Case, Judgement
from .pack import LoadedPack, load_pack


def _validate_refs(j: Judgement, case: Case) -> Judgement:
    j = Judgement.model_validate(j.model_dump())
    return validate_evidence(j, case)


def evaluate(
    case: Case,
    client: JudgeClient,
    pack: LoadedPack | None = None,
    *,
    workers: int = 4,
    plugins: dict[str, Callable[[Case], Judgement]] | None = None,
) -> dict:
    if not 1 <= workers <= 32:
        raise ValueError("workers must be between 1 and 32")
    pack = pack or load_pack()
    plugins = plugins or {}
    unknown = set(plugins) - {
        c.name for c in [*pack.manifest.criteria, *pack.manifest.gates, *pack.manifest.bonuses]
    }
    if unknown:
        raise ValueError(f"Unregistered verifier names: {sorted(unknown)}")
    case = case.model_copy(deep=True)
    if case.context is None:
        case.context = pack.context()
    start = time.monotonic()
    scope = uuid4().hex
    usage_start = len(getattr(client, "usage", []))

    def judge(*args):
        manager = client.usage_scope(scope) if hasattr(client, "usage_scope") else nullcontext()
        with manager:
            return client.judge(*args)

    def usage():
        if hasattr(client, "usage_snapshot"):
            return client.usage_snapshot(scope)
        return deepcopy(getattr(client, "usage", [])[usage_start:])

    record = {
        "schema_version": 1,
        "status": "invalid",
        "reward": None,
        "base": None,
        "quality": None,
        "deduction": None,
        "bonus": None,
        "bonus_details": {
            "status": "not_run",
            "cap": pack.manifest.profile.bonus_max,
            "checks": [],
        },
        "gate": {
            "status": "admitted",
            "enabled": any(rule.enabled for rule in pack.manifest.gates),
            "matched_rule": None,
            "reward": None,
            "checks": [],
        },
        "composition": compose_reward(None, None, None, pack.manifest.profile)["composition"],
        "subjects": [],
        "tasks": [],
        "criteria": [],
        "groups": [],
        "errors": [],
        "case": case.model_dump(mode="json"),
        "metadata": {
            "pack_name": pack.manifest.name,
            "pack_version": pack.manifest.version,
            "pack_fingerprint": pack.fingerprint,
            "model": client.model,
            "profile": pack.manifest.profile.model_dump(),
            "gates": [rule.model_dump(mode="json") for rule in pack.manifest.gates],
            "bonuses": [rule.model_dump(mode="json") for rule in pack.manifest.bonuses],
            "replay": client.model == "offline-replay",
        },
    }
    payload = case.model_dump(mode="json")
    payload["available_evidence_refs"] = [
        "request",
        "response",
        "history",
        *(event.ref for event in case.events),
        *(artifact.ref for artifact in case.artifacts),
    ]
    gates = sorted(pack.manifest.gates, key=lambda rule: rule.priority)
    record["gate"]["checks"] = [
        {
            "name": rule.name,
            "description": rule.description,
            "priority": rule.priority,
            "enabled": rule.enabled,
            "configured_reward": rule.reward,
            "status": "not_run" if rule.enabled else "disabled",
            "value": None,
            "reason": "Awaiting Gate routing" if rule.enabled else "Rule is disabled",
            "evidence_refs": [],
        }
        for rule in gates
    ]

    def deferred(reason):
        record["criteria"] = [
            {
                "name": spec.name,
                "group": spec.group,
                "description": spec.description,
                "weight": spec.weight,
                "status": "not_run",
                "value": None,
                "reason": reason,
                "evidence_refs": [],
            }
            for spec in pack.manifest.criteria
        ]
        record["groups"] = [
            {"name": name, "weight": weight, "status": "not_run", "score": None, "cost": None}
            for name, weight in pack.manifest.profile.group_weights.items()
        ]
        record["bonus_details"] = {
            "status": "not_run",
            "cap": pack.manifest.profile.bonus_max,
            "checks": [
                {
                    "name": rule.name,
                    "description": rule.description,
                    "enabled": rule.enabled,
                    "status": "not_run" if rule.enabled else "disabled",
                    "value": None,
                    "granted": None,
                    "reason": reason if rule.enabled else "Rule is disabled",
                    "evidence_refs": [],
                }
                for rule in pack.manifest.bonuses
            ],
        }

    deferred("Awaiting Gate admission and required Base evidence")
    for index, rule in enumerate(gates):
        if not rule.enabled:
            continue
        try:
            j = _validate_refs(
                plugins[rule.name](case)
                if rule.name in plugins
                else judge(
                    rule.name,
                    pack.prompt(rule.prompt),
                    {**payload, "gate_rule": rule.model_dump()},
                    Judgement,
                ),
                case,
            )
            if j.status == "ok" and type(j.value) is not bool:
                raise ValueError("Gate judges must return booleans")
            record["gate"]["checks"][index].update(j.model_dump(mode="json"))
            if j.status in {"missing_evidence", "error"}:
                raise ValueError(f"Enabled Gate returned {j.status}: {j.reason}")
            if j.status == "ok" and j.value:
                record["gate"].update(status="routed", matched_rule=rule.name, reward=rule.reward)
                for later in record["gate"]["checks"][index + 1 :]:
                    if later["enabled"]:
                        later["reason"] = "A prior Gate routed directly; this rule was not executed"
                deferred("Gate routed directly; downstream scoring was not executed")
                record.update(
                    compose_reward(None, None, None, pack.manifest.profile, gate_reward=rule.reward)
                )
                record["status"] = "ok"
                record["metadata"].update(seconds=time.monotonic() - start, usage=usage())
                return record
        except Exception as exc:
            if record["gate"]["checks"][index]["status"] != "missing_evidence":
                record["gate"]["checks"][index].update(
                    status="error", value=None, reason=str(exc), evidence_refs=[]
                )
            record["gate"]["status"] = "invalid"
            for later in record["gate"]["checks"][index + 1 :]:
                if later["enabled"]:
                    later["reason"] = "A prior Gate failed; this rule was not executed"
            record["errors"].append(f"{rule.name}: {exc}")
            deferred("Gate evaluation failed; downstream scoring was not executed")
            record["metadata"].update(seconds=time.monotonic() - start, usage=usage())
            return record
    try:
        subjects = (
            case.subjects
            if case.subjects is not None
            else judge(
                "labeler",
                pack.prompt(pack.manifest.labeler_prompt),
                {k: payload[k] for k in ["request", "history", "context", "harness"]},
                Analysis,
            ).subjects
        )
        graph = derive_graph(subjects)  # Never replace invalid dependencies with independence.
    except Exception as exc:
        record["errors"].append(f"labeler: {exc}")
        deferred(
            "Labeler did not resolve a valid subject graph; downstream scoring was not executed"
        )
        record["metadata"].update(seconds=time.monotonic() - start, usage=usage())
        return record

    # Every judge sees the same request-side decomposition, including dependency targets.
    payload["subjects"] = [s.model_dump(mode="json") for s in subjects]
    payload["available_evidence_refs"] = [
        "request",
        "response",
        "history",
        *(e.ref for e in case.events),
        *(a.ref for a in case.artifacts),
    ]

    def base_subject(item):
        index, subject = item
        results, errors = {}, []
        names = (
            ["clarification"]
            if subject.clarity == "unclear_blocking"
            else ["understanding", "process", "result", "explanation"]
        )
        for name in names:
            try:
                j = _validate_refs(
                    judge(
                        f"base/{index}/{name}",
                        pack.prompt(pack.manifest.base_prompts[name]),
                        {**payload, "subject": subject.model_dump(mode="json")},
                        Judgement,
                    ),
                    case,
                )
                results[name] = j.model_dump(mode="json")
                if j.status != "ok":
                    errors.append(f"base/{index}/{name}: required judgement returned {j.status}")
                    continue
                if name != "clarification" and not isinstance(j.value, bool):
                    raise ValueError("Main Base judgements must be booleans")
            except Exception as exc:
                results[name] = {
                    "status": "error",
                    "value": None,
                    "reason": str(exc),
                    "evidence_refs": [],
                }
                errors.append(f"base/{index}/{name}: {exc}")
        if errors:
            return {"raw_score": None, "row": None, "row1": False, "judgements": results}, errors
        if names == ["clarification"]:
            # Clarifying is appropriate but yields no usable output for downstream execution.
            score, row, row1 = float(results["clarification"]["value"]), None, False
        else:
            score, row, row1 = lookup(*(results[n]["value"] for n in names), pack.manifest.profile)
        return {"raw_score": score, "row": row, "row1": row1, "judgements": results}, []

    if subjects:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            scored = list(pool.map(base_subject, enumerate(subjects)))
        for _, errors in scored:
            record["errors"].extend(errors)
        if not record["errors"]:
            record.update(aggregate_subjects(subjects, [s for s, _ in scored]))
        else:
            record["subjects"] = [
                {"index": i, **s.model_dump(mode="json"), **scored[i][0]}
                for i, s in enumerate(subjects)
            ]
            for t, hops in enumerate(graph):
                record["tasks"].append(
                    {
                        "index": t,
                        "score": None,
                        "hops": [
                            {
                                "index": h,
                                "subjects": members,
                                "denominator": len(members),
                                "score": None,
                            }
                            for h, members in enumerate(hops)
                        ],
                    }
                )
                for h, members in enumerate(hops):
                    for i in members:
                        record["subjects"][i].update(
                            task=t, hop=h, final_score=None, final_row1=None, blocked_by=[]
                        )
    else:
        try:
            j = _validate_refs(
                judge(
                    "base/conversation",
                    pack.prompt(pack.manifest.base_prompts["conversation"]),
                    payload,
                    Judgement,
                ),
                case,
            )
            record["conversation"] = j.model_dump(mode="json")
            if j.status != "ok":
                raise ValueError("Conversation Base requires an observed score")
            record["base"] = float(j.value)
        except Exception as exc:
            record["errors"].append(f"base/conversation: {exc}")

    def criterion(spec):
        result = {
            "name": spec.name,
            "group": spec.group,
            "weight": spec.weight,
            "description": spec.description,
        }
        try:
            has_tools = any(e.kind == "tool_call" for e in case.events)
            format_declared = bool(
                case.context and (case.context.output_format or case.context.output_schema)
            )
            if spec.name in plugins:
                j = Judgement.model_validate(plugins[spec.name](case))
            elif spec.applicability == "tools" and not has_tools and case.evidence_complete:
                j = Judgement(status="not_applicable", reason="No tool calls to evaluate")
            elif spec.applicability == "format" and spec.checker == "json_format":
                j = check_json(case)
            elif spec.checker == "tool_schema":
                j = check_tools(case)
            else:
                j = judge(
                    f"criterion/{spec.name}",
                    pack.prompt(spec.prompt),
                    {**payload, "criterion": spec.model_dump(), "format_declared": format_declared},
                    Judgement,
                )
            result.update(_validate_refs(j, case).model_dump(mode="json"))
        except Exception as exc:
            result.update(status="error", value=None, reason=str(exc), evidence_refs=[])
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        record["criteria"] = list(pool.map(criterion, pack.manifest.criteria))
    for c in record["criteria"]:
        if c["status"] in {"error", "missing_evidence"}:
            record["errors"].append(f"{c['name']}: {c['status']}: {c['reason']}")
    record.update(aggregate_quality(record["criteria"], pack.manifest.profile))
    if not record["errors"]:
        checks = []
        for rule in pack.manifest.bonuses:
            eligible, reason = bonus_eligibility(
                rule, record["base"], record["quality"], record["subjects"], pack.manifest.profile
            )
            fact = {
                "name": rule.name,
                "status": "ineligible" if rule.enabled else "disabled",
                "value": None,
                "reason": reason,
                "evidence_refs": [],
            }
            if eligible:
                try:
                    j = _validate_refs(
                        plugins[rule.name](case)
                        if rule.name in plugins
                        else judge(
                            rule.name,
                            pack.prompt(rule.prompt),
                            {
                                **payload,
                                "bonus_rule": rule.model_dump(),
                                "evaluation_summary": {
                                    "base": record["base"],
                                    "quality": record["quality"],
                                    "subjects": record["subjects"],
                                },
                            },
                            Judgement,
                        ),
                        case,
                    )
                    fact.update(j.model_dump(mode="json"))
                except Exception as exc:
                    fact.update(status="error", value=None, reason=str(exc))
            checks.append(fact)
        record.update(
            aggregate_bonus(
                checks,
                pack.manifest.bonuses,
                pack.manifest.profile,
                base=record["base"],
                quality=record["quality"],
                subjects=record["subjects"],
            )
        )
        for check in record["bonus_details"]["checks"]:
            if check["status"] in {"error", "missing_evidence"}:
                record["errors"].append(f"{check['name']}: {check['status']}: {check['reason']}")
        record.update(
            compose_reward(
                record["base"], record["deduction"], record["bonus"], pack.manifest.profile
            )
        )
        if not record["errors"]:
            record["status"] = "ok"
    else:
        record["bonus_details"] = {
            "status": "not_run",
            "cap": pack.manifest.profile.bonus_max,
            "checks": [
                {
                    "name": rule.name,
                    "description": rule.description,
                    "enabled": rule.enabled,
                    "status": "not_run" if rule.enabled else "disabled",
                    "value": None,
                    "granted": None,
                    "reason": "Required Base or quality evidence is unresolved"
                    if rule.enabled
                    else "Rule is disabled",
                    "evidence_refs": [],
                }
                for rule in pack.manifest.bonuses
            ],
        }
        record.update(
            compose_reward(
                record["base"], record["deduction"], record["bonus"], pack.manifest.profile
            )
        )
    record["metadata"].update(seconds=time.monotonic() - start, usage=usage())
    return record


def training_reward(record: dict) -> float:
    """Invalid judgements never become zero-valued training rewards."""
    if record.get("status") != "ok" or record.get("reward") is None:
        raise ValueError("Cannot train on an invalid evaluation")
    if type(record["reward"]) not in {int, float}:
        raise ValueError("Training reward must be a numeric scalar")
    value = float(record["reward"])
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Training reward must be finite and in [0,1]")
    return value
