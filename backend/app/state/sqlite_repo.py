"""SQLite-backed repository implementation for durable orchestration state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

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
    utc_now,
)
from app.domain.policies import ensure_node_transition, ensure_run_transition
from app.schemas.api import CheckerConfig, RunConfig
from app.schemas.contracts import CheckerResult
from app.state.repository import (
    DuplicateStateError,
    RunStateRepository,
    StateNotFoundError,
)


class SQLiteRunStateRepository(RunStateRepository):
    """Durable SQLite repository implementation for run state and replay."""

    def __init__(
        self, db_path: str | Path, schema_path: str | Path | None = None
    ) -> None:
        self._db_path = str(db_path)
        self._schema_path = (
            Path(schema_path) if schema_path else self._default_schema_path()
        )
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._apply_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteRunStateRepository:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def create_run(self, run: RunState) -> None:
        try:
            self._conn.execute(
                """
                INSERT INTO runs (
                    run_id,
                    objective,
                    status,
                    config_json,
                    created_at,
                    updated_at,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.objective,
                    run.status.value,
                    self._to_json(run.config.model_dump(mode="json")),
                    self._dt(run.created_at),
                    self._dt(run.updated_at),
                    self._dt_or_none(run.completed_at),
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateStateError(f"run already exists: {run.run_id}") from exc

    def get_run(self, run_id: str) -> RunState:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise StateNotFoundError(f"run not found: {run_id}")
        return self._row_to_run(row)

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
        self._conn.execute(
            """
            UPDATE runs
            SET status = ?, updated_at = ?, completed_at = ?
            WHERE run_id = ?
            """,
            (
                updated.status.value,
                self._dt(updated.updated_at),
                self._dt_or_none(updated.completed_at),
                run_id,
            ),
        )
        self._conn.commit()
        return updated

    def list_runs(self) -> list[RunState]:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY created_at, run_id"
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def create_node(self, node: NodeState) -> None:
        _ = self.get_run(node.run_id)
        try:
            self._conn.execute(
                """
                INSERT INTO nodes (
                    node_id,
                    run_id,
                    parent_id,
                    depth,
                    objective,
                    node_kind,
                    status,
                    persona_id,
                    checker_policy_json,
                    attempt_count,
                    consecutive_checker_failures,
                    ttft_ms,
                    duration_ms,
                    started_at,
                    first_token_at,
                    ended_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.node_id,
                    node.run_id,
                    node.parent_id,
                    node.depth,
                    node.objective,
                    node.node_kind.value,
                    node.status.value,
                    node.persona_id,
                    self._to_json(node.checker_policy.model_dump(mode="json")),
                    node.attempt_count,
                    node.consecutive_checker_failures,
                    node.ttft_ms,
                    node.duration_ms,
                    self._dt_or_none(node.started_at),
                    self._dt_or_none(node.first_token_at),
                    self._dt_or_none(node.ended_at),
                    self._dt(node.created_at),
                    self._dt(node.updated_at),
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            if "FOREIGN KEY" in str(exc):
                raise StateNotFoundError(
                    f"run not found for node: {node.run_id}"
                ) from exc
            raise DuplicateStateError(f"node already exists: {node.node_id}") from exc

    def get_node(self, node_id: str) -> NodeState:
        row = self._conn.execute(
            "SELECT * FROM nodes WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        if row is None:
            raise StateNotFoundError(f"node not found: {node_id}")
        return self._row_to_node(row)

    def list_run_nodes(self, run_id: str) -> list[NodeState]:
        _ = self.get_run(run_id)
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE run_id = ? ORDER BY created_at, node_id",
            (run_id,),
        ).fetchall()
        return [self._row_to_node(row) for row in rows]

    def update_node_status(self, node_id: str, status: NodeStatus) -> NodeState:
        node = self.get_node(node_id)
        ensure_node_transition(node.status, status)
        updated = replace(node, status=status, updated_at=utc_now())
        self._persist_node(updated)
        return updated

    def update_node_objective(self, node_id: str, objective: str) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(node, objective=objective, updated_at=utc_now())
        self._persist_node(updated)
        return updated

    def update_node_persona(self, node_id: str, persona_id: str | None) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(node, persona_id=persona_id, updated_at=utc_now())
        self._persist_node(updated)
        return updated

    def update_node_kind(self, node_id: str, node_kind: NodeKind) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(node, node_kind=node_kind, updated_at=utc_now())
        self._persist_node(updated)
        return updated

    def update_node_checker_policy(self, node_id: str, policy: "CheckerConfig") -> NodeState:
        from app.schemas.api import CheckerConfig as _CC
        node = self.get_node(node_id)
        updated = replace(node, checker_policy=policy, updated_at=utc_now())
        self._persist_node(updated)
        return updated

    def increment_node_attempt_count(self, node_id: str) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(
            node,
            attempt_count=node.attempt_count + 1,
            updated_at=utc_now(),
        )
        self._persist_node(updated)
        return updated

    def reset_checker_failures(self, node_id: str) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(node, consecutive_checker_failures=0, updated_at=utc_now())
        self._persist_node(updated)
        return updated

    def increment_checker_failures(self, node_id: str) -> NodeState:
        node = self.get_node(node_id)
        updated = replace(
            node,
            consecutive_checker_failures=node.consecutive_checker_failures + 1,
            updated_at=utc_now(),
        )
        self._persist_node(updated)
        return updated

    def record_node_started(self, node_id: str) -> NodeState:
        node = self.get_node(node_id)
        ensure_node_transition(node.status, NodeStatus.RUNNING)
        node.mark_running()
        self._persist_node(node)
        return node

    def record_node_first_token(self, node_id: str) -> NodeState:
        node = self.get_node(node_id)
        node.mark_first_token()
        self._persist_node(node)
        return node

    def record_node_ended(self, node_id: str, final_status: NodeStatus) -> NodeState:
        node = self.get_node(node_id)
        ensure_node_transition(node.status, final_status)
        node.mark_ended(final_status=final_status)
        self._persist_node(node)
        return node

    def create_attempt(self, attempt: AttemptState) -> None:
        _ = self.get_node(attempt.node_id)
        try:
            self._conn.execute(
                """
                INSERT INTO attempts (
                    attempt_id,
                    node_id,
                    attempt_index,
                    input_snapshot_json,
                    output_snapshot_json,
                    checker_result_json,
                    error_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.node_id,
                    attempt.attempt_index,
                    self._to_json(attempt.input_snapshot),
                    self._to_json(attempt.output_snapshot),
                    self._to_json(
                        attempt.checker_result.model_dump(mode="json")
                        if attempt.checker_result
                        else None
                    ),
                    self._to_json(attempt.error),
                    self._dt(attempt.created_at),
                    self._dt(attempt.updated_at),
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            if "FOREIGN KEY" in str(exc):
                raise StateNotFoundError(f"node not found: {attempt.node_id}") from exc
            raise DuplicateStateError(
                f"attempt already exists: {attempt.attempt_id}"
            ) from exc

    def list_node_attempts(self, node_id: str) -> list[AttemptState]:
        _ = self.get_node(node_id)
        rows = self._conn.execute(
            """
            SELECT * FROM attempts
            WHERE node_id = ?
            ORDER BY attempt_index ASC, created_at ASC, attempt_id ASC
            """,
            (node_id,),
        ).fetchall()
        return [self._row_to_attempt(row) for row in rows]

    def create_intervention(self, intervention: InterventionState) -> None:
        node = self.get_node(intervention.node_id)
        if node.run_id != intervention.run_id:
            raise ValueError(
                "intervention run_id does not match node run_id: "
                f"{intervention.run_id} != {node.run_id}"
            )
        try:
            self._conn.execute(
                """
                INSERT INTO interventions (
                    intervention_id,
                    run_id,
                    node_id,
                    action,
                    actor,
                    note,
                    payload_delta_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intervention.intervention_id,
                    intervention.run_id,
                    intervention.node_id,
                    intervention.action.value,
                    intervention.actor,
                    intervention.note,
                    self._to_json(intervention.payload_delta),
                    self._dt(intervention.created_at),
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            if "FOREIGN KEY" in str(exc):
                raise StateNotFoundError(
                    f"node or run not found for intervention: {intervention.node_id}"
                ) from exc
            raise DuplicateStateError(
                f"intervention already exists: {intervention.intervention_id}"
            ) from exc

    def list_node_interventions(self, node_id: str) -> list[InterventionState]:
        _ = self.get_node(node_id)
        rows = self._conn.execute(
            """
            SELECT * FROM interventions
            WHERE node_id = ?
            ORDER BY created_at ASC, intervention_id ASC
            """,
            (node_id,),
        ).fetchall()
        return [self._row_to_intervention(row) for row in rows]

    def delete_node(self, run_id: str, node_id: str) -> None:
        """Delete a single node. CASCADE handles attempts/interventions."""
        _ = self.get_run(run_id)
        cursor = self._conn.execute(
            "DELETE FROM nodes WHERE node_id = ? AND run_id = ?",
            (node_id, run_id),
        )
        if cursor.rowcount == 0:
            raise StateNotFoundError(f"node not found: {node_id}")
        self._conn.commit()

    def delete_children_of(self, run_id: str, parent_node_id: str) -> int:
        """Recursively delete all descendant nodes of *parent_node_id*.

        Uses a recursive CTE to walk the subtree.  ON DELETE CASCADE on
        the ``attempts`` and ``interventions`` tables handles child data.
        ``events.node_id`` becomes NULL (ON DELETE SET NULL).
        """
        _ = self.get_run(run_id)
        cursor = self._conn.execute(
            """
            WITH RECURSIVE subtree(node_id) AS (
                SELECT node_id FROM nodes
                WHERE parent_id = ? AND run_id = ?
                UNION ALL
                SELECT n.node_id FROM nodes n
                JOIN subtree s ON n.parent_id = s.node_id
            )
            DELETE FROM nodes WHERE node_id IN (SELECT node_id FROM subtree)
            """,
            (parent_node_id, run_id),
        )
        self._conn.commit()
        return cursor.rowcount

    def append_event(self, event: DomainEvent) -> DomainEvent:
        _ = self.get_run(event.run_id)
        ts = event.ts or utc_now()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            next_seq = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?",
                (event.run_id,),
            ).fetchone()[0]
            stored = replace(event, seq=next_seq, ts=ts)
            self._conn.execute(
                """
                INSERT INTO events (
                    event_id,
                    run_id,
                    node_id,
                    seq,
                    type,
                    payload_json,
                    ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.event_id,
                    stored.run_id,
                    stored.node_id,
                    stored.seq,
                    stored.type.value,
                    self._to_json(stored.payload),
                    self._dt(stored.ts),
                ),
            )
            self._conn.commit()
            return stored
        except Exception:
            self._conn.rollback()
            raise

    def list_run_events(self, run_id: str, after_seq: int = 0) -> list[DomainEvent]:
        _ = self.get_run(run_id)
        rows = self._conn.execute(
            """
            SELECT * FROM events
            WHERE run_id = ? AND seq > ?
            ORDER BY seq ASC
            """,
            (run_id, after_seq),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def _apply_schema(self) -> None:
        if not self._schema_path.exists():
            raise FileNotFoundError(f"SQLite schema not found: {self._schema_path}")
        sql = self._schema_path.read_text(encoding="utf-8")
        self._conn.executescript(sql)
        self._conn.commit()

    def _persist_node(self, node: NodeState) -> None:
        cursor = self._conn.execute(
            """
            UPDATE nodes
            SET
                run_id = ?,
                parent_id = ?,
                depth = ?,
                objective = ?,
                node_kind = ?,
                status = ?,
                persona_id = ?,
                checker_policy_json = ?,
                attempt_count = ?,
                consecutive_checker_failures = ?,
                ttft_ms = ?,
                duration_ms = ?,
                started_at = ?,
                first_token_at = ?,
                ended_at = ?,
                created_at = ?,
                updated_at = ?
            WHERE node_id = ?
            """,
            (
                node.run_id,
                node.parent_id,
                node.depth,
                node.objective,
                node.node_kind.value,
                node.status.value,
                node.persona_id,
                self._to_json(node.checker_policy.model_dump(mode="json")),
                node.attempt_count,
                node.consecutive_checker_failures,
                node.ttft_ms,
                node.duration_ms,
                self._dt_or_none(node.started_at),
                self._dt_or_none(node.first_token_at),
                self._dt_or_none(node.ended_at),
                self._dt(node.created_at),
                self._dt(node.updated_at),
                node.node_id,
            ),
        )
        if cursor.rowcount == 0:
            raise StateNotFoundError(f"node not found: {node.node_id}")
        self._conn.commit()

    @staticmethod
    def _default_schema_path() -> Path:
        return Path(__file__).resolve().parent / "sql" / "schema.sql"

    @staticmethod
    def _to_json(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _from_json(value: str | None) -> Any:
        if value is None:
            return None
        return json.loads(value)

    @staticmethod
    def _dt(value: datetime) -> str:
        return value.isoformat()

    @staticmethod
    def _dt_or_none(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)

    def _row_to_run(self, row: sqlite3.Row) -> RunState:
        return RunState(
            run_id=row["run_id"],
            objective=row["objective"],
            status=RunStatus(row["status"]),
            config=RunConfig.model_validate(self._from_json(row["config_json"])),
            created_at=self._parse_dt(row["created_at"]) or utc_now(),
            updated_at=self._parse_dt(row["updated_at"]) or utc_now(),
            completed_at=self._parse_dt(row["completed_at"]),
        )

    def _row_to_node(self, row: sqlite3.Row) -> NodeState:
        return NodeState(
            node_id=row["node_id"],
            run_id=row["run_id"],
            objective=row["objective"],
            parent_id=row["parent_id"],
            depth=int(row["depth"]),
            node_kind=NodeKind(row["node_kind"]),
            status=NodeStatus(row["status"]),
            persona_id=row["persona_id"],
            checker_policy=CheckerConfig.model_validate(
                self._from_json(row["checker_policy_json"])
            ),
            attempt_count=int(row["attempt_count"]),
            consecutive_checker_failures=int(row["consecutive_checker_failures"]),
            ttft_ms=row["ttft_ms"],
            duration_ms=row["duration_ms"],
            started_at=self._parse_dt(row["started_at"]),
            first_token_at=self._parse_dt(row["first_token_at"]),
            ended_at=self._parse_dt(row["ended_at"]),
            created_at=self._parse_dt(row["created_at"]) or utc_now(),
            updated_at=self._parse_dt(row["updated_at"]) or utc_now(),
        )

    def _row_to_attempt(self, row: sqlite3.Row) -> AttemptState:
        checker_result_payload = self._from_json(row["checker_result_json"])
        checker_result = (
            CheckerResult.model_validate(checker_result_payload)
            if checker_result_payload is not None
            else None
        )
        return AttemptState(
            attempt_id=row["attempt_id"],
            node_id=row["node_id"],
            attempt_index=int(row["attempt_index"]),
            input_snapshot=self._from_json(row["input_snapshot_json"]) or {},
            output_snapshot=self._from_json(row["output_snapshot_json"]),
            checker_result=checker_result,
            error=self._from_json(row["error_json"]),
            created_at=self._parse_dt(row["created_at"]) or utc_now(),
            updated_at=self._parse_dt(row["updated_at"]) or utc_now(),
        )

    def _row_to_intervention(self, row: sqlite3.Row) -> InterventionState:
        return InterventionState(
            intervention_id=row["intervention_id"],
            run_id=row["run_id"],
            node_id=row["node_id"],
            action=InterventionAction(row["action"]),
            actor=row["actor"],
            note=row["note"],
            payload_delta=self._from_json(row["payload_delta_json"]) or {},
            created_at=self._parse_dt(row["created_at"]) or utc_now(),
        )

    def _row_to_event(self, row: sqlite3.Row) -> DomainEvent:
        return DomainEvent(
            event_id=row["event_id"],
            run_id=row["run_id"],
            node_id=row["node_id"],
            seq=int(row["seq"]),
            type=DomainEventType(row["type"]),
            payload=self._from_json(row["payload_json"]) or {},
            ts=self._parse_dt(row["ts"]) or utc_now(),
        )


__all__ = ["DuplicateStateError", "SQLiteRunStateRepository", "StateNotFoundError"]
