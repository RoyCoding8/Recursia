"""Run lifecycle and human intervention REST endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4
from pathlib import Path
import threading
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.adapters.llm_factory import build_llm_client
from app.adapters.llm_client import LLMClientRuntimeError
from app.api.events import set_event_stream_service
from app.config import ConfigError, load_config_from_env
from app.domain.events import DomainEvent, DomainEventType
from app.domain.enums import InterventionAction, NodeStatus, RunStatus
from app.domain.models import InterventionState
from app.schemas.api import (
    CreateRunRequest,
    CreateRunResponse,
    EdgeView,
    EditAndRetryIntervention,
    GetRunResponse,
    InterventionRequest,
    RunResultResponse,
    InterventionResponse,
    NodeView,
    RetryIntervention,
    RunView,
    SkipWithJustificationIntervention,
    RunValidationResult,
)
from app.services.divider import DividerSchemaError, DividerService
from app.services.event_stream import EventStreamService
from app.services.executor import RecursiveExecutor
from app.services.checker import CheckerService, LLMCheckerClient
from app.services.merger import MergerService
from app.services.orchestrator import Orchestrator
from app.services.persona_registry import PersonaRegistry
from app.services.persona_router import PersonaRouter
from app.services.stubs import DeterministicDivider, DeterministicPersonaRouter
from app.services.worker import LLMBaseCaseWorker
from app.state.memory_repo import InMemoryRunStateRepository
from app.state.repository import RunStateRepository

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _default_id_factory() -> str:
    return uuid4().hex


def _build_event_publisher(
    event_stream: EventStreamService,
) -> Callable[[str, str, DomainEventType, dict[str, object]], None]:
    def _publish(
        run_id: str,
        node_id: str,
        event_type: DomainEventType,
        payload: dict[str, object],
    ) -> None:
        event_stream.publish(
            DomainEvent(
                event_id=f"evt_{_default_id_factory()}",
                run_id=run_id,
                node_id=node_id,
                type=event_type,
                payload=payload,
            )
        )

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
    """Provider-backed runtime orchestrator used by production default wiring."""
    if os.getenv("CONTEXT_MANAGER_FORCE_STUB", "0").strip() in {
        "1",
        "true",
        "TRUE",
        "yes",
        "on",
    }:
        return _build_stub_orchestrator(
            repository=repository,
            event_stream=event_stream,
        )

    config = load_config_from_env()
    llm_client = build_llm_client(config)
    divider = DividerService(
        llm_client=llm_client,
        max_schema_retries=config.llm_max_retries,
        temperature=config.llm_temperature,
    )
    checker = CheckerService(checker_client=LLMCheckerClient(llm_client=llm_client))
    merger = MergerService(
        llm_client=llm_client,
        max_schema_retries=config.llm_max_retries,
        temperature=config.llm_temperature,
    )
    event_emitter = _build_event_publisher(event_stream) if event_stream else None
    personas_dir = Path(__file__).resolve().parents[3] / "personas"
    registry = PersonaRegistry(personas_dir)
    registry.reload()
    worker = LLMBaseCaseWorker(
        llm_client=llm_client,
        persona_registry=registry,
        temperature=config.llm_temperature,
        event_emitter=event_emitter,
    )
    executor = RecursiveExecutor(
        repository=repository,
        divider=divider,
        persona_router=PersonaRouter(registry=registry),
        worker=worker,
        checker=checker,
        merger=merger,
        event_emitter=event_emitter,
    )
    return Orchestrator(
        repository=repository,
        executor=executor,
        event_stream=event_stream,
    )


_default_repository = InMemoryRunStateRepository()
_default_event_stream = EventStreamService(repository=_default_repository)
_default_orchestrator: Orchestrator | None = None
_default_orchestrator_error: Exception | None = None
_service_lock = threading.RLock()
_services_overridden = False
_run_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="run-worker")

try:
    _default_orchestrator = _build_runtime_orchestrator(
        repository=_default_repository,
        event_stream=_default_event_stream,
    )
except Exception as exc:  # pragma: no cover - exercised via dependency access
    _default_orchestrator_error = exc

_repository: RunStateRepository = _default_repository
_orchestrator: Orchestrator | None = _default_orchestrator
_event_stream: EventStreamService = _default_event_stream
set_event_stream_service(_event_stream)


def set_runs_services(
    *,
    repository: RunStateRepository,
    orchestrator: Orchestrator,
    event_stream: EventStreamService,
) -> None:
    """Override services for integration wiring and tests."""
    global _repository, _orchestrator, _event_stream, _services_overridden
    with _service_lock:
        _repository = repository
        _orchestrator = orchestrator
        _event_stream = event_stream
        _services_overridden = True
        set_event_stream_service(event_stream)


def reset_runs_services() -> None:
    """Reset to default runtime service wiring (primarily for tests)."""
    global _repository, _orchestrator, _event_stream, _services_overridden
    with _service_lock:
        _repository = _default_repository
        _orchestrator = _default_orchestrator
        _event_stream = _default_event_stream
        _services_overridden = False
        set_event_stream_service(_default_event_stream)


def get_run_repository() -> RunStateRepository:
    return _repository


_ORCHESTRATOR_ERROR_MSG = (
    "Default orchestrator failed to initialize. Configure a live provider "
    "(LLM_PROVIDER=gemini|groq|bedrock with required credentials) or set "
    "LLM_PROVIDER=stub explicitly for deterministic dev/test fallback."
)


def _refresh_orchestrator(*, force: bool) -> None:
    """Refresh orchestrator if needed. Thread-safe."""
    global _orchestrator, _default_orchestrator_error
    if _services_overridden or (_orchestrator and not force):
        return
    try:
        _orchestrator = _build_runtime_orchestrator(
            repository=_repository,
            event_stream=_event_stream,
        )
        _default_orchestrator_error = None
    except Exception as exc:  # pragma: no cover
        _orchestrator = None
        _default_orchestrator_error = exc


def _load_persona_registry() -> PersonaRegistry:
    personas_dir = Path(__file__).resolve().parents[3] / "personas"
    registry = PersonaRegistry(personas_dir)
    registry.reload()
    return registry


def provider_readiness(*, force_refresh: bool = True) -> tuple[bool, str | None]:
    """Return provider readiness and actionable non-secret reason when unhealthy."""
    with _service_lock:
        _refresh_orchestrator(force=force_refresh)
        if _orchestrator is None:
            reason = _ORCHESTRATOR_ERROR_MSG
            if _default_orchestrator_error:
                reason = f"{reason} Details: {_default_orchestrator_error}"
            return False, reason

        return True, None


def get_orchestrator() -> Orchestrator:
    """Get orchestrator dependency. Raises HTTPException if unavailable."""
    with _service_lock:
        _refresh_orchestrator(force=_orchestrator is None)
        if _orchestrator is None:
            detail = _ORCHESTRATOR_ERROR_MSG
            if isinstance(_default_orchestrator_error, ConfigError):
                detail = f"{detail} Details: {_default_orchestrator_error}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail
            )
        return _orchestrator


def get_event_stream() -> EventStreamService:
    return _event_stream


@router.post("", response_model=CreateRunResponse, status_code=status.HTTP_201_CREATED)
def create_run(
    request: CreateRunRequest,
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> CreateRunResponse:
    """Create run + root node and launch orchestration asynchronously."""
    if request.base_persona_id:
        registry = _load_persona_registry()
        if not registry.has_profile(request.base_persona_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown base persona: {request.base_persona_id}",
            )
    try:
        created = orchestrator.create_run(
            objective=request.objective,
            config=request.config,
            base_persona_id=request.base_persona_id,
        )

        def _run_background() -> None:
            try:
                orchestrator.run_existing(
                    run_id=created.run_id,
                    root_node_id=created.root_node_id,
                )
            except Exception:
                # Orchestrator emits failure state/events.
                return

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
        NodeView(
            node_id=node.node_id,
            run_id=node.run_id,
            parent_id=node.parent_id,
            depth=node.depth,
            objective=node.objective,
            status=NodeStatus(node.status.value),
            node_kind=node.node_kind.value,
            persona_id=node.persona_id,
            ttft_ms=node.ttft_ms,
            duration_ms=node.duration_ms,
            checker_failure_count=node.consecutive_checker_failures,
        )
        for node in sorted(nodes, key=lambda item: (item.depth, item.node_id))
    ]
    typed_edges = [
        EdgeView(source=node.parent_id, target=node.node_id, relation="child")
        for node in nodes
        if node.parent_id is not None
    ]

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


_ELIGIBLE_INTERVENTION_STATUSES = {
    NodeStatus.BLOCKED_HUMAN,
    NodeStatus.FAILED_CHECK,
}


@router.post(
    "/{run_id}/nodes/{node_id}/interventions", response_model=InterventionResponse
)
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

    # Determine intervention action, payload, and target status
    if isinstance(request, RetryIntervention):
        action, note, payload_delta, target_status = (
            InterventionAction.RETRY,
            request.note,
            {},
            NodeStatus.RUNNING,
        )
    elif isinstance(request, EditAndRetryIntervention):
        action, note, target_status = (
            InterventionAction.EDIT_AND_RETRY,
            request.note,
            NodeStatus.RUNNING,
        )
        payload_delta = {
            "edited_objective": request.edited_objective,
            "edited_context": request.edited_context,
        }
        node = repository.update_node_objective(node_id, request.edited_objective)
    else:  # SkipWithJustificationIntervention
        if node.status != NodeStatus.BLOCKED_HUMAN:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="skip_with_justification requires blocked_human node status",
            )
        action, note, target_status = (
            InterventionAction.SKIP_WITH_JUSTIFICATION,
            request.justification,
            NodeStatus.COMPLETED,
        )
        payload_delta = {"justification": request.justification}

    repository.create_intervention(
        InterventionState(
            intervention_id=intervention_id,
            run_id=run_id,
            node_id=node_id,
            action=action,
            actor=resolved_actor,
            note=note,
            payload_delta=payload_delta,
        )
    )

    updated_node = repository.update_node_status(node_id, target_status)
    if run.status == RunStatus.BLOCKED_HUMAN:
        repository.update_run_status(run_id, RunStatus.RUNNING)
        event_stream.publish(
            DomainEvent(
                event_id=f"evt_{_default_id_factory()}",
                run_id=run_id,
                node_id=node_id,
                type=DomainEventType.RUN_STATUS_CHANGED,
                payload={"status": RunStatus.RUNNING.value},
            )
        )

    event_stream.publish(
        DomainEvent(
            event_id=f"evt_{_default_id_factory()}",
            run_id=run_id,
            node_id=node_id,
            type=DomainEventType.NODE_INTERVENTION_APPLIED,
            payload={
                "intervention_id": intervention_id,
                "action": action.value,
                "actor": resolved_actor,
                "note": note,
                "node_status": updated_node.status.value,
                "payload_delta": payload_delta,
            },
        )
    )

    if target_status == NodeStatus.RUNNING:

        def _resume_background() -> None:
            try:
                orchestrator.resume_from_node(run_id=run_id, node_id=node_id)
            except Exception:
                # Orchestrator emits terminal events on failure.
                return

        _run_executor.submit(_resume_background)

    return InterventionResponse(
        accepted=True,
        node_status=NodeStatus(updated_node.status.value),
        intervention_id=intervention_id,
    )


__all__ = [
    "get_event_stream",
    "get_orchestrator",
    "get_run_repository",
    "provider_readiness",
    "reset_runs_services",
    "router",
    "set_runs_services",
]
