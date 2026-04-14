from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import NodeStatus
from app.schemas.api import CheckerConfig, RunConfig
from app.schemas.contracts import CheckerResult, DividerDecision
from app.services.checker import CheckerRequest, CheckerService
from app.services.divider import (
    BaseCaseWorkPlan,
    DividerServiceResult,
    RecursiveChildSpec,
    RecursiveDecomposition,
)
from app.services.executor import (
    ExecutionTerminal,
    RecursiveExecutor,
    WorkExecutionResult,
)
from app.services.orchestrator import Orchestrator
from app.services.persona_router import PersonaRouteResult
from app.state.memory_repo import InMemoryRunStateRepository


class StubDivider:
    def __init__(self, responses_by_objective: dict[str, DividerServiceResult]) -> None:
        self._responses = responses_by_objective

    def divide(self, objective: str, depth: int = 0, **kwargs) -> DividerServiceResult:
        try:
            return self._responses[objective]
        except KeyError as exc:
            raise AssertionError(
                f"No divider response for objective '{objective}'"
            ) from exc


class StubPersonaRouter:
    def __init__(self, persona_id: str = "python_developer") -> None:
        self._persona_id = persona_id

    def select_persona(
        self,
        objective: str,
        *,
        context: str | None = None,
        explicit_persona_id: str | None = None,
    ) -> PersonaRouteResult:
        return PersonaRouteResult(
            persona_id=explicit_persona_id or self._persona_id,
            confidence=1.0,
            reason="stub route",
        )


@dataclass
class WorkCall:
    node_id: str
    objective: str


class StubWorker:
    def __init__(
        self,
        responses_by_objective: dict[str, WorkExecutionResult] | None = None,
    ) -> None:
        self._responses_by_objective = responses_by_objective or {}
        self.calls: list[WorkCall] = []

    def execute(
        self,
        *,
        run_id: str,
        node_id: str,
        objective: str,
        depth: int,
        persona_id: str | None,
        work_plan: list[dict[str, object]],
        **kwargs,
    ) -> WorkExecutionResult:
        self.calls.append(WorkCall(node_id=node_id, objective=objective))
        if objective in self._responses_by_objective:
            return self._responses_by_objective[objective]
        return WorkExecutionResult.completed(
            {
                "objective": objective,
                "persona_id": persona_id,
                "steps": [step["description"] for step in work_plan],
            }
        )


class FakeCheckerClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[CheckerRequest] = []

    def evaluate(self, request: CheckerRequest) -> object:
        self.calls.append(request)
        if not self._responses:
            raise AssertionError("No fake checker responses remaining")
        return self._responses.pop(0)


def _base_case(
    *, step_label: str, suggested_persona: str | None = None
) -> DividerServiceResult:
    return DividerServiceResult(
        decision=DividerDecision.BASE_CASE,
        base_case=BaseCaseWorkPlan(
            rationale="Base case",
            work_plan=[{"step": 1, "description": step_label}],
            suggested_persona=suggested_persona,
        ),
        attempts_used=1,
    )


def _recursive_case(children: list[RecursiveChildSpec]) -> DividerServiceResult:
    return DividerServiceResult(
        decision=DividerDecision.RECURSIVE_CASE,
        recursive_case=RecursiveDecomposition(
            rationale="Recursive split",
            children=children,
        ),
        attempts_used=1,
    )


def _id_factory() -> str:
    _id_factory.counter += 1
    return f"{_id_factory.counter:04d}"


_id_factory.counter = 0


def _build_orchestrator(
    *,
    divider_map: dict[str, DividerServiceResult],
    worker: StubWorker,
    checker: CheckerService | None = None,
) -> tuple[InMemoryRunStateRepository, Orchestrator]:
    repo = InMemoryRunStateRepository()
    executor = RecursiveExecutor(
        repository=repo,
        divider=StubDivider(divider_map),
        persona_router=StubPersonaRouter(),
        worker=worker,
        checker=checker,
        id_factory=_id_factory,
    )
    orchestrator = Orchestrator(
        repository=repo, executor=executor, id_factory=_id_factory
    )
    return repo, orchestrator


def test_recursive_mixed_base_and_recursive_builds_consistent_graph() -> None:
    _id_factory.counter = 0
    divider_map = {
        "Build release plan": _recursive_case(
            [
                RecursiveChildSpec(
                    objective="Backend execution plan",
                    dependencies=[],
                    suggested_persona=None,
                    interface_contract=None,
                ),
                RecursiveChildSpec(
                    objective="Frontend rollout prep",
                    dependencies=["child_1"],
                    suggested_persona=None,
                    interface_contract=None,
                ),
            ]
        ),
        "Backend execution plan": _base_case(step_label="Design APIs"),
        "Frontend rollout prep": _recursive_case(
            [
                RecursiveChildSpec(
                    objective="UI implementation",
                    dependencies=[],
                    suggested_persona=None,
                    interface_contract=None,
                ),
                RecursiveChildSpec(
                    objective="QA checklist",
                    dependencies=["child_1"],
                    suggested_persona=None,
                    interface_contract=None,
                ),
            ]
        ),
        "UI implementation": _base_case(step_label="Implement graph canvas"),
        "QA checklist": _base_case(step_label="Define regression plan"),
    }
    worker = StubWorker()
    repo, orchestrator = _build_orchestrator(divider_map=divider_map, worker=worker)

    result = orchestrator.start_run(
        objective="Build release plan",
        config=RunConfig(max_depth=6, max_children_per_node=4),
    )

    assert result.status == "completed"
    run_nodes = repo.list_run_nodes(result.run_id)
    assert len(run_nodes) == 5

    node_by_objective = {node.objective: node for node in run_nodes}
    assert node_by_objective["Build release plan"].parent_id is None
    assert node_by_objective["Backend execution plan"].parent_id == result.root_node_id
    assert node_by_objective["Frontend rollout prep"].parent_id == result.root_node_id
    assert (
        node_by_objective["UI implementation"].parent_id
        == node_by_objective["Frontend rollout prep"].node_id
    )
    assert (
        node_by_objective["QA checklist"].parent_id
        == node_by_objective["Frontend rollout prep"].node_id
    )

    for node in run_nodes:
        assert node.status == NodeStatus.COMPLETED
        assert node.persona_id == "python_developer"

    executed_objectives = [call.objective for call in worker.calls]
    assert executed_objectives == [
        "Backend execution plan",
        "UI implementation",
        "QA checklist",
    ]


def test_dependency_completion_blocks_merge_progression_until_ready() -> None:
    _id_factory.counter = 0
    divider_map = {
        "Plan release": _recursive_case(
            [
                RecursiveChildSpec(
                    objective="First child",
                    dependencies=[],
                    suggested_persona=None,
                    interface_contract=None,
                ),
                RecursiveChildSpec(
                    objective="Second child",
                    dependencies=["child_1"],
                    suggested_persona=None,
                    interface_contract=None,
                ),
            ]
        ),
        "First child": _base_case(step_label="Finish first"),
        "Second child": _base_case(step_label="Finish second"),
    }
    worker = StubWorker()
    repo, orchestrator = _build_orchestrator(divider_map=divider_map, worker=worker)

    result = orchestrator.start_run(
        objective="Plan release",
        config=RunConfig(max_depth=4, max_children_per_node=4),
    )

    assert result.status == "completed"
    assert [call.objective for call in worker.calls] == ["First child", "Second child"]

    run_nodes = repo.list_run_nodes(result.run_id)
    second = next(node for node in run_nodes if node.objective == "Second child")
    first = next(node for node in run_nodes if node.objective == "First child")
    parent = next(node for node in run_nodes if node.objective == "Plan release")

    assert first.status == NodeStatus.COMPLETED
    assert second.status == NodeStatus.COMPLETED
    assert parent.status == NodeStatus.COMPLETED

    second_attempts = repo.list_node_attempts(second.node_id)
    assert len(second_attempts) == 1
    assert second_attempts[0].attempt_index == 1


def test_terminal_run_state_transitions_completed_failed_blocked_human() -> None:
    _id_factory.counter = 0
    completed_map = {"Complete objective": _base_case(step_label="Done")}
    repo_completed, orchestrator_completed = _build_orchestrator(
        divider_map=completed_map,
        worker=StubWorker(),
    )
    completed_result = orchestrator_completed.start_run(objective="Complete objective")
    assert completed_result.status == "completed"
    assert repo_completed.get_run(completed_result.run_id).status.value == "completed"

    _id_factory.counter = 1000
    failed_map = {"Fail objective": _base_case(step_label="Will fail")}
    repo_failed, orchestrator_failed = _build_orchestrator(
        divider_map=failed_map,
        worker=StubWorker(
            responses_by_objective={
                "Fail objective": WorkExecutionResult.failed("execution error")
            }
        ),
    )
    failed_result = orchestrator_failed.start_run(objective="Fail objective")
    assert failed_result.status == "failed"
    assert repo_failed.get_run(failed_result.run_id).status.value == "failed"

    _id_factory.counter = 2000
    blocked_map = {"Blocked objective": _base_case(step_label="Needs review")}
    repo_blocked, orchestrator_blocked = _build_orchestrator(
        divider_map=blocked_map,
        worker=StubWorker(
            responses_by_objective={
                "Blocked objective": WorkExecutionResult.blocked_human("needs operator")
            }
        ),
    )
    blocked_result = orchestrator_blocked.start_run(objective="Blocked objective")
    assert blocked_result.status == "blocked_human"
    assert repo_blocked.get_run(blocked_result.run_id).status.value == "blocked_human"

    blocked_nodes = repo_blocked.list_run_nodes(blocked_result.run_id)
    assert blocked_nodes[0].status == NodeStatus.BLOCKED_HUMAN

    assert ExecutionTerminal.COMPLETED == "completed"
    assert ExecutionTerminal.FAILED == "failed"
    assert ExecutionTerminal.BLOCKED_HUMAN == "blocked_human"


def test_checker_failure_is_recorded_as_validation_without_failing_run() -> None:
    _id_factory.counter = 3000
    divider_map = {"Validation objective": _base_case(step_label="Generate proposal")}
    checker = CheckerService(
        checker_client=FakeCheckerClient(
            responses=[
                {
                    "verdict": "fail",
                    "reason": "selector should target a class instead of a bare tag name",
                    "suggested_fix": "change the CSS selector to a real class used by the HTML",
                    "confidence": 0.74,
                    "violations": ["invalid_selector_target"],
                },
                {
                    "verdict": "fail",
                    "reason": "selector should target a class instead of a bare tag name",
                    "suggested_fix": "change the CSS selector to a real class used by the HTML",
                    "confidence": 0.74,
                    "violations": ["invalid_selector_target"],
                },
            ]
        )
    )
    repo, orchestrator = _build_orchestrator(
        divider_map=divider_map,
        worker=StubWorker(),
        checker=checker,
    )

    result = orchestrator.start_run(
        objective="Validation objective",
        config=RunConfig(
            checker=CheckerConfig(
                enabled=True, node_level=True, merge_level=False,
                on_check_fail="auto_retry", max_retries_per_node=1,
            )
        ),
    )

    assert result.status == "completed"
    root_node = repo.list_run_nodes(result.run_id)[0]
    assert root_node.status == NodeStatus.COMPLETED

    final_output = result.output
    assert isinstance(final_output, dict)
    assert final_output.get("validation_warning") is True
