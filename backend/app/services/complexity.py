"""Complexity estimator — heuristic task complexity scoring.

Uses lightweight heuristics (no LLM calls) to estimate task complexity
and recommend decomposition parameters. Feeds into DividerService and
Executor for adaptive behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.models import NodeContext


@dataclass(slots=True, frozen=True)
class ComplexityEstimate:
    score: float           # 0.0 (trivial) to 1.0 (very complex)
    suggested_depth: int   # recommended max decomposition depth
    model_tier: str        # "fast" | "standard" | "strong"
    reasoning: str         # one-line explanation

    @property
    def is_complex(self) -> bool:
        return self.score >= 0.6


# Heuristic weights for complexity signals
_COMPLEXITY_KEYWORDS = {
    "high": ("architect", "design", "system", "integrate", "optimize", "refactor",
             "distributed", "concurrent", "scalable", "migrate", "security", "auth"),
    "medium": ("implement", "build", "create", "develop", "parse", "transform",
               "validate", "test", "debug", "fix", "analyze"),
    "low": ("rename", "format", "log", "print", "comment", "typo", "move", "copy",
            "delete", "remove", "list", "count", "sort"),
}

_MULTI_STEP_MARKERS = ("and", "then", "also", "additionally", "plus",
                       "as well as", "followed by", "including")


class ComplexityEstimator:
    """Heuristic-based complexity scoring — no LLM calls."""

    def estimate(self, objective: str,
                 context: NodeContext | None = None,
                 depth: int = 0) -> ComplexityEstimate:
        score = self._score_objective(objective, depth)
        if context:
            score = self._adjust_for_context(score, context, depth)
        score = max(0.0, min(1.0, score))
        depth_suggestion = self._suggest_depth(score)
        tier = self._select_tier(score)
        reasoning = self._explain(score, objective)
        return ComplexityEstimate(
            score=round(score, 2),
            suggested_depth=depth_suggestion,
            model_tier=tier,
            reasoning=reasoning,
        )

    def _score_objective(self, objective: str, depth: int) -> float:
        text = objective.lower()
        words = text.split()
        score = 0.3  # baseline

        # Length signal: longer objectives tend to be more complex
        word_count = len(words)
        if word_count > 50:
            score += 0.15
        elif word_count > 20:
            score += 0.08
        elif word_count < 5:
            score -= 0.1

        # Keyword scoring
        for kw in _COMPLEXITY_KEYWORDS["high"]:
            if kw in text:
                score += 0.08
        for kw in _COMPLEXITY_KEYWORDS["medium"]:
            if kw in text:
                score += 0.03
        for kw in _COMPLEXITY_KEYWORDS["low"]:
            if kw in text:
                score -= 0.05

        # Multi-step markers (conjunctions suggesting compound tasks)
        multi_step_count = sum(1 for m in _MULTI_STEP_MARKERS if f" {m} " in f" {text} ")
        score += multi_step_count * 0.06

        # Depth penalty: deeper nodes are naturally simpler sub-tasks
        score -= depth * 0.08

        return score

    def _adjust_for_context(self, score: float, context: NodeContext,
                            depth: int) -> float:
        # Checker feedback = previous failure = harder
        if context.checker_feedback:
            score += 0.15
        # Many siblings = complex parent decomposition
        if len(context.sibling_objectives) > 4:
            score += 0.05
        # Deep parent chain = well-decomposed already
        if len(context.parent_chain) > 3:
            score -= 0.1
        return score

    @staticmethod
    def _suggest_depth(score: float) -> int:
        if score >= 0.8:
            return 6
        if score >= 0.6:
            return 4
        if score >= 0.4:
            return 3
        return 2

    @staticmethod
    def _select_tier(score: float) -> str:
        if score >= 0.7:
            return "strong"
        if score >= 0.4:
            return "standard"
        return "fast"

    @staticmethod
    def _explain(score: float, objective: str) -> str:
        level = "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"
        words = len(objective.split())
        return f"complexity={level} (score={score:.2f}, words={words})"


__all__ = ["ComplexityEstimate", "ComplexityEstimator"]
