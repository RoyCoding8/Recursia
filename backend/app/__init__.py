"""Application package and FastAPI app factory entrypoint."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import ConfigError, build_config_summary, load_config_from_env

DEFAULT_CORS_ORIGINS = ("http://127.0.0.1:3000", "http://localhost:3000")


def _resolve_cors_origins() -> list[str]:
    """Resolve CORS origins from env with secure local defaults."""
    raw = os.getenv("BACKEND_CORS_ORIGINS", "").strip()
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    origins = [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]
    return origins or list(DEFAULT_CORS_ORIGINS)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    from app.api.events import router as events_router
    from app.api.personas import router as personas_router
    from app.api.runs import provider_readiness, router as runs_router

    application = FastAPI(
        title="Recursia API",
        version="0.1.0",
        description="Recursive orchestration backend for Recursia.",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=_resolve_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers
    application.include_router(runs_router)
    application.include_router(personas_router)
    application.include_router(events_router)

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/ready", tags=["system"])
    async def ready() -> JSONResponse:
        ready_ok, reason = provider_readiness(force_refresh=True)
        return JSONResponse(
            status_code=200 if ready_ok else 503,
            content={"status": "ready"}
            if ready_ok
            else {
                "status": "not_ready",
                "reason": reason or "provider initialization failed",
            },
        )

    @application.get("/system/config-summary", tags=["system"])
    async def config_summary() -> JSONResponse:
        try:
            config = load_config_from_env()
            summary = build_config_summary(config)
            summary["cors_origins"] = _resolve_cors_origins()
            return JSONResponse(status_code=200, content=summary)
        except ConfigError as exc:
            return JSONResponse(
                status_code=503,
                content={"status": "invalid_config", "reason": str(exc)},
            )

    return application


app = create_app()

__all__ = ["app", "create_app"]
