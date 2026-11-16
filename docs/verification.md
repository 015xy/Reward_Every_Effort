# Verifying supplied artifact evidence

`ree.verification` provides deterministic checks for JSON artifacts already
supplied in a Case. It does not fetch a snapshot, execute an agent or infer a
successful state transition from a reply. The caller provides the evidence and a
small explicit condition.

```python
from ree.models import Artifact, Case
from ree.verification import ArtifactCheck, evaluate_artifact, verification_event

case = Case(
    request="Update the timeout to 30 and validate it.",
    artifacts=[Artifact(ref="result", name="configuration", content='{"timeout":30}')],
)
check = ArtifactCheck(
    artifact_ref="result", pointer="/timeout", operation="equals", expected=30,
)
judgement = evaluate_artifact(case, check)
# status="ok", value=True, evidence_refs=["result"]
event = verification_event("verify/timeout", judgement)
case_with_verification = Case.model_validate({
    **case.model_dump(), "events": [*case.model_dump()["events"], event.model_dump()],
})
```

The verification Event makes the actual check result available to Base as
observable evidence. Its reference is chosen by the caller and must be unique in
the Case. It is not an automatic reward override or a claim that every requirement
has been checked.

A registered quality criterion can reuse the same check as a Python plugin:

```python
from ree.evaluator import evaluate

# task_pack.manifest declares a criterion named task.target_state.
record = evaluate(
    case_with_verification, judge, task_pack,
    plugins={"task.target_state": lambda case: evaluate_artifact(case, check)},
)
```

Plugin names must exist in the loaded pack; the evaluator retains the normal
profile, evidence validation and Inspector record. The Base judges receive the
verification Event separately from this quality-plugin result. A contradiction
returns `ok, False`; missing required evidence returns `missing_evidence, None`,
so an unavailable snapshot is not silently converted into a passing condition.

## Operations and pointers

| Operation | What is checked | Required evidence |
|---|---|---|
| `equals` | Value equals `expected` | The artifact and target value |
| `exists` | Pointer identifies a value | A present, valid JSON artifact |
| `absent` | Member/index is genuinely missing | A present, valid JSON artifact |
| `changed` | Value differs from the same pointer in `baseline_ref` | Both artifacts and both values |

Pointers follow RFC 6901: empty string selects the whole document, `/` separates
members, `~1` means `/`, and `~0` means `~`. Array indices use canonical nonnegative
integers such as `0` or `12`. Out-of-range indices are absent; invalid array tokens
or traversal through a scalar are unresolvable evidence, not passing absence.
Malformed JSON, ambiguous duplicate object keys and unavailable artifacts return
missing evidence. Schema keys are opaque: a field named `password` is still a
field. Callers decide how to supply appropriately redacted artifacts; the verifier
does not silently remove fields or alter their meaning.

JSON equality distinguishes booleans from numbers, including nested values:
`false` does not equal `0`. Numeric `1` and `1.0` represent the same JSON value.

A static condition can be checked using a final artifact alone. To establish an
actual change, supply explicit initial and final references:

```python
changed = ArtifactCheck(
    artifact_ref="final", baseline_ref="initial", pointer="/timeout", operation="changed",
)
```

`changed` requires both values to resolve. A deletion is represented by separate
`exists` on the supplied initial artifact and `absent` on the supplied final
artifact. Response-only tasks do not need state artifacts. These checks establish
only their explicit conditions; aggregate task success and rewards remain the
responsibility of Base and the configured evaluation policy.
