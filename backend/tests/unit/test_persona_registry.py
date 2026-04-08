from __future__ import annotations

from pathlib import Path

from app.services.persona_registry import PersonaRegistry
from app.services.persona_router import PersonaRouter


def _write_persona(
    path: Path,
    *,
    name: str,
    prompt: str,
    guardrails: list[str],
    tools: list[str],
    hints: list[str],
    description: str = "",
) -> None:
    lines = [
        "# Persona Profile",
        "",
        "## Metadata",
        f"- name: {name}",
        f"- description: {description or (name + ' persona')}",
        "",
        "## System Prompt",
        prompt,
        "",
        "## Guardrails",
        *[f"- {item}" for item in guardrails],
        "",
        "## Tools",
        *[f"- {item}" for item in tools],
        "",
        "## Routing Hints",
        *[f"- {item}" for item in hints],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_registry_loads_and_discovers_new_persona_on_refresh(tmp_path: Path) -> None:
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    _write_persona(
        personas_dir / "python_developer.md",
        name="Python Developer",
        prompt="You write robust Python services.",
        guardrails=["Be explicit", "Prefer tests"],
        tools=["python_runtime", "search_api"],
        hints=["python", "fastapi", "backend"],
    )

    registry = PersonaRegistry(personas_dir)
    registry.reload()

    assert registry.has_profile("python_developer")
    assert not registry.has_profile("sql_developer")

    _write_persona(
        personas_dir / "sql_developer.md",
        name="SQL Developer",
        prompt="You design performant SQL.",
        guardrails=["Assume explicit schema"],
        tools=["sql_console", "search_api"],
        hints=["sql", "query", "database"],
    )

    registry.refresh()

    assert registry.has_profile("python_developer")
    assert registry.has_profile("sql_developer")

    sql_profile = registry.get_profile("sql_developer")
    assert sql_profile is not None
    assert len(sql_profile.profile_hash) == 64
    assert len(sql_profile.prompt_guardrails_hash) == 64


def test_registry_refresh_handles_rename_and_remove(tmp_path: Path) -> None:
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    original = personas_dir / "python_developer.md"
    _write_persona(
        original,
        name="Python Developer",
        prompt="Python prompt",
        guardrails=["Guardrail A"],
        tools=["python_runtime"],
        hints=["python"],
    )

    registry = PersonaRegistry(personas_dir)
    registry.reload()
    assert registry.has_profile("python_developer")

    renamed = personas_dir / "python_engineer.md"
    original.rename(renamed)
    registry.refresh()

    assert not registry.has_profile("python_developer")
    assert registry.has_profile("python_engineer")

    renamed.unlink()
    registry.refresh()

    assert registry.list_profiles() == []


def test_invalid_persona_returns_explicit_diagnostics(tmp_path: Path) -> None:
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    invalid_path = personas_dir / "broken_profile.md"
    invalid_path.write_text(
        "\n".join(
            [
                "# Persona Profile",
                "",
                "## Metadata",
                "- name:",
                "",
                "## System Prompt",
                "",
                "## Guardrails",
                "not a bullet",
                "",
            ]
        ),
        encoding="utf-8",
    )

    registry = PersonaRegistry(personas_dir)
    registry.reload()

    assert registry.get_profile("broken_profile") is None
    diagnostics = registry.diagnostics_for("broken_profile")
    assert diagnostics, "Expected explicit parser diagnostics for invalid persona"

    codes = {item.code for item in diagnostics}
    assert "missing_section" in codes
    assert "invalid_metadata_entry" in codes or "missing_metadata_field" in codes
    assert "invalid_bullet" in codes or "empty_section" in codes


def test_router_selects_persona_with_lightweight_heuristics(tmp_path: Path) -> None:
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    _write_persona(
        personas_dir / "python_developer.md",
        name="Python Developer",
        prompt="Python prompt",
        guardrails=["Guardrail"],
        tools=["python_runtime"],
        hints=["python", "fastapi", "backend"],
    )
    _write_persona(
        personas_dir / "sql_developer.md",
        name="SQL Developer",
        prompt="SQL prompt",
        guardrails=["Guardrail"],
        tools=["sql_console"],
        hints=["sql", "database", "query"],
    )

    registry = PersonaRegistry(personas_dir)
    registry.reload()
    router = PersonaRouter(registry)

    python_choice = router.select_persona(
        objective="Build a FastAPI endpoint with Python",
        context="Backend service refactor",
    )
    assert python_choice.persona_id == "python_developer"
    assert python_choice.confidence > 0.5

    sql_choice = router.select_persona(
        objective="Optimize a SQL query for a relational database",
        context="Need index strategy",
    )
    assert sql_choice.persona_id == "sql_developer"
    assert sql_choice.confidence > 0.5
