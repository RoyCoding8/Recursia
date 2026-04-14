from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.domain.events import DomainEventType
from app.domain.models import NodeState, NodeStatus, RunState, RunStatus
from app.schemas.api import CheckerConfig
from app.schemas.contracts import DividerDecision
from app.services.checker import CheckerRequest, CheckerService
from app.services.divider import BaseCaseWorkPlan, DividerServiceResult
from app.services.event_stream import EventStreamService
from app.services.executor import RecursiveExecutor
from app.services.orchestrator import Orchestrator
from app.services.persona_router import PersonaRouteResult
from app.state.memory_repo import InMemoryRunStateRepository
from main import app


class _StubDivider:
    def divide(self, objective: str, depth: int = 0, **kwargs) -> DividerServiceResult:
        return DividerServiceResult(
            decision=DividerDecision.BASE_CASE,
            base_case=BaseCaseWorkPlan(
                rationale="deterministic test base-case",
                work_plan=[
                    {
                        "step": 1,
                        "description": f"execute {objective} at depth {depth}",
                    }
                ],
                suggested_persona="python_developer",
            ),
            attempts_used=1,
        )


class _StubPersonaRouter:
    def select_persona(
        self,
        objective: str,
        *,
        context: str | None = None,
        explicit_persona_id: str | None = None,
    ) -> PersonaRouteResult:
        _ = (objective, context)
        return PersonaRouteResult(
            persona_id=explicit_persona_id or "python_developer",
            confidence=1.0,
            reason="test route",
        )


class _FakeCheckerClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[CheckerRequest] = []

    def evaluate(self, request: CheckerRequest) -> object:
        self.calls.append(request)
        if not self._responses:
            raise AssertionError("No fake checker responses remaining")
        return self._responses.pop(0)


def _id_factory() -> str:
    _id_factory.counter += 1
    return f"{_id_factory.counter:04d}"


_id_factory.counter = 0


def _wait_until(
    predicate, *, timeout_seconds: float = 2.0, interval_seconds: float = 0.02
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval_seconds)
    raise AssertionError("condition not met before timeout")


def _wire_test_services(
    *,
    checker: CheckerService | None = None,
) -> tuple[InMemoryRunStateRepository, EventStreamService]:
    from app.api.runs import set_runs_services

    _id_factory.counter = 0
    repo = InMemoryRunStateRepository()
    event_stream = EventStreamService(repository=repo)
    executor = RecursiveExecutor(
        repository=repo,
        divider=_StubDivider(),
        persona_router=_StubPersonaRouter(),
        checker=checker,
        id_factory=_id_factory,
    )
    orchestrator = Orchestrator(
        repository=repo, executor=executor, id_factory=_id_factory
    )
    set_runs_services(
        repository=repo, orchestrator=orchestrator, event_stream=event_stream
    )
    return repo, event_stream


def test_post_run_creates_root_node_and_starts_orchestration() -> None:
    repo, _ = _wire_test_services()
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={
            "objective": "Implement run endpoint",
            "config": {
                "checker": {
                    "enabled": True,
                    "node_level": True,
                    "merge_level": True,
                    "max_retries_per_node": 3,
                },
                "max_depth": 4,
                "max_children_per_node": 3,
                "stream": {"mode": "sse"},
            },
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body == {
        "run_id": "run_0001",
        "status": "queued",
        "root_node_id": "node_0002",
    }

    _wait_until(lambda: repo.get_run("run_0001").status == RunStatus.COMPLETED)

    run = repo.get_run("run_0001")
    root = repo.get_node("node_0002")

    assert run.status == RunStatus.COMPLETED
    assert root.run_id == run.run_id
    assert root.parent_id is None
    assert root.depth == 0
    assert root.objective == "Implement run endpoint"
    assert root.status == NodeStatus.COMPLETED


def test_post_run_accepts_base_persona_override() -> None:
    repo, _ = _wire_test_services()
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={
            "objective": "Implement run endpoint",
            "base_persona_id": "sql_developer",
        },
    )

    assert response.status_code == 201
    run_id = response.json()["run_id"]

    _wait_until(lambda: repo.get_run(run_id).status == RunStatus.COMPLETED)

    root_node_id = response.json()["root_node_id"]
    root = repo.get_node(root_node_id)
    assert root.persona_id == "sql_developer"


def test_get_run_returns_typed_nodes_and_edges_payload() -> None:
    repo, _ = _wire_test_services()
    client = TestClient(app)

    create = client.post(
        "/api/runs",
        json={"objective": "Graph retrieval objective"},
    )
    assert create.status_code == 201
    run_id = create.json()["run_id"]

    get_response = client.get(f"/api/runs/{run_id}")
    assert get_response.status_code == 200

    payload = get_response.json()
    assert payload["run"]["run_id"] == run_id
    assert payload["run"]["status"] in {
        "queued",
        "running",
        "blocked_human",
        "completed",
        "failed",
        "canceled",
    }
    assert isinstance(payload["nodes"], list)
    assert isinstance(payload["edges"], list)

    assert len(payload["nodes"]) == 1
    node = payload["nodes"][0]
    assert node["run_id"] == run_id
    assert node["parent_id"] is None
    assert node["depth"] == 0
    assert node["status"] in {
        "queued",
        "running",
        "waiting_check",
        "failed_check",
        "blocked_human",
        "completed",
        "error",
    }

    assert payload["edges"] == []
    assert repo.get_run(run_id).objective == "Graph retrieval objective"


def test_list_personas_returns_markdown_profiles() -> None:
    client = TestClient(app)

    response = client.get("/api/personas")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    persona_ids = {entry["persona_id"] for entry in payload}
    assert "python_developer" in persona_ids
    assert "sql_developer" in persona_ids


def test_intervention_validates_state_and_returns_audit_transition_response() -> None:
    repo, event_stream = _wire_test_services()
    client = TestClient(app)

    run = RunState(run_id="run_blocked", objective="Needs intervention")
    repo.create_run(run)
    repo.update_run_status("run_blocked", RunStatus.RUNNING)
    repo.update_run_status("run_blocked", RunStatus.BLOCKED_HUMAN)

    node = NodeState(
        node_id="node_blocked",
        run_id="run_blocked",
        objective="Blocked objective",
        parent_id=None,
        depth=0,
    )
    repo.create_node(node)
    repo.update_node_status("node_blocked", NodeStatus.RUNNING)
    repo.update_node_status("node_blocked", NodeStatus.WAITING_CHECK)
    repo.update_node_status("node_blocked", NodeStatus.FAILED_CHECK)
    repo.update_node_status("node_blocked", NodeStatus.BLOCKED_HUMAN)

    invalid_payload = client.post(
        "/api/runs/run_blocked/nodes/node_blocked/interventions",
        json={"action": "edit_and_retry"},
    )
    assert invalid_payload.status_code == 422

    applied = client.post(
        "/api/runs/run_blocked/nodes/node_blocked/interventions",
        headers={"X-Actor": "reviewer@unit-test"},
        json={
            "action": "skip_with_justification",
            "justification": "Known non-critical scope; continue downstream",
        },
    )
    assert applied.status_code == 200

    body = applied.json()
    assert body["accepted"] is True
    assert body["node_status"] == "completed"
    assert body["intervention_id"].startswith("int_")

    interventions = repo.list_node_interventions("node_blocked")
    assert len(interventions) == 1
    audit = interventions[0]
    assert audit.action.value == "skip_with_justification"
    assert audit.actor == "reviewer@unit-test"
    assert (
        audit.payload_delta["justification"]
        == "Known non-critical scope; continue downstream"
    )

    updated_node = repo.get_node("node_blocked")
    assert updated_node.status == NodeStatus.COMPLETED

    run_after = repo.get_run("run_blocked")
    assert run_after.status == RunStatus.RUNNING

    events = event_stream.list_events(run_id="run_blocked", after_seq=0)
    intervention_events = [
        event
        for event in events
        if event.type == DomainEventType.NODE_INTERVENTION_APPLIED
    ]
    assert len(intervention_events) == 1
    event_payload = intervention_events[0].payload
    assert event_payload["action"] == "skip_with_justification"
    assert event_payload["actor"] == "reviewer@unit-test"
    assert event_payload["node_status"] == "completed"


def test_run_result_separates_validation_from_terminal_error() -> None:
    checker = CheckerService(
        checker_client=_FakeCheckerClient(
            responses=[
                {
                    "verdict": "fail",
                    "reason": "selector does not match the generated HTML",
                    "suggested_fix": "use a class selector that exists in the markup",
                    "confidence": 0.71,
                    "violations": ["css_selector_mismatch"],
                },
                {
                    "verdict": "fail",
                    "reason": "selector does not match the generated HTML",
                    "suggested_fix": "use a class selector that exists in the markup",
                    "confidence": 0.71,
                    "violations": ["css_selector_mismatch"],
                },
            ]
        )
    )
    repo, _ = _wire_test_services(checker=checker)
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={
            "objective": "Create a tiny site",
            "config": {
                "checker": {
                    "enabled": True,
                    "node_level": True,
                    "merge_level": False,
                    "max_retries_per_node": 1,
                    "on_check_fail": "auto_retry",
                }
            },
        },
    )
    assert response.status_code == 201
    run_id = response.json()["run_id"]

    _wait_until(lambda: repo.get_run(run_id).status == RunStatus.COMPLETED)

    result_response = client.get(f"/api/runs/{run_id}/result")
    assert result_response.status_code == 200
    payload = result_response.json()

    assert payload["status"] == "completed"
    assert payload["error"] is None
    assert payload["validation"] == {
        "source": "checker",
        "verdict": "fail",
        "reason": "selector does not match the generated HTML",
        "suggested_fix": "use a class selector that exists in the markup",
        "confidence": 0.71,
        "violations": ["css_selector_mismatch"],
    }
