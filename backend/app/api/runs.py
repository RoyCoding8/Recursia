"""Run lifecycle and human intervention REST endpoints."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.adapters.llm_client import LLMClientRuntimeError
from app.domain.enums import InterventionAction, NodeStatus, RunStatus
from app.domain.events import DomainEvent, DomainEventType
from app.domain.models import InterventionState, NodeState
from app.schemas.api import (
    CreateRunRequest,
    CreateRunResponse,
    EdgeView,
    EditAndRetryIntervention,
    GetRunResponse,
    InterventionRequest,
    InterventionResponse,
    NodeView,
    RetryIntervention,
    RunResultResponse,
    RunValidationResult,
    RunView,
)
from app.services.divider import DividerSchemaError
from app.services.event_stream import EventStreamService
from app.services.executor import RecursiveExecutor
from app.services.orchestrator import Orchestrator
from app.services.persona_registry import PersonaRegistry
from app.services.stubs import DeterministicDivider, DeterministicPersonaRouter
from app.state.repository import RunStateRepository

router = APIRouter(prefix="/api/runs", tags=["runs"])

_log = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# ---------------------------------------------------------------------------
# Single shared state dict — populated by lifespan or set_runs_services
# ---------------------------------------------------------------------------

_active: dict[str, Any] = {}
_run_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="run-worker")
_active_runs: set[str] = set()
_active_runs_lock = threading.Lock()


def set_orchestrator_error(exc: Exception | None) -> None:
    """Store/clear orchestrator init error (called by lifespan)."""
    if exc is None:
        _active.pop("orchestrator_error", None)
    else:
        _active["orchestrator_error"] = exc


def _ensure_services() -> None:
    """Auto-initialize services if nothing is set yet (e.g. standalone test client)."""
    if "repository" in _active:
        return
    from app.__init__ import _build_default_repository
    try:
        repository = _build_default_repository()
        event_stream = EventStreamService(repository=repository)
        orchestrator = _build_runtime_orchestrator(repository=repository, event_stream=event_stream)
        set_runs_services(repository=repository, orchestrator=orchestrator, event_stream=event_stream)
    except Exception as exc:
        _active["orchestrator_error"] = str(exc)


def set_runs_services(
    *,
    repository: RunStateRepository,
    orchestrator: Orchestrator,
    event_stream: EventStreamService,
) -> None:
    """Override services for integration wiring and tests."""
    from app.api.events import set_event_stream_service
    _active["repository"] = repository
    _active["orchestrator"] = orchestrator
    _active["event_stream"] = event_stream
    # Clear any error from failed startup
    _active.pop("orchestrator_error", None)
    set_event_stream_service(event_stream)


def reset_runs_services() -> None:
    """Clear service overrides (primarily for tests)."""
    from app.api.events import set_event_stream_service
    _active.pop("repository", None)
    _active.pop("orchestrator", None)
    _active.pop("event_stream", None)
    _active.pop("orchestrator_error", None)
    set_event_stream_service(None)


# ---------------------------------------------------------------------------
# Dependency functions
# ---------------------------------------------------------------------------

def get_run_repository() -> RunStateRepository:
    repo = _active.get("repository")
    if repo is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Repository not initialized")
    return repo


def get_orchestrator() -> Orchestrator:
    orch = _active.get("orchestrator")
    if orch is None:
        error = _active.get("orchestrator_error")
        detail = "Orchestrator not initialized"
        if error:
            detail = f"{detail}: {error}"
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
    return orch


def get_event_stream() -> EventStreamService:
    stream = _active.get("event_stream")
    if stream is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Event stream not initialized")
    return stream


def provider_readiness(*, force_refresh: bool = True) -> tuple[bool, str | None]:
    """Return provider readiness and actionable non-secret reason when unhealthy."""
    _ensure_services()
    if _active.get("orchestrator") is not None:
        return True, None
    error = _active.get("orchestrator_error")
    reason = (
        "Default orchestrator failed to initialize. Configure a live provider "
        "(LLM_PROVIDER=gemini|groq|bedrock with required credentials) or set "
        "LLM_PROVIDER=stub explicitly for deterministic dev/test fallback."
    )
    if error:
        reason = f"{reason} Details: {error}"
    return False, reason


def get_run_executor() -> ThreadPoolExecutor:
    return _run_executor


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in _TRUTHY


def _default_id_factory() -> str:
    return uuid4().hex


def _build_event_publisher(event_stream: EventStreamService) -> Callable:
    def _publish(run_id, node_id, event_type, payload):
        event_stream.publish(DomainEvent(
            event_id=f"evt_{_default_id_factory()}", run_id=run_id,
            node_id=node_id, type=event_type, payload=payload))
    return _publish


def _build_stub_orchestrator(
    *,
    repository: RunStateRepository,
    event_stream: EventStreamService | None = None,
) -> Orchestrator:
    """Stub orchestrator for dev/test fallback."""
    event_emitter = _build_event_publisher(event_stream) if event_stream else None
    executor = RecursiveExecutor(
        repository=repository,
        divider=DeterministicDivider(),
        persona_router=DeterministicPersonaRouter(),
        event_emitter=event_emitter,
    )
    return Orchestrator(
        repository=repository,
        executor=executor,
        event_stream=event_stream,
    )


def _build_runtime_orchestrator(
    *,
    repository: RunStateRepository,
    event_stream: EventStreamService | None = None,
) -> Orchestrator:
    """Provider-backed runtime orchestrator. Used by tests and internal wiring."""
    from app.adapters.llm_factory import build_llm_client
    from app.config import load_config_from_env
    from app.services.checker import CheckerService, LLMCheckerClient
    from app.services.divider import DividerService
    from app.services.execution_checker import build_execution_checker
    from app.services.merger import MergerService
    from app.services.persona_registry import PersonaRegistry
    from app.services.persona_router import PersonaRouter
    from app.services.worker import LLMBaseCaseWorker

    config = load_config_from_env()
    llm_client = build_llm_client(config)
    sandbox_enabled = _bool_env("SANDBOX_ENABLED", "true")

    checker_client = build_execution_checker(llm_client) if sandbox_enabled else LLMCheckerClient(llm_client=llm_client)
    checker = CheckerService(checker_client=checker_client)
    divider = DividerService(llm_client=llm_client, max_schema_retries=config.llm_max_retries, temperature=config.llm_temperature)
    merger = MergerService(llm_client=llm_client, max_schema_retries=config.llm_max_retries, temperature=config.llm_temperature)

    event_emitter = _build_event_publisher(event_stream) if event_stream else None
    personas_dir = Path(__file__).resolve().parents[3] / "personas"
    registry = PersonaRegistry(personas_dir)
    registry.reload()

    worker = LLMBaseCaseWorker(llm_client=llm_client, persona_registry=registry, temperature=config.llm_temperature, event_emitter=event_emitter)
    executor = RecursiveExecutor(repository=repository, divider=divider, persona_router=PersonaRouter(registry=registry), worker=worker, checker=checker, merger=merger, event_emitter=event_emitter)
    return Orchestrator(repository=repository, executor=executor, event_stream=event_stream)


def _load_persona_registry() -> PersonaRegistry:
    # Try shared registry first
    registry = _active.get("registry")
    if registry is not None:
        return registry
    personas_dir = Path(__file__).resolve().parents[3] / "personas"
    registry = PersonaRegistry(personas_dir)
    registry.reload()
    return registry


# ---------------------------------------------------------------------------
# Endpoint handlers
# ---------------------------------------------------------------------------

@router.post("", response_model=CreateRunResponse, status_code=status.HTTP_201_CREATED)
def create_run(
    request: CreateRunRequest,
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> CreateRunResponse:
    """Create run + root node and launch orchestration asynchronously."""
    _MAX_OBJECTIVE_LEN = 10000
    objective = request.objective.strip()
    if not objective:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="objective must not be empty")
    if len(objective) > _MAX_OBJECTIVE_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"objective exceeds maximum length ({len(objective)}/{_MAX_OBJECTIVE_LEN})",
        )
    if request.base_persona_id:
        registry = _load_persona_registry()
        if not registry.has_profile(request.base_persona_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown base persona: {request.base_persona_id}",
            )
    try:
        created = orchestrator.create_run(
            objective=objective,
            config=request.config,
            base_persona_id=request.base_persona_id,
        )

        with _active_runs_lock:
            if created.run_id in _active_runs:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"run {created.run_id} is already executing",
                )
            _active_runs.add(created.run_id)

        def _run_background() -> None:
            try:
                orchestrator.run_existing(
                    run_id=created.run_id,
                    root_node_id=created.root_node_id,
                )
            except Exception:
                _log.warning("run_existing failed for run=%s", created.run_id, exc_info=True)
                repo = _active.get("repository")
                stream = _active.get("event_stream")
                if repo:
                    try:
                        repo.update_run_status(created.run_id, RunStatus.FAILED)
                    except Exception:
                        pass
                if stream:
                    try:
                        stream.publish(DomainEvent(
                            event_id=f"evt_{_default_id_factory()}", run_id=created.run_id,
                            node_id=created.root_node_id, type=DomainEventType.RUN_FAILED,
                            payload={"status": RunStatus.FAILED.value, "error": "background_execution_failed"},
                        ))
                    except Exception:
                        pass
            finally:
                with _active_runs_lock:
                    _active_runs.discard(created.run_id)

        _run_executor.submit(_run_background)
    except (LLMClientRuntimeError, DividerSchemaError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"run execution failed: {exc}",
        ) from exc
    return CreateRunResponse(
        run_id=created.run_id,
        status=RunStatus.QUEUED,
        root_node_id=created.root_node_id,
    )


@router.get("/{run_id}", response_model=GetRunResponse)
def get_run_graph(
    run_id: str,
    repository: RunStateRepository = Depends(get_run_repository),
) -> GetRunResponse:
    """Return run + node graph payload for mission-control visualization."""
    try:
        run = repository.get_run(run_id)
        nodes = repository.list_run_nodes(run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"run not found: {run_id}",
        ) from exc

    typed_nodes = [
        NodeView(node_id=n.node_id, run_id=n.run_id, parent_id=n.parent_id,
                 depth=n.depth, objective=n.objective, status=NodeStatus(n.status.value),
                 node_kind=n.node_kind.value, persona_id=n.persona_id,
                 ttft_ms=n.ttft_ms, duration_ms=n.duration_ms,
                 checker_failure_count=n.consecutive_checker_failures)
        for n in sorted(nodes, key=lambda n: (n.depth, n.node_id))
    ]
    typed_edges = [EdgeView(source=n.parent_id, target=n.node_id, relation="child")
                   for n in nodes if n.parent_id is not None]

    return GetRunResponse(
        run=RunView(
            run_id=run.run_id,
            objective=run.objective,
            status=RunStatus(run.status.value),
            root_node_id=next(
                (node.node_id for node in nodes if node.parent_id is None),
                "",
            ),
            created_at=run.created_at.isoformat() if run.created_at else None,
            updated_at=run.updated_at.isoformat() if run.updated_at else None,
        ),
        nodes=typed_nodes,
        edges=typed_edges,
    )


@router.get("/{run_id}/result", response_model=RunResultResponse)
def get_run_result(
    run_id: str,
    repository: RunStateRepository = Depends(get_run_repository),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> RunResultResponse:
    """Return the final output/result for a completed run."""
    try:
        run = repository.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"run not found: {run_id}",
        ) from exc

    output = orchestrator.get_root_output(run_id)
    root_node = next(
        (node for node in repository.list_run_nodes(run_id) if node.parent_id is None),
        None,
    )

    terminal_reason: str | None = None
    if run.status == RunStatus.FAILED:
        events = repository.list_run_events(run_id)
        for evt in reversed(events):
            if evt.type == DomainEventType.RUN_FAILED:
                terminal_reason = evt.payload.get("error")
                break

    validation_result: RunValidationResult | None = None
    if root_node is not None:
        attempts = repository.list_node_attempts(root_node.node_id)
        if attempts:
            latest_attempt = attempts[-1]
            if latest_attempt.checker_result is not None:
                validation_result = RunValidationResult(
                    verdict=latest_attempt.checker_result.verdict.value,
                    reason=latest_attempt.checker_result.reason,
                    suggested_fix=latest_attempt.checker_result.suggested_fix,
                    confidence=latest_attempt.checker_result.confidence,
                    violations=list(latest_attempt.checker_result.violations),
                )

    return RunResultResponse(
        run_id=run.run_id,
        status=RunStatus(run.status.value),
        output=output,
        error=terminal_reason,
        validation=validation_result,
    )


@router.delete("/{run_id}/nodes/{node_id}")
def delete_node(
    run_id: str,
    node_id: str,
    repository: RunStateRepository = Depends(get_run_repository),
) -> dict[str, object]:
    """Delete a node and all its descendants from the run graph."""
    try:
        node = repository.get_node(node_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"node not found: {node_id}",
        ) from exc

    if node.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"node not found in run: {node_id}",
        )

    if node.parent_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete root node",
        )

    count = repository.delete_children_of(run_id, node_id)
    repository.delete_node(run_id, node_id)
    return {"deleted": node_id, "childrenRemoved": count}


_ELIGIBLE_INTERVENTION_STATUSES = {
    NodeStatus.BLOCKED_HUMAN,
    NodeStatus.FAILED_CHECK,
}


def _resolve_intervention(
    *,
    request: InterventionRequest,
    node: NodeState,
    repository: RunStateRepository,
    node_id: str,
) -> tuple[InterventionAction, str, dict[str, object], NodeStatus]:
    if isinstance(request, RetryIntervention):
        return InterventionAction.RETRY, request.note, {}, NodeStatus.RUNNING

    if isinstance(request, EditAndRetryIntervention):
        repository.update_node_objective(node_id, request.edited_objective)
        return (
            InterventionAction.EDIT_AND_RETRY,
            request.note,
            {"edited_objective": request.edited_objective, "edited_context": request.edited_context},
            NodeStatus.RUNNING,
        )

    if node.status != NodeStatus.BLOCKED_HUMAN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="skip_with_justification requires blocked_human node status",
        )
    return (
        InterventionAction.SKIP_WITH_JUSTIFICATION,
        request.justification,
        {"justification": request.justification},
        NodeStatus.COMPLETED,
    )


@router.post("/{run_id}/nodes/{node_id}/interventions", response_model=InterventionResponse)
def apply_intervention(
    run_id: str,
    node_id: str,
    request: InterventionRequest,
    actor: Annotated[str | None, Header(alias="X-Actor")] = None,
    repository: RunStateRepository = Depends(get_run_repository),
    orchestrator: Orchestrator = Depends(get_orchestrator),
    event_stream: EventStreamService = Depends(get_event_stream),
) -> InterventionResponse:
    """Apply human intervention to blocked/eligible nodes with audit+event hooks."""
    try:
        run = repository.get_run(run_id)
        node = repository.get_node(node_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"run/node not found: {run_id}/{node_id}",
        ) from exc

    if node.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"node not found in run: {node_id}",
        )

    if node.status not in _ELIGIBLE_INTERVENTION_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "interventions allowed only for blocked/eligible nodes "
                f"(current_status={node.status.value})"
            ),
        )

    resolved_actor = actor or "system:api"
    intervention_id = f"int_{_default_id_factory()}"

    action, note, payload_delta, target_status = _resolve_intervention(
        request=request, node=node, repository=repository, node_id=node_id,
    )

    def _evt(rtype, payload):
        return DomainEvent(event_id=f"evt_{_default_id_factory()}", run_id=run_id,
                           node_id=node_id, type=rtype, payload=payload)

    repository.create_intervention(InterventionState(
        intervention_id=intervention_id, run_id=run_id, node_id=node_id,
        action=action, actor=resolved_actor, note=note, payload_delta=payload_delta))

    updated_node = repository.update_node_status(node_id, target_status)
    if run.status == RunStatus.BLOCKED_HUMAN:
        repository.update_run_status(run_id, RunStatus.RUNNING)
        event_stream.publish(_evt(DomainEventType.RUN_STATUS_CHANGED, {"status": RunStatus.RUNNING.value}))

    event_stream.publish(_evt(DomainEventType.NODE_INTERVENTION_APPLIED, {
        "intervention_id": intervention_id, "action": action.value, "actor": resolved_actor,
        "note": note, "node_status": updated_node.status.value, "payload_delta": payload_delta,
    }))

    if target_status == NodeStatus.RUNNING:
        with _active_runs_lock:
            if run_id in _active_runs:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"run {run_id} is already executing",
                )
            _active_runs.add(run_id)

        def _resume_background() -> None:
            try:
                orchestrator.resume_from_node(run_id=run_id, node_id=node_id)
            except Exception:
                _log.warning("resume_from_node failed for run=%s node=%s", run_id, node_id, exc_info=True)
            finally:
                with _active_runs_lock:
                    _active_runs.discard(run_id)

        _run_executor.submit(_resume_background)

    return InterventionResponse(
        accepted=True,
        node_status=NodeStatus(updated_node.status.value),
        intervention_id=intervention_id,
    )


__all__ = [
    "_active",
    "get_event_stream",
    "get_orchestrator",
    "get_run_executor",
    "get_run_repository",
    "provider_readiness",
    "reset_runs_services",
    "router",
    "set_runs_services",
]
