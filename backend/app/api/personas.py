"""Persona listing API for frontend persona selection."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from app.schemas.api import PersonaSummary
from app.services.persona_registry import PersonaRegistry

router = APIRouter(prefix="/api/personas", tags=["personas"])


def _get_registry(request: Request) -> PersonaRegistry:
    registry = getattr(request.app.state, "persona_registry", None)
    if registry is not None:
        return registry
    # Fallback: build on the fly
    personas_dir = Path(__file__).resolve().parents[3] / "personas"
    registry = PersonaRegistry(personas_dir)
    registry.reload()
    return registry


@router.get("", response_model=list[PersonaSummary])
def list_personas(request: Request) -> list[PersonaSummary]:
    registry = _get_registry(request)
    return [
        PersonaSummary(
            persona_id=profile.persona_id,
            name=profile.name,
            description=profile.description,
        )
        for profile in registry.list_profiles()
    ]


__all__ = ["router"]
