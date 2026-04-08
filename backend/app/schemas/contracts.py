"""Typed service contracts for divider/checker/merger boundaries."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class DividerDecision(str, Enum):
    BASE_CASE = "BASE_CASE"
    RECURSIVE_CASE = "RECURSIVE_CASE"


class WorkPlanStep(BaseModel):
    step: int = Field(ge=1)
    description: str = Field(min_length=1)


class DividerChild(BaseModel):
    objective: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    suggested_persona: str | None = None
    interface_contract: str | None = None
    needs_qa: bool = True  # divider can opt children out of QA


class DividerBaseCase(BaseModel):
    decision: Literal[DividerDecision.BASE_CASE]
    rationale: str = Field(min_length=1)
    work_plan: list[WorkPlanStep] = Field(min_length=1)
    suggested_persona: str | None = None
    needs_qa: bool = True  # divider can opt base-case out of QA


class DividerRecursiveCase(BaseModel):
    decision: Literal[DividerDecision.RECURSIVE_CASE]
    rationale: str = Field(min_length=1)
    children: list[DividerChild] = Field(min_length=2)


DividerResult = Annotated[
    DividerBaseCase | DividerRecursiveCase,
    Field(discriminator="decision"),
]


class CheckerVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class CheckerResult(BaseModel):
    verdict: CheckerVerdict
    reason: str = Field(min_length=1)
    suggested_fix: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    violations: list[str] = Field(default_factory=list)


class MergeChildOutput(BaseModel):
    node_id: str
    persona_id: str
    output: dict[str, object] | list[object] | str | int | float | bool | None
    boundary_contract: str | None = None


class MergeRequest(BaseModel):
    parent_objective: str = Field(min_length=1)
    child_outputs: list[MergeChildOutput] = Field(min_length=2)


class ConflictResolution(BaseModel):
    conflict: str = Field(min_length=1)
    chosen_approach: str = Field(min_length=1)
    rejected_approach: str | None = None
    rationale: str = Field(min_length=1)


class MergeResponse(BaseModel):
    merged_output: dict[str, object] | list[object] | str | int | float | bool | None
    conflict_resolutions: list[ConflictResolution] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)


__all__ = [
    "CheckerResult",
    "CheckerVerdict",
    "ConflictResolution",
    "DividerBaseCase",
    "DividerChild",
    "DividerDecision",
    "DividerRecursiveCase",
    "DividerResult",
    "MergeChildOutput",
    "MergeRequest",
    "MergeResponse",
    "WorkPlanStep",
]
