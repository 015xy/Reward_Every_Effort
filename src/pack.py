"""Load, validate and fingerprint immutable reward content."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .models import Pack, TaskContext


@dataclass(frozen=True)
class LoadedPack:
    root: Path
    manifest_json: str
    fingerprint: str
    prompts: Mapping[str, str]
    task_context: str | None

    @property
    def manifest(self) -> Pack:
        """Return a copy so callers cannot mutate the fingerprinted scoring plan."""
        return Pack.model_validate_json(self.manifest_json)

    def prompt(self, relative: str) -> str:
        if relative not in self.prompts:
            raise ValueError(f"Unregistered pack prompt: {relative}")
        return self.prompts[relative]

    def context(self) -> TaskContext | None:
        return TaskContext.model_validate_json(self.task_context) if self.task_context else None


def load_pack(path: str | Path | None = None) -> LoadedPack:
    root = Path(path) if path else Path(__file__).parent / "data" / "general-agent"
    if root.is_file():
        root = root.parent
    root = root.resolve()

    def resource(relative: str) -> Path:
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError(f"Invalid pack resource path: {relative}")
        return target

    manifest = Pack.model_validate_json(resource("manifest.json").read_text())
    paths = {
        manifest.labeler_prompt,
        *manifest.base_prompts.values(),
        *(c.prompt for c in manifest.criteria),
        *(rule.prompt for rule in [*manifest.gates, *manifest.bonuses]),
    }
    prompts = {}
    for name in sorted(paths):
        prompts[name] = resource(name).read_text(encoding="utf-8")
    context_path = root / "task_context.json"
    context = (
        TaskContext.model_validate_json(resource("task_context.json").read_text())
        if context_path.exists() or context_path.is_symlink()
        else None
    )
    data = {
        "manifest": manifest.model_dump(mode="json"),
        "prompts": prompts,
        "context": context.model_dump(mode="json") if context else None,
    }
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return LoadedPack(
        root,
        manifest.model_dump_json(),
        digest,
        MappingProxyType(prompts),
        context.model_dump_json() if context else None,
    )
