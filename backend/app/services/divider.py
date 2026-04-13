"""Divider service: LLM-driven base/recursive decomposition decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.adapters.llm_client import LLMClient, LLMGenerateRequest, LLMMessage
from app.domain.models import NodeContext
from app.schemas.contracts import (
    DividerBaseCase,
    DividerDecision,
    DividerRecursiveCase,
    DividerResult,
)


class DividerSchemaError(RuntimeError):
    """Raised when divider cannot obtain a schema-valid model output."""


@dataclass(slots=True, frozen=True)
class DividerDecompositionEvent:
    """Structured decomposition event candidate for persistence/streaming."""

    event_type: str
    payload: dict[str, Any]


@dataclass(slots=True, frozen=True)
class BaseCaseWorkPlan:
    """Normalized base-case output for orchestration usage."""

    rationale: str
    work_plan: list[dict[str, Any]]
    suggested_persona: str | None
    needs_qa: bool = True


@dataclass(slots=True, frozen=True)
class RecursiveChildSpec:
    """Normalized child decomposition unit for recursive scheduling."""

    objective: str
    dependencies: list[str]
    suggested_persona: str | None
    interface_contract: str | None
    needs_qa: bool = True


@dataclass(slots=True, frozen=True)
class RecursiveDecomposition:
    """Normalized recursive-case output for child-node creation."""

    rationale: str
    children: list[RecursiveChildSpec]


@dataclass(slots=True, frozen=True)
class DividerServiceResult:
    """Union-like normalized return shape from divider service."""

    decision: DividerDecision
    base_case: BaseCaseWorkPlan | None = None
    recursive_case: RecursiveDecomposition | None = None
    events: tuple[DividerDecompositionEvent, ...] = ()
    attempts_used: int = 0


class DividerService:
    """Calls LLM and enforces strict divider schema with bounded retries."""

    _DIVIDER_RESULT_ADAPTER = TypeAdapter(DividerResult)

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

    def divide(self, objective: str, depth: int = 0,
               node_context: NodeContext | None = None) -> DividerServiceResult:
        """Return schema-validated divide decision and normalized payload."""
        if not objective.strip():
            raise ValueError("objective must be non-empty")
        if depth < 0:
            raise ValueError("depth must be >= 0")

        max_attempts = self._max_schema_retries + 1
        last_validation_error: ValidationError | None = None

        for attempt in range(1, max_attempts + 1):
            response_payload = self._llm_client.generate_json(
                request=self._build_request(
                    objective=objective, depth=depth, attempt=attempt,
                    node_context=node_context,
                )
            )

            try:
                parsed = self._DIVIDER_RESULT_ADAPTER.validate_python(response_payload)
            except ValidationError as exc:
                last_validation_error = exc
                continue

            return self._to_service_result(parsed=parsed, attempts_used=attempt)

        raise DividerSchemaError(
            f"divider output failed schema validation after {max_attempts} attempts"
        ) from last_validation_error

    def _build_request(
        self,
        *,
        objective: str,
        depth: int,
        attempt: int,
        node_context: NodeContext | None = None,
    ) -> LLMGenerateRequest:
        repair_hint = ""
        if attempt > 1:
            repair_hint = (
                " Previous output was invalid. "
                "Respond with schema-valid JSON only (no markdown/code fences)."
            )

        lineage = ""
        if node_context:
            lineage = f"\n\nContext:\n{node_context.to_prompt_block()}"

        prompt = (
            "Decide: is this a BASE_CASE (single linear work plan) or "
            "RECURSIVE_CASE (decompose into 2+ sub-objectives)?\n\n"
            "BASE_CASE requires: decision, rationale, work_plan (step+description), "
            "suggested_persona, needs_qa.\n"
            "RECURSIVE_CASE requires: decision, rationale, children (objective, "
            "dependencies, suggested_persona, interface_contract, needs_qa).\n\n"
            f"Objective: {objective}\n"
            f"Depth: {depth}."
            f"{lineage}"
            f"{repair_hint}"
        )

        return LLMGenerateRequest(
            messages=[
                LLMMessage(
                    role="system",
                    content="Return strict JSON for divider contract. "
                    'decision MUST be "BASE_CASE" or "RECURSIVE_CASE". '
                    "work_plan steps are integers starting at 1.",
                ),
                LLMMessage(role="user", content=prompt),
            ],
            temperature=self._temperature,
            metadata={
                "service": "divider",
                "attempt": str(attempt),
                "depth": str(depth),
            },
        )

    def _to_service_result(
        self, parsed: DividerBaseCase | DividerRecursiveCase, attempts_used: int
    ) -> DividerServiceResult:
        if parsed.decision == DividerDecision.BASE_CASE:
            base = BaseCaseWorkPlan(
                rationale=parsed.rationale,
                work_plan=[step.model_dump() for step in parsed.work_plan],
                suggested_persona=parsed.suggested_persona,
                needs_qa=getattr(parsed, "needs_qa", True),
            )
            event = DividerDecompositionEvent(
                event_type="node.decomposed",
                payload={
                    "decision": DividerDecision.BASE_CASE.value,
                    "rationale": parsed.rationale,
                    "work_plan": [step.model_dump() for step in parsed.work_plan],
                    "suggested_persona": parsed.suggested_persona,
                },
            )
            return DividerServiceResult(
                decision=DividerDecision.BASE_CASE,
                base_case=base,
                events=(event,),
                attempts_used=attempts_used,
            )

        recursive = RecursiveDecomposition(
            rationale=parsed.rationale,
            children=[
                RecursiveChildSpec(
                    objective=child.objective,
                    dependencies=list(child.dependencies),
                    suggested_persona=child.suggested_persona,
                    interface_contract=child.interface_contract,
                    needs_qa=getattr(child, "needs_qa", True),
                )
                for child in parsed.children
            ],
        )
        event = DividerDecompositionEvent(
            event_type="node.decomposed",
            payload={
                "decision": DividerDecision.RECURSIVE_CASE.value,
                "rationale": parsed.rationale,
                "children": [child.model_dump() for child in parsed.children],
            },
        )
        return DividerServiceResult(
            decision=DividerDecision.RECURSIVE_CASE,
            recursive_case=recursive,
            events=(event,),
            attempts_used=attempts_used,
        )


__all__ = [
    "BaseCaseWorkPlan",
    "DividerDecompositionEvent",
    "DividerSchemaError",
    "DividerService",
    "DividerServiceResult",
    "RecursiveChildSpec",
    "RecursiveDecomposition",
]
