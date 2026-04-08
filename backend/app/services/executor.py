"""Recursive executor for divide-route-execute orchestration flow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from app.domain.events import DomainEventType
from app.domain.models import AttemptState, NodeKind, NodeState, NodeStatus
from app.schemas.api import CheckerConfig
from app.schemas.contracts import CheckerResult, DividerDecision
from app.services.divider import (
    BaseCaseWorkPlan,
    DividerService,
    DividerServiceResult,
    RecursiveChildSpec,
)
from app.services.checker import CheckerOutcome, CheckerScope, CheckerService
from app.services.merger import MergerService
from app.services.persona_router import PersonaRouteResult, PersonaRouter
from app.services.stubs import DeterministicBaseCaseWorker
from app.schemas.contracts import MergeRequest
from app.state.repository import RunStateRepository


def _default_id_factory() -> str:
    return uuid4().hex


class ExecutionTerminal(str):
    """Terminal outcomes surfaced by recursive execution."""

    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED_HUMAN = "blocked_human"


@dataclass(slots=True, frozen=True)
class WorkExecutionResult:
    """Result from base-case work execution."""

    status: str
    output: dict[str, Any] | list[Any] | str | int | float | bool | None = None
    error: str | None = None

    @classmethod
    def completed(
        cls,
        output: dict[str, Any] | list[Any] | str | int | float | bool | None,
    ) -> WorkExecutionResult:
        return cls(status=ExecutionTerminal.COMPLETED, output=output)

    @classmethod
    def failed(cls, error: str) -> WorkExecutionResult:
        return cls(status=ExecutionTerminal.FAILED, error=error)

    @classmethod
    def blocked_human(cls, reason: str) -> WorkExecutionResult:
        return cls(status=ExecutionTerminal.BLOCKED_HUMAN, error=reason)


class BaseCaseWorker(Protocol):
    """Mock-friendly protocol for base-case execution."""

    def execute(
        self,
        *,
        run_id: str,
        node_id: str,
        objective: str,
        depth: int,
        persona_id: str | None,
        work_plan: list[dict[str, Any]],
    ) -> WorkExecutionResult:
        """Execute linear work plan for a base-case node."""


@dataclass(slots=True, frozen=True)
class NodeExecutionResult:
    """Terminal execution summary for one node subtree."""

    status: str
    node_id: str
    output: dict[str, Any] | list[Any] | str | int | float | bool | None = None
    error: str | None = None


@dataclass(slots=True, frozen=True)
class _ChildRuntimeNode:
    alias: str
    node_id: str
    objective: str
    dependencies: tuple[str, ...]
    suggested_persona: str | None


EventEmitter = Callable[[str, str, DomainEventType, dict[str, object]], None]


class RecursiveExecutor:
    """Executes node trees recursively using divider/router abstractions."""

    def __init__(
        self,
        *,
        repository: RunStateRepository,
        divider: DividerService,
        persona_router: PersonaRouter,
        worker: BaseCaseWorker | None = None,
        checker: CheckerService | None = None,
        merger: MergerService | None = None,
        event_emitter: EventEmitter | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._divider = divider
        self._persona_router = persona_router
        self._worker = worker or DeterministicBaseCaseWorker()
        self._checker = checker
        self._merger = merger
        self._event_emitter = event_emitter
        self._id_factory = id_factory or _default_id_factory
        self._outputs: dict[str, Any] = {}

    def get_output(self, node_id: str) -> Any | None:
        """Return stored node output if available."""
        return self._outputs.get(node_id)

    def execute_node(self, *, run_id: str, node_id: str) -> NodeExecutionResult:
        """Execute one node and its descendants until terminal state."""
        run = self._repository.get_run(run_id)
        node = self._repository.get_node(node_id)
        depth_limited = node.depth >= run.config.max_depth

        self._repository.increment_node_attempt_count(node_id)
        if node.status == NodeStatus.RUNNING:
            started_node = self._repository.get_node(node_id)
        else:
            started_node = self._repository.record_node_started(node_id)
            self._emit_node_status(
                node_id=node_id,
                status=started_node.status,
                reason="node_execution_started",
            )

        route = self._persona_router.select_persona(
            objective=node.objective,
            context=f"depth={node.depth}",
            explicit_persona_id=node.persona_id,
        )
        self._apply_persona(node_id=node_id, route=route)

        if depth_limited:
            divide_result = self._forced_base_case_result(
                node=node, max_depth=run.config.max_depth
            )
            self._emit_depth_limit_reached(node=node, max_depth=run.config.max_depth)
        else:
            divide_result = self._divider.divide(
                objective=node.objective, depth=node.depth
            )

        if (
            divide_result.decision == DividerDecision.RECURSIVE_CASE
            and node.depth >= run.config.max_depth
        ):
            divide_result = self._forced_base_case_result(
                node=node,
                max_depth=run.config.max_depth,
                reason="divider_requested_recursive_at_depth_limit",
            )
            self._emit_depth_limit_reached(
                node=node,
                max_depth=run.config.max_depth,
                reason="divider_requested_recursive_at_depth_limit",
            )

        if divide_result.decision == DividerDecision.BASE_CASE:
            return self._execute_base_case(
                node=self._repository.get_node(node_id),
                divide_result=divide_result,
            )

        return self._execute_recursive_case(
            node=self._repository.get_node(node_id),
            divide_result=divide_result,
        )

    def _execute_base_case(
        self,
        *,
        node: NodeState,
        divide_result: DividerServiceResult,
    ) -> NodeExecutionResult:
        if divide_result.base_case is None:
            self._repository.record_node_ended(node.node_id, NodeStatus.ERROR)
            error = "divider returned BASE_CASE without base_case payload"
            self._emit_node_status(
                node_id=node.node_id, status=NodeStatus.ERROR, reason=error
            )
            self._record_attempt(node=node, output=None, error=error)
            return NodeExecutionResult(
                status=ExecutionTerminal.FAILED,
                node_id=node.node_id,
                error=error,
            )

        self._repository.update_node_kind(node.node_id, NodeKind.WORK)
        if divide_result.base_case.suggested_persona and not node.persona_id:
            self._repository.update_node_persona(
                node.node_id, divide_result.base_case.suggested_persona
            )
        # Respect per-node QA policy from divider
        if hasattr(divide_result.base_case, "needs_qa") and not divide_result.base_case.needs_qa:
            self._repository.update_node_checker_policy(
                node.node_id,
                CheckerConfig(enabled=False, node_level=False, merge_level=False),
            )
        node = self._repository.get_node(node.node_id)

        token_node = self._repository.record_node_first_token(node.node_id)
        self._emit_node_ttft(token_node)
        work = self._worker.execute(
            run_id=node.run_id,
            node_id=node.node_id,
            objective=node.objective,
            depth=node.depth,
            persona_id=node.persona_id,
            work_plan=divide_result.base_case.work_plan,
        )

        if work.status == ExecutionTerminal.COMPLETED:
            checker_outcome = self._evaluate_checker(
                node=node,
                scope=CheckerScope.NODE,
                output=work.output,
            )
            checker_result = checker_outcome.result if checker_outcome else None
            if checker_outcome is not None:
                if checker_outcome.next_node_status == NodeStatus.BLOCKED_HUMAN:
                    self._record_attempt(
                        node=node,
                        output=work.output,
                        error=checker_outcome.result.reason if checker_outcome.result else "checker blocked human",
                        checker_result=checker_result,
                    )
                    return NodeExecutionResult(
                        status=ExecutionTerminal.BLOCKED_HUMAN,
                        node_id=node.node_id,
                        error=checker_outcome.result.reason if checker_outcome.result else "checker blocked human",
                    )

            ended_node = self._repository.record_node_ended(
                node.node_id, NodeStatus.COMPLETED
            )
            self._emit_node_status(
                node_id=node.node_id,
                status=ended_node.status,
                duration_ms=ended_node.duration_ms,
                ttft_ms=ended_node.ttft_ms,
            )
            self._outputs[node.node_id] = work.output
            self._record_attempt(
                node=node,
                output=work.output,
                error=None,
                checker_result=checker_result,
            )
            return NodeExecutionResult(
                status=ExecutionTerminal.COMPLETED,
                node_id=node.node_id,
                output=work.output,
            )

        if work.status == ExecutionTerminal.BLOCKED_HUMAN:
            self._mark_blocked_human(node.node_id)
            self._record_attempt(node=node, output=None, error=work.error)
            return NodeExecutionResult(
                status=ExecutionTerminal.BLOCKED_HUMAN,
                node_id=node.node_id,
                error=work.error,
            )

        ended_node = self._repository.record_node_ended(node.node_id, NodeStatus.ERROR)
        self._emit_node_status(
            node_id=node.node_id,
            status=ended_node.status,
            reason=work.error,
            duration_ms=ended_node.duration_ms,
            ttft_ms=ended_node.ttft_ms,
        )
        self._record_attempt(node=node, output=None, error=work.error)
        return NodeExecutionResult(
            status=ExecutionTerminal.FAILED,
            node_id=node.node_id,
            error=work.error,
        )

    def _execute_recursive_case(
        self,
        *,
        node: NodeState,
        divide_result: DividerServiceResult,
    ) -> NodeExecutionResult:
        if divide_result.recursive_case is None:
            self._repository.record_node_ended(node.node_id, NodeStatus.ERROR)
            error = "divider returned RECURSIVE_CASE without recursive_case payload"
            self._emit_node_status(
                node_id=node.node_id, status=NodeStatus.ERROR, reason=error
            )
            self._record_attempt(node=node, output=None, error=error)
            return NodeExecutionResult(
                status=ExecutionTerminal.FAILED,
                node_id=node.node_id,
                error=error,
            )

        children_specs = divide_result.recursive_case.children
        run = self._repository.get_run(node.run_id)
        if len(children_specs) > run.config.max_children_per_node:
            # Graceful truncation instead of hard failure — take first N children
            self._emit_node_status(
                node_id=node.node_id,
                status=NodeStatus.RUNNING,
                reason=(
                    f"guardrail: truncated {len(children_specs)} children "
                    f"to max {run.config.max_children_per_node}"
                ),
            )
            children_specs = children_specs[: run.config.max_children_per_node]

        runtime_children = self._create_child_nodes(parent=node, specs=children_specs)
        pending = list(runtime_children)
        completed_aliases: set[str] = set()
        merged_outputs: list[dict[str, Any]] = []

        while pending:
            ready = [
                child
                for child in pending
                if set(child.dependencies).issubset(completed_aliases)
            ]
            if not ready:
                unresolved = {
                    child.alias: list(child.dependencies) for child in pending
                }
                self._repository.record_node_ended(node.node_id, NodeStatus.ERROR)
                error = f"unresolved sibling dependencies: {unresolved}"
                self._emit_node_status(
                    node_id=node.node_id, status=NodeStatus.ERROR, reason=error
                )
                self._record_attempt(node=node, output=None, error=error)
                return NodeExecutionResult(
                    status=ExecutionTerminal.FAILED,
                    node_id=node.node_id,
                    error=error,
                )

            for child in ready:
                child_result = self.execute_node(
                    run_id=node.run_id, node_id=child.node_id
                )
                pending = [item for item in pending if item.node_id != child.node_id]

                if child_result.status == ExecutionTerminal.COMPLETED:
                    completed_aliases.add(child.alias)
                    merged_outputs.append(
                        {
                            "alias": child.alias,
                            "node_id": child.node_id,
                            "objective": child.objective,
                            "output": child_result.output,
                        }
                    )
                    continue

                if child_result.status == ExecutionTerminal.BLOCKED_HUMAN:
                    self._mark_blocked_human(node.node_id)
                    self._record_attempt(
                        node=node,
                        output=None,
                        error=(
                            f"child node {child.node_id} blocked human: "
                            f"{child_result.error or ''}".strip()
                        ),
                    )
                    return NodeExecutionResult(
                        status=ExecutionTerminal.BLOCKED_HUMAN,
                        node_id=node.node_id,
                        error=child_result.error,
                    )

                self._repository.record_node_ended(node.node_id, NodeStatus.ERROR)
                self._emit_node_status(
                    node_id=node.node_id,
                    status=NodeStatus.ERROR,
                    reason=child_result.error,
                )
                self._record_attempt(
                    node=node,
                    output=None,
                    error=(
                        f"child node {child.node_id} failed: "
                        f"{child_result.error or ''}".strip()
                    ),
                )
                return NodeExecutionResult(
                    status=ExecutionTerminal.FAILED,
                    node_id=node.node_id,
                    error=child_result.error,
                )

        merged = self._merge_children(node=node, merged_outputs=merged_outputs)

        checker_outcome = self._evaluate_checker(
            node=node,
            scope=CheckerScope.MERGE,
            output=merged,
        )
        checker_result = checker_outcome.result if checker_outcome else None
        if checker_outcome is not None:
            if checker_outcome.next_node_status == NodeStatus.BLOCKED_HUMAN:
                self._record_attempt(
                    node=node,
                    output=merged,
                    error=checker_outcome.result.reason if checker_outcome.result else "checker blocked human",
                    checker_result=checker_result,
                )
                return NodeExecutionResult(
                    status=ExecutionTerminal.BLOCKED_HUMAN,
                    node_id=node.node_id,
                    error=checker_outcome.result.reason if checker_outcome.result else "checker blocked human",
                )

        self._outputs[node.node_id] = merged
        ended_node = self._repository.record_node_ended(
            node.node_id, NodeStatus.COMPLETED
        )
        self._emit_node_status(
            node_id=node.node_id,
            status=ended_node.status,
            duration_ms=ended_node.duration_ms,
            ttft_ms=ended_node.ttft_ms,
        )
        self._record_attempt(
            node=node,
            output=merged,
            error=None,
            checker_result=checker_result,
        )
        return NodeExecutionResult(
            status=ExecutionTerminal.COMPLETED,
            node_id=node.node_id,
            output=merged,
        )

    def _create_child_nodes(
        self, *, parent: NodeState, specs: list[RecursiveChildSpec]
    ) -> list[_ChildRuntimeNode]:
        runtime: list[_ChildRuntimeNode] = []
        # Map objective text → alias so LLM-provided deps can be resolved
        objective_to_alias: dict[str, str] = {}

        for index, child in enumerate(specs, start=1):
            alias = f"child_{index}"
            objective_to_alias[child.objective] = alias
            child_id = f"node_{self._id_factory()}"
            # Respect per-child QA policy from divider
            child_checker = parent.checker_policy
            if hasattr(child, "needs_qa") and not child.needs_qa:
                child_checker = CheckerConfig(
                    enabled=False,
                    node_level=False,
                    merge_level=False,
                    max_retries_per_node=parent.checker_policy.max_retries_per_node,
                )
            state = NodeState(
                node_id=child_id,
                run_id=parent.run_id,
                parent_id=parent.node_id,
                depth=parent.depth + 1,
                objective=child.objective,
                node_kind=NodeKind.DIVIDER,
                checker_policy=child_checker,
                persona_id=child.suggested_persona,
            )
            self._repository.create_node(state)
            self._emit_node_created(state)
            runtime.append(
                _ChildRuntimeNode(
                    alias=alias,
                    node_id=child_id,
                    objective=child.objective,
                    dependencies=tuple(child.dependencies),
                    suggested_persona=child.suggested_persona,
                )
            )

        # Normalize dependencies: LLM may return objective text instead of aliases
        normalized: list[_ChildRuntimeNode] = []
        all_aliases = {r.alias for r in runtime}
        for child in runtime:
            resolved_deps: list[str] = []
            for dep in child.dependencies:
                if dep in all_aliases:
                    resolved_deps.append(dep)
                elif dep in objective_to_alias:
                    resolved_deps.append(objective_to_alias[dep])
                # else: drop unrecognized dependency (LLM hallucination)
            normalized.append(
                _ChildRuntimeNode(
                    alias=child.alias,
                    node_id=child.node_id,
                    objective=child.objective,
                    dependencies=tuple(resolved_deps),
                    suggested_persona=child.suggested_persona,
                )
            )

        return normalized

    def _apply_persona(self, *, node_id: str, route: PersonaRouteResult) -> None:
        if not route.persona_id:
            return
        self._repository.update_node_persona(node_id, route.persona_id)

    def _merge_children(
        self, *, node: NodeState, merged_outputs: list[dict[str, Any]]
    ) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
        if self._merger is None or len(merged_outputs) < 2:
            return {
                "parent_objective": node.objective,
                "children": merged_outputs,
            }

        merge_request = MergeRequest(
            parent_objective=node.objective,
            child_outputs=[
                {
                    "node_id": child["node_id"],
                    "persona_id": self._repository.get_node(child["node_id"]).persona_id
                    or "unassigned",
                    "output": child["output"],
                    "boundary_contract": None,
                }
                for child in merged_outputs
            ],
        )

        merge_result = self._merger.merge(merge_request)
        self._emit_merge_events(node=node, merge_result=merge_result)

        return {
            "parent_objective": node.objective,
            "children": merged_outputs,
            "merged_output": merge_result.response.merged_output,
            "conflict_resolutions": [
                resolution.model_dump()
                for resolution in merge_result.response.conflict_resolutions
            ],
            "unresolved_conflicts": list(merge_result.response.unresolved_conflicts),
            "integration_ready": not merge_result.has_unresolved_conflicts,
        }

    def _emit_merge_events(self, *, node: NodeState, merge_result: object) -> None:
        if self._event_emitter is None:
            return
        events = getattr(merge_result, "events", ())
        for event in events:
            event_type = getattr(event, "event_type", "")
            payload = getattr(event, "payload", {})
            if event_type == "merge.started":
                self._event_emitter(
                    node.run_id,
                    node.node_id,
                    DomainEventType.MERGE_STARTED,
                    dict(payload),
                )
            elif event_type == "merge.completed":
                self._event_emitter(
                    node.run_id,
                    node.node_id,
                    DomainEventType.MERGE_COMPLETED,
                    dict(payload),
                )

    def _evaluate_checker(
        self,
        *,
        node: NodeState,
        scope: CheckerScope,
        output: dict[str, Any] | list[Any] | str | int | float | bool | None,
    ) -> CheckerOutcome | None:
        if self._checker is None:
            return None

        checker_config = node.checker_policy
        consecutive_failures = self._repository.get_node(
            node.node_id
        ).consecutive_checker_failures

        self._emit_checker_started(node=node, scope=scope)

        if scope == CheckerScope.NODE:
            outcome = self._checker.evaluate_node(
                checker_config=checker_config,
                objective=node.objective,
                output=output,
                consecutive_failures=consecutive_failures,
                metadata={"node_id": node.node_id, "run_id": node.run_id},
            )
        else:
            outcome = self._checker.evaluate_merge(
                checker_config=checker_config,
                objective=node.objective,
                output=output,
                consecutive_failures=consecutive_failures,
                metadata={"node_id": node.node_id, "run_id": node.run_id},
            )

        if outcome.invoked and outcome.result is not None:
            self._emit_checker_completed(
                node=node,
                scope=scope,
                verdict=outcome.result.verdict.value,
                reason=outcome.result.reason,
                suggested_fix=outcome.result.suggested_fix,
                confidence=outcome.result.confidence,
                violations=list(outcome.result.violations),
                consecutive_failures=outcome.consecutive_failures,
            )

        if not outcome.invoked or outcome.next_node_status is None:
            return None

        if outcome.next_node_status == NodeStatus.COMPLETED:
            self._repository.reset_checker_failures(node.node_id)
            return outcome

        if outcome.next_node_status == NodeStatus.FAILED_CHECK:
            self._repository.increment_checker_failures(node.node_id)
            return outcome

        if outcome.next_node_status == NodeStatus.BLOCKED_HUMAN:
            self._repository.increment_checker_failures(node.node_id)
            self._mark_blocked_human(node.node_id)
            return outcome

        return outcome

    def _emit_checker_started(self, *, node: NodeState, scope: CheckerScope) -> None:
        if self._event_emitter is None:
            return
        self._event_emitter(
            node.run_id,
            node.node_id,
            DomainEventType.CHECKER_STARTED,
            {"scope": scope.value, "attempt": node.attempt_count},
        )

    def _emit_checker_completed(
        self,
        *,
        node: NodeState,
        scope: CheckerScope,
        verdict: str,
        reason: str,
        suggested_fix: str,
        confidence: float,
        violations: list[str],
        consecutive_failures: int,
    ) -> None:
        if self._event_emitter is None:
            return
        self._event_emitter(
            node.run_id,
            node.node_id,
            DomainEventType.CHECKER_COMPLETED,
            {
                "scope": scope.value,
                "verdict": verdict,
                "reason": reason,
                "suggestedFix": suggested_fix,
                "suggested_fix": suggested_fix,
                "confidence": confidence,
                "violations": violations,
                "consecutiveFailures": consecutive_failures,
                "consecutive_failures": consecutive_failures,
            },
        )

    def _mark_blocked_human(self, node_id: str) -> None:
        node = self._repository.get_node(node_id)
        if node.status == NodeStatus.BLOCKED_HUMAN:
            return
        if node.status == NodeStatus.RUNNING:
            self._repository.update_node_status(node_id, NodeStatus.WAITING_CHECK)
            self._repository.update_node_status(node_id, NodeStatus.FAILED_CHECK)
            ended = self._repository.record_node_ended(
                node_id, NodeStatus.BLOCKED_HUMAN
            )
            self._emit_node_status(
                node_id=node_id,
                status=ended.status,
                reason="checker_failed_consecutive_threshold",
                duration_ms=ended.duration_ms,
                ttft_ms=ended.ttft_ms,
            )
            self._emit_node_blocked(node_id=node_id)
            return
        if node.status == NodeStatus.WAITING_CHECK:
            self._repository.update_node_status(node_id, NodeStatus.FAILED_CHECK)
            ended = self._repository.record_node_ended(
                node_id, NodeStatus.BLOCKED_HUMAN
            )
            self._emit_node_status(
                node_id=node_id,
                status=ended.status,
                reason="checker_failed_consecutive_threshold",
                duration_ms=ended.duration_ms,
                ttft_ms=ended.ttft_ms,
            )
            self._emit_node_blocked(node_id=node_id)
            return
        if node.status == NodeStatus.FAILED_CHECK:
            ended = self._repository.record_node_ended(
                node_id, NodeStatus.BLOCKED_HUMAN
            )
            self._emit_node_status(
                node_id=node_id,
                status=ended.status,
                reason="checker_failed_consecutive_threshold",
                duration_ms=ended.duration_ms,
                ttft_ms=ended.ttft_ms,
            )
            self._emit_node_blocked(node_id=node_id)
            return
        ended = self._repository.record_node_ended(node_id, NodeStatus.ERROR)
        self._emit_node_status(
            node_id=node_id,
            status=ended.status,
            reason="invalid_transition_to_blocked_human",
            duration_ms=ended.duration_ms,
            ttft_ms=ended.ttft_ms,
        )

    def _emit_node_created(self, node: NodeState) -> None:
        if self._event_emitter is None:
            return
        self._event_emitter(
            node.run_id,
            node.node_id,
            DomainEventType.NODE_CREATED,
            {
                "node": {
                    "nodeId": node.node_id,
                    "runId": node.run_id,
                    "parentNodeId": node.parent_id,
                    "objective": node.objective,
                    "status": self._event_node_status(node.status),
                    "personaId": node.persona_id,
                    "depth": node.depth,
                    "nodeKind": node.node_kind.value,
                    "ttftMs": node.ttft_ms,
                    "durationMs": node.duration_ms,
                    "checkerFailureCount": node.consecutive_checker_failures,
                },
                "parentNodeId": node.parent_id,
                "relation": "child",
            },
        )

    def _emit_node_status(
        self,
        *,
        node_id: str,
        status: NodeStatus,
        reason: str | None = None,
        duration_ms: int | None = None,
        ttft_ms: int | None = None,
    ) -> None:
        if self._event_emitter is None:
            return
        node = self._repository.get_node(node_id)
        payload: dict[str, object] = {
            "status": self._event_node_status(status),
            "nodeKind": node.node_kind.value,
            "durationMs": duration_ms if duration_ms is not None else node.duration_ms,
            "ttftMs": ttft_ms if ttft_ms is not None else node.ttft_ms,
            "checkerFailureCount": node.consecutive_checker_failures,
        }
        if reason:
            payload["reason"] = reason
            # Classify error source for the frontend
            payload["errorSource"] = self._classify_error_source(reason)
        self._event_emitter(
            node.run_id,
            node.node_id,
            DomainEventType.NODE_STATUS_CHANGED,
            payload,
        )

    def _emit_node_ttft(self, node: NodeState) -> None:
        if self._event_emitter is None or node.ttft_ms is None:
            return
        self._event_emitter(
            node.run_id,
            node.node_id,
            DomainEventType.NODE_TTFT_RECORDED,
            {
                "ttft_ms": node.ttft_ms,
                "ttftMs": node.ttft_ms,
            },
        )

    def _emit_node_blocked(self, *, node_id: str) -> None:
        if self._event_emitter is None:
            return
        node = self._repository.get_node(node_id)
        self._event_emitter(
            node.run_id,
            node.node_id,
            DomainEventType.NODE_BLOCKED_HUMAN,
            {
                "reason": "checker_failed_consecutive_threshold",
                "retryCount": node.consecutive_checker_failures,
            },
        )

    @staticmethod
    def _classify_error_source(reason: str) -> str:
        """Classify whether an error is from the LLM (task failure) or the app (infra)."""
        llm_keywords = (
            "schema validation", "not valid JSON", "checker_failed",
            "step failed", "LLM", "generate_json", "response content",
        )
        infra_keywords = (
            "max_depth", "depth_limit", "unresolved sibling", "guardrail",
            "invalid_transition", "truncated",
        )
        reason_lower = reason.lower()
        for kw in infra_keywords:
            if kw.lower() in reason_lower:
                return "app_guardrail"
        for kw in llm_keywords:
            if kw.lower() in reason_lower:
                return "llm_task_failure"
        return "unknown"

    @staticmethod
    def _event_node_status(status: NodeStatus) -> str:
        if status in {NodeStatus.WAITING_CHECK}:
            return "running"
        if status in {NodeStatus.FAILED_CHECK, NodeStatus.ERROR}:
            return "failed"
        return status.value

    @staticmethod
    def _forced_base_case_result(
        *,
        node: NodeState,
        max_depth: int,
        reason: str = "max_depth_reached",
    ) -> DividerServiceResult:
        return DividerServiceResult(
            decision=DividerDecision.BASE_CASE,
            base_case=BaseCaseWorkPlan(
                rationale=(
                    f"Forced BASE_CASE because node depth {node.depth} reached max_depth {max_depth}"
                ),
                work_plan=[
                    {
                        "step": 1,
                        "description": (
                            f"Produce best-effort direct solution for objective within depth budget. "
                            f"Constraint reason: {reason}."
                        ),
                    }
                ],
                suggested_persona=node.persona_id,
            ),
            attempts_used=0,
        )

    def _emit_depth_limit_reached(
        self,
        *,
        node: NodeState,
        max_depth: int,
        reason: str = "max_depth_reached",
    ) -> None:
        if self._event_emitter is None:
            return
        self._event_emitter(
            node.run_id,
            node.node_id,
            DomainEventType.NODE_TOKEN,
            {
                "token": (
                    f"Depth guardrail active: forcing base-case at depth={node.depth} "
                    f"(max_depth={max_depth}, reason={reason})."
                ),
                "stream": "stderr",
            },
        )

    def _record_attempt(
        self,
        *,
        node: NodeState,
        output: dict[str, Any] | list[Any] | str | int | float | bool | None,
        error: str | None,
        checker_result: CheckerResult | None = None,
    ) -> None:
        latest = self._repository.get_node(node.node_id)
        attempt = AttemptState(
            attempt_id=f"att_{self._id_factory()}",
            node_id=node.node_id,
            attempt_index=latest.attempt_count,
            input_snapshot={
                "objective": node.objective,
                "depth": node.depth,
                "persona_id": latest.persona_id,
            },
            output_snapshot=output,
            checker_result=checker_result,
            error={"message": error} if error else None,
        )
        self._repository.create_attempt(attempt)


__all__ = [
    "BaseCaseWorker",
    "DeterministicBaseCaseWorker",
    "ExecutionTerminal",
    "NodeExecutionResult",
    "RecursiveExecutor",
    "WorkExecutionResult",
]
