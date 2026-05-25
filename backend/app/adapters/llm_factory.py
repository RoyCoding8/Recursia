"""Provider-based LLM client factory."""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.llm_client import (
    LiteLLMClient,
    LLMClient,
    StubLLMClient,
)
from app.config import AppConfig, ConfigError, load_config_from_env


@dataclass(frozen=True, slots=True)
class _ProviderConfig:
    """Extracts credentials + model for a single LLM provider."""
    api_key_attr: str
    model_attr: str
    env_var: str
    model_prefix: str
    error_msg: str


_SIMPLE_PROVIDERS: dict[str, _ProviderConfig] = {
    "gemini": _ProviderConfig(
        api_key_attr="gemini_api_key", model_attr="gemini_model",
        env_var="GEMINI_MODEL", model_prefix="gemini",
        error_msg="GEMINI_API_KEY is required when LLM_PROVIDER=gemini",
    ),
    "groq": _ProviderConfig(
        api_key_attr="groq_api_key", model_attr="groq_model",
        env_var="GROQ_MODEL", model_prefix="groq",
        error_msg="GROQ_API_KEY is required when LLM_PROVIDER=groq",
    ),
}


def build_llm_client(config: AppConfig | None = None) -> LLMClient:
    """Build provider-specific LLM client from runtime configuration."""
    resolved = config or load_config_from_env()
    provider = resolved.llm_provider

    if provider == "stub":
        return StubLLMClient()

    if provider in _SIMPLE_PROVIDERS:
        return _build_simple_client(resolved, _SIMPLE_PROVIDERS[provider])

    if provider == "bedrock":
        return _build_bedrock_client(resolved)

    raise ConfigError(
        f"Unsupported LLM_PROVIDER='{provider}'. Expected gemini|groq|bedrock|stub"
    )


def _build_simple_client(config: AppConfig, pc: _ProviderConfig) -> LiteLLMClient:
    """Build LiteLLMClient for gemini/groq (api_key + model only)."""
    api_key = _require(getattr(config, pc.api_key_attr), pc.error_msg)
    model = _resolve_model(
        explicit=config.llm_model,
        provider_model=getattr(config, pc.model_attr),
        provider_name=pc.model_prefix,
        provider_var=pc.env_var,
    )
    return LiteLLMClient(
        model=f"{pc.model_prefix}/{model}",
        api_key=api_key,
        timeout_seconds=config.llm_timeout_seconds,
        max_retries=config.llm_max_retries,
    )


def _build_bedrock_client(config: AppConfig) -> LiteLLMClient:
    """Build LiteLLMClient for AWS Bedrock (region + keys + model)."""
    region = _require(config.aws_region, "AWS_REGION is required when LLM_PROVIDER=bedrock")
    access_key_id = _require(config.aws_access_key_id, "AWS_ACCESS_KEY_ID is required when LLM_PROVIDER=bedrock")
    secret_access_key = _require(config.aws_secret_access_key, "AWS_SECRET_ACCESS_KEY is required when LLM_PROVIDER=bedrock")
    model_id = _resolve_model(
        explicit=config.llm_model,
        provider_model=config.bedrock_model_id,
        provider_name="bedrock",
        provider_var="BEDROCK_MODEL_ID",
    )
    return LiteLLMClient(
        model=f"bedrock/{model_id}",
        aws_region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        timeout_seconds=config.llm_timeout_seconds,
        max_retries=config.llm_max_retries,
    )


def _resolve_model(
    *,
    explicit: str | None,
    provider_model: str | None,
    provider_name: str,
    provider_var: str,
) -> str:
    if explicit:
        return explicit
    if provider_model:
        return provider_model
    raise ConfigError(
        f"LLM_MODEL or {provider_var} is required when LLM_PROVIDER={provider_name}"
    )


def _require(value: str | None, message: str) -> str:
    if not value:
        raise ConfigError(message)
    return value


__all__ = ["build_llm_client"]
