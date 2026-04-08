from __future__ import annotations

import sys
from pathlib import Path
import os

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.domain.events import DomainEvent, DomainEventType
from app.domain.models import (
    InterventionAction,
    NodeKind,
    NodeState,
    NodeStatus,
    RunState,
    RunStatus,
)
from app.services.event_stream import EventStreamService
from app.state.memory_repo import InMemoryRunStateRepository
from main import app


def _id_factory() -> str:
    _id_factory.counter += 1
    return f"{_id_factory.counter:04d}"


_id_factory.counter = 0


def _wire_events_only_services() -> tuple[
    InMemoryRunStateRepository, EventStreamService
]:
    from app.api.runs import _build_runtime_orchestrator, set_runs_services

    _id_factory.counter = 0
    repo = InMemoryRunStateRepository()
    event_stream = EventStreamService(repository=repo)
    os.environ["LLM_PROVIDER"] = "stub"
    orchestrator = _build_runtime_orchestrator(repository=repo)
    set_runs_services(
        repository=repo, orchestrator=orchestrator, event_stream=event_stream
    )
    return repo, event_stream


def _seed_failed_check_node(
    repo: InMemoryRunStateRepository, *, run_id: str, node_id: str
) -> NodeState:
    run = RunState(run_id=run_id, objective="checker retry scenario")
    repo.create_run(run)
    repo.update_run_status(run_id, RunStatus.RUNNING)

    node = NodeState(
        node_id=node_id,
        run_id=run_id,
        objective="Generate output requiring checker validation",
        parent_id=None,
        depth=0,
        node_kind=NodeKind.WORK,
    )
    repo.create_node(node)
    repo.record_node_started(node_id)
    repo.update_node_status(node_id, NodeStatus.WAITING_CHECK)
    repo.update_node_status(node_id, NodeStatus.FAILED_CHECK)
    return repo.get_node(node_id)


def _simulate_checker_failure_cycle(
    repo: InMemoryRunStateRepository,
    event_stream: EventStreamService,
    *,
    run_id: str,
    node_id: str,
    failures_before: int,
) -> int:
    repo.increment_checker_failures(node_id)
    updated = repo.get_node(node_id)
    event_stream.publish(
        DomainEvent(
            event_id=f"evt_checker_{_id_factory()}",
            run_id=run_id,
            node_id=node_id,
            type=DomainEventType.CHECKER_COMPLETED,
            payload={
                "scope": "node",
                "verdict": "fail",
                "reason": f"checker failed attempt {failures_before + 1}",
                "suggested_fix": "tighten output contract",
                "consecutive_failures": updated.consecutive_checker_failures,
            },
        )
    )
    if updated.consecutive_checker_failures >= 3:
        repo.record_node_ended(node_id, NodeStatus.BLOCKED_HUMAN)
        repo.update_run_status(run_id, RunStatus.BLOCKED_HUMAN)
        event_stream.publish(
            DomainEvent(
                event_id=f"evt_blocked_{_id_factory()}",
                run_id=run_id,
                node_id=node_id,
                type=DomainEventType.NODE_BLOCKED_HUMAN,
                payload={
                    "reason": "checker_failed_consecutive_threshold",
                    "consecutive_failures": updated.consecutive_checker_failures,
                    "threshold": 3,
                },
            )
        )
    else:
        repo.update_node_status(node_id, NodeStatus.RUNNING)
        repo.update_node_status(node_id, NodeStatus.WAITING_CHECK)
        repo.update_node_status(node_id, NodeStatus.FAILED_CHECK)
    return updated.consecutive_checker_failures


def test_ac_f_checker_fail_x3_transitions_to_blocked_human_and_requires_intervention() -> (
    None
):
    repo, event_stream = _wire_events_only_services()
    client = TestClient(app)

    run_id = "run_hitl_01"
    node_id = "node_hitl_01"
    _seed_failed_check_node(repo, run_id=run_id, node_id=node_id)

    failures = 0
    failures = _simulate_checker_failure_cycle(
        repo, event_stream, run_id=run_id, node_id=node_id, failures_before=failures
    )
    assert failures == 1
    assert repo.get_node(node_id).status == NodeStatus.FAILED_CHECK

    failures = _simulate_checker_failure_cycle(
        repo, event_stream, run_id=run_id, node_id=node_id, failures_before=failures
    )
    assert failures == 2
    assert repo.get_node(node_id).status == NodeStatus.FAILED_CHECK

    failures = _simulate_checker_failure_cycle(
        repo, event_stream, run_id=run_id, node_id=node_id, failures_before=failures
    )
    assert failures == 3

    blocked_node = repo.get_node(node_id)
    blocked_run = repo.get_run(run_id)
    assert blocked_node.status == NodeStatus.BLOCKED_HUMAN
    assert blocked_node.consecutive_checker_failures == 3
    assert blocked_run.status == RunStatus.BLOCKED_HUMAN

    events = event_stream.list_events(run_id=run_id, after_seq=0)
    blocked_events = [e for e in events if e.type == DomainEventType.NODE_BLOCKED_HUMAN]
    checker_events = [e for e in events if e.type == DomainEventType.CHECKER_COMPLETED]
    assert len(checker_events) == 3
    assert len(blocked_events) == 1
    assert blocked_events[0].payload["threshold"] == 3
    assert blocked_events[0].payload["consecutive_failures"] == 3

    intervention_response = client.post(
        f"/api/runs/{run_id}/nodes/{node_id}/interventions",
        headers={"X-Actor": "reviewer@integration-test"},
        json={
            "action": "edit_and_retry",
            "edited_objective": "Generate revised output with explicit constraints",
            "edited_context": "Add stricter schema notes",
            "note": "human adjusted prompt",
        },
    )
    assert intervention_response.status_code == 200
    body = intervention_response.json()
    assert body["accepted"] is True
    assert body["node_status"] == "running"

    updated_node = repo.get_node(node_id)
    updated_run = repo.get_run(run_id)
    assert updated_node.status in {NodeStatus.RUNNING, NodeStatus.COMPLETED}
    assert updated_node.objective == "Generate revised output with explicit constraints"
    assert updated_run.status in {RunStatus.RUNNING, RunStatus.COMPLETED}

    interventions = repo.list_node_interventions(node_id)
    assert len(interventions) == 1
    intervention = interventions[0]
    assert intervention.action == InterventionAction.EDIT_AND_RETRY
    assert intervention.actor == "reviewer@integration-test"
    assert intervention.payload_delta["edited_objective"] == updated_node.objective

    post_events = event_stream.list_events(run_id=run_id, after_seq=0)
    intervention_events = [
        event
        for event in post_events
        if event.type == DomainEventType.NODE_INTERVENTION_APPLIED
    ]
    assert len(intervention_events) == 1
    assert intervention_events[0].payload["action"] == "edit_and_retry"
    assert intervention_events[0].payload["node_status"] == "running"
