"""LLM adapter contracts + minimal provider clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import re
from typing import Any, Protocol
from urllib import error as urlerror
from urllib import request as urlrequest
import litellm


JSONLike = dict[str, Any] | list[Any] | str | int | float | bool | None


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
    metadata: dict[str, str] = field(default_factory=dict)


class LLMClient(Protocol):
    """Protocol for mock-friendly LLM JSON generation."""

    def generate_json(self, request: LLMGenerateRequest) -> JSONLike:
        """Return provider output as JSON-like payload."""


class BaseLLMClient(ABC):
    """Abstract base adapter for real provider implementations."""

    @abstractmethod
    def generate_json(self, request: LLMGenerateRequest) -> JSONLike:
        """Return provider output as JSON-like payload."""


class LLMClientRuntimeError(RuntimeError):
    """Raised when provider runtime invocation fails."""


@dataclass(slots=True)
class StubLLMClient(BaseLLMClient):
    """Explicit deterministic fallback adapter for dev/test only."""

    default_persona: str = "python_developer"

    def generate_json(self, request: LLMGenerateRequest) -> JSONLike:
        service = request.metadata.get("service", "divider")
        if service == "merger":
            return {
                "merged_output": {
                    "note": "stub merger output",
                    "message": "Set LLM_PROVIDER to a live provider for production",
                },
                "conflict_resolutions": [],
                "unresolved_conflicts": [],
            }
        if service == "checker":
            return {
                "verdict": "pass",
                "reason": "stub checker pass",
                "suggested_fix": "none",
                "confidence": 1.0,
                "violations": [],
            }
        if service == "worker":
            step = request.metadata.get("step", "1")
            objective = _extract_objective(request.messages)
            return {
                "reasoning": f"Deterministic stub execution for step {step} (LLM_PROVIDER=stub)",
                "output": {
                    "step": step,
                    "objective": objective,
                    "result": "stub_completed",
                    "note": "Set LLM_PROVIDER to a live provider for real work execution",
                },
            }

        objective = _extract_objective(request.messages)
        return {
            "decision": "BASE_CASE",
            "rationale": (
                "Deterministic dev/test fallback (LLM_PROVIDER=stub). "
                "Use a live provider for production runtime decomposition decisions."
            ),
            "work_plan": [
                {
                    "step": 1,
                    "description": f"Execute objective deterministically: {objective}",
                }
            ],
            "suggested_persona": self.default_persona,
        }


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

    def generate_json(self, request: LLMGenerateRequest) -> JSONLike:
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
                "model": self.model,
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
            return _load_json_text(content, provider=self.model.split("/")[0] if "/" in self.model else "litellm")
        except Exception as exc:
            raise LLMClientRuntimeError(f"LiteLLM generate_json failed: {exc}") from exc


def _json_schema_for_service(service: str) -> dict[str, Any] | None:
    """Return minimal strict schema for Bedrock structured outputs."""
    if service == "divider":
        return {
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
                            "dependencies": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
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
                {
                    "if": {"properties": {"decision": {"const": "BASE_CASE"}}},
                    "then": {"required": ["work_plan"]},
                },
                {
                    "if": {"properties": {"decision": {"const": "RECURSIVE_CASE"}}},
                    "then": {"required": ["children"]},
                },
            ],
        }

    if service == "checker":
        return {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "fail"]},
                "reason": {"type": "string"},
                "suggested_fix": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "violations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "verdict",
                "reason",
                "suggested_fix",
                "confidence",
                "violations",
            ],
            "additionalProperties": False,
        }

    if service == "merger":
        return {
            "type": "object",
            "properties": {
                "merged_output": {
                    "anyOf": [
                        {"type": "object"},
                        {"type": "array"},
                        {"type": "string"},
                        {"type": "number"},
                        {"type": "boolean"},
                        {"type": "null"},
                    ]
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
                "unresolved_conflicts": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "merged_output",
                "conflict_resolutions",
                "unresolved_conflicts",
            ],
            "additionalProperties": False,
        }

    if service == "worker":
        return {
            "type": "object",
            "properties": {
                "reasoning": {"type": "string"},
                "output": {
                    "anyOf": [
                        {"type": "object"},
                        {"type": "array"},
                        {"type": "string"},
                        {"type": "number"},
                        {"type": "boolean"},
                        {"type": "null"},
                    ]
                },
            },
            "required": ["reasoning", "output"],
            "additionalProperties": False,
        }

    return None


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

    # Try decoder from each likely JSON start token ({ or [)
    decoder = json.JSONDecoder()
    for index, ch in enumerate(text):
        if ch not in "[{":
            continue
        candidate = text[index:].lstrip()
        try:
            value, _ = decoder.raw_decode(candidate)
            return value
        except json.JSONDecodeError:
            continue

    return None


def _extract_objective(messages: list[LLMMessage]) -> str:
    user_messages = [message.content for message in messages if message.role == "user"]
    if not user_messages:
        return "unknown objective"
    prompt = user_messages[-1]
    match = re.search(r"Objective:\s*(.+?)\nDepth:", prompt, flags=re.DOTALL)
    if not match:
        return prompt[:160]
    return match.group(1).strip()[:500]


__all__ = [
    "BaseLLMClient",
    "LiteLLMClient",
    "JSONLike",
    "LLMClient",
    "LLMClientRuntimeError",
    "LLMGenerateRequest",
    "LLMMessage",
    "StubLLMClient",
]
