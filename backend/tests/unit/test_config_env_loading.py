from __future__ import annotations

from pathlib import Path

import app.config as config_module
from app.config import _load_dotenv_into_environ, load_config_from_env


def test_dotenv_values_loaded_when_env_absent(tmp_path: Path, monkeypatch) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n".join(
            [
                "# provider config",
                "",
                "LLM_PROVIDER=groq",
                "GROQ_API_KEY=test-groq-key",
                "GROQ_MODEL=llama-3.1-8b-instant",
            ]
        ),
        encoding="utf-8",
    )

    for key in ("LLM_PROVIDER", "GROQ_API_KEY", "GROQ_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config_module, "_default_dotenv_path", lambda: dotenv_path)

    loaded = _load_dotenv_into_environ(dotenv_path)
    config = load_config_from_env()

    assert loaded is True
    assert config.llm_provider == "groq"
    assert config.groq_api_key == "test-groq-key"
    assert config.groq_model == "llama-3.1-8b-instant"


def test_existing_os_environ_value_overrides_dotenv(
    tmp_path: Path, monkeypatch
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("LLM_PROVIDER=gemini\n", encoding="utf-8")

    monkeypatch.setenv("BACKEND_ENV_PRECEDENCE", "os_wins")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setattr(config_module, "_default_dotenv_path", lambda: dotenv_path)
    loaded = _load_dotenv_into_environ(dotenv_path)
    config = load_config_from_env()

    assert loaded is True
    assert config.llm_provider == "groq"


def test_dotenv_can_override_os_env_when_precedence_is_dotenv_wins(
    tmp_path: Path, monkeypatch
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n".join(
            [
                "BACKEND_ENV_PRECEDENCE=dotenv_wins",
                "LLM_PROVIDER=stub",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setattr(config_module, "_default_dotenv_path", lambda: dotenv_path)

    loaded = _load_dotenv_into_environ(dotenv_path)
    config = load_config_from_env()

    assert loaded is True
    assert config.llm_provider == "stub"
    assert config.backend_env_precedence == "dotenv_wins"


def test_missing_dotenv_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    missing_path = tmp_path / ".env"
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(config_module, "_default_dotenv_path", lambda: missing_path)

    loaded = _load_dotenv_into_environ(missing_path)
    config = load_config_from_env()

    assert loaded is False
    assert config.llm_provider == "gemini"
