# Bonus: grounded additional insight beyond required delivery

Code has already checked eligibility. Evaluate the additional value of an insight
actually present in the response or supplied artifact. Return its fraction in
[0,1] of this rule's configured maximum, not an absolute bonus or final reward.

An insight merits credit when it connects available evidence into a useful extra
conclusion, explains a material tradeoff, or identifies a relevant implication that
reduces follow-up decision work. It must be correct, evidence-grounded, relevant
to the user's task, and beyond the required answer. Repeating facts or explaining
an analysis that the user already requested is required delivery, not extra credit.
Only assess observable explanations and evidence; never request hidden reasoning.

Do not reward word count, depth labels, many tool calls, speculation, decorative
commentary, generic warnings, unsolicited actions, or a long chain of reasoning.
Do not require an insight on a task where none is useful. Prefer no bonus to an
invented benefit. Mere grouping belongs to meaningful_organization; the existence
or reusability of an artifact belongs to reusable_artifact. This rule credits an
independent content insight rather than counting either of those benefits again.

No useful additional insight: ok,value=0. A useful grounded extra conclusion with
material limits: a partial value such as 0.5. A correct, relevant extra insight
with clear practical benefit: ok,value=1. Necessary evidence genuinely unavailable:
missing_evidence,value=null. A concrete contradiction in supplied evidence warrants
ok,value=0, not a guessed positive award. Cite the insight and its actual support.

Examples:
- User asks for an average from supplied measurements. The answer computes it
  and additionally shows how one documented outlier affects that average, helping
  the user interpret the result -> ok,1, with the measurement and calculation refs.
- User asks to analyze outliers and their effect. The same explanation is required
  work -> ok,0 for this bonus, even if it earns full Base.
- User asks for one fact; several paragraphs repeat it and add generic cautions ->
  ok,0. More words are not more insight.
- An optional tradeoff explanation is grounded but covers only a clearly disclosed
  part of the supplied evidence -> a supported partial value such as 0.5.
- A response claims a performance improvement with no necessary comparison data ->
  missing_evidence,null when the claim cannot be checked from the supplied evidence;
  if the supplied data contradict it, ok,0.

Return only Judgement JSON: status=ok|not_applicable|missing_evidence,
value=number in [0,1]|null, reason, evidence_refs, optional annotations.
