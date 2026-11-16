# Working on Reward Every Effort

Read README.md, architecture.md, contracts.md, prompts.md, and
adaptation.md. `packs/general-agent` is the generic policy source; packaged
data is synchronized by `python scripts/sync_pack.py`. Main must run on public
synthetic cases without organization services or task-specific private data.

Preserve subject/task/hop distinction, true direct dependencies, per-edge
propagation, all six Base rows and hierarchical averaging. Subject IDs are not
required. Errors, missing evidence and not-applicable are distinct statuses.
Do not turn an evaluator failure into a valid reward or infer success from prose.

Task adapters should prefer generic context slots over duplicate criteria.
Generated overlays may only add bounded Markdown criteria; they cannot modify
baseline weights, aggregation or run arbitrary code. Trusted verifier extensions
are explicitly registered. Store environment-specific implementation in a
separate branch/TaskPack. Move only genuinely reusable mechanics into main.

Run pytest and ruff after changes. Keep fixtures explicit about replay vs actual
model evaluation. Never claim live calibration from replay or structural tests.
