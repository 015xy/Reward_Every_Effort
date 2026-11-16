# Reward Every Effort

General-purpose, evidence-based rewards for agent trajectories. The default pack
contains a working Labeler, task-completion judgements, the six-row Base table,
17 quality criteria, exponential aggregation, and a standalone interactive Inspector.
Provide a request and observed trajectory; task-specific rubrics are optional.

## Quick start

Python 3.10+:

```bash
python -m pip install -e '.[dev]'
ree doctor
# No API needed: replay a declared synthetic fixture to inspect the entire pipeline.
ree score examples/config-case.json --replay examples/config-replay.json \
  --output outputs/config.json --html outputs/config.html
```

Open `outputs/config.en.html` or `outputs/config.zh.html`. The CLI exports complete
English and Chinese interfaces by default; `--locale en` or `--locale zh` exports
one page at the exact requested path. The Inspector follows the actual calculation:
Gate, Base, Deduct, Bonus and the final reward. Select a stage to inspect its
judgements and arithmetic, or a deduction to locate the issue in the trace.
Offline replay is a reproducibility example; it does
not claim to judge a new trajectory. For real evaluation configure an
OpenAI-compatible chat-completions endpoint:

```bash
export REE_MODEL=your-judge-model
export REE_API_KEY=your-key
# Optional; default https://api.openai.com/v1
export REE_BASE_URL=https://your-provider.example/v1
ree score your-case.json --output outputs/score.json --html outputs/score.html
```

No provider-specific headers, internal services, actor runtime, Docker environment,
or business IDs are required. The judge must support JSON mode. The default
timeout, call budget and retries are bounded and configurable; keys are never
saved to task packs or evaluation records. The provider receives supplied case
evidence. Persistent judge caching is opt-in with `--cache <directory>`.

## Subjects, tasks, and hops

| Concept | Definition | Produced by |
|---|---|---|
| subject | Minimum scored unit: **do what to what**, with its constraints and direct dependencies | Labeler (`P_REQUIREMENTS`), or supplied labels |
| task | A weakly connected component of the subject dependency graph | Deterministic runtime |
| hop | A topological dependency layer inside a task; same-layer subjects can run independently | Deterministic runtime |

No external subject IDs are needed. `depends_on` contains zero-based positions
into the generated subject list: an internal representation of input-output edges.
The Labeler does not invent tasks, hops, or dependencies from mere execution order.

Base judgements distinguish understanding, sufficient process, actual outcome,
and honest explanation. Code applies the six-row table. Only row 1 enables
downstream propagation; an honest unsuccessful attempt can score 1.0 locally but
does not produce a usable dependency. Failure follows **individual edges**, never
zeros an unrelated branch in the same hop.

```text
hop score  = mean(final subject scores)  # blocked subjects remain in denominator
task score = mean(hop scores)
base       = mean(task scores)
```

Quality gaps from safety, tool use, hallucination and format yield weighted costs.
Each group uses the endpoint-normalized exponential `2^(-cost/H)`, then active
groups are averaged and deducted from Base. Empty applicable groups are excluded;
no applicable groups means quality=1. Errors and missing evidence produce an
**invalid evaluation with reward=null**, never an implicit training reward of zero.
The reference profile uses H=8, max_cost=32, deduction_max=0.8 and [0.1,1] clipping.
These are published defaults, not claims of universal optimality.

Inspect multiple rollouts for one request, including issue locations and their
supporting evidence, with the public synthetic comparison:

```bash
python examples/trace_demo.py --output outputs/trace-demo.jsonl
ree inspect outputs/trace-demo.jsonl --output outputs/trace-demo.html
```

The left column selects data, the center shows its trace, and the right column
shows one score-flow diagram. JSONL and JSON arrays work alongside single records.
The comparison uses explicitly authored replay judgements; it makes no API calls.

## Task adaptation

```bash
# Add local requirements while reusing every generic predicate.
ree adapt examples/task-context.yaml --output outputs/config-pack
# A bounded designer may propose additional declarative checks.
ree adapt your-context.yaml --designer --output outputs/task-pack
ree score your-case.json --pack outputs/task-pack --output outputs/result.json
```

TaskContext accepts task description, success criteria, constraints, tool/skill
information, references, examples and output contracts. Packs freeze content and
fingerprints. The designer sees full generic policies, adds only validated prompt
criteria, cannot modify aggregation or execute generated code, and gets at most
two validation attempts. A passed structural check is distinct from real model
calibration. See [adaptation](adaptation.md) and
[lessons from environment integration](adaptation-lessons.md).

## Python API and extensions

```python
from ree import Case
from ree.judge import OpenAIJudge
from ree.evaluator import evaluate, training_reward

client = OpenAIJudge.from_env()
try:
    record = evaluate(Case.model_validate(case_data), client)
    reward = training_reward(record)  # rejects invalid records
finally:
    client.close()
```

A verifier can be passed as `plugins={criterion_name: function}` to `evaluate`.
It consumes a `Case` and returns a `Judgement`. Register its criterion in a pack;
the runtime validates value/status/evidence, aggregation and Inspector reuse it.
Explicit trusted plugins are Python code; generated task overlays never execute code.
For supplied JSON state or artifacts, [built-in evidence verifiers](verification.md)
support equality, existence, absence and before/after changes without a model call.

`ree batch` evaluates JSONL, preserving individual failures; `ree regrade` judges
the saved case again; `ree recompute` applies another profile to retained facts
without a model call. `ree diagnostics` shows group reward variance and high-tail
ties for RL debugging. These diagnostics do not establish GRPO/GSPO effectiveness.

## Repository map

```text
src/   # schemas, bounded judge, checkers, runtime, adaptation, CLI, Inspector
packs/general-agent/      # editable source of the full default policy
examples/                 # public synthetic cases and explicit replay responses
docs/                     # architecture, contracts, prompts, adaptation, Inspector
tests/                    # graph, aggregation, failure, transport, adaptation, XSS regressions
```

From the repository root, run `python scripts/sync_pack.py` after editing pack sources to refresh packaged
data; CI verifies they match. `pytest` exercises the deterministic framework and
mocked transport; live judge quality and environment-specific calibration are
separate experiments. See [architecture](architecture.md),
[data contracts](contracts.md), and [development](CONTRIBUTING.md).

## Scope of this release

This local implementation covers the default evaluation/adaptation/inspection
path. Automatic container synthesis, arbitrary actor execution, multimodal parsers,
and benchmark integrations are extension work. [Gate and Bonus](gate-bonus.md)
include working rules and general reference prompts. Their reference rules are
disabled by default: enabling a short circuit or extra credit is an explicit
frozen-pack policy choice. They do not silently grant length-based bonuses.
