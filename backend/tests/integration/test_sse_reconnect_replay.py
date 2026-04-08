from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.api.events import stream_run_events
from app.schemas.contracts import DividerDecision
from app.services.divider import BaseCaseWorkPlan, DividerServiceResult
from app.services.event_stream import EventStreamService
from app.services.executor import RecursiveExecutor
from app.services.orchestrator import Orchestrator
from app.services.persona_router import PersonaRouteResult
from app.state.memory_repo import InMemoryRunStateRepository
from main import app


class _BaseCaseDivider:
    def divide(self, objective: str, depth: int = 0) -> DividerServiceResult:
        return DividerServiceResult(
            decision=DividerDecision.BASE_CASE,
            base_case=BaseCaseWorkPlan(
                rationale="single-node deterministic flow",
                work_plan=[
                    {
                        "step": 1,
                        "description": f"execute objective '{objective}' at depth {depth}",
                    }
                ],
                suggested_persona="python_developer",
            ),
            attempts_used=1,
        )


class _PersonaRouter:
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
            reason="integration-test route",
        )


class _FakeDisconnectingRequest:
    def __init__(self) -> None:
        self._checked = False

    async def is_disconnected(self) -> bool:
        if self._checked:
            return True
        self._checked = True
        return False


async def _collect_response_body(response, *, limit: int = 10) -> str:
    chunks: list[str] = []
    count = 0
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        count += 1
        if count >= limit:
            break
    return "".join(chunks)


def _collect_sse_ids(body: str) -> list[int]:
    ids: list[int] = []
    for line in body.splitlines():
        if line.startswith("id: "):
            ids.append(int(line.replace("id: ", "", 1)))
    return ids


def _id_factory() -> str:
    _id_factory.counter += 1
    return f"{_id_factory.counter:04d}"


_id_factory.counter = 0


def _wire_services() -> tuple[InMemoryRunStateRepository, EventStreamService]:
    from app.api.runs import set_runs_services

    _id_factory.counter = 0
    repo = InMemoryRunStateRepository()
    event_stream = EventStreamService(repository=repo)
    executor = RecursiveExecutor(
        repository=repo,
        divider=_BaseCaseDivider(),
        persona_router=_PersonaRouter(),
        id_factory=_id_factory,
    )
    orchestrator = Orchestrator(
        repository=repo, executor=executor, id_factory=_id_factory
    )
    set_runs_services(
        repository=repo, orchestrator=orchestrator, event_stream=event_stream
    )
    return repo, event_stream


def test_ac_e_sse_reconnect_and_replay_with_last_event_id_and_after_seq() -> None:
    repo, stream_service = _wire_services()
    client = TestClient(app)

    create = client.post(
        "/api/runs", json={"objective": "SSE replay integration objective"}
    )
    assert create.status_code == 201
    run_id = create.json()["run_id"]

    stored_events = repo.list_run_events(run_id=run_id, after_seq=0)
    assert [event.seq for event in stored_events] == [1, 2, 3, 4]

    first_response = asyncio.run(
        stream_run_events(
            run_id=run_id,
            request=_FakeDisconnectingRequest(),
            after_seq=0,
            last_event_id=None,
            stream_service=stream_service,
        )
    )
    first_body = asyncio.run(_collect_response_body(first_response, limit=8))
    first_stream_ids = _collect_sse_ids(first_body)
    assert first_stream_ids == [1, 2, 3, 4]

    resumed_with_header_response = asyncio.run(
        stream_run_events(
            run_id=run_id,
            request=_FakeDisconnectingRequest(),
            after_seq=1,
            last_event_id="3",
            stream_service=stream_service,
        )
    )
    resumed_with_header_body = asyncio.run(
        _collect_response_body(resumed_with_header_response, limit=4)
    )
    resumed_with_header_ids = _collect_sse_ids(resumed_with_header_body)
    assert resumed_with_header_ids == [4]

    resumed_with_after_seq_response = asyncio.run(
        stream_run_events(
            run_id=run_id,
            request=_FakeDisconnectingRequest(),
            after_seq=3,
            last_event_id=None,
            stream_service=stream_service,
        )
    )
    resumed_with_after_seq_body = asyncio.run(
        _collect_response_body(resumed_with_after_seq_response, limit=4)
    )
    resumed_with_after_seq_ids = _collect_sse_ids(resumed_with_after_seq_body)
    assert resumed_with_after_seq_ids == [4]

    invalid_header = client.get(
        f"/api/runs/{run_id}/events",
        headers={"Last-Event-ID": "bad-seq"},
    )
    assert invalid_header.status_code == 400
    assert "Last-Event-ID" in invalid_header.json()["detail"]
