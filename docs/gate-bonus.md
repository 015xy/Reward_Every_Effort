# Gate and Bonus policies

The runtime records the complete `Gate → Base → Deduct / Bonus → reward`
computation. Gate and Bonus are executable policies with immutable prompts,
declared configuration and evidence-bearing judgements. General pack `0.2.0`
enables one hard-boundary Gate and three general Bonus policies by default.
The Gate judges an actual violation; an eligible Bonus judges actual additional
value. No rule infers success from a routing decision or rewards length.

Packs frozen under `0.1.0` retain their earlier disabled reference policies.
Regrade with a new pack to evaluate new policies; recompute cannot invent facts
for rules that were previously disabled or skipped.

## Gate: explicit routing before scoring

Gate rules run in increasing `priority`. Each priority and every component name
must be unique within the pack. A boolean `true` means the rule matched and its
declared `reward` becomes the final reward. `false` or `not_applicable` continues
to the next rule and then to normal evaluation. Missing evidence or a verifier
failure makes the record invalid; it does not authorize admission.

A match stops execution. Later enabled gates, Base, criteria, quality groups and
enabled Bonus policies are recorded as `not_run`, with a reason explaining the
short circuit. Base, deduction and Bonus remain `null`; the evaluator never
fabricates zero-valued downstream scores. Disabled policies retain `disabled`.

The default `gate.explicit_prohibition` checks actual violations of explicit
safety, authorization or confidentiality hard boundaries in trusted instructions
or context. Ordinary task failure, workflow requirements, formatting conventions,
long answers and hypothetical dangers are insufficient to trigger it. Necessary
clarification, authorized handoff and honest external failure remain on the normal
Base path. Missing evidence is invalid evaluation, not a successful route or an
agent reward of zero. Read the prompt when configuring a task's boundaries.

```json
{
  "name": "gate.explicit_prohibition",
  "prompt": "gate/explicit_prohibition.md",
  "description": "Route actual violations of explicit hard prohibitions",
  "priority": 0,
  "reward": 0.0,
  "enabled": true
}
```

Direct Gate rewards must be in `[0, 1]` and bypass normal reward clipping. Thus a
Gate reward of `0` remains `0` even when `profile.reward_min` is `0.1`. The record
exposes `gate.matched_rule`, per-rule checks and `composition.path = "gate"`.

## Bonus: eligibility before extra credit

Bonus policies execute after a valid Base and quality calculation. Eligibility
is deterministic and separate from judging the presence of extra value:

- The rule is enabled, and both its `max_value` and `profile.bonus_max` are positive.
- Base meets `min_base`; quality meets `min_quality`.
- When `require_all_subjects` is true, there is at least one subject and every
  subject has `final_row1 = true` after dependency propagation.

The last requirement matters because the six-row Base table can assign a high
score to an honest, well-handled external failure. That score does not establish
successful delivery, and it cannot by itself unlock extra credit. Empty-subject
conversations also do not qualify under this reference policy.

The default policies require three distinct observable benefits beyond required
delivery. Each prompt excludes the same benefit already credited by another policy:

| Policy | Maximum | Additional value |
|---|---:|---|
| `bonus.insightful_synthesis` | 0.04 | Grounded extra conclusions or tradeoffs that reduce decision work |
| `bonus.meaningful_organization` | 0.03 | Useful relationships or comparisons beyond the required structure |
| `bonus.reusable_artifact` | 0.03 | An actually supplied, correct reusable artifact beyond required outputs |

No fixed business format, entity count, group size or word threshold is required.
An analysis, grouping or artifact explicitly requested by the user belongs to
Base, rather than earning extra credit for the same work. With no relevant extra
value a rule grants zero or returns `not_applicable`; it never invents a need for
extra work. Hidden reasoning, length and unsupported promises earn no credit.

```json
{
  "name": "bonus.reusable_artifact",
  "prompt": "bonus/reusable_artifact.md",
  "description": "Credit useful reusable artifacts beyond required delivery",
  "max_value": 0.03,
  "min_base": 1.0,
  "min_quality": 0.9,
  "require_all_subjects": true,
  "enabled": true
}
```

An eligible judge returns `value` in `[0, 1]`. This is the fraction of the rule's
maximum, so `value = 0.5` with `max_value = 0.03` grants `0.015`. The runtime sums
grants and caps the sum at `profile.bonus_max`. `not_applicable` grants zero;
`missing_evidence` or `error` makes the enabled, eligible Bonus and reward
invalid. Ineligible policies are not judged.

Normal reward is `clip(base - deduction + bonus, reward_min, reward_max)`. For
example, Base `1`, deduction `0.08` and Bonus `0.05` produce `0.97`. A Bonus at an
already perfect reward remains visible in the record but clipping prevents the
training reward from exceeding `1`. Turning Bonus on therefore does not by itself
distinguish two perfect, deduction-free rewards. Extra-credit facts and clipping
remain inspectable; changing that ceiling or reserving reward headroom is a
separate numerical policy and experimental question.
With the default Base threshold `1`, quality threshold `0.9` and deduction maximum
`0.8`, an eligible record has deduction at most `0.08`. Bonus can recover that
deduction up to reward `1`; extra raw grants above the available headroom are
clipped. Three independent judges do not provide automatic cross-rule deduplication:
each prompt requires a distinct extra benefit and evidence, and excludes value
already credited under another rule.

`bonus_details.checks` retains eligibility, reasons, evidence, judge values and
each granted amount. `uncapped`, `cap` and `capped` distinguish the raw sum from
the effective Bonus. `composition` preserves all known inputs, the value before
clipping, bounds and clipping status even when a required downstream judgement
is unresolved.

## Extending and replaying policies

Add trusted rules and Markdown prompts to a TaskPack manifest, or explicitly
register a verifier with `evaluate(..., plugins={rule_name: callable})`. The
callable receives the normalized `Case` and returns a `Judgement`. Unregistered
names fail validation. Generated adaptation overlays cannot overwrite these
baseline policies or silently enable arbitrary code. Prompt content participates
in the frozen pack fingerprint; configuration and evidence belong in the record.

Recompute only changes numeric policy using saved facts. Bonus aggregation
rechecks eligibility against the recomputed Base, quality and final subject
qualification. Executed judgements are retained under `judge_fact` even if a new
profile makes their rule ineligible, so later recomputations can reuse them.

If a previously skipped rule becomes eligible and has no saved judgement,
recompute raises `requires regrade`. Changing a policy prompt or enabling a Gate
that was never evaluated also needs regrade. Regrade calls judges on saved
evidence; it cannot recover environment state that was never recorded.

Keep statuses distinct: `disabled` means configuration turned a rule off,
`ineligible` means Bonus prerequisites failed, `not_run` means the stage was
short-circuited, `not_applicable` is an executed judgement, and
`missing_evidence` / `error` invalidate required evaluation. An invalid record
cannot be exported as a numeric training reward.
