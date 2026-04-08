"""SSE API for run-scoped event streaming with replay semantics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.services.event_stream import EventStreamService
from app.state.memory_repo import InMemoryRunStateRepository

router = APIRouter(prefix="/api/runs", tags=["events"])

_default_repository = InMemoryRunStateRepository()
_event_stream_service: EventStreamService = EventStreamService(
    repository=_default_repository
)


def set_event_stream_service(service: EventStreamService) -> None:
    """Override event stream service instance (used by integration wiring/tests)."""
    global _event_stream_service
    _event_stream_service = service


def get_event_stream_service() -> EventStreamService:
    """FastAPI dependency accessor for event stream service."""
    return _event_stream_service


def _resolve_after_seq(*, after_seq: int | None, last_event_id: str | None) -> int:
    """Resolve replay cursor from query or Last-Event-ID header."""
    resolved = after_seq if after_seq is not None else 0
    if last_event_id is None:
        return resolved
    try:
        header_seq = int(last_event_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Last-Event-ID header: must be integer sequence",
        ) from exc
    if header_seq < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Last-Event-ID header: must be >= 0",
        )
    return max(resolved, header_seq)


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    stream_service: EventStreamService = Depends(get_event_stream_service),
) -> StreamingResponse:
    """Stream replay + live run events via SSE transport."""
    resolved_after_seq = _resolve_after_seq(
        after_seq=after_seq,
        last_event_id=last_event_id,
    )

    try:
        # Validate run existence upfront before opening stream.
        stream_service.list_events(run_id=run_id, after_seq=0)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"run not found: {run_id}",
        ) from exc

    response = StreamingResponse(
        stream_service.stream_sse(
            run_id=run_id,
            after_seq=resolved_after_seq,
            request=request,
        ),
        media_type="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    return response


__all__ = [
    "get_event_stream_service",
    "router",
    "set_event_stream_service",
]
