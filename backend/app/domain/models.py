"""Domain state entities for runs, nodes, attempts, events, and interventions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import InterventionAction, NodeKind, NodeStatus, RunStatus
from app.schemas.api import CheckerConfig, RunConfig
from app.schemas.contracts import CheckerResult

# Re-export enums so existing `from app.domain.models import NodeStatus` still works.
__all_enums__ = [InterventionAction, NodeKind, NodeStatus, RunStatus]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


@dataclass(slots=True, frozen=True)
class NodeContext:
    root_objective: str
    parent_chain: tuple[str, ...] = ()
    sibling_objectives: tuple[str, ...] = ()
    completed_sibling_summaries: tuple[str, ...] = ()
    boundary_constraints: tuple[str, ...] = ()
    checker_feedback: str | None = None

    def child(self, objective: str, siblings: list[str] | None = None,
              constraints: list[str] | None = None) -> NodeContext:
        return NodeContext(
            root_objective=self.root_objective,
            parent_chain=(*self.parent_chain, objective),
            sibling_objectives=tuple(siblings or ()),
            boundary_constraints=tuple(constraints or ()),
        )

    def with_sibling_output(self, summary: str) -> NodeContext:
        return NodeContext(
            root_objective=self.root_objective,
            parent_chain=self.parent_chain,
            sibling_objectives=self.sibling_objectives,
            completed_sibling_summaries=(*self.completed_sibling_summaries, summary),
            boundary_constraints=self.boundary_constraints,
            checker_feedback=self.checker_feedback,
        )

    def with_checker_feedback(self, fix: str, violations: list[str]) -> NodeContext:
        feedback = f"Previous attempt failed validation.\nFix: {fix}"
        if violations:
            feedback += "\nViolations: " + "; ".join(violations)
        return NodeContext(
            root_objective=self.root_objective,
            parent_chain=self.parent_chain,
            sibling_objectives=self.sibling_objectives,
            completed_sibling_summaries=self.completed_sibling_summaries,
            boundary_constraints=self.boundary_constraints,
            checker_feedback=feedback,
        )

    def to_prompt_block(self) -> str:
        parts = [f"Root goal: {self.root_objective}"]
        if self.parent_chain:
            parts.append(f"Decomposition path: {' → '.join(self.parent_chain)}")
        if self.sibling_objectives:
            parts.append(f"Sibling tasks: {'; '.join(self.sibling_objectives)}")
        if self.completed_sibling_summaries:
            parts.append("Completed siblings:\n" + "\n".join(
                f"  - {s}" for s in self.completed_sibling_summaries))
        if self.boundary_constraints:
            parts.append("Constraints: " + "; ".join(self.boundary_constraints))
        if self.checker_feedback:
            parts.append(f"⚠ CHECKER FEEDBACK (fix this):\n{self.checker_feedback}")
        return "\n".join(parts)


@dataclass(slots=True)
class RunState:
    run_id: str
    objective: str
    config: RunConfig = field(default_factory=RunConfig)
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None


@dataclass(slots=True)
class NodeState:
    node_id: str
    run_id: str
    objective: str
    parent_id: str | None = None
    depth: int = 0
    node_kind: NodeKind = NodeKind.WORK
    status: NodeStatus = NodeStatus.QUEUED
    persona_id: str | None = None
    checker_policy: CheckerConfig = field(default_factory=CheckerConfig)
    attempt_count: int = 0
    consecutive_checker_failures: int = 0
    ttft_ms: int | None = None
    duration_ms: int | None = None
    started_at: datetime | None = None
    first_token_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def mark_running(self, at: datetime | None = None) -> None:
        """Move node to running and capture start timestamp."""
        ts = at or utc_now()
        self.status = NodeStatus.RUNNING
        self.started_at = self.started_at or ts
        self.updated_at = ts

    def mark_first_token(self, at: datetime | None = None) -> None:
        """Capture first token timestamp and TTFT in milliseconds."""
        ts = at or utc_now()
        if self.first_token_at is not None:
            return
        self.first_token_at = ts
        if self.started_at is not None:
            delta = self.first_token_at - self.started_at
            self.ttft_ms = max(int(delta.total_seconds() * 1000), 0)
        self.updated_at = ts

    def mark_ended(self, final_status: NodeStatus, at: datetime | None = None) -> None:
        """Set terminal-like status and compute duration if possible."""
        ts = at or utc_now()
        self.status = final_status
        self.ended_at = ts
        if self.started_at is not None:
            delta = self.ended_at - self.started_at
            self.duration_ms = max(int(delta.total_seconds() * 1000), 0)
        self.updated_at = ts


@dataclass(slots=True)
class AttemptState:
    attempt_id: str
    node_id: str
    attempt_index: int
    input_snapshot: dict[str, Any]
    output_snapshot: dict[str, Any] | list[Any] | str | int | float | bool | None = None
    checker_result: CheckerResult | None = None
    error: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class InterventionState:
    intervention_id: str
    run_id: str
    node_id: str
    action: InterventionAction
    actor: str
    note: str | None = None
    payload_delta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


__all__ = [
    "AttemptState",
    "InterventionAction",
    "InterventionState",
    "NodeContext",
    "NodeKind",
    "NodeState",
    "NodeStatus",
    "RunState",
    "RunStatus",
    "utc_now",
]
