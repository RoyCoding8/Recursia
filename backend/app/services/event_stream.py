"""In-process event stream service with SSE replay/resume support."""

from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from fastapi import Request

from app.domain.events import DomainEvent, DomainEventType
from app.state.repository import RunStateRepository


@dataclass(slots=True, frozen=True)
class EventEnvelope:
    """Serialized envelope pushed to SSE clients."""

    event_id: str
    run_id: str
    node_id: str | None
    seq: int
    type: str
    ts: str
    payload: dict[str, object]


def serialize_event(event: DomainEvent) -> EventEnvelope:
    """Convert a domain event into transport-safe envelope."""
    return EventEnvelope(
        event_id=event.event_id,
        run_id=event.run_id,
        node_id=event.node_id,
        seq=event.seq,
        type=event.type.value,
        ts=event.ts.isoformat(),
        payload=dict(event.payload),
    )


def format_sse(event: DomainEvent) -> str:
    """Format one domain event as a Server-Sent Event frame."""
    envelope = serialize_event(event)
    body = {
        "event_id": envelope.event_id,
        "run_id": envelope.run_id,
        "node_id": envelope.node_id,
        "seq": envelope.seq,
        "type": envelope.type,
        "ts": envelope.ts,
        "payload": envelope.payload,
    }
    return (
        f"id: {envelope.seq}\n"
        f"event: {envelope.type}\n"
        f"data: {json.dumps(body, separators=(',', ':'))}\n\n"
    )


class EventStreamService:
    """Publishes domain events with per-run sequence and SSE fanout."""

    def __init__(self, *, repository: RunStateRepository) -> None:
        self._repository = repository
        self._subscribers: dict[str, set[asyncio.Queue[DomainEvent]]] = defaultdict(set)
        self._subscribers_lock = threading.Lock()

    def publish(self, event: DomainEvent) -> DomainEvent:
        """Persist event and broadcast to live run subscribers."""
        stored = self._repository.append_event(event)
        with self._subscribers_lock:
            subscribers = tuple(self._subscribers.get(stored.run_id, set()))

        for queue in subscribers:
            try:
                queue.put_nowait(stored)
            except asyncio.QueueFull:
                # Defensive: unbounded queues are used in MVP, but never crash on fanout.
                continue
        return stored

    def publish_ttft(
        self,
        *,
        run_id: str,
        node_id: str,
        ttft_ms: int,
        started_at: datetime | None = None,
        first_token_at: datetime | None = None,
        event_id: str,
    ) -> DomainEvent:
        """Publish a typed TTFT metric event for first token timing."""
        payload: dict[str, object] = {"ttft_ms": ttft_ms}
        if started_at is not None:
            payload["started_at"] = started_at.isoformat()
        if first_token_at is not None:
            payload["first_token_at"] = first_token_at.isoformat()
        return self.publish(
            DomainEvent(
                event_id=event_id,
                run_id=run_id,
                node_id=node_id,
                type=DomainEventType.NODE_TTFT_RECORDED,
                payload=payload,
            )
        )

    def list_events(self, *, run_id: str, after_seq: int = 0) -> list[DomainEvent]:
        """List persisted events after sequence for replay semantics."""
        return self._repository.list_run_events(run_id=run_id, after_seq=after_seq)

    async def stream_events(
        self,
        *,
        run_id: str,
        after_seq: int = 0,
        request: Request | None = None,
    ) -> AsyncIterator[DomainEvent]:
        """Yield replay + live events in strict sequence order."""
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue()
        with self._subscribers_lock:
            self._subscribers[run_id].add(queue)

        last_seq = after_seq
        try:
            for replay in self._repository.list_run_events(
                run_id=run_id, after_seq=after_seq
            ):
                if replay.seq <= last_seq:
                    continue
                last_seq = replay.seq
                yield replay

            while True:
                if request is not None and await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                except TimeoutError:
                    continue

                if event.seq <= last_seq:
                    continue
                last_seq = event.seq
                yield event
        finally:
            with self._subscribers_lock:
                subscribers = self._subscribers.get(run_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(run_id, None)

    async def stream_sse(
        self,
        *,
        run_id: str,
        after_seq: int = 0,
        request: Request | None = None,
    ) -> AsyncIterator[str]:
        """Yield SSE-formatted frames with replay and reconnect support."""
        async for event in self.stream_events(
            run_id=run_id,
            after_seq=after_seq,
            request=request,
        ):
            yield format_sse(event)


__all__ = [
    "EventEnvelope",
    "EventStreamService",
    "format_sse",
    "serialize_event",
]
