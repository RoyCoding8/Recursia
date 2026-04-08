"""Shared API request/response models for backend endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

# Canonical enum definitions live in domain.enums; re-export for API consumers.
from app.domain.enums import NodeStatus, RunStatus


class CheckerConfig(BaseModel):
    enabled: bool = True
    node_level: bool = True
    merge_level: bool = True
    max_retries_per_node: int = Field(default=3, ge=0)


class StreamConfig(BaseModel):
    mode: Literal["sse", "websocket"] = "sse"


class WorkspaceConfig(BaseModel):
    output_dir: str | None = None  # resolved at runtime if None


class RunConfig(BaseModel):
    checker: CheckerConfig = Field(default_factory=CheckerConfig)
    max_depth: int = Field(default=8, ge=1)
    max_children_per_node: int = Field(default=10, ge=1)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)


class CreateRunRequest(BaseModel):
    objective: str = Field(min_length=1)
    config: RunConfig = Field(default_factory=RunConfig)
    base_persona_id: str | None = None


class CreateRunResponse(BaseModel):
    run_id: str
    status: RunStatus
    root_node_id: str


class RunView(BaseModel):
    run_id: str
    objective: str
    status: RunStatus
    root_node_id: str
    created_at: str | None = None
    updated_at: str | None = None


class PersonaSummary(BaseModel):
    persona_id: str
    name: str
    description: str


class NodeView(BaseModel):
    node_id: str
    run_id: str
    parent_id: str | None = None
    depth: int = Field(ge=0)
    objective: str
    status: NodeStatus
    node_kind: str | None = None
    persona_id: str | None = None
    ttft_ms: int | None = None
    duration_ms: int | None = None
    checker_failure_count: int | None = None


class EdgeView(BaseModel):
    source: str
    target: str
    relation: Literal["child", "merge_input"] = "child"


class GetRunResponse(BaseModel):
    run: RunView
    nodes: list[NodeView] = Field(default_factory=list)
    edges: list[EdgeView] = Field(default_factory=list)


class RunValidationResult(BaseModel):
    source: Literal["checker"] = "checker"
    verdict: Literal["pass", "fail"]
    reason: str
    suggested_fix: str | None = None
    confidence: float | None = None
    violations: list[str] = Field(default_factory=list)


class RunResultResponse(BaseModel):
    run_id: str
    status: RunStatus
    output: object | None = None
    error: str | None = None
    validation: RunValidationResult | None = None


class RetryIntervention(BaseModel):
    action: Literal["retry"]
    note: str | None = None


class EditAndRetryIntervention(BaseModel):
    action: Literal["edit_and_retry"]
    edited_objective: str = Field(min_length=1)
    edited_context: str | None = None
    note: str | None = None


class SkipWithJustificationIntervention(BaseModel):
    action: Literal["skip_with_justification"]
    justification: str = Field(min_length=1)


InterventionRequest = Annotated[
    RetryIntervention | EditAndRetryIntervention | SkipWithJustificationIntervention,
    Field(discriminator="action"),
]


class InterventionResponse(BaseModel):
    accepted: bool
    node_status: NodeStatus
    intervention_id: str


__all__ = [
    "CheckerConfig",
    "CreateRunRequest",
    "CreateRunResponse",
    "EdgeView",
    "EditAndRetryIntervention",
    "GetRunResponse",
    "InterventionRequest",
    "InterventionResponse",
    "NodeStatus",
    "NodeView",
    "PersonaSummary",
    "RetryIntervention",
    "RunConfig",
    "RunResultResponse",
    "RunValidationResult",
    "RunStatus",
    "RunView",
    "SkipWithJustificationIntervention",
    "StreamConfig",
    "WorkspaceConfig",
]
