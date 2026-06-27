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
