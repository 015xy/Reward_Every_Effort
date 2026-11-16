# Reward Inspector

The Inspector opens saved Agent evaluations in an offline workspace: select a
query on the left, read its original trajectory in the middle, and inspect its
reward flow and expandable scoring details on the right. It makes no model calls
and loads no external scripts, fonts, or assets.

```python
from pathlib import Path
from ree.inspector import render_report, render_reports

# A single record or a nonempty list of records uses the same workspace.
render_report(record, Path("evaluation.html"), locale="en")
render_report([record_a, record_b], Path("rollouts.html"), locale="zh")

# Two complete interfaces with local sibling links.
paths = render_reports([record_a, record_b], Path("rollouts.html"))
# rollouts.en.html and rollouts.zh.html
```

CLI `ree inspect RECORD --output OUTPUT --locale en|zh|both` selects the output
interface. The default `both` produces separate English and Chinese files. The
CLI accepts saved JSON objects, JSON arrays, or JSONL evaluation records. UI text
is localized; requests, judge reasons, policy identifiers, and evidence preserve
their original language. A single-language export has no alternate-file link.

## One reward flow

Each selected record has one **Gate → Base → Deduct / Bonus → final reward**
diagram. Select a node to inspect that phase below the diagram.

- **Gate:** recorded decisions in priority order and the rule that supplied a
  direct reward, if any. Direct routing visibly bypasses downstream stages;
  their values remain “Not run” rather than becoming zero.
- **Base:** select a subject to inspect understanding, process, observed result,
  explanation, the recorded lookup row, raw score, direct dependency blocking,
  and final score. Expand the actual six-row table or hop / task averaging.
  Zeroed subjects retain their denominator positions. The conversation branch
  displays its own Base judgement without creating a task.
- **Deduct:** select a quality group to inspect violation costs, reasons,
  references, and the recorded exponential profile. Full criteria support text
  and status filters. Formula details use actual weights and parameters.
- **Bonus:** actual eligibility decisions, judged values, granted amounts,
  uncapped sum, and configured cap. A disabled policy can show recorded zero;
  an unexecuted stage cannot.
- **Final reward:** actual Base − Deduct + Bonus composition and configured
  clipping, or the direct Gate reward. Deduction entries link back to their
  quality group and original evidence.

Missing, invalid, deferred, and not-applicable states remain distinct. An invalid
evaluation has no usable training scalar. Older records without Gate, Bonus, or
composition fields display “Not recorded”; the Inspector does not infer a policy
or reconstruct missing values. Other completed judgements remain inspectable.

## Trace annotations

Evidence buttons open and scroll the corresponding recorded message, event, or
artifact in the trajectory pane. System context and lengthy tool details are
collapsible. An optional judgement annotation can identify a precise location:

```json
{
  "ref": "response",
  "kind": "issue",
  "quote": "Saved: debug=true",
  "reason": "The completion claim contradicts the observed configuration"
}
```

`kind: "issue"` draws a solid red boundary around the problem location;
`kind: "support"` draws a dashed boundary around supporting evidence. An exact
`quote` highlights matching original text, and the annotation reason appears
beside the trace. The evaluator validates references and exact quotes against
observed text. Selecting a deduction prioritizes the issue location for focus.

A plain `evidence_refs` citation remains **related evidence**. It never becomes
an issue annotation solely because the criterion lost points.

## Responsive and offline behavior

Desktop uses three independently scrollable columns. Medium windows place a
compact data selector above the side-by-side trajectory and score panes. Small
screens use a trajectory / score switch; evidence navigation opens the trajectory
pane automatically. Tables scroll locally, keyboard focus is visible, and
navigation respects reduced-motion preferences.

`src/inspector.py` validates and safely serializes records and embeds the template,
CSS, JavaScript, and selected locale JSON from `src/web/`. The two-pass design
brief is in `docs/inspector-design.md`.

Untrusted record JSON is escaped against script termination and inserted in one
substitution pass. Trace text and highlights are built with `textContent`, never
HTML interpolation. Evidence buttons target generated local elements, and a
content security policy disables network requests. These protections preserve,
rather than redact, the input data.
