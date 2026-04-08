"""Provider-based LLM client factory."""

from __future__ import annotations

from app.adapters.llm_client import (
    LiteLLMClient,
    LLMClient,
    StubLLMClient,
)
from app.config import AppConfig, ConfigError, load_config_from_env


def build_llm_client(config: AppConfig | None = None) -> LLMClient:
    """Build provider-specific LLM client from runtime configuration."""

    resolved = config or load_config_from_env()
    provider = resolved.llm_provider

    if provider == "stub":
        return StubLLMClient()

    if provider == "gemini":
        api_key = _require(
            resolved.gemini_api_key,
            "GEMINI_API_KEY is required when LLM_PROVIDER=gemini",
        )
        model = _resolve_model(
            explicit=resolved.llm_model,
            provider_model=resolved.gemini_model,
            provider_name="gemini",
            provider_var="GEMINI_MODEL",
        )
        return LiteLLMClient(
            model=f"gemini/{model}",
            api_key=api_key,
            timeout_seconds=resolved.llm_timeout_seconds,
            max_retries=resolved.llm_max_retries,
        )

    if provider == "groq":
        api_key = _require(
            resolved.groq_api_key,
            "GROQ_API_KEY is required when LLM_PROVIDER=groq",
        )
        model = _resolve_model(
            explicit=resolved.llm_model,
            provider_model=resolved.groq_model,
            provider_name="groq",
            provider_var="GROQ_MODEL",
        )
        return LiteLLMClient(
            model=f"groq/{model}",
            api_key=api_key,
            timeout_seconds=resolved.llm_timeout_seconds,
            max_retries=resolved.llm_max_retries,
        )

    if provider == "bedrock":
        region = _require(
            resolved.aws_region,
            "AWS_REGION is required when LLM_PROVIDER=bedrock",
        )
        
        access_key_id = _require(
            resolved.aws_access_key_id,
            "AWS_ACCESS_KEY_ID is required when LLM_PROVIDER=bedrock",
        )
        
        secret_access_key = _require(
            resolved.aws_secret_access_key,
            "AWS_SECRET_ACCESS_KEY is required when LLM_PROVIDER=bedrock",
        )
        
        model_id = _resolve_model(
            explicit=resolved.llm_model,
            provider_model=resolved.bedrock_model_id,
            provider_name="bedrock",
            provider_var="BEDROCK_MODEL_ID",
        )
        return LiteLLMClient(
            model=f"bedrock/{model_id}",
            aws_region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            timeout_seconds=resolved.llm_timeout_seconds,
            max_retries=resolved.llm_max_retries,
        )

    raise ConfigError(
        f"Unsupported LLM_PROVIDER='{provider}'. Expected gemini|groq|bedrock|stub"
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
