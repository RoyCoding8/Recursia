"""Persona listing API for frontend persona selection."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from app.schemas.api import PersonaSummary
from app.services.persona_registry import PersonaRegistry

router = APIRouter(prefix="/api/personas", tags=["personas"])


@router.get("", response_model=list[PersonaSummary])
def list_personas() -> list[PersonaSummary]:
    personas_dir = Path(__file__).resolve().parents[3] / "personas"
    registry = PersonaRegistry(personas_dir)
    registry.reload()

    return [
        PersonaSummary(
            persona_id=profile.persona_id,
            name=profile.name,
            description=profile.description,
        )
        for profile in registry.list_profiles()
    ]


__all__ = ["router"]
