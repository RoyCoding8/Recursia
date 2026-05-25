"""Orchestrator runtime coordinating recursive execution and run terminal state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from app.domain.events import DomainEvent, DomainEventType
from app.domain.models import NodeKind, NodeState, NodeStatus, RunState, RunStatus
from app.schemas.api import RunConfig
from app.services.event_stream import EventStreamService
from app.services.executor import (
    ExecutionTerminal,
    NodeExecutionResult,
    RecursiveExecutor,
)
from app.state.repository import RunStateRepository

_TERMINAL_MAP: dict[str, tuple[RunStatus, DomainEventType, DomainEventType | None]] = {
    ExecutionTerminal.COMPLETED: (RunStatus.COMPLETED, DomainEventType.RUN_COMPLETED, None),
    ExecutionTerminal.BLOCKED_HUMAN: (RunStatus.BLOCKED_HUMAN, DomainEventType.RUN_STATUS_CHANGED, DomainEventType.NODE_BLOCKED_HUMAN),
    ExecutionTerminal.FAILED: (RunStatus.FAILED, DomainEventType.RUN_FAILED, None),
}


def _default_id_factory() -> str:
    return uuid4().hex


@dataclass(slots=True, frozen=True)
class OrchestrationResult:
    """Final run result from orchestration execution."""

    run_id: str
    root_node_id: str
    status: str
    error: str | None = None
    output: object | None = None


@dataclass(slots=True, frozen=True)
class CreatedRun:
    """Created run identifiers before execution begins."""

    run_id: str
    root_node_id: str


class Orchestrator:
    """Coordinates run lifecycle and delegates provider-driven recursion."""

    def __init__(
        self,
        *,
        repository: RunStateRepository,
        executor: RecursiveExecutor,
        event_stream: EventStreamService | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._event_stream = event_stream
        self._id_factory = id_factory or _default_id_factory

    def create_run(
        self,
        *,
        objective: str,
        config: RunConfig | None = None,
        base_persona_id: str | None = None,
    ) -> CreatedRun:
        """Create run+root node and emit initial creation events."""
        run_id = f"run_{self._id_factory()}"
        root_node_id = f"node_{self._id_factory()}"
        run = RunState(run_id=run_id, objective=objective, config=config or RunConfig())
        root = NodeState(
            node_id=root_node_id,
            run_id=run_id,
            objective=objective,
            parent_id=None,
            depth=0,
            node_kind=NodeKind.DIVIDER,
            checker_policy=run.config.checker,
            persona_id=base_persona_id,
        )
        self._repository.create_run(run)
        self._repository.create_node(root)
        self._append_event(
            run_id=run_id,
            node_id=root_node_id,
            event_type=DomainEventType.RUN_CREATED,
            payload={
                "run": {
                    "runId": run.run_id,
                    "objective": run.objective,
                    "status": run.status.value,
                    "rootNodeId": root_node_id,
                    "createdAt": run.created_at.isoformat(),
                    "updatedAt": run.updated_at.isoformat(),
                }
            },
        )
        self._append_event(
            run_id=run_id,
            node_id=root_node_id,
            event_type=DomainEventType.NODE_CREATED,
            payload={
                "node": {
                    "nodeId": root.node_id,
                    "runId": root.run_id,
                    "parentNodeId": root.parent_id,
                    "objective": root.objective,
                    "status": root.status.value,
                    "personaId": root.persona_id,
                    "depth": root.depth,
                    "nodeKind": root.node_kind.value,
                    "ttftMs": root.ttft_ms,
                    "durationMs": root.duration_ms,
                    "checkerFailureCount": root.consecutive_checker_failures,
                },
                "parentNodeId": root.parent_id,
                "relation": "child",
            },
        )
        return CreatedRun(run_id=run_id, root_node_id=root_node_id)

    def start_run(
        self,
        *,
        objective: str,
        config: RunConfig | None = None,
        base_persona_id: str | None = None,
    ) -> OrchestrationResult:
        """Create run+root node and execute to terminal state."""
        created = self.create_run(
            objective=objective,
            config=config,
            base_persona_id=base_persona_id,
        )
        return self.run_existing(
            run_id=created.run_id, root_node_id=created.root_node_id
        )

    def run_existing(self, *, run_id: str, root_node_id: str) -> OrchestrationResult:
        """Execute already-created run root and compute deterministic terminal state."""
        self._mark_run_running(run_id=run_id, node_id=root_node_id)

        try:
            node_result = self._executor.execute_node(
                run_id=run_id, node_id=root_node_id
            )
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            self._repository.update_run_status(run_id, RunStatus.FAILED)
            self._append_event(
                run_id=run_id, node_id=root_node_id,
                event_type=DomainEventType.RUN_FAILED,
                payload={"status": RunStatus.FAILED.value, "error": str(exc)},
            )
            return OrchestrationResult(
                run_id=run_id, root_node_id=root_node_id,
                status=RunStatus.FAILED.value, error=str(exc),
            )

        return self._finalize_run_from_node_result(
            run_id=run_id, root_node_id=root_node_id, node_result=node_result,
        )

    def resume_from_node(self, *, run_id: str, node_id: str) -> OrchestrationResult:
        """Resume execution from a specific node after intervention."""
        root_node_id = self._resolve_root_node_id(run_id)
        self._mark_run_running(run_id=run_id, node_id=root_node_id)

        try:
            node_result = self._executor.execute_node(run_id=run_id, node_id=node_id)
        except Exception as exc:
            self._finalize_run(run_id, node_id, RunStatus.FAILED, str(exc))
            self._executor.clear_run(run_id)
            return OrchestrationResult(run_id=run_id, root_node_id=root_node_id,
                                       status=RunStatus.FAILED.value, error=str(exc))

        if node_result.status == ExecutionTerminal.BLOCKED_HUMAN:
            self._finalize_run(run_id, node_id, RunStatus.BLOCKED_HUMAN, node_result.error)
            return OrchestrationResult(run_id=run_id, root_node_id=root_node_id,
                                       status=RunStatus.BLOCKED_HUMAN.value,
                                       error=node_result.error, output=node_result.output)

        if node_result.status == ExecutionTerminal.FAILED:
            self._finalize_run(run_id, node_id, RunStatus.FAILED, node_result.error)
            self._executor.clear_run(run_id)
            return OrchestrationResult(run_id=run_id, root_node_id=root_node_id,
                                       status=RunStatus.FAILED.value,
                                       error=node_result.error, output=node_result.output)

        run_nodes = self._repository.list_run_nodes(run_id)
        aggregate = self._aggregate_run_status(run_nodes)
        self._finalize_run(run_id, root_node_id, aggregate)
        if aggregate != RunStatus.BLOCKED_HUMAN:
            self._executor.clear_run(run_id)
        return OrchestrationResult(run_id=run_id, root_node_id=root_node_id,
                                   status=aggregate.value, output=node_result.output)

    def _finalize_run(self, run_id: str, node_id: str, status: RunStatus,
                      error: str | None = None) -> None:
        self._repository.update_run_status(run_id, status)
        entry = _TERMINAL_MAP.get(status.value, _TERMINAL_MAP[ExecutionTerminal.FAILED])
        self._append_event(run_id=run_id, node_id=node_id, event_type=entry[1],
                           payload={"status": status.value, **({"error": error} if error else {})})
        if entry[2]:
            self._append_event(run_id=run_id, node_id=node_id, event_type=entry[2],
                               payload={"status": status.value, "error": error})

    def _finalize_run_from_node_result(
        self, *, run_id: str, root_node_id: str, node_result: NodeExecutionResult,
    ) -> OrchestrationResult:
        status_map = {
            ExecutionTerminal.COMPLETED: RunStatus.COMPLETED,
            ExecutionTerminal.BLOCKED_HUMAN: RunStatus.BLOCKED_HUMAN,
        }
        run_status = status_map.get(node_result.status, RunStatus.FAILED)
        self._finalize_run(run_id, root_node_id, run_status, node_result.error)
        if run_status != RunStatus.BLOCKED_HUMAN:
            self._executor.clear_run(run_id)
        return OrchestrationResult(
            run_id=run_id, root_node_id=root_node_id,
            status=run_status.value, error=node_result.error, output=node_result.output,
        )

    @staticmethod
    def _aggregate_run_status(nodes: list[NodeState]) -> RunStatus:
        statuses = {n.status for n in nodes}
        if all(s == NodeStatus.COMPLETED for s in statuses):
            return RunStatus.COMPLETED
        if NodeStatus.BLOCKED_HUMAN in statuses:
            return RunStatus.BLOCKED_HUMAN
        return RunStatus.RUNNING

    def _append_event(
        self,
        *,
        run_id: str,
        node_id: str,
        event_type: DomainEventType,
        payload: dict[str, object],
    ) -> None:
        event = DomainEvent(
            event_id=f"evt_{self._id_factory()}",
            run_id=run_id,
            node_id=node_id,
            type=event_type,
            payload=payload,
        )
        if self._event_stream is not None:
            self._event_stream.publish(event)
            return
        self._repository.append_event(event)

    def _mark_run_running(self, *, run_id: str, node_id: str) -> None:
        run = self._repository.get_run(run_id)
        if run.status != RunStatus.RUNNING:
            self._repository.update_run_status(run_id, RunStatus.RUNNING)
            self._append_event(
                run_id=run_id,
                node_id=node_id,
                event_type=DomainEventType.RUN_STATUS_CHANGED,
                payload={"status": RunStatus.RUNNING.value},
            )

    def get_root_output(self, run_id: str) -> object | None:
        """Return the final output for a run's root node, if available."""
        root_node_id = self._resolve_root_node_id(run_id)
        return self._executor.get_output(root_node_id)

    def _resolve_root_node_id(self, run_id: str) -> str:
        for node in self._repository.list_run_nodes(run_id):
            if node.parent_id is None:
                return node.node_id
        raise KeyError(f"root node not found for run: {run_id}")


__all__ = ["CreatedRun", "OrchestrationResult", "Orchestrator"]
