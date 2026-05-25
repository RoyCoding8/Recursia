"""LLM adapter contracts + minimal provider clients."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

import litellm

JSONLike = dict[str, Any] | list[Any] | str | int | float | bool | None


@dataclass(slots=True, frozen=True)
class LLMUsage:
    """Token usage from an LLM call."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None = None


@dataclass(slots=True, frozen=True)
class LLMResult:
    """LLM response wrapping data and usage."""
    data: JSONLike
    usage: LLMUsage


@dataclass(slots=True, frozen=True)
class LLMMessage:
    """Single chat-style message sent to an LLM provider."""

    role: str
    content: str


@dataclass(slots=True, frozen=True)
class LLMGenerateRequest:
    """Structured request envelope used by services."""

    messages: list[LLMMessage]
    temperature: float = 0.0
    model: str | None = None  # per-request override (cost-aware routing)
    metadata: dict[str, str] = field(default_factory=dict)


class LLMClient(Protocol):
    """Protocol for mock-friendly LLM JSON generation."""

    def generate_json(self, request: LLMGenerateRequest) -> LLMResult:
        """Return provider output as JSON-like payload with usage."""


class BaseLLMClient(ABC):
    """Abstract base adapter for real provider implementations."""

    @abstractmethod
    def generate_json(self, request: LLMGenerateRequest) -> LLMResult:
        """Return provider output as JSON-like payload with usage."""


class LLMClientRuntimeError(RuntimeError):
    """Raised when provider runtime invocation fails."""


@dataclass(slots=True)
class StubLLMClient(BaseLLMClient):
    """Explicit deterministic fallback adapter for dev/test only."""

    default_persona: str = "python_developer"

    def generate_json(self, request: LLMGenerateRequest) -> LLMResult:
        service = request.metadata.get("service", "divider")
        _zero_usage = LLMUsage(0, 0, 0)
        if service == "merger":
            return LLMResult(data={
                "merged_output": {"note": "stub merger output",
                    "message": "Set LLM_PROVIDER to a live provider for production"},
                "conflict_resolutions": [], "unresolved_conflicts": [],
            }, usage=_zero_usage)
        if service == "checker":
            return LLMResult(data={
                "verdict": "pass", "reason": "stub checker pass",
                "suggested_fix": "none", "confidence": 1.0, "violations": [],
            }, usage=_zero_usage)
        if service == "worker":
            step = request.metadata.get("step", "1")
            objective = _extract_objective(request.messages)
            return LLMResult(data={
                "reasoning": f"Deterministic stub execution for step {step} (LLM_PROVIDER=stub)",
                "output": {"step": step, "objective": objective, "result": "stub_completed",
                    "note": "Set LLM_PROVIDER to a live provider for real work execution"},
            }, usage=_zero_usage)

        objective = _extract_objective(request.messages)
        if _stub_should_decompose(objective):
            children = _stub_split_objective(objective)
            return LLMResult(data={
                "decision": "RECURSIVE_CASE",
                "rationale": "Deterministic stub decomposition (LLM_PROVIDER=stub).",
                "children": [{"objective": c, "dependencies": [], "needs_qa": False} for c in children],
            }, usage=_zero_usage)
        return LLMResult(data={
            "decision": "BASE_CASE",
            "rationale": "Deterministic dev/test fallback (LLM_PROVIDER=stub). Use a live provider for production.",
            "work_plan": [{"step": 1, "description": f"Execute objective deterministically: {objective}"}],
            "suggested_persona": self.default_persona,
        }, usage=_zero_usage)


@dataclass(slots=True)
class LiteLLMClient(BaseLLMClient):
    """Unified LLM adapter using litellm for multiple providers."""

    model: str
    api_key: str | None = None
    aws_region_name: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    timeout_seconds: int = 60
    max_retries: int = 2

    def generate_json(self, request: LLMGenerateRequest) -> LLMResult:
        messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

        service = request.metadata.get("service", "")
        schema = _json_schema_for_service(service)

        if schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{service}_schema" if service else "response_schema",
                    "schema": schema,
                    "strict": False
                }
            }
        else:
            response_format = {"type": "json_object"}

        try:
            completion_kwargs = {
                "model": request.model or self.model,
                "messages": messages,
                "temperature": request.temperature,
                "response_format": response_format,
                "timeout": self.timeout_seconds,
                "num_retries": self.max_retries,
            }
            if self.api_key:
                completion_kwargs["api_key"] = self.api_key
            if self.aws_region_name:
                completion_kwargs["aws_region_name"] = self.aws_region_name
            if self.aws_access_key_id:
                completion_kwargs["aws_access_key_id"] = self.aws_access_key_id
            if self.aws_secret_access_key:
                completion_kwargs["aws_secret_access_key"] = self.aws_secret_access_key

            response = litellm.completion(**completion_kwargs)
            content = response.choices[0].message.content
            data = _load_json_text(content, provider=self.model.split("/")[0] if "/" in self.model else "litellm")

            # Extract token usage from litellm response
            usage = LLMUsage(0, 0, 0)
            if hasattr(response, 'usage') and response.usage:
                usage = LLMUsage(
                    prompt_tokens=getattr(response.usage, 'prompt_tokens', 0),
                    completion_tokens=getattr(response.usage, 'completion_tokens', 0),
                    total_tokens=getattr(response.usage, 'total_tokens', 0),
                    cost_usd=getattr(response, '_hidden_params', {}).get('response_cost'),
                )

            return LLMResult(data=data, usage=usage)
        except Exception as exc:
            raise LLMClientRuntimeError(f"LiteLLM generate_json failed: {exc}") from exc


_JSON_SCHEMAS: dict[str, dict[str, Any]] = {
    "divider": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["BASE_CASE", "RECURSIVE_CASE"]},
            "rationale": {"type": "string"},
            "work_plan": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "integer", "minimum": 1},
                        "description": {"type": "string"},
                    },
                    "required": ["step", "description"],
                    "additionalProperties": False,
                },
            },
            "suggested_persona": {"type": ["string", "null"]},
            "children": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "objective": {"type": "string"},
                        "dependencies": {"type": "array", "items": {"type": "string"}},
                        "suggested_persona": {"type": ["string", "null"]},
                        "interface_contract": {"type": ["string", "null"]},
                    },
                    "required": ["objective", "dependencies"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["decision", "rationale"],
        "additionalProperties": False,
        "allOf": [
            {"if": {"properties": {"decision": {"const": "BASE_CASE"}}},
             "then": {"required": ["work_plan"]}},
            {"if": {"properties": {"decision": {"const": "RECURSIVE_CASE"}}},
             "then": {"required": ["children"]}},
        ],
    },
    "checker": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "fail"]},
            "reason": {"type": "string"},
            "suggested_fix": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "violations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["verdict", "reason", "suggested_fix", "confidence", "violations"],
        "additionalProperties": False,
    },
    "merger": {
        "type": "object",
        "properties": {
            "merged_output": {
                "anyOf": [{"type": "object"}, {"type": "array"}, {"type": "string"},
                           {"type": "number"}, {"type": "boolean"}, {"type": "null"}],
            },
            "conflict_resolutions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "conflict": {"type": "string"},
                        "chosen_approach": {"type": "string"},
                        "rejected_approach": {"type": ["string", "null"]},
                        "rationale": {"type": "string"},
                    },
                    "required": ["conflict", "chosen_approach", "rationale"],
                    "additionalProperties": False,
                },
            },
            "unresolved_conflicts": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["merged_output", "conflict_resolutions", "unresolved_conflicts"],
        "additionalProperties": False,
    },
    "worker": {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string"},
            "output": {
                "anyOf": [{"type": "object"}, {"type": "array"}, {"type": "string"},
                           {"type": "number"}, {"type": "boolean"}, {"type": "null"}],
            },
        },
        "required": ["reasoning", "output"],
        "additionalProperties": False,
    },
}


def _json_schema_for_service(service: str) -> dict[str, Any] | None:
    """Return minimal strict schema for Bedrock structured outputs."""
    return _JSON_SCHEMAS.get(service)


def _load_json_text(content: str, *, provider: str) -> JSONLike:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # Fallback: extract JSON from mixed text
    extracted = _extract_json_from_text(content)
    if extracted is not None:
        return extracted
    preview = " ".join(content.split())[:240]
    raise LLMClientRuntimeError(
        f"{provider} response content is not valid JSON text. preview={preview!r}"
    )

def _extract_json_from_text(text: str) -> JSONLike | None:
    """Best-effort JSON extraction from mixed prose+JSON responses."""
    # Try ```json fenced blocks first
    fenced = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try decoder from each likely JSON start token ({ or [), prefer largest match
    decoder = json.JSONDecoder()
    best: JSONLike | None = None
    best_len = 0
    for index, ch in enumerate(text):
        if ch not in "[{":
            continue
        candidate = text[index:].lstrip()
        try:
            value, end = decoder.raw_decode(candidate)
            if end > best_len and isinstance(value, (dict, list)):
                best = value
                best_len = end
        except json.JSONDecodeError:
            continue
    return best


_DECOMPOSE_MARKERS = (" and ", " then ", " also ", " additionally ", " plus ",
                      " as well as ", " followed by ", " including ")


def _stub_should_decompose(objective: str) -> bool:
    """Heuristic: does the objective contain multiple distinct tasks?"""
    lower = objective.lower()
    return any(m in lower for m in _DECOMPOSE_MARKERS)


def _stub_split_objective(objective: str) -> list[str]:
    """Split objective on conjunction markers into 2-3 child objectives."""
    import re as _re
    pattern = "|".join(_re.escape(m.strip()) for m in _DECOMPOSE_MARKERS)
    parts = [p.strip() for p in _re.split(pattern, objective, flags=_re.IGNORECASE) if p.strip()]
    if len(parts) < 2:
        mid = len(objective) // 2
        space = objective.find(" ", mid)
        parts = [objective[:space].strip(), objective[space:].strip()] if space > 0 else [objective]
    return parts[:3]


def _extract_objective(messages: list[LLMMessage]) -> str:
    user_messages = [message.content for message in messages if message.role == "user"]
    if not user_messages:
        return "unknown objective"
    prompt = user_messages[-1]
    match = re.search(r"Objective:\s*(.+?)\nDepth:", prompt, flags=re.DOTALL)
    if not match:
        return prompt[:160]
    return match.group(1).strip()[:500]


class UsageTrackingClient:
    """Wraps an LLMClient to accumulate token usage across calls."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.total_tokens: int = 0
        self.total_cost_usd: float = 0.0

    def generate_json(self, request: LLMGenerateRequest) -> LLMResult:
        result = self._inner.generate_json(request)
        self.total_prompt_tokens += result.usage.prompt_tokens
        self.total_completion_tokens += result.usage.completion_tokens
        self.total_tokens += result.usage.total_tokens
        if result.usage.cost_usd is not None:
            self.total_cost_usd += result.usage.cost_usd
        return result


__all__ = [
    "BaseLLMClient",
    "LiteLLMClient",
    "JSONLike",
    "LLMClient",
    "LLMClientRuntimeError",
    "LLMGenerateRequest",
    "LLMMessage",
    "LLMResult",
    "LLMUsage",
    "StubLLMClient",
    "UsageTrackingClient",
]
