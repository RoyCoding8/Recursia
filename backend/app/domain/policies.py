"""Domain policy helpers for status transitions and checker thresholds."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import NodeStatus, RunStatus


class InvalidTransitionError(ValueError):
    """Raised when an invalid state transition is attempted."""


RUN_ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELED, RunStatus.FAILED},
    RunStatus.RUNNING: {
        RunStatus.BLOCKED_HUMAN,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELED,
    },
    RunStatus.BLOCKED_HUMAN: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELED: set(),
}


NODE_ALLOWED_TRANSITIONS: dict[NodeStatus, set[NodeStatus]] = {
    NodeStatus.QUEUED: {NodeStatus.RUNNING, NodeStatus.ERROR},
    NodeStatus.RUNNING: {
        NodeStatus.WAITING_CHECK,
        NodeStatus.COMPLETED,
        NodeStatus.ERROR,
    },
    NodeStatus.WAITING_CHECK: {
        NodeStatus.FAILED_CHECK,
        NodeStatus.COMPLETED,
        NodeStatus.ERROR,
    },
    NodeStatus.FAILED_CHECK: {
        NodeStatus.RUNNING,
        NodeStatus.BLOCKED_HUMAN,
        NodeStatus.ERROR,
    },
    NodeStatus.BLOCKED_HUMAN: {
        NodeStatus.RUNNING,
        NodeStatus.COMPLETED,
        NodeStatus.ERROR,
    },
    NodeStatus.COMPLETED: set(),
    NodeStatus.ERROR: set(),
}


def ensure_run_transition(current: RunStatus, next_status: RunStatus) -> None:
    """Validate run transition according to domain policy."""
    if next_status not in RUN_ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(
            f"invalid run transition: {current.value} -> {next_status.value}"
        )


def ensure_node_transition(current: NodeStatus, next_status: NodeStatus) -> None:
    """Validate node transition according to domain policy."""
    if next_status not in NODE_ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(
            f"invalid node transition: {current.value} -> {next_status.value}"
        )


@dataclass(frozen=True, slots=True)
class CheckerFailurePolicy:
    """Simple checker failure threshold policy."""

    block_after_consecutive_failures: int = 3

    def should_block(self, consecutive_failures: int) -> bool:
        return consecutive_failures >= self.block_after_consecutive_failures


DEFAULT_CHECKER_FAILURE_POLICY = CheckerFailurePolicy()


__all__ = [
    "CheckerFailurePolicy",
    "DEFAULT_CHECKER_FAILURE_POLICY",
    "InvalidTransitionError",
    "NODE_ALLOWED_TRANSITIONS",
    "RUN_ALLOWED_TRANSITIONS",
    "ensure_node_transition",
    "ensure_run_transition",
]
