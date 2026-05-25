"""Merger service: integration-aware synthesis with conflict contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.adapters.llm_client import LLMClient, LLMGenerateRequest, LLMMessage
from app.schemas.contracts import MergeRequest, MergeResponse


class MergerSchemaError(RuntimeError):
    """Raised when merger cannot produce a schema-valid merge response."""


@dataclass(slots=True, frozen=True)
class MergerEvent:
    """Structured merge event candidate for orchestration/streaming hooks."""

    event_type: str
    payload: dict[str, Any]


@dataclass(slots=True, frozen=True)
class MergerServiceResult:
    """Normalized merger result shape for orchestration and checker phases."""

    response: MergeResponse
    checker_payload: dict[str, Any]
    has_unresolved_conflicts: bool
    attempts_used: int
    events: tuple[MergerEvent, ...] = ()


class MergerService:
    """Calls LLM merger and enforces strict MergeResponse schema validation."""

    _MERGE_RESPONSE_ADAPTER = TypeAdapter(MergeResponse)

    def __init__(
        self,
        llm_client: LLMClient,
        max_schema_retries: int = 2,
        temperature: float = 0.0,
    ) -> None:
        if max_schema_retries < 0:
            raise ValueError("max_schema_retries must be >= 0")
        self._llm_client = llm_client
        self._max_schema_retries = max_schema_retries
        self._temperature = temperature

    def merge(self, request: MergeRequest) -> MergerServiceResult:
        """Merge sibling outputs under interface constraints with strict validation."""
        max_attempts = self._max_schema_retries + 1
        last_validation_error: ValidationError | None = None

        started_event = MergerEvent(
            event_type="merge.started",
            payload={
                "parent_objective": request.parent_objective,
                "child_count": len(request.child_outputs),
            },
        )

        for attempt in range(1, max_attempts + 1):
            response_payload = self._llm_client.generate_json(
                request=self._build_request(request=request, attempt=attempt)
            ).data

            try:
                parsed = self._MERGE_RESPONSE_ADAPTER.validate_python(response_payload)
            except ValidationError as exc:
                last_validation_error = exc
                continue

            return self._to_service_result(
                parsed=parsed,
                started_event=started_event,
                attempts_used=attempt,
            )

        raise MergerSchemaError(
            f"merger output failed schema validation after {max_attempts} attempts"
        ) from last_validation_error

    def _build_request(self, *, request: MergeRequest, attempt: int) -> LLMGenerateRequest:
        children = [{"node_id": c.node_id, "persona_id": c.persona_id,
                     "boundary_contract": c.boundary_contract, "output": c.output}
                    for c in request.child_outputs]
        parts = [
            "Synthesize sibling outputs into one coherent result. Resolve conflicts explicitly.",
            f"Parent objective: {request.parent_objective}.",
            f"Child inputs: {json.dumps(children, ensure_ascii=False, sort_keys=True)[:3000]}.",
        ]
        if attempt > 1:
            parts.append("Previous output invalid. Respond with strict JSON only.")
        return LLMGenerateRequest(
            messages=[
                LLMMessage(role="system", content='Return JSON: {merged_output, conflict_resolutions: [{conflict, chosen_approach, rationale}], unresolved_conflicts: [string]}.'),
                LLMMessage(role="user", content=" ".join(parts)),
            ],
            temperature=self._temperature,
            metadata={"service": "merger", "attempt": str(attempt), "child_count": str(len(request.child_outputs))},
        )

    def _to_service_result(self, *, parsed: MergeResponse, started_event: MergerEvent,
                           attempts_used: int) -> MergerServiceResult:
        has_unresolved = len(parsed.unresolved_conflicts) > 0
        resolutions = [r.model_dump() for r in parsed.conflict_resolutions]
        unresolved = list(parsed.unresolved_conflicts)
        completed = MergerEvent("merge.completed", {
            "conflict_resolutions": resolutions, "unresolved_conflicts": unresolved,
            "has_unresolved_conflicts": has_unresolved})
        return MergerServiceResult(
            response=parsed,
            checker_payload={"merged_output": parsed.merged_output, "conflict_resolutions": resolutions,
                             "unresolved_conflicts": unresolved, "integration_ready": not has_unresolved},
            has_unresolved_conflicts=has_unresolved, attempts_used=attempts_used,
            events=(started_event, completed))


__all__ = [
    "MergerEvent",
    "MergerSchemaError",
    "MergerService",
    "MergerServiceResult",
]
