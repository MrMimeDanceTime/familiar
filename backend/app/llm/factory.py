from app.config import settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import ChatProvider
from app.llm.deepseek_provider import DeepSeekProvider


def get_provider(name: str | None = None) -> ChatProvider:
    provider_name = name or settings.llm_provider

    if provider_name == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    if provider_name == "deepseek":
        return DeepSeekProvider(
            api_key=settings.deepseek_api_key, model=settings.deepseek_model
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {provider_name!r}")


def get_fast_model(name: str | None = None) -> str | None:
    """The per-call Flash model override for pipeline stages that want throughput
    over reasoning depth. Only DeepSeek has a distinct fast tier; other providers
    return None (use the provider default)."""
    provider_name = name or settings.llm_provider
    if provider_name == "deepseek":
        return settings.deepseek_model_fast
    return None
