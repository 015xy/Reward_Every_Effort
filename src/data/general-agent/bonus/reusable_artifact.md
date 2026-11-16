# Bonus: helpful reusable artifact beyond the required delivery

Eligibility is determined by code before this judge runs. Evaluate only the
additional value of an actually supplied, reusable artifact beyond the task's
required deliverables. The output value is a fraction in [0,1] of this rule's
configured maximum; it is not the final reward or an absolute bonus amount.

An extra artifact merits credit when it is correct, relevant, directly usable,
and reduces real follow-up work: for example a small validated reusable script,
a focused reproduction case, or a concise documented configuration template.
Do not award credit for length, decorative prose, repeated answers, many files,
unsupported promises, irrelevant additions, or a required deliverable renamed
as a bonus. Do not reward disallowed extra actions. Required outputs belong to
Base, not this bonus. Missing evidence of correctness is unknown, not a pass.

No extra artifact was supplied or it adds no useful value: ok,value=0. Some
concrete reusable value with material limitations: ok,value between 0 and 1.
A correct, relevant and clearly reusable additional artifact: ok,value=1.
Necessary artifact or verification evidence is unavailable: missing_evidence,
value=null. Explain the additional benefit and cite actual evidence. Annotations
may mark the supplied artifact as support and a defect as issue.

Examples:
- Task only asks to fix a configuration. The agent additionally supplies a
  tested validator the user can reuse for future edits -> ok,1.
- Task requires a validator. Delivering that validator is necessary work ->
  ok,0 for bonus, regardless of whether Base is fully satisfied.
- An optional reusable script works but assumes a stated restricted input set
  that still covers a useful case -> partial value such as 0.5, with evidence.
- A long explanation repeats the required answer, with no new usable output ->
  ok,0. More words do not create a bonus.
- The answer claims an extra tested script exists, but its artifact or test
  evidence is missing -> missing_evidence, not credit based on the claim.

Return only Judgement JSON: status=ok|not_applicable|missing_evidence,
value=number in [0,1]|null, reason, evidence_refs, optional annotations.
