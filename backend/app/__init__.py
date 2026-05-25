"""Application package and FastAPI app factory entrypoint."""

import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import ConfigError, build_config_summary, load_config_from_env

DEFAULT_CORS_ORIGINS = ("http://127.0.0.1:3000", "http://localhost:3000")
_log = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _resolve_cors_origins() -> list[str]:
    raw = os.getenv("BACKEND_CORS_ORIGINS", "").strip()
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    origins = [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]
    return origins or list(DEFAULT_CORS_ORIGINS)


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in _TRUTHY


def _build_default_repository():
    from app.state.memory_repo import InMemoryRunStateRepository
    from app.state.sqlite_repo import SQLiteRunStateRepository
    url = os.getenv("DATABASE_URL", "sqlite:///recursia.db")
    if url.startswith("sqlite"):
        path = url.replace("sqlite:///", "").replace("sqlite:", "")
        if not path:
            path = "recursia.db"
        return SQLiteRunStateRepository(path)
    return InMemoryRunStateRepository()


def _build_services():
    from app.adapters.llm_factory import build_llm_client
    from app.domain.events import DomainEvent
    from app.services.checker import CheckerService, LLMCheckerClient
    from app.services.divider import DividerService
    from app.services.event_stream import EventStreamService
    from app.services.execution_checker import build_execution_checker
    from app.services.executor import RecursiveExecutor
    from app.services.merger import MergerService
    from app.services.orchestrator import Orchestrator
    from app.services.persona_registry import PersonaRegistry
    from app.services.persona_router import PersonaRouter
    from app.services.worker import LLMBaseCaseWorker

    repository = _build_default_repository()
    event_stream = EventStreamService(repository=repository)

    def _emit(run_id, node_id, event_type, payload):
        event_stream.publish(DomainEvent(
            event_id=f"evt_{uuid4().hex}",
            run_id=run_id, node_id=node_id, type=event_type, payload=payload,
        ))

    config = load_config_from_env()
    raw_llm = build_llm_client(config)
    from app.adapters.llm_client import UsageTrackingClient
    llm_client = UsageTrackingClient(raw_llm)
    sandbox_enabled = _bool_env("SANDBOX_ENABLED", "true")

    checker_client = build_execution_checker(llm_client) if sandbox_enabled else LLMCheckerClient(llm_client=llm_client)
    checker = CheckerService(checker_client=checker_client)
    divider = DividerService(llm_client=llm_client, max_schema_retries=config.llm_max_retries, temperature=config.llm_temperature)
    merger = MergerService(llm_client=llm_client, max_schema_retries=config.llm_max_retries, temperature=config.llm_temperature)

    personas_dir = Path(__file__).resolve().parents[2] / "personas"
    registry = PersonaRegistry(personas_dir)
    registry.reload()

    cancellation = threading.Event()

    worker = LLMBaseCaseWorker(llm_client=llm_client, persona_registry=registry, temperature=config.llm_temperature, event_emitter=_emit)
    executor = RecursiveExecutor(repository=repository, divider=divider, persona_router=PersonaRouter(registry=registry), worker=worker, checker=checker, merger=merger, event_emitter=_emit, cancellation=cancellation, usage_tracker=llm_client)
    orchestrator = Orchestrator(repository=repository, executor=executor, event_stream=event_stream)

    return repository, event_stream, orchestrator, registry, cancellation


def _recover_interrupted_runs(repository, event_stream):
    from app.domain.enums import RunStatus
    from app.domain.events import DomainEvent, DomainEventType

    try:
        running_runs = repository.list_runs_by_status(RunStatus.RUNNING)
    except Exception:
        return

    for run in running_runs:
        try:
            repository.update_run_status(run.run_id, RunStatus.FAILED)
            event_stream.publish(DomainEvent(
                event_id=f"evt_recovery_{run.run_id[:8]}",
                run_id=run.run_id, node_id=None,
                type=DomainEventType.RUN_FAILED,
                payload={"reason": "process_restart_recovery", "error": "Server restarted while run was in progress"},
            ))
            _log.warning("Recovered interrupted run %s (marked FAILED)", run.run_id)
        except Exception:
            _log.warning("Failed to recover run %s", run.run_id, exc_info=True)


def create_app() -> FastAPI:
    from app.api.events import router as events_router
    from app.api.personas import router as personas_router
    from app.api.runs import (
        router as runs_router,
    )
    from app.api.runs import set_runs_services

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        from app.api.runs import _active, _run_executor

        cancellation = None
        if "repository" not in _active:
            try:
                repository, event_stream, orchestrator, registry, cancellation = _build_services()
                set_runs_services(
                    repository=repository,
                    orchestrator=orchestrator,
                    event_stream=event_stream,
                )
                _recover_interrupted_runs(repository, event_stream)
            except Exception as exc:
                _log.warning("Failed to build runtime services, using stub fallback", exc_info=True)
                _active["orchestrator_error"] = str(exc)

        if cancellation is None:
            cancellation = threading.Event()
        app.state.cancellation = cancellation

        yield

        cancellation.set()
        _run_executor.shutdown(wait=True, cancel_futures=False)

    application = FastAPI(
        title="Recursia API",
        version="0.2.0",
        description="Recursive orchestration backend for Recursia.",
        lifespan=_lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=_resolve_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(runs_router)
    application.include_router(personas_router)
    application.include_router(events_router)

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/ready", tags=["system"])
    async def ready() -> JSONResponse:
        from app.api.runs import provider_readiness
        ready_ok, reason = provider_readiness(force_refresh=True)
        return JSONResponse(
            status_code=200 if ready_ok else 503,
            content={"status": "ready"} if ready_ok else {"status": "not_ready", "reason": reason or "provider init failed"},
        )

    @application.get("/system/config-summary", tags=["system"])
    async def config_summary() -> JSONResponse:
        try:
            config = load_config_from_env()
            summary = build_config_summary(config)
            summary["cors_origins"] = _resolve_cors_origins()
            return JSONResponse(status_code=200, content=summary)
        except ConfigError as exc:
            return JSONResponse(status_code=503, content={"status": "invalid_config", "reason": str(exc)})

    return application


app = create_app()

__all__ = ["app", "create_app"]
