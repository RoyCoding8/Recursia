"""Recursive executor for divide-route-execute orchestration flow."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

_log = logging.getLogger(__name__)


def _init_model_tiers() -> dict[str, str | None]:
    """Resolve model tiers from env vars: LLM_MODEL_TIER_FAST, etc."""
    return {
        "fast": os.getenv("LLM_MODEL_TIER_FAST") or None,
        "standard": os.getenv("LLM_MODEL_TIER_STANDARD") or None,
        "strong": os.getenv("LLM_MODEL_TIER_STRONG") or None,
    }


_MODEL_TIERS = _init_model_tiers()

from app.domain.events import DomainEventType
from app.domain.models import AttemptState, NodeContext, NodeKind, NodeState, NodeStatus
from app.schemas.api import CheckerConfig, RunConfig
from app.schemas.contracts import CheckerResult, DividerDecision, MergeRequest
from app.services.checker import CheckerOutcome, CheckerScope, CheckerService
from app.services.checker_handler import build_checker_feedback
from app.services.complexity import ComplexityEstimator
from app.services.divider import (
    BaseCaseWorkPlan,
    DividerService,
    DividerServiceResult,
    RecursiveChildSpec,
)
from app.services.merger import MergerService, MergerServiceResult
from app.services.persona_router import PersonaRouter, PersonaRouteResult
from app.services.stubs import DeterministicBaseCaseWorker
from app.state.repository import RunStateRepository

_INFRA_KEYWORDS = frozenset({
    "max_depth", "depth_limit", "unresolved sibling", "guardrail",
    "invalid_transition", "truncated",
})

_LLM_KEYWORDS = frozenset({
    "schema validation", "not valid json", "checker_failed",
    "step failed", "llm", "generate_json", "response content",
})

_EVENT_STATUS_MAP: dict[NodeStatus, str] = {
    NodeStatus.WAITING_CHECK: "running",
    NodeStatus.FAILED_CHECK: "failed",
    NodeStatus.ERROR: "failed",
}


def _default_id_factory() -> str:
    return uuid4().hex


class ExecutionTerminal(str, Enum):
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
        node_context: NodeContext | None = None,
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
        complexity_estimator: ComplexityEstimator | None = None,
        event_emitter: EventEmitter | None = None,
        id_factory: Callable[[], str] | None = None,
        cancellation: threading.Event | None = None,
        usage_tracker: Any | None = None,
    ) -> None:
        self._repository = repository
        self._divider = divider
        self._persona_router = persona_router
        self._worker = worker or DeterministicBaseCaseWorker()
        self._checker = checker
        self._merger = merger
        self._complexity_estimator = complexity_estimator or ComplexityEstimator()
        self._event_emitter = event_emitter
        self._id_factory = id_factory or _default_id_factory
        self._cancellation = cancellation
        self._usage_tracker = usage_tracker
        self._outputs: dict[str, dict[str, Any]] = {}

    def get_output(self, node_id: str) -> Any | None:
        for run_outputs in self._outputs.values():
            if node_id in run_outputs:
                return run_outputs[node_id]
        return None

    def clear_run(self, run_id: str) -> None:
        self._outputs.pop(run_id, None)

    def _check_cancelled(self) -> bool:
        """Return True if cancellation has been requested."""
        return self._cancellation is not None and self._cancellation.is_set()

    def _persist_usage(self, run_id: str, node_id: str) -> bool:
        """Flush tracked LLM token usage to repository and emit event. Returns True if budget exceeded."""
        if self._usage_tracker is None:
            return False
        tokens = self._usage_tracker.total_tokens
        if tokens > 0:
            self._repository.increment_run_tokens(run_id, tokens)
            self._emit(run_id, node_id, DomainEventType.TOKEN_USAGE_RECORDED, {
                "tokens_this_node": tokens, "total_tokens": tokens,
            })
            self._usage_tracker.total_tokens = 0
            self._usage_tracker.total_prompt_tokens = 0
            self._usage_tracker.total_completion_tokens = 0
        run = self._repository.get_run(run_id)
        budget = run.config.token_budget
        return budget.on_exhausted == "fail" and run.tokens_used >= budget.max_total_tokens

    def execute_node(self, *, run_id: str, node_id: str,
                     node_context: NodeContext | None = None) -> NodeExecutionResult:
        """Execute one node and its descendants until terminal state."""
        if self._check_cancelled():
            return NodeExecutionResult(status=ExecutionTerminal.FAILED, node_id=node_id, error="execution cancelled")

        run = self._repository.get_run(run_id)
        budget = run.config.token_budget
        if budget.on_exhausted == "fail" and run.tokens_used >= budget.max_total_tokens:
            self._emit(run_id, node_id, DomainEventType.NODE_STATUS_CHANGED,
                       {"status": NodeStatus.FAILED_CHECK.value, "reason": "token_budget_exceeded"})
            return NodeExecutionResult(status=ExecutionTerminal.FAILED, node_id=node_id,
                                       error=f"token budget exhausted ({run.tokens_used}/{budget.max_total_tokens})")

        node = self._repository.get_node(node_id)

        # Complexity estimation (needed for adaptive depth and cost-aware routing)
        complexity = self._complexity_estimator.estimate(
            node.objective, context=node_context, depth=node.depth,
        )

        effective_max_depth = run.config.max_depth
        if run.config.adaptive_depth:
            effective_max_depth = min(run.config.max_depth, complexity.suggested_depth)
        depth_limited = node.depth >= effective_max_depth

        if node_context is None:
            node_context = NodeContext(root_objective=run.objective)

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

        num_candidates = (
            run.config.decomposition_candidates
            if complexity.score >= run.config.complexity_threshold else 1
        )

        if depth_limited:
            divide_result = self._forced_base_case_result(
                node=node, max_depth=effective_max_depth
            )
            self._emit_depth_limit_reached(node=node, max_depth=effective_max_depth)
        else:
            divide_result = self._divider.divide(
                objective=node.objective, depth=node.depth,
                node_context=node_context,
                complexity=complexity, num_candidates=num_candidates,
            )

        if (
            divide_result.decision == DividerDecision.RECURSIVE_CASE
            and node.depth >= effective_max_depth
        ):
            divide_result = self._forced_base_case_result(
                node=node,
                max_depth=effective_max_depth,
                reason="divider_requested_recursive_at_depth_limit",
            )
            self._emit_depth_limit_reached(
                node=node,
                max_depth=effective_max_depth,
                reason="divider_requested_recursive_at_depth_limit",
            )

        if divide_result.decision == DividerDecision.BASE_CASE:
            model = _MODEL_TIERS.get(complexity.model_tier)
            result = self._execute_base_case(
                run_config=run.config,
                node=self._repository.get_node(node_id),
                divide_result=divide_result,
                node_context=node_context,
                complexity=complexity,
                model=model,
            )
        else:
            result = self._execute_recursive_case(
                run_config=run.config,
                node=self._repository.get_node(node_id),
                divide_result=divide_result,
                node_context=node_context,
                complexity=complexity,
            )

        budget_exceeded = self._persist_usage(run_id, node_id)
        if budget_exceeded:
            self._emit(run_id, node_id, DomainEventType.NODE_STATUS_CHANGED,
                       {"status": NodeStatus.FAILED_CHECK.value, "reason": "token_budget_exceeded"})
            return NodeExecutionResult(status=ExecutionTerminal.FAILED, node_id=node_id,
                                       error="token budget exhausted during execution")
        return result

    def _execute_base_case(
        self,
        *,
        run_config: RunConfig,
        node: NodeState,
        divide_result: DividerServiceResult,
        node_context: NodeContext,
        complexity: object | None = None,
        model: str | None = None,
    ) -> NodeExecutionResult:
        if divide_result.base_case is None:
            self._end_node(node.node_id, NodeStatus.ERROR, "divider returned BASE_CASE without base_case payload")
            return self._terminal(
                status=ExecutionTerminal.FAILED, node=node, error="divider returned BASE_CASE without base_case payload",
            )

        base = divide_result.base_case
        self._repository.update_node_kind(node.node_id, NodeKind.WORK)
        if base.suggested_persona and not node.persona_id:
            self._repository.update_node_persona(node.node_id, base.suggested_persona)
        if not base.needs_qa:
            self._repository.update_node_checker_policy(
                node.node_id, CheckerConfig(enabled=False, node_level=False, merge_level=False),
            )
        node = self._repository.get_node(node.node_id)

        token_node = self._repository.record_node_first_token(node.node_id)
        self._emit_node_ttft(token_node)

        # Persona chain: run through personas sequentially, each refining prior output
        personas = run_config.persona_chain or [node.persona_id]
        work = None
        current_ctx = node_context
        for pid in personas:
            work = self._worker.execute(
                run_id=node.run_id, node_id=node.node_id, objective=node.objective,
                depth=node.depth, persona_id=pid,
                work_plan=base.work_plan, node_context=current_ctx, model=model,
            )
            if work.status != ExecutionTerminal.COMPLETED:
                break
            if pid != personas[-1]:
                import json as _json
                summary = _json.dumps(work.output, ensure_ascii=False, default=str)[:2000]
                current_ctx = current_ctx.with_prior_output(summary)

        if work.status == ExecutionTerminal.BLOCKED_HUMAN:
            self._mark_blocked_human(node.node_id)
            return self._terminal(
                status=ExecutionTerminal.BLOCKED_HUMAN, node=node, error=work.error,
            )

        if work.status != ExecutionTerminal.COMPLETED:
            self._end_node(node.node_id, NodeStatus.ERROR, work.error)
            return self._terminal(
                status=ExecutionTerminal.FAILED, node=node, error=work.error,
            )

        # --- work succeeded; evaluate checker ---
        checker_outcome = self._evaluate_checker(node=node, scope=CheckerScope.NODE, output=work.output)
        checker_result = checker_outcome.result if checker_outcome else None
        final_output = work.output

        if checker_outcome is not None and checker_outcome.next_node_status == NodeStatus.FAILED_CHECK:
            final_output, checker_outcome, checker_result = self._checker_retry_loop(
                node=node, node_context=node_context, run_config=run_config,
                divide_result=divide_result, complexity=complexity,
                work=work, checker_outcome=checker_outcome, checker_result=checker_result,
                model=model,
            )
            if checker_outcome is not None and checker_outcome.next_node_status == NodeStatus.FAILED_CHECK:
                raw = final_output if isinstance(final_output, dict) else {"raw": final_output}
                final_output = {
                    **raw, "validation_warning": True,
                    "validation_reason": checker_result.reason if checker_result else "retries exhausted",
                }

        blocked = self._handle_checker_outcome(
            node=node, work_output=work.output, checker_outcome=checker_outcome,
            checker_result=checker_result,
        )
        if blocked is not None:
            return blocked

        self._end_node(node.node_id, NodeStatus.COMPLETED)
        self._outputs.setdefault(node.run_id, {})[node.node_id] = final_output
        return self._terminal(
            status=ExecutionTerminal.COMPLETED, node=node, output=final_output,
            checker_result=checker_result,
        )

    def _checker_retry_loop(
        self, *, node: NodeState, node_context: NodeContext, run_config: RunConfig,
        divide_result: DividerServiceResult, complexity: object | None,
        work: WorkExecutionResult, checker_outcome: CheckerOutcome, checker_result: CheckerResult | None,
        model: str | None = None,
    ) -> tuple[Any, CheckerOutcome | None, CheckerResult | None]:
        max_retries = node.checker_policy.max_retries_per_node
        re_decompose_after = run_config.re_decompose_after
        final_output = work.output
        consecutive_fails = 0

        for _retry in range(max_retries):
            if node.checker_policy.on_check_fail == "pause":
                break

            fix = checker_result.suggested_fix if checker_result else ""
            violations = list(checker_result.violations) if checker_result else []
            retry_ctx = node_context.with_checker_feedback(fix, violations)
            consecutive_fails += 1

            if consecutive_fails >= re_decompose_after and divide_result.base_case is not None:
                self._record_attempt(
                    node=node, output=work.output,
                    error=f"re-decomposing after {consecutive_fails} failures",
                    checker_result=checker_result,
                )
                try:
                    new_divide = self._divider.divide(
                        objective=node.objective, depth=node.depth,
                        node_context=retry_ctx, complexity=complexity,
                    )
                    if new_divide.base_case is not None:
                        divide_result = new_divide
                        consecutive_fails = 0
                except Exception:
                    _log.warning("re-decompose attempt failed for node=%s", node.node_id, exc_info=True)

            self._record_attempt(
                node=node, output=work.output,
                error=f"self-heal retry (checker: {checker_result.reason if checker_result else 'failed'})",
                checker_result=checker_result,
            )
            work = self._worker.execute(
                run_id=node.run_id, node_id=node.node_id, objective=node.objective,
                depth=node.depth, persona_id=node.persona_id,
                work_plan=divide_result.base_case.work_plan, node_context=retry_ctx, model=model,
            )
            if work.status != ExecutionTerminal.COMPLETED:
                break
            checker_outcome = self._evaluate_checker(node=node, scope=CheckerScope.NODE, output=work.output)
            checker_result = checker_outcome.result if checker_outcome else None
            if checker_outcome is None or checker_outcome.next_node_status == NodeStatus.COMPLETED:
                break
            if checker_outcome.next_node_status == NodeStatus.BLOCKED_HUMAN:
                break

        final_output = work.output
        return final_output, checker_outcome, checker_result

    def _execute_recursive_case(
        self,
        *,
        run_config: RunConfig,
        node: NodeState,
        divide_result: DividerServiceResult,
        node_context: NodeContext,
        complexity: object | None = None,
    ) -> NodeExecutionResult:
        if divide_result.recursive_case is None:
            error = "divider returned RECURSIVE_CASE without recursive_case payload"
            self._end_node(node.node_id, NodeStatus.ERROR, error)
            return self._terminal(status=ExecutionTerminal.FAILED, node=node, error=error)

        children_specs = divide_result.recursive_case.children
        run = self._repository.get_run(node.run_id)
        if len(children_specs) > run.config.max_children_per_node:
            self._emit_node_status(
                node_id=node.node_id, status=NodeStatus.RUNNING,
                reason=f"guardrail: truncated {len(children_specs)} children to max {run.config.max_children_per_node}",
            )
            children_specs = children_specs[: run.config.max_children_per_node]

        pruned = self._repository.delete_children_of(node.run_id, node.node_id)
        if pruned > 0:
            self._emit_subtree_pruned(node=node, pruned_count=pruned)

        runtime_children = self._create_child_nodes(parent=node, specs=children_specs)
        all_sibling_objectives = [c.objective for c in runtime_children]
        child_constraints = [c.interface_contract for c in children_specs if c.interface_contract]

        pending = list(runtime_children)
        completed_aliases: set[str] = set()
        merged_outputs: list[dict[str, Any]] = []
        running_context = node_context

        while pending:
            ready = [c for c in pending if set(c.dependencies).issubset(completed_aliases)]
            if not ready:
                unresolved = {c.alias: list(c.dependencies) for c in pending}
                error = f"unresolved sibling dependencies: {unresolved}"
                self._end_node(node.node_id, NodeStatus.ERROR, error)
                return self._terminal(status=ExecutionTerminal.FAILED, node=node, error=error)

            for child in ready:
                child_ctx = running_context.child(
                    objective=node.objective, siblings=all_sibling_objectives,
                    constraints=child_constraints,
                )
                child_result = self.execute_node(run_id=node.run_id, node_id=child.node_id, node_context=child_ctx)
                pending = [item for item in pending if item.node_id != child.node_id]

                if child_result.status == ExecutionTerminal.COMPLETED:
                    completed_aliases.add(child.alias)
                    merged_outputs.append({
                        "alias": child.alias, "node_id": child.node_id,
                        "objective": child.objective, "output": child_result.output,
                    })
                    running_context = running_context.with_sibling_output(f"{child.objective}: completed")
                    continue

                error_prefix = "blocked" if child_result.status == ExecutionTerminal.BLOCKED_HUMAN else "failed"
                self._mark_blocked_human(node.node_id) if error_prefix == "blocked" else self._end_node(
                    node.node_id, NodeStatus.ERROR, child_result.error,
                )
                return self._terminal(
                    status=child_result.status, node=node,
                    error=f"child node {child.node_id} {error_prefix}: {child_result.error or ''}".strip(),
                )

        merged = self._merge_children(node=node, merged_outputs=merged_outputs)
        checker_outcome = self._evaluate_checker(node=node, scope=CheckerScope.MERGE, output=merged)
        checker_result = checker_outcome.result if checker_outcome else None

        if checker_outcome is not None and checker_outcome.next_node_status == NodeStatus.FAILED_CHECK:
            merge_max_retries = min(node.checker_policy.max_retries_per_node, 2)
            for _ in range(merge_max_retries):
                self._record_attempt(node=node, output=merged, error="merge-level retry", checker_result=checker_result)
                merged = self._merge_children(node=node, merged_outputs=merged_outputs, checker_feedback=build_checker_feedback(checker_result))
                checker_outcome = self._evaluate_checker(node=node, scope=CheckerScope.MERGE, output=merged)
                checker_result = checker_outcome.result if checker_outcome else None
                if checker_outcome is None or checker_outcome.next_node_status in (NodeStatus.COMPLETED, NodeStatus.BLOCKED_HUMAN):
                    break

        blocked = self._handle_checker_outcome(
            node=node, work_output=merged, checker_outcome=checker_outcome,
            checker_result=checker_result, record_output=merged,
        )
        if blocked is not None:
            return blocked

        self._outputs.setdefault(node.run_id, {})[node.node_id] = merged
        self._end_node(node.node_id, NodeStatus.COMPLETED)
        return self._terminal(
            status=ExecutionTerminal.COMPLETED, node=node, output=merged,
            checker_result=checker_result,
        )

    def _create_child_nodes(
        self, *, parent: NodeState, specs: list[RecursiveChildSpec]
    ) -> list[_ChildRuntimeNode]:
        objective_to_alias: dict[str, str] = {}
        children: list[_ChildRuntimeNode] = []

        for index, child in enumerate(specs, start=1):
            alias = f"child_{index}"
            objective_to_alias[child.objective] = alias
            child_id = f"node_{self._id_factory()}"
            checker = (CheckerConfig(enabled=False, node_level=False, merge_level=False,
                                     max_retries_per_node=parent.checker_policy.max_retries_per_node)
                       if not child.needs_qa else parent.checker_policy)
            state = NodeState(node_id=child_id, run_id=parent.run_id, parent_id=parent.node_id,
                              depth=parent.depth + 1, objective=child.objective,
                              node_kind=NodeKind.DIVIDER, checker_policy=checker,
                              persona_id=child.suggested_persona)
            self._repository.create_node(state)
            self._emit_node_created(state)
            children.append(_ChildRuntimeNode(alias=alias, node_id=child_id,
                                              objective=child.objective,
                                              dependencies=tuple(child.dependencies),
                                              suggested_persona=child.suggested_persona))

        # Normalize deps: LLM may return objective text instead of aliases
        all_aliases = {c.alias for c in children}
        resolve = lambda dep: objective_to_alias.get(dep, dep) if dep not in all_aliases else dep
        return [_ChildRuntimeNode(c.alias, c.node_id, c.objective,
                                  tuple(resolve(d) for d in c.dependencies),
                                  c.suggested_persona) for c in children]

    def _apply_persona(self, *, node_id: str, route: PersonaRouteResult) -> None:
        if not route.persona_id:
            return
        self._repository.update_node_persona(node_id, route.persona_id)

    def _merge_children(self, *, node: NodeState, merged_outputs: list[dict[str, Any]],
                        checker_feedback: str | None = None) -> Any:
        if self._merger is None or len(merged_outputs) < 2:
            return {"parent_objective": node.objective, "children": merged_outputs}

        obj = node.objective
        if checker_feedback:
            obj += f"\n\n[CHECKER FEEDBACK]: {checker_feedback}"

        children = [{"node_id": c["node_id"],
                     "persona_id": self._repository.get_node(c["node_id"]).persona_id or "unassigned",
                     "output": c["output"], "boundary_contract": None} for c in merged_outputs]
        result = self._merger.merge(MergeRequest(parent_objective=obj, child_outputs=children))
        self._emit_merge_events(node=node, merge_result=result)

        return {"parent_objective": node.objective, "children": merged_outputs,
                "merged_output": result.response.merged_output,
                "conflict_resolutions": [r.model_dump() for r in result.response.conflict_resolutions],
                "unresolved_conflicts": list(result.response.unresolved_conflicts),
                "integration_ready": not result.has_unresolved_conflicts}

    def _emit_merge_events(self, *, node: NodeState, merge_result: MergerServiceResult) -> None:
        _type_map = {"merge.started": DomainEventType.MERGE_STARTED,
                     "merge.completed": DomainEventType.MERGE_COMPLETED}
        for event in merge_result.events:
            evt = _type_map.get(event.event_type)
            if evt:
                self._emit(node.run_id, node.node_id, evt, dict(event.payload))

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

        outcome = self._checker.evaluate(
            checker_config=checker_config, scope=scope,
            objective=node.objective, output=output,
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
        elif outcome.next_node_status in (NodeStatus.FAILED_CHECK, NodeStatus.BLOCKED_HUMAN):
            self._repository.increment_checker_failures(node.node_id)
            if outcome.next_node_status == NodeStatus.BLOCKED_HUMAN:
                self._mark_blocked_human(node.node_id)

        return outcome

    def _emit_checker_started(self, *, node: NodeState, scope: CheckerScope) -> None:
        self._emit(node.run_id, node.node_id, DomainEventType.CHECKER_STARTED,
                   {"scope": scope.value, "attempt": node.attempt_count})

    def _emit_checker_completed(self, *, node: NodeState, scope: CheckerScope,
                                verdict: str, reason: str, suggested_fix: str,
                                confidence: float, violations: list[str],
                                consecutive_failures: int) -> None:
        self._emit(node.run_id, node.node_id, DomainEventType.CHECKER_COMPLETED, {
            "scope": scope.value, "verdict": verdict, "reason": reason,
            "suggestedFix": suggested_fix, "confidence": confidence,
            "violations": violations, "consecutiveFailures": consecutive_failures,
        })

    def _mark_blocked_human(self, node_id: str) -> None:
        node = self._repository.get_node(node_id)
        if node.status == NodeStatus.BLOCKED_HUMAN:
            return
        _PATHS = {NodeStatus.RUNNING: [NodeStatus.WAITING_CHECK, NodeStatus.FAILED_CHECK],
                  NodeStatus.WAITING_CHECK: [NodeStatus.FAILED_CHECK], NodeStatus.FAILED_CHECK: []}
        path = _PATHS.get(node.status)
        if path is not None:
            for s in path:
                self._repository.update_node_status(node_id, s)
            ended = self._repository.record_node_ended(node_id, NodeStatus.BLOCKED_HUMAN)
            self._emit_node_status(node_id=node_id, status=ended.status,
                                   reason="checker_failed_consecutive_threshold",
                                   duration_ms=ended.duration_ms, ttft_ms=ended.ttft_ms)
            self._emit_node_blocked(node_id=node_id)
        else:
            ended = self._repository.record_node_ended(node_id, NodeStatus.ERROR)
            self._emit_node_status(node_id=node_id, status=ended.status,
                                   reason="invalid_transition_to_blocked_human",
                                   duration_ms=ended.duration_ms, ttft_ms=ended.ttft_ms)

    def _emit(self, run_id: str, node_id: str, event_type: DomainEventType,
             payload: dict[str, object]) -> None:
        if self._event_emitter is not None:
            self._event_emitter(run_id, node_id, event_type, payload)

    def _emit_node_created(self, node: NodeState) -> None:
        n = {"nodeId": node.node_id, "runId": node.run_id, "parentNodeId": node.parent_id,
             "objective": node.objective, "status": self._event_node_status(node.status),
             "personaId": node.persona_id, "depth": node.depth, "nodeKind": node.node_kind.value,
             "ttftMs": node.ttft_ms, "durationMs": node.duration_ms,
             "checkerFailureCount": node.consecutive_checker_failures}
        self._emit(node.run_id, node.node_id, DomainEventType.NODE_CREATED,
                   {"node": n, "parentNodeId": node.parent_id, "relation": "child"})

    def _emit_node_status(self, *, node_id: str, status: NodeStatus,
                          reason: str | None = None, duration_ms: int | None = None,
                          ttft_ms: int | None = None) -> None:
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
            payload["errorSource"] = self._classify_error_source(reason)
        self._emit(node.run_id, node.node_id, DomainEventType.NODE_STATUS_CHANGED, payload)

    def _emit_node_ttft(self, node: NodeState) -> None:
        if node.ttft_ms is not None:
            self._emit(node.run_id, node.node_id, DomainEventType.NODE_TTFT_RECORDED,
                       {"ttft_ms": node.ttft_ms, "ttftMs": node.ttft_ms})

    def _emit_node_blocked(self, *, node_id: str) -> None:
        node = self._repository.get_node(node_id)
        self._emit(node.run_id, node.node_id, DomainEventType.NODE_BLOCKED_HUMAN,
                   {"reason": "checker_failed_consecutive_threshold",
                    "retryCount": node.consecutive_checker_failures})

    def _emit_subtree_pruned(self, node: NodeState, pruned_count: int) -> None:
        self._emit(node.run_id, node.node_id, DomainEventType.SUBTREE_PRUNED,
                   {"parentNodeId": node.node_id, "prunedCount": pruned_count,
                    "reason": "retry_recursive_case"})

    @staticmethod
    def _classify_error_source(reason: str) -> str:
        reason_lower = reason.lower()
        if any(kw in reason_lower for kw in _INFRA_KEYWORDS):
            return "app_guardrail"
        if any(kw in reason_lower for kw in _LLM_KEYWORDS):
            return "llm_task_failure"
        return "unknown"

    @staticmethod
    def _event_node_status(status: NodeStatus) -> str:
        return _EVENT_STATUS_MAP.get(status, status.value)

    @staticmethod
    def _forced_base_case_result(*, node: NodeState, max_depth: int,
                                 reason: str = "max_depth_reached") -> DividerServiceResult:
        return DividerServiceResult(
            decision=DividerDecision.BASE_CASE,
            base_case=BaseCaseWorkPlan(
                rationale=f"Forced BASE_CASE at depth {node.depth}/{max_depth}",
                work_plan=[{"step": 1, "description": f"Best-effort solution. Constraint: {reason}."}],
                suggested_persona=node.persona_id),
            attempts_used=0)

    def _emit_depth_limit_reached(self, *, node: NodeState, max_depth: int,
                                   reason: str = "max_depth_reached") -> None:
        self._emit(node.run_id, node.node_id, DomainEventType.NODE_TOKEN, {
            "token": f"Depth guardrail active: forcing base-case at depth={node.depth} "
                     f"(max_depth={max_depth}, reason={reason}).",
            "stream": "stderr",
        })

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

    def _terminal(
        self, *, status: str, node: NodeState, output: Any = None, error: str | None = None,
        checker_result: CheckerResult | None = None, record: bool = True,
    ) -> NodeExecutionResult:
        if record:
            self._record_attempt(node=node, output=output, error=error, checker_result=checker_result)
        return NodeExecutionResult(status=status, node_id=node.node_id, output=output, error=error)

    def _end_node(self, node_id: str, node_status: NodeStatus, reason: str | None = None) -> NodeState:
        ended = self._repository.record_node_ended(node_id, node_status)
        self._emit_node_status(
            node_id=node_id, status=ended.status, reason=reason,
            duration_ms=ended.duration_ms, ttft_ms=ended.ttft_ms,
        )
        return ended

    def _handle_checker_outcome(
        self, *, node: NodeState, work_output: Any, checker_outcome: CheckerOutcome | None,
        checker_result: CheckerResult | None, record_output: Any = None,
    ) -> NodeExecutionResult | None:
        if checker_outcome is None:
            return None
        status = checker_outcome.next_node_status
        error_msg = checker_result.reason if checker_result else "checker failed"

        if status == NodeStatus.BLOCKED_HUMAN:
            self._mark_blocked_human(node.node_id)
            return self._terminal(
                status=ExecutionTerminal.BLOCKED_HUMAN, node=node, output=record_output or work_output,
                error=error_msg, checker_result=checker_result,
            )
        if status == NodeStatus.FAILED_CHECK and node.checker_policy.on_check_fail == "pause":
            self._mark_blocked_human(node.node_id)
            return self._terminal(
                status=ExecutionTerminal.BLOCKED_HUMAN, node=node, output=work_output,
                error=error_msg, checker_result=checker_result,
            )
        return None


__all__ = [
    "BaseCaseWorker",
    "ExecutionTerminal",
    "NodeExecutionResult",
    "RecursiveExecutor",
    "WorkExecutionResult",
]
