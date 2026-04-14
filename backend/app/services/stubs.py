"""Deterministic stub implementations for dev/test fallback wiring."""

from __future__ import annotations

from typing import Any

from app.domain.models import NodeContext
from app.schemas.contracts import DividerDecision
from app.services.divider import BaseCaseWorkPlan, DividerServiceResult
from app.services.persona_router import PersonaRouteResult


class DeterministicDivider:
    """Explicit deterministic divider for dev/test fallback wiring."""

    def divide(self, objective: str, depth: int = 0,
               node_context: NodeContext | None = None) -> DividerServiceResult:
        return DividerServiceResult(
            decision=DividerDecision.BASE_CASE,
            base_case=BaseCaseWorkPlan(
                rationale="Explicit deterministic fallback path (dev/test)",
                work_plan=[{"step": 1, "description": f"Execute objective at depth {depth}: {objective}"}],
                suggested_persona="python_developer",
            ),
            attempts_used=1,
        )


class DeterministicPersonaRouter:
    """Explicit deterministic persona router for dev/test fallback wiring."""

    def select_persona(
        self,
        objective: str,
        *,
        context: str | None = None,
        explicit_persona_id: str | None = None,
    ) -> PersonaRouteResult:
        return PersonaRouteResult(
            persona_id=explicit_persona_id or "python_developer",
            confidence=1.0,
            reason="deterministic fallback persona route",
        )


class DeterministicBaseCaseWorker:
    """Fallback worker used when no real executor is supplied."""

    def execute(
        self,
        *,
        run_id: str,
        node_id: str,
        objective: str,
        depth: int,
        persona_id: str | None,
        work_plan: list[dict[str, Any]],
        node_context: NodeContext | None = None,
    ) -> Any:
        from app.services.executor import WorkExecutionResult

        synthesized = {
            "run_id": run_id,
            "node_id": node_id,
            "objective": objective,
            "depth": depth,
            "persona_id": persona_id,
            "steps": [step["description"] for step in work_plan],
        }
        return WorkExecutionResult.completed(synthesized)


__all__ = ["DeterministicDivider", "DeterministicPersonaRouter", "DeterministicBaseCaseWorker"]
