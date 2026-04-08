from __future__ import annotations

import asyncio
import itertools
import json
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.events import router as events_router
from app.api.events import stream_run_events
from app.api.events import set_event_stream_service
from app.domain.events import DomainEvent, DomainEventType
from app.services.event_stream import EventStreamService, format_sse
from app.state.memory_repo import InMemoryRunStateRepository
from app.state.repository import RunStateRepository


def _seed_run(repository: RunStateRepository, run_id: str = "run_001") -> str:
    from app.domain.models import RunState

    repository.create_run(RunState(run_id=run_id, objective="test objective"))
    return run_id


def _event(
    *,
    event_id: str,
    run_id: str,
    node_id: str = "node_001",
    event_type: DomainEventType = DomainEventType.NODE_STATUS_CHANGED,
    payload: dict[str, object] | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_id=event_id,
        run_id=run_id,
        node_id=node_id,
        type=event_type,
        payload=payload or {},
    )


async def _collect_n(async_iterable, count: int):
    items = []
    async for item in async_iterable:
        items.append(item)
        if len(items) >= count:
            break
    return items


def _build_test_app(service: EventStreamService) -> FastAPI:
    app = FastAPI()
    set_event_stream_service(service)
    app.include_router(events_router)
    return app


class FakeDisconnectingRequest:
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


def test_ordered_seq_assignment_and_replay_behavior() -> None:
    repo = InMemoryRunStateRepository()
    run_id = _seed_run(repo)
    stream = EventStreamService(repository=repo)

    stored_1 = stream.publish(
        _event(
            event_id="evt_1",
            run_id=run_id,
            event_type=DomainEventType.RUN_CREATED,
            payload={"objective": "test objective"},
        )
    )
    stored_2 = stream.publish(
        _event(
            event_id="evt_2",
            run_id=run_id,
            payload={"status": "running"},
        )
    )
    stored_3 = stream.publish(
        _event(
            event_id="evt_3",
            run_id=run_id,
            payload={"status": "completed"},
        )
    )

    assert [stored_1.seq, stored_2.seq, stored_3.seq] == [1, 2, 3]

    replay = stream.list_events(run_id=run_id, after_seq=1)
    assert [event.seq for event in replay] == [2, 3]
    assert [event.event_id for event in replay] == ["evt_2", "evt_3"]


def test_disconnect_reconnect_resume_with_last_event_id() -> None:
    repo = InMemoryRunStateRepository()
    run_id = _seed_run(repo)
    stream = EventStreamService(repository=repo)

    for index in range(1, 6):
        stream.publish(
            _event(
                event_id=f"evt_{index}",
                run_id=run_id,
                payload={"index": index},
            )
        )

    first_response = asyncio.run(
        stream_run_events(
            run_id=run_id,
            request=FakeDisconnectingRequest(),
            after_seq=0,
            last_event_id=None,
            stream_service=stream,
        )
    )
    first_body = asyncio.run(_collect_response_body(first_response, limit=8))

    assert "id: 1" in first_body
    assert "id: 5" in first_body

    resumed_response = asyncio.run(
        stream_run_events(
            run_id=run_id,
            request=FakeDisconnectingRequest(),
            after_seq=None,
            last_event_id="3",
            stream_service=stream,
        )
    )
    resumed_body = asyncio.run(_collect_response_body(resumed_response, limit=6))

    assert "id: 1" not in resumed_body
    assert "id: 2" not in resumed_body
    assert "id: 3" not in resumed_body
    assert "id: 4" in resumed_body
    assert "id: 5" in resumed_body


def test_ttft_event_shape_and_propagation() -> None:
    repo = InMemoryRunStateRepository()
    run_id = _seed_run(repo)
    stream = EventStreamService(repository=repo)

    started_at = datetime(2026, 4, 6, 10, 0, 0, tzinfo=UTC)
    first_token_at = datetime(2026, 4, 6, 10, 0, 1, 250000, tzinfo=UTC)

    ttft = stream.publish_ttft(
        run_id=run_id,
        node_id="node_ttft",
        ttft_ms=1250,
        started_at=started_at,
        first_token_at=first_token_at,
        event_id="evt_ttft",
    )

    assert ttft.seq == 1
    assert ttft.type == DomainEventType.NODE_TTFT_RECORDED
    assert ttft.payload["ttft_ms"] == 1250
    assert ttft.payload["started_at"] == started_at.isoformat()
    assert ttft.payload["first_token_at"] == first_token_at.isoformat()

    sse = format_sse(ttft)
    assert "event: node.ttft_recorded" in sse
    assert "id: 1" in sse

    data_line = [line for line in sse.splitlines() if line.startswith("data: ")][0]
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["type"] == "node.ttft_recorded"
    assert payload["payload"]["ttft_ms"] == 1250


def test_stream_events_replays_then_delivers_live_in_order() -> None:
    repo = InMemoryRunStateRepository()
    run_id = _seed_run(repo)
    stream = EventStreamService(repository=repo)

    stream.publish(_event(event_id="evt_1", run_id=run_id, payload={"index": 1}))
    stream.publish(_event(event_id="evt_2", run_id=run_id, payload={"index": 2}))

    async def _run() -> list[int]:
        iterator = stream.stream_events(run_id=run_id, after_seq=1, request=None)
        task = asyncio.create_task(_collect_n(iterator, count=2))
        await asyncio.sleep(0)
        stream.publish(_event(event_id="evt_3", run_id=run_id, payload={"index": 3}))
        events = await task
        return [event.seq for event in events]

    assert asyncio.run(_run()) == [2, 3]


def test_rejects_invalid_last_event_id_header() -> None:
    repo = InMemoryRunStateRepository()
    run_id = _seed_run(repo)
    stream = EventStreamService(repository=repo)
    app = _build_test_app(stream)
    client = TestClient(app)

    response = client.get(
        f"/api/runs/{run_id}/events",
        headers={"Last-Event-ID": "not-an-int"},
    )

    assert response.status_code == 400
    assert "Last-Event-ID" in response.json()["detail"]


def test_stream_stops_when_request_disconnects() -> None:
    repo = InMemoryRunStateRepository()
    run_id = _seed_run(repo)
    stream = EventStreamService(repository=repo)

    class FakeRequest:
        def __init__(self) -> None:
            self._calls = 0

        async def is_disconnected(self) -> bool:
            self._calls += 1
            return self._calls > 1

    async def _collect() -> list[DomainEvent]:
        iterator = stream.stream_events(
            run_id=run_id, after_seq=0, request=FakeRequest()
        )
        return await _collect_n(iterator, count=1)

    stream.publish(_event(event_id="evt_1", run_id=run_id))
    events = asyncio.run(_collect())
    assert [event.seq for event in events] == [1]
