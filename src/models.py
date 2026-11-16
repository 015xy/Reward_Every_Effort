"""Public data contracts. Task subjects do not need external IDs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Constraint(Model):
    text: str = Field(min_length=1)
    kind: Literal["required", "preference", "context"] = "required"
    inherited: bool = False


class Subject(Model):
    subject: str = Field(min_length=1)
    constraints: list[Constraint] = Field(default_factory=list)
    # Internal positional references, not user/business IDs. Only direct input-output edges.
    depends_on: list[int] = Field(default_factory=list)
    clarity: Literal["clear", "unclear_open", "unclear_blocking"] = "clear"
    blocking_reason: str | None = None
    expected_output: str = ""


class Analysis(Model):
    subjects: list[Subject]


class Message(Model):
    role: Literal["user", "assistant", "system", "tool"]
    content: str


class Tool(Model):
    name: str = Field(min_length=1)
    description: str = ""
    parameters: dict[str, Any] | None = None


class Harness(Model):
    system_prompt: str = ""
    tools: list[Tool] = Field(default_factory=list)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    compression: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_tools(self):
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("Harness tool names must be unique")
        return self


class Event(Model):
    ref: str = Field(min_length=1)
    kind: Literal["message", "tool_call", "tool_result", "context", "verification"]
    tool: str | None = None
    call_ref: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    error: str | None = None
    content: str = ""
    visible_context: str | None = None
    harness: Harness | None = None

    @model_validator(mode="after")
    def named_tool_call(self):
        if self.kind == "tool_call" and not self.tool:
            raise ValueError("Observed tool calls require a nonempty tool name")
        return self


class Artifact(Model):
    ref: str = Field(min_length=1)
    name: str
    content: str


class TaskContext(Model):
    task_description: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    harness: dict[str, Any] = Field(default_factory=dict)
    references: list[str] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    output_format: str | None = None
    output_schema: dict[str, Any] | None = None


class Case(Model):
    schema_version: Literal[1] = 1
    request: str = Field(min_length=1)
    history: list[Message] = Field(default_factory=list)
    response: str = ""
    events: list[Event] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    harness: Harness = Field(default_factory=Harness)
    context: TaskContext | None = None
    subjects: list[Subject] | None = None
    evidence_complete: bool = True

    @model_validator(mode="after")
    def unique_evidence(self):
        refs = [x.ref for x in self.events] + [x.ref for x in self.artifacts]
        if len(refs) != len(set(refs)) or any(
            r in {"request", "response", "history"} for r in refs
        ):
            raise ValueError("Evidence refs must be unique and not reserved")
        calls = {}
        for e in self.events:
            if e.kind == "tool_call":
                calls[e.ref] = e
            elif e.kind == "tool_result":
                if e.call_ref is None or e.call_ref not in calls:
                    raise ValueError("Tool results must reference a preceding observed tool_call")
                if e.tool is not None and e.tool != calls[e.call_ref].tool:
                    raise ValueError("Tool result name conflicts with its associated call")
        return self


class EvidenceAnnotation(Model):
    ref: str = Field(min_length=1)
    kind: Literal["issue", "support"]
    quote: str | None = Field(default=None, min_length=1)
    reason: str = Field(min_length=1)


class Judgement(Model):
    status: Literal["ok", "not_applicable", "missing_evidence"]
    value: bool | float | None = None
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    annotations: list[EvidenceAnnotation] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_value(self):
        if self.status == "ok":
            if self.value is None or not 0 <= float(self.value) <= 1:
                raise ValueError("ok requires a value in [0,1]")
        elif self.value is not None:
            raise ValueError("Skipped or missing evidence judgements have no value")
        return self


class Criterion(Model):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    group: Literal["safety", "tool_use", "hallucination", "format"]
    description: str
    prompt: str
    weight: float = Field(default=2, gt=0, le=32)
    applicability: Literal["always", "tools", "format"] = "always"
    checker: Literal["llm", "json_format", "tool_schema"] = "llm"


class GateRule(Model):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    prompt: str
    description: str
    priority: int = Field(default=0, ge=0)
    reward: float = Field(default=0, ge=0, le=1)
    enabled: bool = False


class BonusRule(Model):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    prompt: str
    description: str
    max_value: float = Field(default=0.1, ge=0, le=1)
    min_base: float = Field(default=1, ge=0, le=1)
    min_quality: float = Field(default=0.9, ge=0, le=1)
    require_all_subjects: bool = True
    enabled: bool = False


class RewardProfile(Model):
    half_life: float = Field(default=8, gt=0)
    max_cost: float = Field(default=32, gt=0)
    deduction_max: float = Field(default=0.8, ge=0, le=1)
    bonus_max: float = Field(default=0.1, ge=0, le=1)
    reward_min: float = Field(default=0.1, ge=0, le=1)
    reward_max: float = Field(default=1, ge=0, le=1)
    group_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "safety": 1,
            "tool_use": 1,
            "hallucination": 1,
            "format": 1,
        }
    )
    table: list[float] = Field(default_factory=lambda: [1, 0.8, 1, 0.3, 0.5, 0])

    @model_validator(mode="after")
    def valid_profile(self):
        if self.reward_max < self.reward_min:
            raise ValueError("reward_max must be >= reward_min")
        if len(self.table) != 6 or any(not 0 <= x <= 1 for x in self.table):
            raise ValueError("Base table must contain six values in [0,1]")
        if set(self.group_weights) != {"safety", "tool_use", "hallucination", "format"}:
            raise ValueError("Declare a weight for each of the four quality groups")
        if any(v <= 0 for v in self.group_weights.values()):
            raise ValueError("Group weights must be positive")
        return self


class Pack(Model):
    schema_version: Literal[1] = 1
    name: str
    version: str
    criteria: list[Criterion]
    gates: list[GateRule] = Field(default_factory=list)
    bonuses: list[BonusRule] = Field(default_factory=list)
    base_prompts: dict[str, str]
    labeler_prompt: str
    profile: RewardProfile = Field(default_factory=RewardProfile)

    @model_validator(mode="after")
    def complete_plan(self):
        names = [c.name for c in [*self.criteria, *self.gates, *self.bonuses]]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate scoring component name")
        priorities = [gate.priority for gate in self.gates]
        if len(priorities) != len(set(priorities)):
            raise ValueError("Gate priorities must be unique")
        required = {
            "understanding",
            "process",
            "result",
            "explanation",
            "clarification",
            "conversation",
        }
        if set(self.base_prompts) != required:
            raise ValueError(f"Declare these Base prompts: {sorted(required)}")
        if not self.criteria:
            raise ValueError("Empty quality plan is not allowed")
        return self
