from app.config import Settings
from app.llm import factory


def test_pro_is_the_default_deepseek_model():
    # Locks the "default to Pro everywhere" decision — a regression back to Flash
    # for the chat loop should fail loudly.
    s = Settings(_env_file=None)
    assert s.deepseek_model == "deepseek-v4-pro"
    assert s.deepseek_model_fast == "deepseek-v4-flash"


def test_get_fast_model_returns_flash_for_deepseek():
    assert factory.get_fast_model("deepseek") == "deepseek-v4-flash"


def test_get_fast_model_is_none_for_providers_without_a_fast_tier():
    assert factory.get_fast_model("anthropic") is None
