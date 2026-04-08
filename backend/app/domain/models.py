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
    "NodeKind",
    "NodeState",
    "NodeStatus",
    "RunState",
    "RunStatus",
    "utc_now",
]
