from __future__ import annotations
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.runs import reset_runs_services
from main import app


def _clear_provider_env(monkeypatch) -> None:
    for key in (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "AWS_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "BEDROCK_MODEL_ID",
        "BACKEND_ENV_PRECEDENCE",
        "BACKEND_CORS_ORIGINS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("app.config._default_dotenv_path", lambda: Path("/nonexistent/.env"))


def test_ready_returns_200_for_stub_provider(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    reset_runs_services()

    client = TestClient(app)
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_non_200_with_actionable_reason_when_provider_invalid(
    monkeypatch,
) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    reset_runs_services()

    client = TestClient(app)
    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert "AWS_REGION is required when LLM_PROVIDER=bedrock" in body["reason"]


def test_config_summary_exposes_effective_non_secret_diagnostics(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "http://127.0.0.1:3000")
    monkeypatch.setenv("BACKEND_ENV_PRECEDENCE", "os_wins")
    reset_runs_services()

    client = TestClient(app)
    response = client.get("/system/config-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "stub"
    assert payload["resolved_model_source"] == "unset"
    assert payload["fallback_mode"] is True
    assert payload["env_precedence"] == "os_wins"
    assert payload["cors_origins"] == ["http://127.0.0.1:3000"]
