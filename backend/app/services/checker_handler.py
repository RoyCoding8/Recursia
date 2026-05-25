"""Shared checker retry logic for both base-case and merge-level evaluation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.domain.models import NodeState, NodeStatus
from app.schemas.contracts import CheckerResult
from app.services.checker import CheckerOutcome, CheckerScope


def build_checker_feedback(result: CheckerResult | None) -> str:
    """Build feedback string from checker result for retry prompts."""
    if result is None:
        return ""
    fix = result.suggested_fix
    violations = list(result.violations)
    return f"{fix}\nViolations: {'; '.join(violations)}" if violations else fix


def retry_checker_loop(
    *,
    evaluate_checker: Callable[..., CheckerOutcome | None],
    node: NodeState,
    checker_outcome: CheckerOutcome,
    checker_result: CheckerResult | None,
    max_retries: int,
    action: Callable[[], Any],
    scope: CheckerScope,
    on_retry: Callable[[Any, CheckerResult | None], None] | None = None,
) -> tuple[Any, CheckerOutcome | None, CheckerResult | None]:
    """Common retry loop: retry action, re-evaluate checker, break on pass/blocked.

    Returns:
        (final_output, final_checker_outcome, final_checker_result)
    """
    output = None
    for _ in range(max_retries):
        output = action()

        if on_retry:
            on_retry(output, checker_result)

        checker_outcome = evaluate_checker(node=node, scope=scope, output=output)
        checker_result = checker_outcome.result if checker_outcome else None

        if checker_outcome is None or checker_outcome.next_node_status in (
            NodeStatus.COMPLETED, NodeStatus.BLOCKED_HUMAN,
        ):
            break

    return output, checker_outcome, checker_result


__all__ = ["build_checker_feedback", "retry_checker_loop"]
