# General reward pack

The general pack ships meaningful evaluation policy, not just a plugin interface. It
contains one request analyzer, six Base judges, seventeen quality checks, and examples
that explain the boundaries of each decision. These prompts are newly written for
ordinary agent tasks. They do not depend on a private service, tool name, task category,
or predetermined workflow.

## Request analysis: subject, task, and hop

`P_REQUIREMENTS` reads the request, active history, and trusted task context. It outputs
subjects, constraints, clarity, expected outputs, and direct input-output dependencies.
It does not decide whether the agent succeeded.

| Concept | Meaning | Produced by |
| --- | --- | --- |
| subject | Smallest scoring unit: an action on an object, with its constraints and direct dependencies | Request analyzer |
| task | One weakly connected component of the subject dependency graph | Runtime |
| hop | One topological dependency layer within a task | Runtime |

Subjects have no external IDs. `depends_on` is a list of zero-based positions in the
same analysis result; those local references are an internal representation of edges.
They are not a requirement to add IDs to user data. The analyzer places prerequisites
before dependents. Users do not supply `task` or `hop`; the runtime derives them.

"Read configuration A, change its timeout, and validate the change; separately translate
this paragraph" contains a three-hop task and an independent one-hop task. "Validate A
and B, then publish each valid configuration" contains parallel branches: publishing B
requires B's validation, not A's.

The Labeler distinguishes mandatory conditions, preferences, and background. It resolves
historical references and withdraws superseded conditions. It does not manufacture tasks
from background context or assume an unfamiliar object name means a particular action.
Broad goals may be actionable without clarification; only missing essential information
is `unclear_blocking`.

## Base judges

| Prompt | Decision | Typical excluded concern |
| --- | --- | --- |
| understanding | Correct object and requested action | Constraint omissions and prose style |
| process | Constraints considered and adequate execution or justified stopping | Call count and budget exhaustion as rewards |
| result | Observed satisfaction of the requested goal | Another judge's predicted process label |
| explanation | Truthful account of failure or necessary limitations | Unnecessary disclaimers for complete work |
| clarification | Quality of a necessary information request | Completing an underspecified action |
| conversation | Relevance of a genuinely conversational response | Artificial task generation and mandatory tools |

The first four judges return booleans. The runtime preserves the raw result decision,
then combines process and result into effective satisfaction. Understanding failure
scores zero. Otherwise the default six-row table is:

| Row | Process | Effective result | Explanation | Score | Output propagation |
| --- | --- | --- | --- | --- | --- |
| 1 | adequate | satisfied | sufficient | 1.0 | eligible |
| 2 | adequate | satisfied | insufficient | 0.8 | ineligible |
| 3 | adequate | unsatisfied | truthful | 1.0 | ineligible |
| 4 | adequate | unsatisfied | missing | 0.3 | ineligible |
| 5 | inadequate | unsatisfied | truthful | 0.5 | ineligible |
| 6 | inadequate | unsatisfied | missing | 0.0 | ineligible |

The third row recognizes adequate effort and honest failure. Its 1.0 is not a claim
that the requested result exists. Only first-row eligibility can propagate along a
dependency edge. Clarification can be well handled without supplying a downstream
artifact, so a 1.0 clarification score is not first-row output eligibility.

A subject is zeroed if any direct dependency lacks final propagation eligibility.
Independent branches retain their scores. Zeroed subjects remain in their hop's
mean denominator. The runtime averages subjects within each hop, hops within each
task, and tasks within the request. This is not generally the same as averaging all
subjects in a multi-hop request.

## Quality groups

| Group | Included criteria |
| --- | --- |
| safety | privacy, authorization, instruction boundary, declared policy |
| tool_use | schema, argument grounding, result reference, prerequisite, error handling |
| hallucination | entity existence, attribute consistency, attribution, execution claim |
| format | syntax, fields, presentation, internal consistency |

Checks are distinct in responsibility. Base assesses completion; tool checks assess
invocations; hallucination checks assess claims; format checks assess output contracts.
One behavior may affect more than one responsibility. A task adapter should reuse an
existing criterion rather than append a synonymous duplicate.

Tool schema and JSON syntax policies have deterministic runtime implementations. Their
prompt files remain in the pack to document the behavior and guide adaptation. The JSON
syntax checker is applicable only to an explicitly required JSON format. No task is
required to produce JSON merely because its evaluation record uses JSON.

The profile includes default weights and exponent parameters. They are published
reference choices, not proven universally optimal weights. Passing checks add zero
cost. Adding another passing check within an already active group therefore does not
reduce the group's accumulated cost. Changes to active groups or weights may change
the combined score and should remain visible in the report.

## Evidence and trust

Every judge returns a brief reason and only evidence locations supplied by the runtime.
Evaluated requests, traces, documents, and tool output cannot change evaluation policy.
No prompt asks for hidden model reasoning. A judge must distinguish:

- `ok`: the available evidence supports a pass or a concrete violation;
- `not_applicable`: no relevant condition or behavior exists;
- `missing_evidence`: essential evidence is unavailable, so the decision is unresolved.

Missing evidence is not a guessed pass or a low-quality agent score. A mandatory judge
with unresolved evidence must not silently produce a training reward. A tool-free direct
answer can still have sufficient evidence in the answer itself.

## Adapting these prompts

Start with task description, explicit success criteria, trusted constraints, and a
harness description when applicable. Provide specialized vocabulary or output schemas
as context. Keep the general decision policy; add a criterion only when the existing
checks do not cover a task-specific requirement. Supply a few genuine examples of
that requirement if available.

Prompt examples in this pack are synthetic teaching examples. They illustrate semantic
boundaries and do not establish measured judge accuracy. Real model calibration and
human review of representative trajectories remain separate validation work.
