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
            )

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

    def _build_request(
        self, *, request: MergeRequest, attempt: int
    ) -> LLMGenerateRequest:
        repair_hint = ""
        if attempt > 1:
            repair_hint = (
                " Previous output was invalid. Respond with strict JSON only "
                "and include all required fields."
            )

        child_inputs = [
            {
                "node_id": child.node_id,
                "persona_id": child.persona_id,
                "boundary_contract": child.boundary_contract,
                "output": child.output,
            }
            for child in request.child_outputs
        ]

        prompt = (
            "Synthesize sibling outputs into one coherent result. "
            "Resolve interface and assumption conflicts explicitly. "
            f"Parent objective: {request.parent_objective}. "
            f"Child inputs: {json.dumps(child_inputs, ensure_ascii=False, sort_keys=True)[:3000]}."
            f"{repair_hint}"
        )

        return LLMGenerateRequest(
            messages=[
                LLMMessage(
                    role="system",
                    content="Return JSON: {merged_output, conflict_resolutions:"
                    "[{conflict,chosen_approach,rejected_approach?,rationale}], "
                    "unresolved_conflicts:[string]}.",
                ),
                LLMMessage(role="user", content=prompt),
            ],
            temperature=self._temperature,
            metadata={
                "service": "merger",
                "attempt": str(attempt),
                "child_count": str(len(request.child_outputs)),
            },
        )

    def _to_service_result(
        self,
        *,
        parsed: MergeResponse,
        started_event: MergerEvent,
        attempts_used: int,
    ) -> MergerServiceResult:
        has_unresolved = len(parsed.unresolved_conflicts) > 0
        completed_event = MergerEvent(
            event_type="merge.completed",
            payload={
                "conflict_resolutions": [
                    resolution.model_dump()
                    for resolution in parsed.conflict_resolutions
                ],
                "unresolved_conflicts": list(parsed.unresolved_conflicts),
                "has_unresolved_conflicts": has_unresolved,
            },
        )

        checker_payload = {
            "merged_output": parsed.merged_output,
            "conflict_resolutions": [
                resolution.model_dump() for resolution in parsed.conflict_resolutions
            ],
            "unresolved_conflicts": list(parsed.unresolved_conflicts),
            "integration_ready": not has_unresolved,
        }

        return MergerServiceResult(
            response=parsed,
            checker_payload=checker_payload,
            has_unresolved_conflicts=has_unresolved,
            attempts_used=attempts_used,
            events=(started_event, completed_event),
        )


__all__ = [
    "MergerEvent",
    "MergerSchemaError",
    "MergerService",
    "MergerServiceResult",
]
