"""Lightweight persona routing helpers based on objective/context heuristics."""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.services.persona_registry import PersonaProfile, PersonaRegistry


@dataclass(frozen=True, slots=True)
class PersonaRouteResult:
    """Persona selection result with explainable lightweight scoring."""

    persona_id: str | None
    confidence: float
    reason: str


class PersonaRouter:
    """Routes objective/context text to best persona from registry."""

    def __init__(self, registry: PersonaRegistry) -> None:
        self._registry = registry

    def select_persona(
        self,
        objective: str,
        *,
        context: str | None = None,
        explicit_persona_id: str | None = None,
    ) -> PersonaRouteResult:
        if explicit_persona_id:
            profile = self._registry.get_profile(explicit_persona_id)
            if profile is not None:
                return PersonaRouteResult(
                    persona_id=profile.persona_id,
                    confidence=1.0,
                    reason="explicit persona override",
                )
            return PersonaRouteResult(
                persona_id=None,
                confidence=0.0,
                reason=f"explicit persona '{explicit_persona_id}' not found",
            )

        profiles = self._registry.list_profiles()
        if not profiles:
            return PersonaRouteResult(
                persona_id=None,
                confidence=0.0,
                reason="no personas loaded in registry",
            )

        combined_text = " ".join(
            part for part in (objective, context or "") if part
        ).lower()
        tokens = _tokenize(combined_text)

        scored: list[tuple[float, PersonaProfile]] = []
        for profile in profiles:
            score = _score_profile(profile, tokens)
            scored.append((score, profile))

        scored.sort(key=lambda item: (item[0], item[1].persona_id), reverse=True)
        top_score, top_profile = scored[0]

        if top_score <= 0:
            fallback = sorted(profiles, key=lambda profile: profile.persona_id)[0]
            return PersonaRouteResult(
                persona_id=fallback.persona_id,
                confidence=0.25,
                reason="no keyword match; defaulted to lexicographically first persona",
            )

        second_score = scored[1][0] if len(scored) > 1 else 0.0
        margin = max(top_score - second_score, 0.0)
        confidence = min(1.0, 0.55 + (margin / max(top_score, 1.0)) * 0.45)
        return PersonaRouteResult(
            persona_id=top_profile.persona_id,
            confidence=round(confidence, 3),
            reason=f"matched routing hints/tools/name with score={top_score:.2f}",
        )


def _score_profile(profile: PersonaProfile, tokens: set[str]) -> float:
    score = 0.0
    hints = {hint.lower() for hint in profile.routing_hints}
    tools = {tool.lower() for tool in profile.tools}
    name_tokens = _tokenize(profile.name.lower())

    for token in tokens:
        if token in hints:
            score += 3.0
        if token in name_tokens:
            score += 1.5
        if token in tools:
            score += 1.0

    if hints and hints.issubset(tokens):
        score += 1.0

    return score


def _tokenize(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9_\-]+", value.lower()) if token}


__all__ = ["PersonaRouteResult", "PersonaRouter"]
