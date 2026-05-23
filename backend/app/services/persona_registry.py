"""Persona registry v2: loads SKILL.md folder packages with YAML frontmatter.

Supports both:
  - Folder packages: personas/<name>/SKILL.md (preferred)
  - Legacy flat files: personas/<name>.md (auto-detected, backward compat)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class PersonaDiagnostic:
    code: str
    message: str
    persona_id: str
    path: str
    line: int | None = None


@dataclass(frozen=True, slots=True)
class ModelConfig:
    preferred: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class RoutingConfig:
    hints: tuple[str, ...] = ()
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class HooksConfig:
    pre: str | None = None   # relative path to pre-hook script
    post: str | None = None  # relative path to post-hook script


@dataclass(frozen=True, slots=True)
class PersonaProfile:
    persona_id: str
    name: str
    description: str
    system_prompt: str
    guardrails: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    output_schema: dict[str, Any] | None = None
    examples: tuple[dict[str, Any], ...] = ()
    source_path: str = ""
    package_dir: str | None = None  # folder path if folder package
    profile_hash: str = ""
    prompt_guardrails_hash: str = ""
    is_builtin: bool = False
    # Legacy compat
    routing_hints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedPersona:
    profile: PersonaProfile | None = None
    diagnostics: tuple[PersonaDiagnostic, ...] = field(default_factory=tuple)


class PersonaRegistry:
    """Loads persona packages from disk. Supports folder and legacy flat formats."""

    def __init__(self, personas_dir: str | Path) -> None:
        self._personas_dir = Path(personas_dir)
        self._profiles: dict[str, PersonaProfile] = {}
        self._diagnostics: dict[str, tuple[PersonaDiagnostic, ...]] = {}

    @property
    def personas_dir(self) -> Path:
        return self._personas_dir

    def reload(self) -> None:
        profiles: dict[str, PersonaProfile] = {}
        diagnostics: dict[str, tuple[PersonaDiagnostic, ...]] = {}

        if not self._personas_dir.exists():
            self._profiles = {}
            self._diagnostics = {}
            return

        # Scan folder packages: personas/<name>/SKILL.md
        for skill_md in sorted(self._personas_dir.rglob("SKILL.md")):
            pkg_dir = skill_md.parent
            persona_id = _normalize_id(pkg_dir.name)
            parsed = parse_skill_package(pkg_dir)
            if parsed.profile:
                profiles[parsed.profile.persona_id] = parsed.profile
            else:
                diagnostics[persona_id] = parsed.diagnostics

        # Scan legacy flat files: personas/<name>.md (skip if folder version exists)
        for md_path in sorted(self._personas_dir.glob("*.md")):
            persona_id = _normalize_id(md_path.stem)
            if persona_id in profiles:
                continue  # folder package takes precedence
            parsed = parse_legacy_markdown(md_path)
            if parsed.profile:
                profiles[parsed.profile.persona_id] = parsed.profile
            else:
                diagnostics[persona_id] = parsed.diagnostics

        self._profiles = profiles
        self._diagnostics = diagnostics

    def refresh(self) -> None:
        self.reload()

    def list_profiles(self) -> list[PersonaProfile]:
        return sorted(self._profiles.values(), key=lambda p: p.persona_id)

    def get_profile(self, persona_id: str) -> PersonaProfile | None:
        return self._profiles.get(_normalize_id(persona_id))

    def has_profile(self, persona_id: str) -> bool:
        return _normalize_id(persona_id) in self._profiles

    def diagnostics_for(self, persona_id: str) -> tuple[PersonaDiagnostic, ...]:
        return self._diagnostics.get(_normalize_id(persona_id), ())

    def all_diagnostics(self) -> dict[str, tuple[PersonaDiagnostic, ...]]:
        return dict(self._diagnostics)


# ---------------------------------------------------------------------------
# SKILL.md folder package parser
# ---------------------------------------------------------------------------

def parse_skill_package(pkg_dir: Path) -> ParsedPersona:
    skill_md = pkg_dir / "SKILL.md"
    if not skill_md.exists():
        return ParsedPersona(diagnostics=(
            PersonaDiagnostic("missing_skill_md", "No SKILL.md found", _normalize_id(pkg_dir.name), str(pkg_dir)),
        ))

    persona_id = _normalize_id(pkg_dir.name)
    raw = skill_md.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)
    diags: list[PersonaDiagnostic] = []

    if frontmatter is None:
        diags.append(PersonaDiagnostic("no_frontmatter", "SKILL.md missing YAML frontmatter", persona_id, str(skill_md)))
        return ParsedPersona(diagnostics=tuple(diags))

    fm = _parse_yaml_simple(frontmatter)

    name = str(fm.get("name", "")).strip()
    if not name:
        diags.append(PersonaDiagnostic("missing_name", "frontmatter 'name' required", persona_id, str(skill_md)))

    description = str(fm.get("description", "")).strip()
    system_prompt = body.strip()

    if not system_prompt:
        diags.append(PersonaDiagnostic("empty_prompt", "SKILL.md body (system prompt) is empty", persona_id, str(skill_md)))

    if diags:
        return ParsedPersona(diagnostics=tuple(diags))

    # Parse structured fields
    model_raw = fm.get("model", {}) or {}
    model = ModelConfig(
        preferred=model_raw.get("preferred"),
        temperature=_safe_float(model_raw.get("temperature")),
        max_tokens=_safe_int(model_raw.get("max_tokens")),
    )

    routing_raw = fm.get("routing", {}) or {}
    hints_raw = routing_raw.get("hints", []) or []
    routing = RoutingConfig(
        hints=tuple(str(h) for h in hints_raw),
        weight=float(routing_raw.get("weight", 1.0)),
    )

    guardrails = tuple(str(g) for g in (fm.get("guardrails", []) or []))
    tools = tuple(str(t) for t in (fm.get("tools", []) or []))

    hooks_raw = fm.get("hooks", {}) or {}
    hooks = HooksConfig(pre=hooks_raw.get("pre"), post=hooks_raw.get("post"))

    meta = fm.get("metadata", {}) or {}
    is_builtin = bool(meta.get("builtin", False))

    # Load output.schema.json if present
    output_schema = _load_json_file(pkg_dir / "output.schema.json")

    # Load examples
    examples = _load_examples(pkg_dir / "examples")

    p_hash = _hash(persona_id, name, description, system_prompt, guardrails, tools, routing.hints)
    pg_hash = _hash(system_prompt, *guardrails)

    profile = PersonaProfile(
        persona_id=persona_id,
        name=name,
        description=description,
        system_prompt=system_prompt,
        guardrails=guardrails,
        tools=tools,
        routing=routing,
        routing_hints=routing.hints,  # legacy compat
        model=model,
        hooks=hooks,
        output_schema=output_schema,
        examples=tuple(examples),
        source_path=str(skill_md),
        package_dir=str(pkg_dir),
        profile_hash=p_hash,
        prompt_guardrails_hash=pg_hash,
        is_builtin=is_builtin,
    )
    return ParsedPersona(profile=profile)


# ---------------------------------------------------------------------------
# Legacy flat .md parser (backward compat with original ## Section format)
# ---------------------------------------------------------------------------

def parse_legacy_markdown(path: Path) -> ParsedPersona:
    persona_id = _normalize_id(path.stem)
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    sections = _extract_sections(lines)
    diags: list[PersonaDiagnostic] = []

    for s in ("Metadata", "System Prompt", "Guardrails", "Tools"):
        if s not in sections:
            diags.append(PersonaDiagnostic("missing_section", f"Missing: '{s}'", persona_id, str(path)))

    metadata: dict[str, str] = {}
    if "Metadata" in sections:
        metadata, md = _parse_kv_bullets(sections["Metadata"], persona_id, str(path), "Metadata")
        diags.extend(md)

    name = metadata.get("name", "").strip()
    description = metadata.get("description", "").strip()
    if not name:
        diags.append(PersonaDiagnostic("missing_metadata_field", "'name' required in Metadata", persona_id, str(path)))

    system_prompt = ""
    if "System Prompt" in sections:
        system_prompt = "\n".join(l for _, l in sections["System Prompt"]).strip()

    guardrails: tuple[str, ...] = ()
    if "Guardrails" in sections:
        guardrails, gd = _parse_list_bullets(sections["Guardrails"], persona_id, str(path), "Guardrails")
        diags.extend(gd)

    tools: tuple[str, ...] = ()
    if "Tools" in sections:
        tools, td = _parse_list_bullets(sections["Tools"], persona_id, str(path), "Tools")
        diags.extend(td)

    routing_hints: tuple[str, ...] = ()
    if "Routing Hints" in sections:
        routing_hints, rd = _parse_list_bullets(sections["Routing Hints"], persona_id, str(path), "Routing Hints")
        diags.extend(rd)

    if diags:
        return ParsedPersona(diagnostics=tuple(diags))

    p_hash = _hash(persona_id, name, description, system_prompt, guardrails, tools, routing_hints)
    pg_hash = _hash(system_prompt, *guardrails)

    profile = PersonaProfile(
        persona_id=persona_id,
        name=name,
        description=description,
        system_prompt=system_prompt,
        guardrails=guardrails,
        tools=tools,
        routing=RoutingConfig(hints=routing_hints),
        routing_hints=routing_hints,
        model=ModelConfig(),
        hooks=HooksConfig(),
        source_path=str(path),
        profile_hash=p_hash,
        prompt_guardrails_hash=pg_hash,
    )
    return ParsedPersona(profile=profile)


# Keep old function name for backward compat (tests import it)
parse_persona_markdown = parse_legacy_markdown


# ---------------------------------------------------------------------------
# YAML frontmatter parser (no PyYAML dependency — handles the subset we need)
# ---------------------------------------------------------------------------

def _split_frontmatter(raw: str) -> tuple[str | None, str]:
    """Split '---\\nyaml\\n---\\nbody' into (yaml_str, body)."""
    if not raw.startswith("---"):
        return None, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return None, raw
    fm = raw[3:end].strip()
    body = raw[end + 4:].strip()
    return fm, body


def _parse_yaml_simple(text: str) -> dict[str, Any]:
    """Minimal YAML parser for frontmatter. Handles scalars, lists, nested dicts.
    
    Defers dict-vs-list decision until first child line is seen.
    """
    lines = text.split("\n")
    return _parse_yaml_block(lines, 0, len(lines), -1)[0]


def _parse_yaml_block(lines: list[str], start: int, end: int, parent_indent: int
                       ) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    i = start
    while i < end:
        line = lines[i]
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            i += 1
            continue

        indent = len(line) - len(line.lstrip())
        if indent <= parent_indent and parent_indent >= 0:
            break  # dedented past our block

        # List item at this level
        m_list = re.match(r"^(\s*)- (.*)$", line)
        if m_list:
            i += 1
            continue  # lists at top level of a block are unusual, skip

        # Key: value
        m_kv = re.match(r"^(\s*)([a-zA-Z_][a-zA-Z0-9_-]*)\s*:\s*(.*)$", line)
        if not m_kv:
            i += 1
            continue

        key = m_kv.group(2).strip()
        val_str = m_kv.group(3).strip()

        if val_str == ">":
            # Folded scalar
            result[key], i = _parse_folded(lines, i + 1, end, indent)
        elif val_str:
            result[key] = _yaml_scalar(val_str)
            i += 1
        else:
            # Empty value — peek at next non-empty line to decide dict vs list
            child_val, i = _parse_yaml_child(lines, i + 1, end, indent)
            result[key] = child_val
    return result, i


def _parse_yaml_child(lines: list[str], start: int, end: int, parent_indent: int
                       ) -> tuple[Any, int]:
    """Parse child block under a key — returns list if children are '- items', dict if 'key: val'."""
    # Peek to decide type
    for j in range(start, end):
        s = lines[j].rstrip()
        if not s or s.lstrip().startswith("#"):
            continue
        child_indent = len(lines[j]) - len(lines[j].lstrip())
        if child_indent <= parent_indent:
            return {}, start  # empty block
        if s.lstrip().startswith("- "):
            return _parse_yaml_list(lines, start, end, parent_indent)
        else:
            return _parse_yaml_block(lines, start, end, parent_indent)
    return {}, start


def _parse_yaml_list(lines: list[str], start: int, end: int, parent_indent: int
                      ) -> tuple[list[Any], int]:
    result: list[Any] = []
    i = start
    while i < end:
        line = lines[i]
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= parent_indent:
            break
        m = re.match(r"^\s*- (.*)$", line)
        if m:
            result.append(_yaml_scalar(m.group(1).strip()))
            i += 1
        else:
            break
    return result, i


def _parse_folded(lines: list[str], start: int, end: int, parent_indent: int
                   ) -> tuple[str, int]:
    parts: list[str] = []
    i = start
    while i < end:
        line = lines[i]
        stripped = line.rstrip()
        if not stripped:
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= parent_indent:
            break
        parts.append(stripped.strip())
        i += 1
    return " ".join(parts), i


def _yaml_scalar(val: str) -> Any:
    """Convert YAML scalar string to Python type."""
    if not val:
        return ""
    # Strip quotes
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    low = val.lower()
    if low == "null" or low == "~":
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_examples(examples_dir: Path) -> list[dict[str, Any]]:
    if not examples_dir.exists():
        return []
    result = []
    for f in sorted(examples_dir.glob("*.json")):
        data = _load_json_file(f)
        if data:
            result.append(data)
    return result


def _extract_sections(lines: list[str]) -> dict[str, list[tuple[int, str]]]:
    sections: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None
    for idx, line in enumerate(lines, 1):
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append((idx, line))
    return sections


def _parse_kv_bullets(section: list[tuple[int, str]], pid: str, path: str, sname: str
                      ) -> tuple[dict[str, str], list[PersonaDiagnostic]]:
    vals: dict[str, str] = {}
    diags: list[PersonaDiagnostic] = []
    for ln, text in section:
        s = text.strip()
        if not s:
            continue
        if not s.startswith("- "):
            diags.append(PersonaDiagnostic("invalid_bullet", f"'{sname}' expects '- key: value'", pid, path, ln))
            continue
        content = s[2:].strip()
        if ":" not in content:
            diags.append(PersonaDiagnostic("invalid_metadata", f"Expected 'key: value' got '{content}'", pid, path, ln))
            continue
        k, v = content.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if not k or not v:
            diags.append(PersonaDiagnostic("invalid_metadata_entry", f"Invalid entry '{content}', key and value required", pid, path, ln))
            continue
        vals[k] = v
    return vals, diags


def _parse_list_bullets(section: list[tuple[int, str]], pid: str, path: str, sname: str
                        ) -> tuple[tuple[str, ...], list[PersonaDiagnostic]]:
    entries: list[str] = []
    diags: list[PersonaDiagnostic] = []
    for ln, text in section:
        s = text.strip()
        if not s:
            continue
        if not s.startswith("- "):
            diags.append(PersonaDiagnostic("invalid_bullet", f"'{sname}' expects '- value'", pid, path, ln))
            continue
        v = s[2:].strip()
        if v:
            entries.append(v)
    return tuple(dict.fromkeys(entries)), diags


def _normalize_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value.strip().lower()).strip("_")
    return cleaned


def _hash(*parts) -> str:
    flat = []
    for p in parts:
        if isinstance(p, tuple):
            flat.extend(p)
        else:
            flat.append(str(p))
    return sha256("\n".join(flat).encode()).hexdigest()


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


__all__ = [
    "HooksConfig", "ModelConfig", "ParsedPersona", "PersonaDiagnostic",
    "PersonaProfile", "PersonaRegistry", "RoutingConfig",
    "parse_legacy_markdown", "parse_persona_markdown", "parse_skill_package",
]
