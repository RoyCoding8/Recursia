"""Checker service with granular scopes and fail-x3 human-gate policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any, Protocol

from pydantic import TypeAdapter, ValidationError

from app.adapters.llm_client import LLMClient, LLMGenerateRequest, LLMMessage
from app.domain.models import NodeStatus
from app.domain.policies import CheckerFailurePolicy, DEFAULT_CHECKER_FAILURE_POLICY
from app.schemas.api import CheckerConfig
from app.schemas.contracts import CheckerResult, CheckerVerdict


class CheckerScope(str, Enum):
    """Granularity levels where checker can be applied."""

    NODE = "node"
    MERGE = "merge"


@dataclass(slots=True, frozen=True)
class CheckerRequest:
    """Input passed to a checker client implementation."""

    scope: CheckerScope
    objective: str
    output: dict[str, Any] | list[Any] | str | int | float | bool | None
    metadata: dict[str, str] = field(default_factory=dict)


class CheckerClient(Protocol):
    """Protocol for adapter-friendly checker clients."""

    def evaluate(self, request: CheckerRequest) -> object:
        """Return JSON-like payload that validates as CheckerResult."""


class CheckerServiceError(RuntimeError):
    """Raised when checker cannot produce a schema-valid result."""


class LLMCheckerClient:
    """Checker client backed by LLM structured output generation."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def evaluate(self, request: CheckerRequest) -> object:
        output_str = json.dumps(
            request.output, ensure_ascii=False, sort_keys=True)[:2000]
        prompt = (
            f"Evaluate this output against the objective. "
            f"Scope: {request.scope.value}. "
            f"Objective: {request.objective}. "
            f"Output: {output_str}"
        )
        return self._llm_client.generate_json(
            LLMGenerateRequest(
                messages=[
                    LLMMessage(
                        role="system",
                        content="Return JSON: {verdict:'pass'|'fail', reason, "
                        "suggested_fix, confidence (0-1), violations:[string]}.",
                    ),
                    LLMMessage(role="user", content=prompt),
                ],
                temperature=0.0,
                metadata={
                    "service": "checker",
                    "scope": request.scope.value,
                    **request.metadata,
                },
            )
        )


@dataclass(slots=True, frozen=True)
class CheckerEvent:
    """Structured event candidate for persistence/streaming."""

    event_type: str
    payload: dict[str, Any]


@dataclass(slots=True, frozen=True)
class CheckerOutcome:
    """Normalized result of a checker invocation (or deliberate skip)."""

    invoked: bool
    scope: CheckerScope
    result: CheckerResult | None
    consecutive_failures: int
    should_block_human: bool
    next_node_status: NodeStatus | None
    attempts_used: int
    events: tuple[CheckerEvent, ...] = ()
    skipped_reason: str | None = None


class CheckerService:
    """Runs checker with global/scope policies and fail-x3 behavior."""

    _CHECKER_RESULT_ADAPTER = TypeAdapter(CheckerResult)

    def __init__(
        self,
        checker_client: CheckerClient,
        *,
        failure_policy: CheckerFailurePolicy = DEFAULT_CHECKER_FAILURE_POLICY,
        max_validation_retries: int = 1,
    ) -> None:
        if max_validation_retries < 0:
            raise ValueError("max_validation_retries must be >= 0")
        self._checker_client = checker_client
        self._failure_policy = failure_policy
        self._max_validation_retries = max_validation_retries

    def evaluate_node(
        self,
        *,
        checker_config: CheckerConfig,
        objective: str,
        output: dict[str, Any] | list[Any] | str | int | float | bool | None,
        consecutive_failures: int = 0,
        metadata: dict[str, str] | None = None,
    ) -> CheckerOutcome:
        """Evaluate checker for node-level output."""
        return self.evaluate(
            checker_config=checker_config,
            scope=CheckerScope.NODE,
            objective=objective,
            output=output,
            consecutive_failures=consecutive_failures,
            metadata=metadata,
        )

    def evaluate_merge(
        self,
        *,
        checker_config: CheckerConfig,
        objective: str,
        output: dict[str, Any] | list[Any] | str | int | float | bool | None,
        consecutive_failures: int = 0,
        metadata: dict[str, str] | None = None,
    ) -> CheckerOutcome:
        """Evaluate checker for merge-level output."""
        return self.evaluate(
            checker_config=checker_config,
            scope=CheckerScope.MERGE,
            objective=objective,
            output=output,
            consecutive_failures=consecutive_failures,
            metadata=metadata,
        )

    def evaluate(
        self,
        *,
        checker_config: CheckerConfig,
        scope: CheckerScope,
        objective: str,
        output: dict[str, Any] | list[Any] | str | int | float | bool | None,
        consecutive_failures: int = 0,
        metadata: dict[str, str] | None = None,
    ) -> CheckerOutcome:
        """Evaluate output under configured checker policy."""
        if consecutive_failures < 0:
            raise ValueError("consecutive_failures must be >= 0")

        if not self.should_run(checker_config=checker_config, scope=scope):
            return CheckerOutcome(
                invoked=False,
                scope=scope,
                result=None,
                consecutive_failures=consecutive_failures,
                should_block_human=False,
                next_node_status=None,
                attempts_used=0,
                events=(),
                skipped_reason="checker disabled for selected scope",
            )

        checker_result, attempts_used = self._run_with_validation_retries(
            request=CheckerRequest(
                scope=scope,
                objective=objective,
                output=output,
                metadata=dict(metadata or {}),
            )
        )

        if checker_result.verdict == CheckerVerdict.PASS:
            return CheckerOutcome(
                invoked=True,
                scope=scope,
                result=checker_result,
                consecutive_failures=0,
                should_block_human=False,
                next_node_status=NodeStatus.COMPLETED,
                attempts_used=attempts_used,
                events=(
                    CheckerEvent(
                        event_type="checker.completed",
                        payload={
                            "scope": scope.value,
                            "verdict": checker_result.verdict.value,
                            "reason": checker_result.reason,
                            "suggested_fix": checker_result.suggested_fix,
                            "confidence": checker_result.confidence,
                            "violations": list(checker_result.violations),
                            "consecutive_failures": 0,
                        },
                    ),
                ),
            )

        updated_failures = consecutive_failures + 1
        should_block = self._failure_policy.should_block(updated_failures)
        next_status = (
            NodeStatus.BLOCKED_HUMAN if should_block else NodeStatus.FAILED_CHECK
        )
        events = [
            CheckerEvent(
                event_type="checker.completed",
                payload={
                    "scope": scope.value,
                    "verdict": checker_result.verdict.value,
                    "reason": checker_result.reason,
                    "suggested_fix": checker_result.suggested_fix,
                    "confidence": checker_result.confidence,
                    "violations": list(checker_result.violations),
                    "consecutive_failures": updated_failures,
                },
            )
        ]
        if should_block:
            events.append(
                CheckerEvent(
                    event_type="node.blocked_human",
                    payload={
                        "scope": scope.value,
                        "reason": "checker_failed_consecutive_threshold",
                        "consecutive_failures": updated_failures,
                        "threshold": self._failure_policy.block_after_consecutive_failures,
                    },
                )
            )

        return CheckerOutcome(
            invoked=True,
            scope=scope,
            result=checker_result,
            consecutive_failures=updated_failures,
            should_block_human=should_block,
            next_node_status=next_status,
            attempts_used=attempts_used,
            events=tuple(events),
        )

    @staticmethod
    def should_run(*, checker_config: CheckerConfig, scope: CheckerScope) -> bool:
        """Return true when checker is enabled for the requested scope."""
        if not checker_config.enabled:
            return False
        if scope == CheckerScope.NODE:
            return checker_config.node_level
        return checker_config.merge_level

    def _run_with_validation_retries(
        self, request: CheckerRequest
    ) -> tuple[CheckerResult, int]:
        max_attempts = self._max_validation_retries + 1
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                payload = self._checker_client.evaluate(request)
                parsed = self._CHECKER_RESULT_ADAPTER.validate_python(payload)
                return parsed, attempt
            except (ValidationError, ValueError, TypeError) as exc:
                last_error = exc
                continue

        raise CheckerServiceError(
            f"checker output failed schema validation after {max_attempts} attempts"
        ) from last_error


__all__ = [
    "CheckerClient",
    "CheckerEvent",
    "LLMCheckerClient",
    "CheckerOutcome",
    "CheckerRequest",
    "CheckerScope",
    "CheckerService",
    "CheckerServiceError",
]
