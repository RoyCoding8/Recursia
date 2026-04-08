from __future__ import annotations

import time
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.domain.events import DomainEventType
from app.domain.models import NodeStatus, RunStatus
from app.schemas.contracts import DividerDecision
from app.services.divider import (
    BaseCaseWorkPlan,
    DividerServiceResult,
    RecursiveChildSpec,
    RecursiveDecomposition,
)
from app.services.event_stream import EventStreamService
from app.services.executor import RecursiveExecutor, WorkExecutionResult
from app.services.orchestrator import Orchestrator
from app.services.persona_router import PersonaRouteResult
from app.state.memory_repo import InMemoryRunStateRepository
from main import app


class _ScenarioDivider:
    def __init__(self) -> None:
        self._map = {
            "Ship analytics platform": DividerServiceResult(
                decision=DividerDecision.RECURSIVE_CASE,
                recursive_case=RecursiveDecomposition(
                    rationale="Split by delivery lane",
                    children=[
                        RecursiveChildSpec(
                            objective="Implement backend API service",
                            dependencies=[],
                            suggested_persona="python_developer",
                            interface_contract=None,
                        ),
                        RecursiveChildSpec(
                            objective="Design SQL analytics model",
                            dependencies=["child_1"],
                            suggested_persona="sql_developer",
                            interface_contract=None,
                        ),
                        RecursiveChildSpec(
                            objective="Documentation and QA track",
                            dependencies=["child_1", "child_2"],
                            suggested_persona="python_developer",
                            interface_contract=None,
                        ),
                    ],
                ),
                attempts_used=1,
            ),
            "Implement backend API service": DividerServiceResult(
                decision=DividerDecision.BASE_CASE,
                base_case=BaseCaseWorkPlan(
                    rationale="Direct implementation",
                    work_plan=[
                        {
                            "step": 1,
                            "description": "Build API endpoints for orchestration status",
                        }
                    ],
                    suggested_persona="python_developer",
                ),
                attempts_used=1,
            ),
            "Design SQL analytics model": DividerServiceResult(
                decision=DividerDecision.BASE_CASE,
                base_case=BaseCaseWorkPlan(
                    rationale="Single SQL design pass",
                    work_plan=[
                        {
                            "step": 1,
                            "description": "Produce normalized SQL schema and indexes",
                        }
                    ],
                    suggested_persona="sql_developer",
                ),
                attempts_used=1,
            ),
            "Documentation and QA track": DividerServiceResult(
                decision=DividerDecision.RECURSIVE_CASE,
                recursive_case=RecursiveDecomposition(
                    rationale="Split docs and checks",
                    children=[
                        RecursiveChildSpec(
                            objective="Write release checklist",
                            dependencies=[],
                            suggested_persona="python_developer",
                            interface_contract=None,
                        )
                    ],
                ),
                attempts_used=1,
            ),
            "Write release checklist": DividerServiceResult(
                decision=DividerDecision.BASE_CASE,
                base_case=BaseCaseWorkPlan(
                    rationale="Single docs pass",
                    work_plan=[
                        {
                            "step": 1,
                            "description": "Create QA checklist and release notes",
                        }
                    ],
                    suggested_persona="python_developer",
                ),
                attempts_used=1,
            ),
        }

    def divide(self, objective: str, depth: int = 0) -> DividerServiceResult:
        _ = depth
        return self._map[objective]


class _PersonaRouter:
    def select_persona(
        self,
        objective: str,
        *,
        context: str | None = None,
        explicit_persona_id: str | None = None,
    ) -> PersonaRouteResult:
        _ = context
        if explicit_persona_id:
            persona_id = explicit_persona_id
        elif "sql" in objective.lower():
            persona_id = "sql_developer"
        else:
            persona_id = "python_developer"
        return PersonaRouteResult(persona_id=persona_id, confidence=1.0, reason="test")


class _Worker:
    def execute(
        self,
        *,
        run_id: str,
        node_id: str,
        objective: str,
        depth: int,
        persona_id: str | None,
        work_plan: list[dict[str, object]],
    ) -> WorkExecutionResult:
        return WorkExecutionResult.completed(
            {
                "run_id": run_id,
                "node_id": node_id,
                "objective": objective,
                "depth": depth,
                "persona_id": persona_id,
                "steps": [step["description"] for step in work_plan],
            }
        )


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


def _wire_integration_services() -> tuple[
    InMemoryRunStateRepository, EventStreamService
]:
    from app.api.runs import set_runs_services

    _id_factory.counter = 0
    repo = InMemoryRunStateRepository()
    event_stream = EventStreamService(repository=repo)
    executor = RecursiveExecutor(
        repository=repo,
        divider=_ScenarioDivider(),
        persona_router=_PersonaRouter(),
        worker=_Worker(),
        id_factory=_id_factory,
    )
    orchestrator = Orchestrator(
        repository=repo, executor=executor, id_factory=_id_factory
    )
    set_runs_services(
        repository=repo, orchestrator=orchestrator, event_stream=event_stream
    )
    return repo, event_stream


def test_ac_a_recursive_run_pipeline_create_to_completion_and_graph_consistency() -> (
    None
):
    repo, event_stream = _wire_integration_services()
    client = TestClient(app)

    create_response = client.post(
        "/api/runs",
        json={
            "objective": "Ship analytics platform",
            "config": {
                "checker": {
                    "enabled": True,
                    "node_level": True,
                    "merge_level": True,
                    "max_retries_per_node": 3,
                },
                "max_depth": 6,
                "max_children_per_node": 4,
                "stream": {"mode": "sse"},
            },
        },
    )

    assert create_response.status_code == 201
    create_payload = create_response.json()
    assert create_payload["status"] == "queued"
    run_id = create_payload["run_id"]
    root_node_id = create_payload["root_node_id"]

    _wait_until(lambda: repo.get_run(run_id).status == RunStatus.COMPLETED)

    graph_response = client.get(f"/api/runs/{run_id}")
    assert graph_response.status_code == 200
    graph = graph_response.json()

    assert graph["run"]["run_id"] == run_id
    assert graph["run"]["status"] == "completed"
    assert len(graph["nodes"]) == 5
    assert len(graph["edges"]) == 4

    nodes_by_objective = {node["objective"]: node for node in graph["nodes"]}
    assert nodes_by_objective["Ship analytics platform"]["node_id"] == root_node_id
    assert nodes_by_objective["Ship analytics platform"]["parent_id"] is None
    assert nodes_by_objective["Implement backend API service"]["depth"] == 1
    assert (
        nodes_by_objective["Design SQL analytics model"]["persona_id"]
        == "sql_developer"
    )
    assert nodes_by_objective["Write release checklist"]["depth"] == 2
    assert all(node["status"] == "completed" for node in graph["nodes"])

    run = repo.get_run(run_id)
    assert run.status == RunStatus.COMPLETED

    for node in repo.list_run_nodes(run_id):
        assert node.attempt_count == 1
        assert len(repo.list_node_attempts(node.node_id)) == 1
        assert node.status == NodeStatus.COMPLETED

    base_nodes = [
        node
        for node in repo.list_run_nodes(run_id)
        if node.objective
        in {
            "Implement backend API service",
            "Design SQL analytics model",
            "Write release checklist",
        }
    ]
    assert all(node.first_token_at is not None for node in base_nodes)
    assert all((node.ttft_ms or 0) >= 0 for node in base_nodes)

    events = event_stream.list_events(run_id=run_id, after_seq=0)
    assert [event.seq for event in events] == [1, 2, 3, 4]
    assert [event.type for event in events] == [
        DomainEventType.RUN_CREATED,
        DomainEventType.NODE_CREATED,
        DomainEventType.RUN_STATUS_CHANGED,
        DomainEventType.RUN_COMPLETED,
    ]
