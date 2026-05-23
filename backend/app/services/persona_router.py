"""Persona routing: matches objective text to best persona using structured routing config."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.persona_registry import PersonaProfile, PersonaRegistry


@dataclass(frozen=True, slots=True)
class PersonaRouteResult:
    persona_id: str | None
    confidence: float
    reason: str


class PersonaRouter:
    def __init__(self, registry: PersonaRegistry) -> None:
        self._registry = registry

    def select_persona(self, objective: str, *, context: str | None = None,
                       explicit_persona_id: str | None = None) -> PersonaRouteResult:
        if explicit_persona_id:
            p = self._registry.get_profile(explicit_persona_id)
            if p:
                return PersonaRouteResult(p.persona_id, 1.0, "explicit override")
            return PersonaRouteResult(None, 0.0, f"explicit '{explicit_persona_id}' not found")

        profiles = self._registry.list_profiles()
        if not profiles:
            return PersonaRouteResult(None, 0.0, "no personas loaded")

        tokens = _tokenize(" ".join(p for p in (objective, context or "") if p))
        scored = [(self._score(p, tokens), p) for p in profiles]
        scored.sort(key=lambda x: (x[0], x[1].persona_id), reverse=True)
        top_score, top = scored[0]

        if top_score <= 0:
            fallback = sorted(profiles, key=lambda p: p.persona_id)[0]
            return PersonaRouteResult(fallback.persona_id, 0.25, "no match; defaulted to first")

        second = scored[1][0] if len(scored) > 1 else 0.0
        margin = max(top_score - second, 0.0)
        conf = min(1.0, 0.55 + (margin / max(top_score, 1.0)) * 0.45)
        return PersonaRouteResult(top.persona_id, round(conf, 3),
                                  f"matched routing hints with score={top_score:.2f}")

    @staticmethod
    def _score(profile: PersonaProfile, tokens: set[str]) -> float:
        score = 0.0
        # Use structured routing config
        hints = {h.lower() for h in profile.routing.hints}
        tools = {t.lower() for t in profile.tools}
        name_tokens = _tokenize(profile.name.lower())
        desc_tokens = _tokenize(profile.description.lower()) if profile.description else set()

        for t in tokens:
            if t in hints:
                score += 3.0
            if t in name_tokens:
                score += 1.5
            if t in tools:
                score += 1.0
            if t in desc_tokens:
                score += 0.5

        if hints and hints.issubset(tokens):
            score += 1.0

        # Apply routing weight multiplier
        score *= profile.routing.weight
        return score


def _tokenize(value: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9_\-]+", value.lower()) if t}


__all__ = ["PersonaRouteResult", "PersonaRouter"]
