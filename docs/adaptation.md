# Task adaptation

The default pack works without task adaptation. Adaptation adds local success
conditions, terminology, output contracts, references and harness descriptions to
the evaluator's context, while retaining its generic judge policy and scoring
formula. It does not create an agent execution environment.

```python
from pathlib import Path
from ree.models import TaskContext
from ree.pack import load_pack
from ree.adaptation import adapt

report = adapt(
    TaskContext(
        task_description="Update a configuration file and validate it.",
        success_criteria=["The timeout is 30 seconds and unrelated keys are preserved."],
        constraints=["Report the validation result accurately."],
        output_format="json",
    ),
    Path("./task-packs/config-v1"),
    load_pack(),
)
```

Coverage records preserve the exact source fields for each requirement. A task
description supplies the labeler and Base understanding/result judges; explicit
success conditions supply Base result. Untyped natural-language constraints supply
both Base process and result, without guessing whether they concern execution or
outcome. Explicit JSON output format and JSON Schema route to `format.syntax`, the
actual deterministic JSON checker. Other output formats route to the semantic
`format.presentation` check; this does not claim a parser for every format.
Duplicate requirement text retains all of its source-field references.

This context-only path requires no API. The generated pack contains the complete
baseline, `task_context.json`, an executable `manifest.json`, and
`adaptation.json`. It is usable immediately with the same evaluator. It records
that semantic calibration has not been run. Specifying local conditions does not
prove that a judge applies them accurately.

When a designer client is supplied, it receives the full baseline prompts, the
explicit requirements and the task context. It returns an exact coverage mapping
and at most eight additional prompt checks. Existing predicates normally consume
local conditions through context; the designer should not duplicate task outcome
checks under different names. Baseline criteria, profiles, weights, labeler and
judge prompts remain unchanged. New semantic checks use the default weight 2.

The proposer cannot choose paths, execute commands, install dependencies, modify
baseline files, change aggregation or create verifier plugins. The framework
chooses the generated Markdown paths, validates names, group membership,
references and coverage, and checks the complete pack. Invalid proposals receive
one repair attempt; after two failed proposals, adaptation raises
`AdaptationError` and does not publish a target. Model transport itself may have
its own finite retry policy; this proposal limit is not an unbounded agent loop.

All references and harness fields are supplied data. The adapter does not scan
the user's filesystem or silently read referenced URLs/files. Load any materials
explicitly before constructing `TaskContext`. API keys and transport settings
stay with the runtime client, outside the task pack. Common structured credential
fields are rejected; callers should also redact credentials embedded in free text.

The target directory must be new. Generate a fresh version for every change; the
returned fingerprint identifies the complete published pack. Provenance records
baseline, context and overlay hashes, the coverage map, proposal outcomes,
limitations and structural validation. These records support review and replay;
real task calibration remains a separate evaluation step.

## Subject, task and hop

Adaptation retains the default labeler. A subject is a scoring unit describing
what action applies to what object, with its constraints and direct input/output
dependencies. The input needs no subject ID. Only the normalized list uses
positional dependency references internally. Tasks are weakly connected
components of that dependency graph; hops are its topological layers within each
task. The framework derives task and hop; neither the user nor the labeler needs
to write them manually. An independent branch retains its score when another
branch fails. A downstream subject loses its score only when one of its own direct
dependencies lacks final first-row eligibility.

## Current boundary

This first implementation performs context injection and bounded declarative
rubric generation. Explicitly registered verifier functions and supplied-artifact
checks are available; see [verification](verification.md). Automatic generation of
executable verifiers, container creation, tool-running
designer agents and empirical selection between model APIs require explicit
extension work. Structural validation is available now; success-rate claims
require real held-out task evaluations.
