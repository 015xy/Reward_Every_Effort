"""Deterministic Base lookup, per-edge propagation and exponential quality costs."""

from __future__ import annotations

import math
from statistics import mean

from .models import BonusRule, RewardProfile, Subject


def lookup(
    understood: bool, process: bool, observed: bool, explained: bool, profile: RewardProfile
) -> tuple[float, int | None, bool]:
    if any(type(value) is not bool for value in [understood, process, observed, explained]):
        raise ValueError("Base lookup requires boolean judgements")
    if not understood:
        return 0.0, None, False
    effective = process and observed
    row = (
        (1 if explained else 2)
        if effective
        else ((3 if explained else 4) if process else (5 if explained else 6))
    )
    return profile.table[row - 1], row, row == 1


def derive_graph(subjects: list[Subject]) -> list[list[list[int]]]:
    """task=weak component, hop=topological layer; reject damaged graphs."""
    n = len(subjects)
    adjacency = [set() for _ in subjects]
    for i, s in enumerate(subjects):
        if len(s.depends_on) != len(set(s.depends_on)):
            raise ValueError(f"Subject {i}: duplicate dependency")
        for d in s.depends_on:
            if isinstance(d, bool) or not 0 <= d < n or d == i:
                raise ValueError(f"Subject {i}: invalid dependency {d}")
            adjacency[i].add(d)
            adjacency[d].add(i)
    visited: set[int] = set()
    tasks = []
    for i in range(n):
        if i in visited:
            continue
        stack, component = [i], set()
        while stack:
            u = stack.pop()
            if u in component:
                continue
            component.add(u)
            stack.extend(adjacency[u] - component)
        visited |= component
        remaining, placed, hops = set(component), set(), []
        while remaining:
            current = sorted(u for u in remaining if set(subjects[u].depends_on) <= placed)
            if not current:
                raise ValueError("Task dependency graph contains a cycle")
            hops.append(current)
            placed.update(current)
            remaining.difference_update(current)
        tasks.append(hops)
    return tasks


def aggregate_subjects(subjects: list[Subject], scores: list[dict]) -> dict:
    if len(subjects) != len(scores) or not subjects:
        raise ValueError("Each nonempty subject must have exactly one score")
    graph = derive_graph(subjects)
    final, qualified, details = {}, {}, [dict(s) for s in scores]
    tasks = []
    for t, hops in enumerate(graph):
        hd = []
        for h, members in enumerate(hops):
            for i in members:
                blocked = [d for d in subjects[i].depends_on if not qualified[d]]
                if (
                    type(scores[i]["raw_score"]) not in {int, float}
                    or type(scores[i]["row1"]) is not bool
                ):
                    raise ValueError("Subject scores require a numeric raw_score and boolean row1")
                raw = float(scores[i]["raw_score"])
                if not math.isfinite(raw) or not 0 <= raw <= 1:
                    raise ValueError("Subject score outside [0,1]")
                final[i] = 0.0 if blocked else raw
                qualified[i] = bool(scores[i]["row1"]) and not blocked
                details[i].update(
                    **subjects[i].model_dump(mode="json"),
                    index=i,
                    task=t,
                    hop=h,
                    final_score=final[i],
                    final_row1=qualified[i],
                    blocked_by=blocked,
                )
            hd.append(
                {
                    "index": h,
                    "subjects": members,
                    "score": mean(final[i] for i in members),
                    "denominator": len(members),
                }
            )
        tasks.append({"index": t, "score": mean(x["score"] for x in hd), "hops": hd})
    return {"base": mean(x["score"] for x in tasks), "subjects": details, "tasks": tasks}


def aggregate_quality(criteria: list[dict], profile: RewardProfile) -> dict:
    for criterion in criteria:
        if criterion["group"] not in profile.group_weights or criterion["status"] not in {
            "ok",
            "not_applicable",
            "missing_evidence",
            "error",
        }:
            raise ValueError("Unknown quality group or status")
        weight = criterion["weight"]
        if type(weight) not in {int, float} or not math.isfinite(weight) or not 0 < weight <= 32:
            raise ValueError("Criterion weight must be finite and in (0,32]")
        if criterion["status"] == "ok":
            value = criterion["value"]
            if (
                type(value) not in {bool, int, float}
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError("Quality value must be in [0,1]")
        elif criterion.get("value") is not None:
            raise ValueError("Unresolved or skipped criteria cannot carry a score")
    groups = []
    denominator = -math.expm1(-math.log(2) * (profile.max_cost / profile.half_life))
    for name, weight in profile.group_weights.items():
        unresolved = [
            c
            for c in criteria
            if c["group"] == name and c["status"] in {"missing_evidence", "error"}
        ]
        if unresolved:
            groups.append(
                {"name": name, "cost": None, "score": None, "weight": weight, "status": "invalid"}
            )
            continue
        active = [c for c in criteria if c["group"] == name and c["status"] == "ok"]
        if not active:
            continue
        cost = sum(c["weight"] * (1 - float(c["value"])) for c in active)
        score = (
            max(0, 1 + math.expm1(-math.log(2) * (cost / profile.half_life)) / denominator)
            if denominator
            else max(0, 1 - cost / profile.max_cost)
        )
        groups.append(
            {"name": name, "cost": cost, "score": score, "weight": weight, "status": "ok"}
        )
    if any(g["status"] == "invalid" for g in groups):
        return {"quality": None, "groups": groups, "deduction": None}
    # Normalize weights before summation to avoid overflow with finite large weights.
    scale = max((g["weight"] for g in groups), default=1.0)
    q = (
        sum((g["weight"] / scale) * g["score"] for g in groups)
        / sum(g["weight"] / scale for g in groups)
        if groups
        else 1.0
    )
    return {"quality": q, "groups": groups, "deduction": profile.deduction_max * (1 - q)}


def bonus_eligibility(
    rule: BonusRule, base: float, quality: float, subjects: list[dict], profile: RewardProfile
) -> tuple[bool, str]:
    """Eligibility is deterministic and distinct from the bonus judge's facts."""
    if any(
        type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1
        for value in [base, quality]
    ):
        raise ValueError("Bonus eligibility requires known base and quality in [0,1]")
    if not rule.enabled:
        return False, "Rule is disabled"
    if profile.bonus_max == 0 or rule.max_value == 0:
        return False, "Configured bonus amount is zero"
    if base < rule.min_base:
        return False, "Base is below the declared bonus threshold"
    if quality < rule.min_quality:
        return False, "Quality is below the declared bonus threshold"
    if rule.require_all_subjects and (
        not subjects or any(subject.get("final_row1") is not True for subject in subjects)
    ):
        return False, "Every task subject must have final first-row eligibility"
    return True, "Declared bonus eligibility is satisfied"


def aggregate_bonus(
    checks: list[dict],
    rules: list[BonusRule],
    profile: RewardProfile,
    *,
    base: float,
    quality: float,
    subjects: list[dict],
) -> dict:
    """Reapply eligibility/cap to recorded bonus facts; never invent missing facts.

    A formerly skipped rule that becomes eligible requires a new judge execution.
    This lets recompute change profiles without creating unobserved bonus credit.
    """
    by_name = {check["name"]: check for check in checks}
    if len(by_name) != len(checks) or set(by_name) - {rule.name for rule in rules}:
        raise ValueError("Bonus facts must uniquely reference declared rules")
    details, total, invalid = [], 0.0, False
    for rule in rules:
        detail = {
            "name": rule.name,
            "description": rule.description,
            "enabled": rule.enabled,
            "max_value": rule.max_value,
            "granted": 0.0,
        }
        original = by_name.get(rule.name)
        raw_fact = original.get("judge_fact", original) if original else None
        if raw_fact and raw_fact.get("status") in {
            "ok",
            "not_applicable",
            "error",
            "missing_evidence",
        }:
            raw_fact = {
                key: value
                for key, value in raw_fact.items()
                if key in {"status", "value", "reason", "evidence_refs", "annotations"}
            }
            detail["judge_fact"] = raw_fact
        else:
            raw_fact = None
        eligible, reason = bonus_eligibility(rule, base, quality, subjects, profile)
        if not eligible:
            detail.update(
                status="disabled" if not rule.enabled else "ineligible",
                value=None,
                reason=reason,
                evidence_refs=[],
                eligible=False,
            )
        else:
            fact = raw_fact
            if not fact:
                raise ValueError(f"Bonus {rule.name} requires regrade: eligible but no judge facts")
            detail.update(fact, eligible=True)
            if fact.get("status") in {"error", "missing_evidence"}:
                invalid = True
                detail["granted"] = None
            elif fact.get("status") == "not_applicable":
                detail["granted"] = 0.0
            elif fact.get("status") == "ok":
                value = fact.get("value")
                if (
                    type(value) not in {bool, int, float}
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                ):
                    raise ValueError("Bonus judge facts require a value in [0,1]")
                detail["granted"] = rule.max_value * float(value)
                total += detail["granted"]
            else:
                raise ValueError("Unknown bonus judge status")
        details.append(detail)
    status = (
        "invalid"
        if invalid
        else "disabled"
        if not any(rule.enabled for rule in rules)
        else "ineligible"
        if not any(detail.get("eligible") for detail in details)
        else "ok"
    )
    return {
        "bonus": None if invalid else min(profile.bonus_max, total),
        "bonus_details": {
            "status": status,
            "cap": profile.bonus_max,
            "uncapped": None if invalid else total,
            "capped": None if invalid else total > profile.bonus_max,
            "checks": details,
        },
    }


def compose_reward(
    base: float | None,
    deduction: float | None,
    bonus: float | None,
    profile: RewardProfile,
    *,
    gate_reward: float | None = None,
) -> dict:
    """Compose either a direct Gate route or the normal clipped reward."""
    bounds = {"min": profile.reward_min, "max": profile.reward_max}
    if gate_reward is not None:
        if (
            type(gate_reward) not in {int, float}
            or not math.isfinite(gate_reward)
            or not 0 <= gate_reward <= 1
        ):
            raise ValueError("Gate reward must be in [0,1]")
        return {
            "reward": float(gate_reward),
            "composition": {
                "path": "gate",
                "before_clip": float(gate_reward),
                "base": None,
                "deduction": None,
                "bonus": None,
                "bounds": bounds,
                "clipped": False,
                "gate_reward": float(gate_reward),
            },
        }
    if any(value is None for value in [base, deduction, bonus]):
        return {
            "reward": None,
            "composition": {
                "path": "invalid",
                "before_clip": None,
                "base": base,
                "deduction": deduction,
                "bonus": bonus,
                "bounds": bounds,
                "clipped": None,
                "gate_reward": None,
            },
        }
    if any(
        type(value) not in {int, float} or not math.isfinite(value)
        for value in [base, deduction, bonus]
    ):
        raise ValueError("Reward composition requires finite numeric values")
    before = base - deduction + bonus
    reward = min(profile.reward_max, max(profile.reward_min, before))
    return {
        "reward": reward,
        "composition": {
            "path": "normal",
            "before_clip": before,
            "base": base,
            "deduction": deduction,
            "bonus": bonus,
            "bounds": bounds,
            "clipped": reward != before,
            "gate_reward": None,
        },
    }
