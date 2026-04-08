"""In-memory repository implementation for MVP orchestration state."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from app.domain.events import DomainEvent
from app.domain.models import (
    AttemptState,
    InterventionState,
    NodeKind,
    NodeState,
    NodeStatus,
    RunState,
    RunStatus,
    utc_now,
)
from app.schemas.api import CheckerConfig
from app.domain.policies import ensure_node_transition, ensure_run_transition
from app.state.repository import (
    DuplicateStateError,
    RunStateRepository,
    StateNotFoundError,
)


class InMemoryRunStateRepository(RunStateRepository):
    """Simple deterministic in-memory state repository."""

    def __init__(self) -> None:
        self._runs: dict[str, RunState] = {}
        self._nodes: dict[str, NodeState] = {}
        self._run_nodes: dict[str, list[str]] = defaultdict(list)
        self._attempts: dict[str, list[AttemptState]] = defaultdict(list)
        self._interventions: dict[str, list[InterventionState]] = defaultdict(list)
        self._events: dict[str, list[DomainEvent]] = defaultdict(list)
        self._event_seq: dict[str, int] = defaultdict(int)

    def create_run(self, run: RunState) -> None:
        if run.run_id in self._runs:
            raise DuplicateStateError(f"run already exists: {run.run_id}")
        self._runs[run.run_id] = run

    def get_run(self, run_id: str) -> RunState:
        run = self._runs.get(run_id)
        if run is None:
            raise StateNotFoundError(f"run not found: {run_id}")
        return run

    def update_run_status(self, run_id: str, status: RunStatus) -> RunState:
        run = self.get_run(run_id)
        ensure_run_transition(run.status, status)
        now = utc_now()
        completed_at = (
            now
            if status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED}
            else None
        )
        updated = replace(run, status=status, updated_at=now, completed_at=completed_at)
        self._runs[run_id] = updated
        return updated

    def list_runs(self) -> list[RunState]:
        return list(self._runs.values())

    def create_node(self, node: NodeState) -> None:
        if node.node_id in self._nodes:
            raise DuplicateStateError(f"node already exists: {node.node_id}")
        if node.run_id not in self._runs:
            raise StateNotFoundError(f"run not found for node: {node.run_id}")
        self._nodes[node.node_id] = node
        self._run_nodes[node.run_id].append(node.node_id)

    def get_node(self, node_id: str) -> NodeState:
        node = self._nodes.get(node_id)
        if node is None:
            raise StateNotFoundError(f"node not found: {node_id}")
        return node

    def list_run_nodes(self, run_id: str) -> list[NodeState]:
        if run_id not in self._runs:
            raise StateNotFoundError(f"run not found: {run_id}")
        return [self._nodes[node_id] for node_id in self._run_nodes[run_id]]

    def update_node_status(self, node_id: str, status: NodeStatus) -> NodeState:
        node = self.get_node(node_id)
        ensure_node_transition(node.status, status)
        now = utc_now()
        updated = replace(node, status=status, updated_at=now)
        self._nodes[node_id] = updated
        return updated

    def update_node_objective(self, node_id: str, objective: str) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(node, objective=objective, updated_at=utc_now())
        self._nodes[node_id] = updated
        return updated

    def update_node_persona(self, node_id: str, persona_id: str | None) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(node, persona_id=persona_id, updated_at=utc_now())
        self._nodes[node_id] = updated
        return updated

    def update_node_kind(self, node_id: str, node_kind: NodeKind) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(node, node_kind=node_kind, updated_at=utc_now())
        self._nodes[node_id] = updated
        return updated

    def update_node_checker_policy(self, node_id: str, policy: CheckerConfig) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(node, checker_policy=policy, updated_at=utc_now())
        self._nodes[node_id] = updated
        return updated

    def increment_node_attempt_count(self, node_id: str) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(
            node, attempt_count=node.attempt_count + 1, updated_at=utc_now()
        )
        self._nodes[node_id] = updated
        return updated

    def reset_checker_failures(self, node_id: str) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(node, consecutive_checker_failures=0, updated_at=utc_now())
        self._nodes[node_id] = updated
        return updated

    def increment_checker_failures(self, node_id: str) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(
            node,
            consecutive_checker_failures=node.consecutive_checker_failures + 1,
            updated_at=utc_now(),
        )
        self._nodes[node_id] = updated
        return updated

    def record_node_started(self, node_id: str) -> NodeState:
        node = self.get_node(node_id)
        ensure_node_transition(node.status, NodeStatus.RUNNING)
        node.mark_running()
        self._nodes[node_id] = node
        return node

    def record_node_first_token(self, node_id: str) -> NodeState:
        node = self.get_node(node_id)
        node.mark_first_token()
        self._nodes[node_id] = node
        return node

    def record_node_ended(self, node_id: str, final_status: NodeStatus) -> NodeState:
        node = self.get_node(node_id)
        ensure_node_transition(node.status, final_status)
        node.mark_ended(final_status=final_status)
        self._nodes[node_id] = node
        return node

    def create_attempt(self, attempt: AttemptState) -> None:
        _ = self.get_node(attempt.node_id)
        attempts = self._attempts[attempt.node_id]
        if any(existing.attempt_id == attempt.attempt_id for existing in attempts):
            raise DuplicateStateError(f"attempt already exists: {attempt.attempt_id}")
        attempts.append(attempt)

    def list_node_attempts(self, node_id: str) -> list[AttemptState]:
        _ = self.get_node(node_id)
        return list(self._attempts[node_id])

    def create_intervention(self, intervention: InterventionState) -> None:
        node = self.get_node(intervention.node_id)
        if node.run_id != intervention.run_id:
            raise ValueError(
                "intervention run_id does not match node run_id: "
                f"{intervention.run_id} != {node.run_id}"
            )
        interventions = self._interventions[intervention.node_id]
        if any(
            existing.intervention_id == intervention.intervention_id
            for existing in interventions
        ):
            raise DuplicateStateError(
                f"intervention already exists: {intervention.intervention_id}"
            )
        interventions.append(intervention)

    def list_node_interventions(self, node_id: str) -> list[InterventionState]:
        _ = self.get_node(node_id)
        return list(self._interventions[node_id])

    def append_event(self, event: DomainEvent) -> DomainEvent:
        if event.run_id not in self._runs:
            raise StateNotFoundError(f"run not found for event: {event.run_id}")
        next_seq = self._event_seq[event.run_id] + 1
        self._event_seq[event.run_id] = next_seq
        stored = replace(event, seq=next_seq, ts=event.ts or utc_now())
        self._events[event.run_id].append(stored)
        return stored

    def list_run_events(self, run_id: str, after_seq: int = 0) -> list[DomainEvent]:
        if run_id not in self._runs:
            raise StateNotFoundError(f"run not found: {run_id}")
        return [event for event in self._events[run_id] if event.seq > after_seq]


__all__ = ["DuplicateStateError", "InMemoryRunStateRepository", "StateNotFoundError"]
