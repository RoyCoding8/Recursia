"""Domain event types and event log records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.domain.models import utc_now


class DomainEventType(str, Enum):
    RUN_CREATED = "run.created"
    RUN_STATUS_CHANGED = "run.status_changed"
    NODE_CREATED = "node.created"
    NODE_STATUS_CHANGED = "node.status_changed"
    NODE_TOKEN = "node.token"
    NODE_TTFT_RECORDED = "node.ttft_recorded"
    CHECKER_STARTED = "checker.started"
    CHECKER_COMPLETED = "checker.completed"
    MERGE_STARTED = "merge.started"
    MERGE_COMPLETED = "merge.completed"
    NODE_BLOCKED_HUMAN = "node.blocked_human"
    NODE_INTERVENTION_APPLIED = "node.intervention_applied"
    WORK_STEP_STARTED = "work.step_started"
    SUBTREE_PRUNED = "node.subtree_pruned"
    WORK_STEP_COMPLETED = "work.step_completed"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


@dataclass(slots=True)
class DomainEvent:
    event_id: str
    run_id: str
    type: DomainEventType
    payload: dict[str, Any] = field(default_factory=dict)
    node_id: str | None = None
    seq: int = 0
    ts: datetime = field(default_factory=utc_now)


__all__ = ["DomainEvent", "DomainEventType"]
