# Bonus: useful organization beyond the required structure

Code has already checked eligibility. Evaluate whether the actual output organizes
comparable information in a useful way beyond the structure the task requires.
Return a fraction in [0,1] of this rule's configured maximum. It is not a final reward.

Useful organization makes an already evidenced comparison, category, priority or tradeoff
easier to see and act on. Groups, tables, ordering, transitions or another clear
structure may qualify when their relationships are supported by supplied evidence.
No particular markup, entity type, item count, group size or balanced layout is
required. A small uneven grouping can be useful; headings alone are insufficient.

Required formatting and grouping belong to Base and format checks, not this bonus.
Do not reward length, many sections, decorative headings, arbitrary batches,
duplicated items, extra unsupported categories or prohibited additional actions.
This rule assesses the organization benefit only: additional content insight and
artifact reusability are evaluated separately. Do not count the same benefit twice.
Reformatting supplied category or priority labels can qualify. Newly inferring a
shared cause or recommendation belongs to insightful_synthesis, not this rule;
both rules need independently useful benefits to earn credit together.

No comparable information or organization is already fully required:
not_applicable,value=null. An opportunity exists but no additional useful structure
is supplied: ok,value=0. A useful structure with material limits: a partial value
such as 0.5. Correct organization with a clear extra comparison or navigation benefit:
ok,value=1. Necessary evidence for the claimed relationships unavailable:
missing_evidence,value=null. Cite the actual structure and supporting items.

Examples:
- User asks for several observed check results whose input already labels their
  categories. The reply additionally groups them by those supplied categories so
  the relevant results are easier to locate -> ok,1. No new cause is inferred.
- User explicitly asks to group those failures by cause. That structure is required
  delivery -> not_applicable for this bonus, regardless of the Base score.
- The output labels successive rows "batch one" and "batch two" without a meaningful
  relationship -> ok,0.
- A small comparison uses prose transitions rather than headings; the relationships
  are correct and useful -> can earn credit. Markup and fixed group sizes are not
  requirements.
- Some groups are useful but one combines materially different supplied categories ->
  a supported partial value such as 0.5, explaining the limitation.

Return only Judgement JSON: status=ok|not_applicable|missing_evidence,
value=number in [0,1]|null, reason, evidence_refs, optional annotations.
