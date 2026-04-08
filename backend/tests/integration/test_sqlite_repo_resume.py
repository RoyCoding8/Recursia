from __future__ import annotations

from pathlib import Path

from app.domain.events import DomainEvent, DomainEventType
from app.domain.models import (
    AttemptState,
    InterventionAction,
    InterventionState,
    NodeKind,
    NodeState,
    NodeStatus,
    RunState,
    RunStatus,
)
from app.state.sqlite_repo import SQLiteRunStateRepository


def test_sqlite_repo_persists_and_resumes_run_state(tmp_path: Path) -> None:
    db_path = tmp_path / "run_state.sqlite3"

    with SQLiteRunStateRepository(db_path=db_path) as repo:
        run = RunState(run_id="run_resume", objective="Durable restore objective")
        repo.create_run(run)
        repo.update_run_status("run_resume", RunStatus.RUNNING)

        root = NodeState(
            node_id="node_root",
            run_id="run_resume",
            objective="Root objective",
            node_kind=NodeKind.WORK,
            depth=0,
        )
        repo.create_node(root)
        repo.record_node_started("node_root")
        repo.record_node_first_token("node_root")
        repo.increment_node_attempt_count("node_root")

        attempt = AttemptState(
            attempt_id="attempt_root_1",
            node_id="node_root",
            attempt_index=1,
            input_snapshot={"objective": "Root objective"},
            output_snapshot={"result": "partial"},
        )
        repo.create_attempt(attempt)

        intervention = InterventionState(
            intervention_id="int_root_1",
            run_id="run_resume",
            node_id="node_root",
            action=InterventionAction.RETRY,
            actor="operator@test",
            note="Resume after validation",
            payload_delta={"reason": "manual retry"},
        )
        repo.create_intervention(intervention)

        appended_1 = repo.append_event(
            DomainEvent(
                event_id="evt_1",
                run_id="run_resume",
                node_id="node_root",
                type=DomainEventType.NODE_STATUS_CHANGED,
                payload={"status": "running"},
            )
        )
        appended_2 = repo.append_event(
            DomainEvent(
                event_id="evt_2",
                run_id="run_resume",
                node_id="node_root",
                type=DomainEventType.NODE_TOKEN,
                payload={"chunk": "hello"},
            )
        )

        assert appended_1.seq == 1
        assert appended_2.seq == 2

        repo.record_node_ended("node_root", final_status=NodeStatus.COMPLETED)
        repo.update_run_status("run_resume", RunStatus.COMPLETED)

    with SQLiteRunStateRepository(db_path=db_path) as resumed_repo:
        resumed_run = resumed_repo.get_run("run_resume")
        assert resumed_run.status == RunStatus.COMPLETED

        resumed_nodes = resumed_repo.list_run_nodes("run_resume")
        assert len(resumed_nodes) == 1
        resumed_node = resumed_nodes[0]
        assert resumed_node.node_id == "node_root"
        assert resumed_node.status == NodeStatus.COMPLETED
        assert resumed_node.ttft_ms is not None
        assert resumed_node.duration_ms is not None
        assert resumed_node.attempt_count == 1

        attempts = resumed_repo.list_node_attempts("node_root")
        assert len(attempts) == 1
        assert attempts[0].attempt_id == "attempt_root_1"
        assert attempts[0].attempt_index == 1
        assert attempts[0].output_snapshot == {"result": "partial"}

        interventions = resumed_repo.list_node_interventions("node_root")
        assert len(interventions) == 1
        assert interventions[0].intervention_id == "int_root_1"
        assert interventions[0].action == InterventionAction.RETRY

        replay_all = resumed_repo.list_run_events("run_resume")
        assert [event.seq for event in replay_all] == [1, 2]
        assert [event.type for event in replay_all] == [
            DomainEventType.NODE_STATUS_CHANGED,
            DomainEventType.NODE_TOKEN,
        ]

        replay_after_1 = resumed_repo.list_run_events("run_resume", after_seq=1)
        assert len(replay_after_1) == 1
        assert replay_after_1[0].event_id == "evt_2"


def test_sqlite_schema_creates_mvp_tables_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "schema_check.sqlite3"

    with SQLiteRunStateRepository(db_path=db_path) as repo:
        tables = {
            row[0]
            for row in repo._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"runs", "nodes", "attempts", "interventions", "events"}.issubset(tables)

        indexes = {
            row[0]
            for row in repo._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assert "idx_nodes_run_status" in indexes
        assert "idx_events_run_seq" in indexes
