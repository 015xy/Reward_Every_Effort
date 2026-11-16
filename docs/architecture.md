# Architecture

Four layers:

1. **Core runtime**: strict contracts, content fingerprint, evidence validation,
   bounded model transport, concurrency, errors, deterministic aggregation.
2. **General reward pack**: Labeler, six Base prompts, six-row table and seventeen
   safety/tool/hallucination/format criteria with explicit boundary examples.
3. **Task adapter**: normalized TaskContext, reuse coverage, bounded incremental
   Markdown proposal, structural validation, immutable TaskPack publication.
4. **Evaluation / Inspector**: full records, explanations and evidence anchors,
   per-subject blocking, hop/task denominators, regrade, recompute and RL diagnostics.

The actor loop and evaluation loop are independent. Harness describes declared
tools, skills, permissions and compression; it is not a task-success definition.
Tool-call/result references and per-event visible_context retain evidence of what
the actor could see. Hidden evaluator references belong in TaskContext and must
not be copied into actor inputs by downstream integrations.

Labeler reads only request/history/trusted task and harness context. It extracts
subjects, constraint ownership, clarity and input-output edges. Task groups and
hops are derived in code. Judge policies receive untrusted case data as JSON and
do not acquire actor tools. Base predicates are independently judged; only code
conjoins process and outcome, looks up rows, and applies qualification propagation.

An evaluation computation is not the task dependency graph. Required judgements
are scheduled irrespective of branch failure so raw quality remains inspectable.
Dependency propagation then zeros only descendants whose required inputs failed.
Cycles and missing edges are validation errors, not permission to flatten tasks.

Each record includes the exact normalized case, criteria, statuses, reasons,
evidence refs, raw and final subject scores, task/hop hierarchy, active group
costs, reference profile, model and pack content fingerprint. Recompute changes
numeric policy without re-calling judges; regrade judges saved evidence again.
Missing environment evidence cannot be reconstructed by regrade.

The current synchronous runtime uses a bounded thread pool, per-request timeout,
global deadline/call budget and limited retries. Optional cache keys cover model,
endpoint, exact prompt, output schema and actual input; cache is opt-in because
evaluation records can contain local data. It does not promise vendor reproducibility.

TaskPack validity means schemas, paths and plan references resolve. Accuracy,
cross-provider agreement and RL learning gains require separate empirical tests.
The framework intentionally retains rich universal judgement examples while
leaving novel environment semantics to context or registered verifiers.
