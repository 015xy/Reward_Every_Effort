# Data contracts

`Case` JSON/YAML requires only `request`. Real scoring also needs sufficient
observable response, events or artifacts. With no labels, the configured model
performs Labeler extraction; optional `subjects` bypass extraction when verified
labels are available. An empty list explicitly represents a conversational turn.

```json
{"request":"Read config, update timeout and verify it",
 "response":"Updated and verified",
 "subjects":[
   {"subject":"Read config","depends_on":[]},
   {"subject":"Update timeout","depends_on":[0]},
   {"subject":"Verify config","depends_on":[1]}],
 "events":[],"artifacts":[],"evidence_complete":true}
```

Subjects require no IDs. The numbers in `depends_on` are positions in the local
list, not business/entity identifiers. References must exist, cannot be self or
duplicate edges, and must form a DAG. A task is a weak component and a hop is a
topological layer inside that task. A hop is not a tool call or conversation turn.

Events have unique `ref` strings for evidence navigation, `kind`, optional `tool`,
`arguments`, `output`, `error`, `content`, `visible_context`, and `harness`. A per-event
`harness` records the tools and permissions available at that point; schema checks
use it before falling back to the case-level harness. A `tool_result`
explicitly names its observed `tool_call` using `call_ref`. These event references
are distinct from subject identifiers. Artifacts have `ref`, `name`, `content`.
Reserved evidence refs are request, response and history.

TaskContext fields: `task_description`, `success_criteria`, `constraints`, `harness`,
`references`, `examples`, `output_format`, `output_schema`. Text files can be read
by the caller and supplied inline; the adapter never scans a working directory or
opens arbitrary paths named by a designer. A frozen pack stores task_context.json;
an explicit Case context takes precedence. Harness tool parameters use JSON Schema.

Judgement has `status`, `value`, `reason`, `evidence_refs`, and optional `annotations`:

| Status | Value | Meaning |
|---|---|---|
| ok | Boolean pass/fail, or declared score [0,1] | Available evidence supports a judgement |
| not_applicable | null | Predicate does not apply; excluded from cost |
| missing_evidence | null | Necessary evidence absent; evaluation invalid |
| error (runtime) | null | Transport, schema, plugin or reference failed |

The four main Base predicates require Boolean values. Clarification and
conversation use scores [0,1]; successful clarification does not qualify as
row-1 output. Default quality criteria use Boolean values; plugins may define a
fractional value. `value=1` means no quality cost and `value=0` contributes its full
weight. Missing and skipped items are never colored as passed/failed zeroes.

An annotation is `{ref, kind, quote?, reason}`. `kind="issue"` identifies the
erroneous claim or action; `kind="support"` identifies evidence used to check it.
The runtime validates the reference and any verbatim quote against the actual
text at that location. Quoted tool arguments and structured output use pretty JSON.
An omitted action has no observed message to highlight: describe that omission
without inventing a location. Plain `evidence_refs` remain citations and never
automatically mark their targets as errors. The Inspector uses these distinctions
to highlight the problem and its supporting evidence separately.

Use `Case.model_json_schema()`, `TaskContext.model_json_schema()` and
`Pack.model_json_schema()` for complete machine-readable schemas. Validation rejects
unknown fields. `training_reward` rejects invalid evaluation records.
