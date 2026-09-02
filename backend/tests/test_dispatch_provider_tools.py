"""Provider-injected tools must bind their arguments by name.

`dispatch` used to inject the provider positionally, as
`fn(session, provider, **arguments)`. That matched `_suggest_cards`, whose
signature is `(session, provider, deck_id, ...)`, but not `deck_get_stats`,
which is `(session, deck_id, provider=None)`. The provider bound to `deck_id`,
then `deck_id` arrived again as a keyword:

    TypeError: deck_get_stats() got multiple values for argument 'deck_id'

The broad handler in dispatch turned that into an `ok=False` tool result, so the
model saw "the stats tool is erroring on its end" and the chat loop carried on
without stats. Nothing surfaced in the API, because `/api/decks/{id}/stats`
calls `compute_deck_stats` directly and never goes through dispatch.
"""

from unittest.mock import MagicMock


from app.tools.dispatch import PROVIDER_SESSION_TOOLS, dispatch


def test_deck_get_stats_dispatches_without_argument_collision(monkeypatch):
    captured = {}

    def fake_deck_get_stats(session, deck_id, provider=None):
        captured["deck_id"] = deck_id
        captured["provider"] = provider
        return {"power_level": 7, "bracket": 3}

    monkeypatch.setitem(PROVIDER_SESSION_TOOLS, "deck_get_stats", fake_deck_get_stats)

    provider = MagicMock(name="provider")
    result = dispatch("deck_get_stats", {"deck_id": 42}, MagicMock(), provider=provider)

    assert result.ok, f"stats tool failed: {result.content}"
    assert result.content["power_level"] == 7
    # The real defect: these two were swapped.
    assert captured["deck_id"] == 42
    assert captured["provider"] is provider


def test_suggest_cards_still_receives_its_provider(monkeypatch):
    """The other provider tool takes provider second. Both must work."""
    captured = {}

    def fake_suggest(session, provider, deck_id, intent, conversation_id=None, message_id=None):
        captured["provider"] = provider
        captured["deck_id"] = deck_id
        captured["intent"] = intent
        return {"ok": True, "proposals": []}

    monkeypatch.setitem(PROVIDER_SESSION_TOOLS, "suggest_cards", fake_suggest)

    provider = MagicMock(name="provider")
    result = dispatch(
        "suggest_cards",
        {"deck_id": 7, "intent": "more removal"},
        MagicMock(),
        provider=provider,
    )

    assert result.ok, f"suggest_cards failed: {result.content}"
    assert captured["provider"] is provider
    assert captured["deck_id"] == 7
    assert captured["intent"] == "more removal"


def test_every_provider_tool_accepts_a_provider_keyword():
    """Guards the contract rather than the two known cases.

    Any tool registered in PROVIDER_SESSION_TOOLS is called with provider as a
    keyword, so it must actually have a parameter by that name. A future tool
    that names it something else fails here instead of at runtime, inside a
    swallowed exception the user only sees as the model complaining.
    """
    import inspect

    for name, fn in PROVIDER_SESSION_TOOLS.items():
        params = inspect.signature(fn).parameters
        assert "provider" in params, f"{name} has no 'provider' parameter"
        assert "session" in params, f"{name} has no 'session' parameter"


def test_missing_provider_is_reported_clearly():
    result = dispatch("deck_get_stats", {"deck_id": 1}, MagicMock(), provider=None)
    assert not result.ok
    assert "provider" in result.content.lower()
