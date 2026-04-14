"""Regression test: intervention retry on recursive-case nodes must prune old children.

Reproduces the bug where ``_create_child_nodes`` mints fresh UUIDs without
deleting the stale children from a previous attempt, causing the UI to show
duplicates.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.domain.events import DomainEventType
from app.domain.models import NodeContext, NodeKind, NodeState, NodeStatus, RunState
from app.schemas.api import CheckerConfig, RunConfig
from app.schemas.contracts import DividerDecision
from app.services.divider import (
    DividerServiceResult,
    RecursiveDecomposition,
    RecursiveChildSpec,
)
from app.services.executor import (
    ExecutionTerminal,
    NodeExecutionResult,
    RecursiveExecutor,
    WorkExecutionResult,
)
from app.services.stubs import DeterministicBaseCaseWorker, DeterministicPersonaRouter
from app.state.memory_repo import InMemoryRunStateRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _TwoStageDivider:
    """First call returns RECURSIVE_CASE with N children.

    Subsequent calls (for the children themselves) return BASE_CASE so the
    executor can finish without recursing further.
    """

    def __init__(self, child_specs: list[RecursiveChildSpec]) -> None:
        self._child_specs = child_specs
        self._call_count = 0

    def divide(
        self, *, objective: str, depth: int, node_context: NodeContext | None = None
    ) -> DividerServiceResult:
        self._call_count += 1
        if depth == 0:
            return DividerServiceResult(
                decision=DividerDecision.RECURSIVE_CASE,
                recursive_case=RecursiveDecomposition(
                    rationale="test split",
                    children=self._child_specs,
                ),
                attempts_used=1,
            )
        # Children are base cases
        from app.services.divider import BaseCaseWorkPlan

        return DividerServiceResult(
            decision=DividerDecision.BASE_CASE,
            base_case=BaseCaseWorkPlan(
                rationale="leaf",
                work_plan=[{"step": 1, "description": objective}],
                suggested_persona=None,
            ),
            attempts_used=1,
        )


def _make_child_specs(n: int = 2) -> list[RecursiveChildSpec]:
    return [
        RecursiveChildSpec(
            objective=f"child-objective-{i}",
            dependencies=[],
            suggested_persona=None,
            interface_contract=None,
        )
        for i in range(1, n + 1)
    ]


def _build_executor(
    repo: InMemoryRunStateRepository,
    child_specs: list[RecursiveChildSpec],
    *,
    captured_events: list[tuple[str, str, DomainEventType, dict[str, object]]]
    | None = None,
    id_counter: list[int] | None = None,
) -> RecursiveExecutor:
    if id_counter is None:
        id_counter = [0]

    def _id() -> str:
        id_counter[0] += 1
        return f"id{id_counter[0]}"

    def _emitter(
        run_id: str,
        node_id: str,
        event_type: DomainEventType,
        payload: dict[str, object],
    ) -> None:
        if captured_events is not None:
            captured_events.append((run_id, node_id, event_type, payload))

    return RecursiveExecutor(
        repository=repo,
        divider=_TwoStageDivider(child_specs),
        persona_router=DeterministicPersonaRouter(),
        worker=DeterministicBaseCaseWorker(),
        event_emitter=_emitter if captured_events is not None else None,
        id_factory=_id,
    )


def _setup_run(repo: InMemoryRunStateRepository) -> tuple[str, str]:
    """Create a run + root node and return (run_id, root_node_id)."""
    run_id = "run_test"
    root_id = "node_root"
    repo.create_run(RunState(run_id=run_id, objective="test"))
    repo.create_node(
        NodeState(
            node_id=root_id,
            run_id=run_id,
            parent_id=None,
            depth=0,
            objective="test",
            node_kind=NodeKind.DIVIDER,
        )
    )
    return run_id, root_id

class TestDeleteChildrenOf:
    """Unit tests for repository.delete_children_of (in-memory)."""

    def test_deletes_direct_children(self) -> None:
        repo = InMemoryRunStateRepository()
        run_id, root_id = _setup_run(repo)

        # Create 2 children
        for i in range(1, 3):
            repo.create_node(
                NodeState(
                    node_id=f"child_{i}",
                    run_id=run_id,
                    parent_id=root_id,
                    depth=1,
                    objective=f"child-{i}",
                    node_kind=NodeKind.DIVIDER,
                )
            )

        assert len(repo.list_run_nodes(run_id)) == 3  # root + 2 children

        deleted = repo.delete_children_of(run_id, root_id)

        assert deleted == 2
        nodes = repo.list_run_nodes(run_id)
        assert len(nodes) == 1
        assert nodes[0].node_id == root_id

    def test_deletes_grandchildren_recursively(self) -> None:
        repo = InMemoryRunStateRepository()
        run_id, root_id = _setup_run(repo)

        repo.create_node(
            NodeState(
                node_id="child_1",
                run_id=run_id,
                parent_id=root_id,
                depth=1,
                objective="child-1",
                node_kind=NodeKind.DIVIDER,
            )
        )
        repo.create_node(
            NodeState(
                node_id="grandchild_1",
                run_id=run_id,
                parent_id="child_1",
                depth=2,
                objective="grandchild-1",
                node_kind=NodeKind.DIVIDER,
            )
        )

        deleted = repo.delete_children_of(run_id, root_id)

        assert deleted == 2  # child + grandchild
        assert len(repo.list_run_nodes(run_id)) == 1

    def test_noop_when_no_children(self) -> None:
        repo = InMemoryRunStateRepository()
        run_id, root_id = _setup_run(repo)

        deleted = repo.delete_children_of(run_id, root_id)

        assert deleted == 0
        assert len(repo.list_run_nodes(run_id)) == 1

    def test_preserves_parent_node(self) -> None:
        repo = InMemoryRunStateRepository()
        run_id, root_id = _setup_run(repo)

        repo.create_node(
            NodeState(
                node_id="child_x",
                run_id=run_id,
                parent_id=root_id,
                depth=1,
                objective="x",
                node_kind=NodeKind.DIVIDER,
            )
        )

        repo.delete_children_of(run_id, root_id)

        # Parent must still exist
        parent = repo.get_node(root_id)
        assert parent.node_id == root_id


class TestRetryPrunesChildren:
    """Integration test: re-executing a recursive node prunes old children."""

    def test_retry_replaces_children(self) -> None:
        """Execute root → children created → re-execute → old children pruned,
        new children created with fresh IDs."""
        repo = InMemoryRunStateRepository()
        run_id, root_id = _setup_run(repo)
        specs = _make_child_specs(2)
        shared_counter: list[int] = [0]

        # --- First execution ---
        executor = _build_executor(repo, specs, id_counter=shared_counter)
        result = executor.execute_node(run_id=run_id, node_id=root_id)
        assert result.status == ExecutionTerminal.COMPLETED

        nodes_after_first = repo.list_run_nodes(run_id)
        first_child_ids = {
            n.node_id for n in nodes_after_first if n.parent_id == root_id
        }
        assert len(first_child_ids) == 2

        # --- Simulate intervention: reset root to RUNNING ---
        # (In production this happens via apply_intervention endpoint)
        root = repo.get_node(root_id)
        # Force root back to a re-executable state
        repo._nodes[root_id] = replace(root, status=NodeStatus.RUNNING)

        # --- Second execution (retry) ---
        events: list[tuple[str, str, DomainEventType, dict[str, object]]] = []
        executor2 = _build_executor(repo, specs, captured_events=events, id_counter=shared_counter)
        result2 = executor2.execute_node(run_id=run_id, node_id=root_id)
        assert result2.status == ExecutionTerminal.COMPLETED

        nodes_after_retry = repo.list_run_nodes(run_id)
        second_child_ids = {
            n.node_id for n in nodes_after_retry if n.parent_id == root_id
        }

        # Old children must be gone, new children present
        assert first_child_ids.isdisjoint(second_child_ids), (
            f"Stale children survived retry: {first_child_ids & second_child_ids}"
        )
        assert len(second_child_ids) == 2

        # Total nodes = root + 2 new children (no duplicates)
        assert len(nodes_after_retry) == 3

    def test_subtree_pruned_event_emitted(self) -> None:
        """A SUBTREE_PRUNED event must fire when stale children exist."""
        repo = InMemoryRunStateRepository()
        run_id, root_id = _setup_run(repo)
        specs = _make_child_specs(2)
        shared_counter: list[int] = [0]

        # First run
        executor = _build_executor(repo, specs, id_counter=shared_counter)
        executor.execute_node(run_id=run_id, node_id=root_id)

        # Force re-runnable
        root = repo.get_node(root_id)
        repo._nodes[root_id] = replace(root, status=NodeStatus.RUNNING)

        events: list[tuple[str, str, DomainEventType, dict[str, object]]] = []
        executor2 = _build_executor(repo, specs, captured_events=events, id_counter=shared_counter)
        executor2.execute_node(run_id=run_id, node_id=root_id)

        pruned_events = [
            e for e in events if e[2] == DomainEventType.SUBTREE_PRUNED
        ]
        assert len(pruned_events) == 1
        payload = pruned_events[0][3]
        assert payload["parentNodeId"] == root_id
        assert payload["prunedCount"] == 2

    def test_no_pruned_event_on_first_run(self) -> None:
        """No SUBTREE_PRUNED event on a fresh first execution."""
        repo = InMemoryRunStateRepository()
        run_id, root_id = _setup_run(repo)
        specs = _make_child_specs(2)

        events: list[tuple[str, str, DomainEventType, dict[str, object]]] = []
        executor = _build_executor(repo, specs, captured_events=events)
        executor.execute_node(run_id=run_id, node_id=root_id)

        pruned_events = [
            e for e in events if e[2] == DomainEventType.SUBTREE_PRUNED
        ]
        assert len(pruned_events) == 0
