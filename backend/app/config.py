"""Runtime configuration loader for provider-backed LLM wiring."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


class ConfigError(ValueError):
    """Raised when required configuration is invalid or missing."""


@dataclass(slots=True, frozen=True)
class AppConfig:
    """Application runtime configuration sourced from environment variables."""

    llm_provider: str
    llm_model: str | None
    llm_temperature: float
    llm_timeout_seconds: int
    llm_max_retries: int
    gemini_api_key: str | None
    gemini_model: str | None
    groq_api_key: str | None
    groq_model: str | None
    aws_region: str | None
    aws_access_key_id: str | None
    aws_secret_access_key: str | None
    bedrock_model_id: str | None
    backend_env_precedence: str


def _default_dotenv_path() -> Path:
    """Resolve backend/.env path from this module location."""
    return Path(__file__).resolve().parents[1] / ".env"


def _load_dotenv_into_environ(dotenv_path: Path | None = None) -> bool:
    """Load KEY=VALUE pairs from .env into os.environ using configured precedence."""
    path = dotenv_path or _default_dotenv_path()
    if not path.exists() or not path.is_file():
        return False

    dotenv_values = _read_dotenv_values(path)
    precedence = _resolve_env_precedence(dotenv_values)

    if precedence == "os_wins":
        for key, value in dotenv_values.items():
            os.environ.setdefault(key, value)
    else:
        for key, value in dotenv_values.items():
            os.environ[key] = value

    return True


def _read_dotenv_values(path: Path) -> dict[str, str]:
    """Parse .env file into key-value dict."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _resolve_env_precedence(dotenv_values: dict[str, str]) -> str:
    """Determine merge precedence for dotenv vs OS environment."""
    mode = (
        (
            os.getenv("BACKEND_ENV_PRECEDENCE")
            or dotenv_values.get("BACKEND_ENV_PRECEDENCE", "os_wins")
        )
        .strip()
        .lower()
    )
    if mode not in {"os_wins", "dotenv_wins"}:
        raise ConfigError("BACKEND_ENV_PRECEDENCE must be one of: os_wins, dotenv_wins")
    return mode


def _resolve_effective_environment(dotenv_path: Path | None = None) -> dict[str, str]:
    """Merge dotenv and OS environment with configured precedence."""
    path = dotenv_path or _default_dotenv_path()
    dotenv_values = (
        _read_dotenv_values(path) if path.exists() and path.is_file() else {}
    )
    precedence = _resolve_env_precedence(dotenv_values)
    resolved = (
        {**dotenv_values, **dict(os.environ)}
        if precedence == "os_wins"
        else {**dict(os.environ), **dotenv_values}
    )
    resolved["BACKEND_ENV_PRECEDENCE"] = precedence
    return resolved


def load_config_from_env() -> AppConfig:
    """Load typed runtime configuration from environment variables."""
    env = _resolve_effective_environment()

    provider = _env(env, "LLM_PROVIDER", default="gemini").strip().lower()
    if provider not in {"gemini", "groq", "bedrock", "stub"}:
        raise ConfigError("LLM_PROVIDER must be one of: gemini, groq, bedrock, stub")

    llm_temperature = _parse_float(env, "LLM_TEMPERATURE", default=0.0)
    llm_timeout_seconds = _parse_int(env, "LLM_TIMEOUT_SECONDS", default=60)
    llm_max_retries = _parse_int(env, "LLM_MAX_RETRIES", default=2)

    if llm_timeout_seconds <= 0:
        raise ConfigError("LLM_TIMEOUT_SECONDS must be > 0")
    if llm_max_retries < 0:
        raise ConfigError("LLM_MAX_RETRIES must be >= 0")

    return AppConfig(
        llm_provider=provider,
        llm_model=_maybe(_env(env, "LLM_MODEL")),
        llm_temperature=llm_temperature,
        llm_timeout_seconds=llm_timeout_seconds,
        llm_max_retries=llm_max_retries,
        gemini_api_key=_maybe(_env(env, "GEMINI_API_KEY")),
        gemini_model=_maybe(_env(env, "GEMINI_MODEL")),
        groq_api_key=_maybe(_env(env, "GROQ_API_KEY")),
        groq_model=_maybe(_env(env, "GROQ_MODEL")),
        aws_region=_maybe(_env(env, "AWS_REGION")),
        aws_access_key_id=_maybe(_env(env, "AWS_ACCESS_KEY_ID")),
        aws_secret_access_key=_maybe(_env(env, "AWS_SECRET_ACCESS_KEY")),
        bedrock_model_id=_maybe(_env(env, "BEDROCK_MODEL_ID")),
        backend_env_precedence=_env(env, "BACKEND_ENV_PRECEDENCE", default="os_wins"),
    )


def model_source_for_config(config: AppConfig) -> str:
    """Return non-secret source label for effective model resolution."""
    if config.llm_model:
        return "LLM_MODEL"
    if config.llm_provider == "gemini" and config.gemini_model:
        return "GEMINI_MODEL"
    if config.llm_provider == "groq" and config.groq_model:
        return "GROQ_MODEL"
    if config.llm_provider == "bedrock" and config.bedrock_model_id:
        return "BEDROCK_MODEL_ID"
    return "unset"


def _resolved_model_for_config(config: AppConfig) -> str | None:
    if config.llm_model:
        return config.llm_model
    if config.llm_provider == "gemini":
        return config.gemini_model
    if config.llm_provider == "groq":
        return config.groq_model
    if config.llm_provider == "bedrock":
        return config.bedrock_model_id
    return None


def build_config_summary(config: AppConfig | None = None) -> dict[str, object]:
    """Build a non-secret effective runtime config summary."""
    resolved = config or load_config_from_env()
    return {
        "provider": resolved.llm_provider,
        "resolved_model": _resolved_model_for_config(resolved),
        "resolved_model_source": model_source_for_config(resolved),
        "fallback_mode": resolved.llm_provider == "stub",
        "env_precedence": resolved.backend_env_precedence,
    }


def _env(source: dict[str, str], name: str, *, default: str = "") -> str:
    return source.get(name, default)


def _maybe(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _parse_int(source: dict[str, str], name: str, *, default: int) -> int:
    raw = _env(source, name, default=str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _parse_float(source: dict[str, str], name: str, *, default: float) -> float:
    raw = _env(source, name, default=str(default)).strip()
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a float") from exc


__all__ = [
    "AppConfig",
    "ConfigError",
    "build_config_summary",
    "load_config_from_env",
    "model_source_for_config",
    "_load_dotenv_into_environ",
]
