"""Divider service: LLM-driven base/recursive decomposition decisions.

Supports multi-candidate generation for complex tasks — generates N candidates
at temperature > 0, scores them with a pure-function heuristic, picks the best.
"""
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
from app.services.complexity import ComplexityEstimate


class DividerSchemaError(RuntimeError):
    """Raised when divider cannot obtain a schema-valid model output."""


@dataclass(slots=True, frozen=True)
class DividerDecompositionEvent:
    event_type: str
    payload: dict[str, Any]


@dataclass(slots=True, frozen=True)
class BaseCaseWorkPlan:
    rationale: str
    work_plan: list[dict[str, Any]]
    suggested_persona: str | None
    needs_qa: bool = True


@dataclass(slots=True, frozen=True)
class RecursiveChildSpec:
    objective: str
    dependencies: list[str]
    suggested_persona: str | None
    interface_contract: str | None
    needs_qa: bool = True


@dataclass(slots=True, frozen=True)
class RecursiveDecomposition:
    rationale: str
    children: list[RecursiveChildSpec]


@dataclass(slots=True, frozen=True)
class DividerServiceResult:
    decision: DividerDecision
    base_case: BaseCaseWorkPlan | None = None
    recursive_case: RecursiveDecomposition | None = None
    events: tuple[DividerDecompositionEvent, ...] = ()
    attempts_used: int = 0
    candidates_generated: int = 1


# ---------------------------------------------------------------------------
# Decomposition quality scoring (pure function, no LLM)
# ---------------------------------------------------------------------------

def score_decomposition(parsed: DividerBaseCase | DividerRecursiveCase,
                        objective: str) -> float:
    """Score a decomposition candidate. Higher = better. Range ~0.0-1.0."""
    score = 0.5  # baseline

    if parsed.decision == DividerDecision.BASE_CASE:
        steps = len(parsed.work_plan)
        if 2 <= steps <= 6:
            score += 0.15  # good granularity
        elif steps == 1:
            score -= 0.1   # too coarse
        elif steps > 8:
            score -= 0.05  # too many steps
        if parsed.suggested_persona:
            score += 0.05  # persona awareness
        # Penalize very short rationale
        if len(parsed.rationale) < 20:
            score -= 0.1
        return max(0.0, min(1.0, score))

    # RECURSIVE_CASE
    children = parsed.children
    n = len(children)

    # Child count: 2-5 is sweet spot
    if 2 <= n <= 5:
        score += 0.15
    elif n == 1:
        score -= 0.2  # should be base case
    elif n > 7:
        score -= 0.1  # too many

    # Check for interface contracts (good practice)
    contracts = sum(1 for c in children if c.interface_contract)
    score += min(contracts * 0.03, 0.12)

    # Check for persona suggestions
    personas = sum(1 for c in children if c.suggested_persona)
    score += min(personas * 0.02, 0.08)

    # Penalize duplicate objectives
    objectives = [c.objective.lower().strip() for c in children]
    unique_ratio = len(set(objectives)) / max(len(objectives), 1)
    if unique_ratio < 1.0:
        score -= (1.0 - unique_ratio) * 0.3

    # Check dependency DAG validity (no self-refs, basic sanity)
    all_objectives = set(objectives)
    for c in children:
        for dep in c.dependencies:
            if dep.lower().strip() == c.objective.lower().strip():
                score -= 0.15  # self-dependency
                break

    # Rationale quality
    if len(parsed.rationale) < 20:
        score -= 0.1

    return max(0.0, min(1.0, score))


class DividerService:
    """LLM divider with multi-candidate generation for complex tasks."""

    _DIVIDER_RESULT_ADAPTER = TypeAdapter(DividerResult)

    def __init__(self, llm_client: LLMClient, max_schema_retries: int = 2,
                 temperature: float = 0.0) -> None:
        if max_schema_retries < 0:
            raise ValueError("max_schema_retries must be >= 0")
        self._llm_client = llm_client
        self._max_schema_retries = max_schema_retries
        self._temperature = temperature

    def divide(self, objective: str, depth: int = 0,
               node_context: NodeContext | None = None, *,
               complexity: ComplexityEstimate | None = None,
               num_candidates: int = 1) -> DividerServiceResult:
        """Return schema-validated divide decision.

        When num_candidates > 1, generates multiple candidates at elevated
        temperature and picks the highest-scored one.
        """
        if not objective.strip():
            raise ValueError("objective must be non-empty")
        if depth < 0:
            raise ValueError("depth must be >= 0")

        if num_candidates > 1:
            return self._multi_candidate_divide(
                objective, depth, node_context, num_candidates, complexity,
            )
        return self._single_divide(objective, depth, node_context, complexity)

    def _single_divide(self, objective: str, depth: int,
                       node_context: NodeContext | None,
                       complexity: ComplexityEstimate | None) -> DividerServiceResult:
        """Single-candidate path (original behavior)."""
        max_attempts = self._max_schema_retries + 1
        last_err: ValidationError | None = None

        for attempt in range(1, max_attempts + 1):
            payload = self._llm_client.generate_json(
                request=self._build_request(
                    objective=objective, depth=depth, attempt=attempt,
                    node_context=node_context, complexity=complexity,
                )
            )
            try:
                parsed = self._DIVIDER_RESULT_ADAPTER.validate_python(payload)
            except ValidationError as exc:
                last_err = exc
                continue
            return self._to_service_result(parsed=parsed, attempts_used=attempt)

        raise DividerSchemaError(
            f"divider output failed schema validation after {max_attempts} attempts"
        ) from last_err

    def _multi_candidate_divide(self, objective: str, depth: int,
                                node_context: NodeContext | None,
                                num_candidates: int,
                                complexity: ComplexityEstimate | None,
                                ) -> DividerServiceResult:
        """Generate N candidates, score, pick best."""
        candidates: list[tuple[float, DividerBaseCase | DividerRecursiveCase, int]] = []
        total_attempts = 0

        for _ in range(num_candidates):
            max_attempts = self._max_schema_retries + 1
            for attempt in range(1, max_attempts + 1):
                total_attempts += 1
                payload = self._llm_client.generate_json(
                    request=self._build_request(
                        objective=objective, depth=depth, attempt=attempt,
                        node_context=node_context, complexity=complexity,
                        temperature_override=max(self._temperature, 0.4),
                    )
                )
                try:
                    parsed = self._DIVIDER_RESULT_ADAPTER.validate_python(payload)
                except ValidationError:
                    continue
                sc = score_decomposition(parsed, objective)
                candidates.append((sc, parsed, total_attempts))
                break  # got valid candidate, next

        if not candidates:
            raise DividerSchemaError(
                f"divider failed to produce any valid candidate after {total_attempts} attempts"
            )

        # Pick highest-scored candidate
        candidates.sort(key=lambda t: t[0], reverse=True)
        best_score, best_parsed, best_attempts = candidates[0]

        result = self._to_service_result(parsed=best_parsed, attempts_used=best_attempts)
        return DividerServiceResult(
            decision=result.decision,
            base_case=result.base_case,
            recursive_case=result.recursive_case,
            events=result.events,
            attempts_used=best_attempts,
            candidates_generated=len(candidates),
        )

    def _build_request(self, *, objective: str, depth: int, attempt: int,
                       node_context: NodeContext | None = None,
                       complexity: ComplexityEstimate | None = None,
                       temperature_override: float | None = None,
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

        complexity_hint = ""
        if complexity:
            complexity_hint = (
                f"\n\nComplexity assessment: {complexity.reasoning}. "
                f"Suggested max depth: {complexity.suggested_depth}."
            )

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
            f"{complexity_hint}"
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
            temperature=temperature_override if temperature_override is not None else self._temperature,
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
                decision=DividerDecision.BASE_CASE, base_case=base,
                events=(event,), attempts_used=attempts_used,
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
            decision=DividerDecision.RECURSIVE_CASE, recursive_case=recursive,
            events=(event,), attempts_used=attempts_used,
        )


__all__ = [
    "BaseCaseWorkPlan", "DividerDecompositionEvent", "DividerSchemaError",
    "DividerService", "DividerServiceResult", "RecursiveChildSpec",
    "RecursiveDecomposition", "score_decomposition",
]
