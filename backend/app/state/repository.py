"""Repository abstraction for run state persistence and event log access."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.events import DomainEvent
from app.domain.models import (
    AttemptState,
    InterventionState,
    NodeKind,
    NodeState,
    NodeStatus,
    RunState,
    RunStatus,
)
from app.schemas.api import CheckerConfig


class StateNotFoundError(KeyError):
    """Raised when requested run/node state does not exist."""


class DuplicateStateError(ValueError):
    """Raised when attempting to insert duplicate state keys."""


class RunStateRepository(ABC):
    """Abstract repository interface for orchestration state."""

    @abstractmethod
    def create_run(self, run: RunState) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_run(self, run_id: str) -> RunState:
        raise NotImplementedError

    @abstractmethod
    def update_run_status(self, run_id: str, status: RunStatus) -> RunState:
        raise NotImplementedError

    @abstractmethod
    def list_runs(self) -> list[RunState]:
        raise NotImplementedError

    @abstractmethod
    def create_node(self, node: NodeState) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_node(self, node_id: str) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def list_run_nodes(self, run_id: str) -> list[NodeState]:
        raise NotImplementedError

    @abstractmethod
    def update_node_status(self, node_id: str, status: NodeStatus) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def update_node_objective(self, node_id: str, objective: str) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def update_node_persona(self, node_id: str, persona_id: str | None) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def update_node_kind(self, node_id: str, node_kind: NodeKind) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def update_node_checker_policy(self, node_id: str, policy: CheckerConfig) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def increment_node_attempt_count(self, node_id: str) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def reset_checker_failures(self, node_id: str) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def increment_checker_failures(self, node_id: str) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def record_node_started(self, node_id: str) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def record_node_first_token(self, node_id: str) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def record_node_ended(self, node_id: str, final_status: NodeStatus) -> NodeState:
        raise NotImplementedError

    @abstractmethod
    def create_attempt(self, attempt: AttemptState) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_node_attempts(self, node_id: str) -> list[AttemptState]:
        raise NotImplementedError

    @abstractmethod
    def create_intervention(self, intervention: InterventionState) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_node_interventions(self, node_id: str) -> list[InterventionState]:
        raise NotImplementedError

    @abstractmethod
    def delete_children_of(self, run_id: str, parent_node_id: str) -> int:
        """Recursively delete all descendant nodes of *parent_node_id*.

        Removes every node whose ancestor chain includes *parent_node_id*,
        together with their associated attempts and interventions.
        Events referencing deleted nodes keep ``node_id = NULL`` (audit trail).

        Returns the count of deleted nodes.
        """
        raise NotImplementedError

    @abstractmethod
    def delete_node(self, run_id: str, node_id: str) -> None:
        """Delete a single node (not the root) and its associated data."""
        raise NotImplementedError

    @abstractmethod
    def append_event(self, event: DomainEvent) -> DomainEvent:
        """Append event and assign deterministic per-run sequence."""
        raise NotImplementedError

    @abstractmethod
    def list_run_events(self, run_id: str, after_seq: int = 0) -> list[DomainEvent]:
        raise NotImplementedError


__all__ = ["DuplicateStateError", "RunStateRepository", "StateNotFoundError"]
