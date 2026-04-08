"""Persona markdown registry with validation diagnostics and hot-reload support."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Iterable
import re


_REQUIRED_SECTIONS: tuple[str, ...] = (
    "Metadata",
    "System Prompt",
    "Guardrails",
    "Tools",
)


@dataclass(frozen=True, slots=True)
class PersonaDiagnostic:
    """Validation diagnostic for an invalid persona profile."""

    code: str
    message: str
    persona_id: str
    path: str
    line: int | None = None


@dataclass(frozen=True, slots=True)
class PersonaProfile:
    """Normalized persona profile loaded from markdown."""

    persona_id: str
    name: str
    description: str
    system_prompt: str
    guardrails: tuple[str, ...]
    tools: tuple[str, ...]
    routing_hints: tuple[str, ...]
    source_path: str
    profile_hash: str
    prompt_guardrails_hash: str


@dataclass(frozen=True, slots=True)
class ParsedPersona:
    """Internal parse result containing either profile or diagnostics."""

    profile: PersonaProfile | None = None
    diagnostics: tuple[PersonaDiagnostic, ...] = field(default_factory=tuple)


class PersonaRegistry:
    """Loads markdown personas from disk and supports explicit refresh/reload."""

    def __init__(self, personas_dir: str | Path) -> None:
        self._personas_dir = Path(personas_dir)
        self._profiles: dict[str, PersonaProfile] = {}
        self._diagnostics: dict[str, tuple[PersonaDiagnostic, ...]] = {}

    @property
    def personas_dir(self) -> Path:
        return self._personas_dir

    def reload(self) -> None:
        """Rescan `personas/*.md`, replacing registry state atomically."""
        profiles: dict[str, PersonaProfile] = {}
        diagnostics: dict[str, tuple[PersonaDiagnostic, ...]] = {}

        if not self._personas_dir.exists():
            self._profiles = {}
            self._diagnostics = {}
            return

        for md_path in sorted(self._personas_dir.glob("*.md")):
            parsed = parse_persona_markdown(md_path)
            persona_id = _persona_id_from_path(md_path)

            if parsed.profile is not None:
                profiles[parsed.profile.persona_id] = parsed.profile
            else:
                diagnostics[persona_id] = parsed.diagnostics

        self._profiles = profiles
        self._diagnostics = diagnostics

    def refresh(self) -> None:
        """Alias for reload to match refresh-oriented callers."""
        self.reload()

    def list_profiles(self) -> list[PersonaProfile]:
        return sorted(self._profiles.values(), key=lambda profile: profile.persona_id)

    def get_profile(self, persona_id: str) -> PersonaProfile | None:
        return self._profiles.get(_normalize_persona_id(persona_id))

    def has_profile(self, persona_id: str) -> bool:
        return _normalize_persona_id(persona_id) in self._profiles

    def diagnostics_for(self, persona_id: str) -> tuple[PersonaDiagnostic, ...]:
        return self._diagnostics.get(_normalize_persona_id(persona_id), tuple())

    def all_diagnostics(self) -> dict[str, tuple[PersonaDiagnostic, ...]]:
        return dict(self._diagnostics)


def parse_persona_markdown(path: str | Path) -> ParsedPersona:
    """Parse markdown persona file into a validated profile or diagnostics."""
    md_path = Path(path)
    persona_id = _persona_id_from_path(md_path)
    raw = md_path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    sections = _extract_sections(lines)

    diagnostics: list[PersonaDiagnostic] = []

    for section in _REQUIRED_SECTIONS:
        if section not in sections:
            diagnostics.append(
                PersonaDiagnostic(
                    code="missing_section",
                    message=f"Missing required section: '{section}'",
                    persona_id=persona_id,
                    path=str(md_path),
                )
            )

    metadata: dict[str, str] = {}
    if "Metadata" in sections:
        metadata, metadata_diags = _parse_key_value_bullets(
            sections["Metadata"],
            persona_id=persona_id,
            path=str(md_path),
            section_name="Metadata",
        )
        diagnostics.extend(metadata_diags)

    name = metadata.get("name", "").strip()
    description = metadata.get("description", "").strip()
    if not name:
        diagnostics.append(
            PersonaDiagnostic(
                code="missing_metadata_field",
                message="Metadata field 'name' is required and must be non-empty",
                persona_id=persona_id,
                path=str(md_path),
                line=_first_line_of_section(sections.get("Metadata")),
            )
        )

    system_prompt = ""
    if "System Prompt" in sections:
        system_prompt = _join_section_content(sections["System Prompt"]).strip()
        if not system_prompt:
            diagnostics.append(
                PersonaDiagnostic(
                    code="empty_section",
                    message="Section 'System Prompt' must contain non-empty content",
                    persona_id=persona_id,
                    path=str(md_path),
                    line=_first_line_of_section(sections["System Prompt"]),
                )
            )

    guardrails: tuple[str, ...] = tuple()
    if "Guardrails" in sections:
        guardrails, guardrail_diags = _parse_list_bullets(
            sections["Guardrails"],
            persona_id=persona_id,
            path=str(md_path),
            section_name="Guardrails",
        )
        diagnostics.extend(guardrail_diags)
        if not guardrails:
            diagnostics.append(
                PersonaDiagnostic(
                    code="empty_section",
                    message="Section 'Guardrails' must contain at least one bullet item",
                    persona_id=persona_id,
                    path=str(md_path),
                    line=_first_line_of_section(sections["Guardrails"]),
                )
            )

    tools: tuple[str, ...] = tuple()
    if "Tools" in sections:
        tools, tool_diags = _parse_list_bullets(
            sections["Tools"],
            persona_id=persona_id,
            path=str(md_path),
            section_name="Tools",
        )
        diagnostics.extend(tool_diags)
        if not tools:
            diagnostics.append(
                PersonaDiagnostic(
                    code="empty_section",
                    message="Section 'Tools' must contain at least one bullet item",
                    persona_id=persona_id,
                    path=str(md_path),
                    line=_first_line_of_section(sections["Tools"]),
                )
            )

    routing_hints: tuple[str, ...] = tuple()
    if "Routing Hints" in sections:
        routing_hints, hint_diags = _parse_list_bullets(
            sections["Routing Hints"],
            persona_id=persona_id,
            path=str(md_path),
            section_name="Routing Hints",
        )
        diagnostics.extend(hint_diags)

    if diagnostics:
        return ParsedPersona(profile=None, diagnostics=tuple(diagnostics))

    profile_hash = _compute_profile_hash(
        persona_id=persona_id,
        name=name,
        description=description,
        system_prompt=system_prompt,
        guardrails=guardrails,
        tools=tools,
        routing_hints=routing_hints,
    )

    prompt_guardrails_hash = _compute_prompt_guardrails_hash(
        system_prompt=system_prompt,
        guardrails=guardrails,
    )

    profile = PersonaProfile(
        persona_id=persona_id,
        name=name,
        description=description,
        system_prompt=system_prompt,
        guardrails=guardrails,
        tools=tools,
        routing_hints=routing_hints,
        source_path=str(md_path),
        profile_hash=profile_hash,
        prompt_guardrails_hash=prompt_guardrails_hash,
    )
    return ParsedPersona(profile=profile, diagnostics=tuple())


def _extract_sections(lines: list[str]) -> dict[str, list[tuple[int, str]]]:
    sections: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None

    for idx, line in enumerate(lines, start=1):
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append((idx, line))

    return sections


def _parse_key_value_bullets(
    section_lines: Iterable[tuple[int, str]],
    *,
    persona_id: str,
    path: str,
    section_name: str,
) -> tuple[dict[str, str], list[PersonaDiagnostic]]:
    values: dict[str, str] = {}
    diagnostics: list[PersonaDiagnostic] = []

    for line_no, text in section_lines:
        stripped = text.strip()
        if not stripped:
            continue
        if not stripped.startswith("- "):
            diagnostics.append(
                PersonaDiagnostic(
                    code="invalid_bullet",
                    message=f"Section '{section_name}' expects '- key: value' bullet entries",
                    persona_id=persona_id,
                    path=path,
                    line=line_no,
                )
            )
            continue

        content = stripped[2:].strip()
        if ":" not in content:
            diagnostics.append(
                PersonaDiagnostic(
                    code="invalid_metadata_entry",
                    message=f"Invalid metadata entry '{content}', expected 'key: value'",
                    persona_id=persona_id,
                    path=path,
                    line=line_no,
                )
            )
            continue

        key, value = content.split(":", 1)
        key = key.strip().lower()
        value = value.strip()

        if not key or not value:
            diagnostics.append(
                PersonaDiagnostic(
                    code="invalid_metadata_entry",
                    message=f"Invalid metadata entry '{content}', key and value are required",
                    persona_id=persona_id,
                    path=path,
                    line=line_no,
                )
            )
            continue

        values[key] = value

    return values, diagnostics


def _parse_list_bullets(
    section_lines: Iterable[tuple[int, str]],
    *,
    persona_id: str,
    path: str,
    section_name: str,
) -> tuple[tuple[str, ...], list[PersonaDiagnostic]]:
    entries: list[str] = []
    diagnostics: list[PersonaDiagnostic] = []

    for line_no, text in section_lines:
        stripped = text.strip()
        if not stripped:
            continue
        if not stripped.startswith("- "):
            diagnostics.append(
                PersonaDiagnostic(
                    code="invalid_bullet",
                    message=f"Section '{section_name}' expects '- value' bullet entries",
                    persona_id=persona_id,
                    path=path,
                    line=line_no,
                )
            )
            continue

        value = stripped[2:].strip()
        if not value:
            diagnostics.append(
                PersonaDiagnostic(
                    code="empty_list_entry",
                    message=f"Section '{section_name}' contains an empty bullet entry",
                    persona_id=persona_id,
                    path=path,
                    line=line_no,
                )
            )
            continue
        entries.append(value)

    unique_entries = tuple(dict.fromkeys(entries))
    return unique_entries, diagnostics


def _join_section_content(section_lines: Iterable[tuple[int, str]]) -> str:
    values = [line for _, line in section_lines]
    return "\n".join(values).strip()


def _first_line_of_section(section_lines: list[tuple[int, str]] | None) -> int | None:
    if not section_lines:
        return None
    return section_lines[0][0]


def _persona_id_from_path(path: Path) -> str:
    return _normalize_persona_id(path.stem)


def _normalize_persona_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value.strip().lower())
    cleaned = cleaned.strip("_")
    return cleaned


def _compute_profile_hash(
    *,
    persona_id: str,
    name: str,
    description: str,
    system_prompt: str,
    guardrails: tuple[str, ...],
    tools: tuple[str, ...],
    routing_hints: tuple[str, ...],
) -> str:
    payload = "\n".join(
        [
            persona_id,
            name,
            description,
            system_prompt,
            *guardrails,
            *tools,
            *routing_hints,
        ]
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _compute_prompt_guardrails_hash(
    *, system_prompt: str, guardrails: tuple[str, ...]
) -> str:
    payload = "\n".join([system_prompt, *guardrails])
    return sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "PersonaDiagnostic",
    "PersonaProfile",
    "PersonaRegistry",
    "ParsedPersona",
    "parse_persona_markdown",
]
