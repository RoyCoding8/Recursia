"""Canonical enum definitions shared across domain and API layers."""

from __future__ import annotations

from enum import Enum


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED_HUMAN = "blocked_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class NodeStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_CHECK = "waiting_check"
    FAILED_CHECK = "failed_check"
    BLOCKED_HUMAN = "blocked_human"
    COMPLETED = "completed"
    ERROR = "error"


class NodeKind(str, Enum):
    DIVIDER = "divider"
    WORK = "work"
    MERGE = "merge"


class InterventionAction(str, Enum):
    RETRY = "retry"
    EDIT_AND_RETRY = "edit_and_retry"
    SKIP_WITH_JUSTIFICATION = "skip_with_justification"


__all__ = [
    "InterventionAction",
    "NodeKind",
    "NodeStatus",
    "RunStatus",
]
