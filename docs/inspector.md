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

Use **Open data** to choose or paste a saved JSON object, JSON array, or JSONL file
(up to 20 MB). A successful import replaces the current dataset; an invalid import
leaves it intact and explains which record could not be loaded. **Export JSON**
downloads the selected evaluation with its original facts and scores. Language
switching preserves loaded data and the current selection. Reading files, switching
languages, and exporting records happen locally without a server or model call.

Unscored `Case` inputs need evaluation first: run
`ree score your-case.json --output result.json`, then open `result.json`. The
Inspector reports this distinction instead of inventing scores for raw trajectories.
Imported data lives in the current page until it is closed or reloaded.

## Display metadata

Task adapters may add presentation information to a saved evaluation's metadata:

```json
{
  "metadata": {
    "display_query": "Update the timeout and verify it",
    "display_reasoning": {
      "msg-3": "Recorded reasoning for this assistant message",
      "response": "Recorded reasoning for the final response"
    }
  }
}
```

`display_query` is an optional string or null. A nonblank value supplies the
query list, its tooltip, the selected query heading and query search. Missing,
null or whitespace-only values fall back to `case.request`. Adapters choose the
source query explicitly; the generic Inspector does not parse task-specific
wrappers. It preserves the complete original `case.request` in the trajectory,
including all text used by the evaluator and its `request` evidence reference.

`display_reasoning` is an optional object containing only already supplied text.
Each value must be a string; each key must be `response` or the ref of an existing
`case.events` entry whose kind is `message` or `tool_call`. The example's `msg-3`
must therefore exist as an assistant event. Request, history, system context,
tool results and artifacts cannot be reasoning targets. A missing mapping adds
no reasoning; the Inspector does not infer, recover or generate hidden content.

These fields remain outside `Case` and all judge inputs. They do not change
reward facts, scoring policy, cache identities or exact evidence anchors, and
they are not additional scored evidence. Validation, HTML export and JSON
import/export preserve their complete original values without normalizing or
rewriting the saved record. Do not copy a display field into scoring input when
adapting a task.

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
  and status filters, including an all-groups view and groups whose checks are all
  not applicable. Formula details use actual weights and parameters.
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

### Reading deduction calculations

Each quality criterion has a score and an unmet-cost weight. These are separate
from the weight assigned to its quality group:

| Quantity | Meaning |
|---|---|
| Criterion score `s_i` | Observed pass/fail (`1`/`0`) or a declared fractional score |
| Criterion weight `w_i` | Cost of completely failing this criterion |
| Criterion cost `c_i` | `w_i × (1 − s_i)` for a completed applicable check |
| Group cost `C_g` | Sum of that group's criterion costs |
| Group score `q_g` | Group cost converted through the declared exponential curve |
| Group weight `W_g` | This group's relative influence on combined quality |
| Combined quality `Q` | `sum(W_g × q_g) / sum(W_g)` over active groups |
| Deduction `D` | `deduction_max × (1 − Q)` |

For example, four checks with weights `2, 2, 2, 4` and scores `1, 0, 1, 1`
produce individual costs `0, 2, 0, 0` and group cost `2`. With `H=8` and
`C_max=32`, the group's score is about `0.8303`. Its influence on combined
quality then depends on its group weight and the other active groups.

The criterion table exposes each step and its evidence; filtering visible rows
does not change the recorded group total. The group aggregation table exposes
each weighted contribution before combined quality becomes a deduction. The
exponential formula and parameters remain available as expandable details.
Not-applicable checks are excluded; missing evidence, failed execution and
unrecorded values remain unresolved, rather than appearing as zero-cost passes.

## Trace annotations

Each judgement has one **View evidence** button. It expands all referenced
messages, events or artifacts together, and focuses a recorded issue before
supporting evidence. System context, recorded reasoning and tool details are
collapsible. Display-only reasoning is never registered as scoring evidence.
An optional judgement annotation can identify a precise location:

```json
{
  "ref": "response",
  "kind": "issue",
  "quote": "Saved: debug=true",
  "reason": "The completion claim contradicts the observed configuration"
}
```

`kind: "issue"` marks the problem location and matching quote in red;
`kind: "support"` marks supporting evidence in yellow. Overlapping issue and
support quotes use the issue color. The annotation reason appears beside the
trace. The evaluator validates references and exact quotes against observed
text. Text supplied only for display never receives evidence highlighting.

A plain `evidence_refs` citation remains yellow **related evidence**. It never
becomes an issue annotation solely because the criterion lost points.

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
