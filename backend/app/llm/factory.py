from app.config import settings
from app.llm.base import ChatProvider
from app.llm.deepseek_provider import DeepSeekProvider


def get_provider(name: str | None = None) -> ChatProvider:
    """The configured chat provider.

    DeepSeek is the only backend. The Anthropic provider was removed in
    September 2026 (see PROVIDERS.md); the ``ChatProvider`` protocol and the
    ``LLM_PROVIDER`` setting stay so a second backend can be added behind the
    same seam without touching the engine.
    """
    provider_name = name or settings.llm_provider

    if provider_name == "deepseek":
        return DeepSeekProvider(
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
            timeout=settings.llm_timeout_seconds,
            max_tokens=settings.chat_max_tokens,
            reasoning_effort=settings.chat_reasoning_effort,
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {provider_name!r}")


def get_fast_model(name: str | None = None) -> str | None:
    """The per-call Flash model override for pipeline stages that want throughput
    over reasoning depth. Only DeepSeek has a distinct fast tier; an unknown
    provider returns None (use the provider default)."""
    provider_name = name or settings.llm_provider
    if provider_name == "deepseek":
        return settings.deepseek_model_fast
    return None
