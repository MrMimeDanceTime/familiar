import json

from app.chat import card_facts, engine
from app.tools import render


class _Provider:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete_json(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        return self.reply


def _state(provider):
    return engine._TurnState(
        session=None, provider=provider, conversation_id=1, deck_id=None,
        user_text="what removal fits?", system_prompt="BASE", history=[],
        sequence=0, should_stop=None, base_prompt="BASE",
    )


def test_anticipated_cards_are_grounded_before_the_model_writes(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "chat_anticipate_cards", True)
    monkeypatch.setattr(card_facts, "is_available", lambda: True)
    monkeypatch.setattr(card_facts, "resolve", lambda names: (
        {n.lower(): {"name": n, "oracle_text": f"{n} text", "type_line": "Instant"}
         for n in names if n != "Made Up Card"},
        [n for n in names if n == "Made Up Card"],
    ))
    provider = _Provider(json.dumps({"cards": ["Go for the Throat", "Made Up Card"]}))
    state = _state(provider)
    engine._anticipate_cards(state, deck_state_text="Commander: X")
    assert "go for the throat" in state.grounded
    assert "Go for the Throat text" in state.system_prompt
    # An invented guess is dropped, not reported to the model as a card it named.
    assert "made up card" not in state.unknown_names
    assert provider.calls[0]["thinking"] is False
    assert "what removal fits?" in provider.calls[0]["user"]


def test_anticipation_failing_never_blocks_the_turn(monkeypatch):
    monkeypatch.setattr(card_facts, "is_available", lambda: True)
    state = _state(_Provider("not json"))
    engine._anticipate_cards(state, deck_state_text="")
    assert state.system_prompt == "BASE"


def test_suggest_result_grounds_the_alternatives_it_lists():
    content = {"summary": "s", "proposals": [
        {"id": 1, "action": "add", "card_name": "Lightning Bolt", "oracle_text": "Deal 3."},
    ], "alternatives": [
        {"name": "Etali, Primal Storm", "mana_cost": "{4}{R}{R}",
         "type_line": "Legendary Creature", "oracle_text": "Whenever Etali attacks, exile..."},
    ]}
    text = render.render_proposals(content)
    assert "Also considered, not proposed" in text and "Whenever Etali attacks" in text
    assert "etali, primal storm" in render.grounded_card_names(content)


def test_proposed_cards_count_as_grounded():
    content = {"proposals": [{"id": 1, "action": "add", "card_name": "Hurl Through Hell",
                              "oracle_text": "Exile target..."}]}
    assert "hurl through hell" in render.grounded_card_names(content)
