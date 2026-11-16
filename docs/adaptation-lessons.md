# What real environment adaptation requires

An adapter must preserve what happened and what the actor could know. Most of the
work is defining that evidence boundary; changing reward prompts is only one part.
The following lessons informed the public interfaces. They are implementation
findings, not measurements of judge accuracy or training gains.

## Acceptance conditions are not automatically subjects

A task's checklist may mix requested outputs, constraints, optional preferences
and suggested execution steps. Turning every line into a subject changes the Base
denominators and can award repeated credit for one outcome. Pass these materials
through TaskContext first. The Labeler extracts actions on objects and attaches
constraints to their owners. Supply subjects directly only when those annotations
have been checked against this contract.

Likewise, source dependency metadata is useful context, but optional prerequisites
must not become mandatory edges. Task and hop remain derived properties of real
input-output dependencies. Environment-specific object IDs may remain in evidence;
the general subject schema never requires them.

## Normalize trajectories without fabricating history

Rollout files may contain cumulative snapshots rather than individual messages.
Blindly concatenating them duplicates actions. Keeping only the last snapshot loses
history after compression. An integration must understand its source format,
reconcile overlapping observations, retain explicit call/result associations and
mark uncertain reconstruction as incomplete.

Tool availability can change during a run. A final tool list cannot establish
whether an earlier call was legal. Each Event can carry the contemporaneous Harness
and visible context; deterministic tool-schema checks use that snapshot. Preserve
skipped calls and execution errors as such. An assistant's text before a tool call
is not necessarily its final answer.

## A claim of a state change is not the changed state

For artifact or environment tasks, provide the observed final artifact. When the
condition concerns an actual change, also provide the initial artifact. The public
`ree.verification` module checks equality, existence, absence and changes at explicit
JSON pointers. Its result can become a verification Event and a registered quality
criterion, while Base remains responsible for overall task completion.

An unavailable snapshot produces missing evidence, not an invented state or an
implicit success. A response-only task may need no external state at all. The
adapter must decide which evidence its local conditions require.

## Separate policy, schema and data

Harness describes the actor's system prompt, tools, skills, permissions and context
compression. TaskContext describes desired outcomes and local constraints. Hidden
evaluation references must stay outside the actor's input. TaskPack freezes the
evaluation policies and context; records retain its fingerprint and actual profile.

Tool-schema property names are part of a contract. A property named `password` is
not itself a credential value. Redaction must distinguish schema declarations from
runtime values, or it can corrupt the very evidence needed to judge tool usage.
Avoid copying an environment's entire configuration into the reward pack.

## Keep adaptation small and inspectable

Start by filling the generic context slots. Add a new criterion only for a local
condition absent from the existing checks. Keep optional bonus conditions separate
from mandatory success criteria. A design model can propose bounded Markdown checks;
it cannot rewrite baseline policy, choose executable paths or run generated code.

An adapter should emit coverage and limitations alongside its TaskPack. Evaluate a
known passing case, a concrete violation and a case with missing evidence. Inspect
subject boundaries, graph edges, denominators and every quality cost before using
the final scalar. An invalid judgement leaves that scalar unavailable while the
Inspector retains the completed portions of the computation.

## Validate at the right level

Structural tests establish that data imports, graph derivation, schemas, resources
and arithmetic work. Replay exercises the complete evaluator with declared fixture
judgements. Neither establishes that a live model judges the task correctly.

Use representative held-out trajectories and human review for semantic calibration;
compare providers for adaptation quality, success rate, latency and cost. Reward
variance and high-score ties are useful diagnostics, but claims about GRPO/GSPO
require controlled training experiments. Environment construction and rollout
execution remain responsibilities of the environment integration.
