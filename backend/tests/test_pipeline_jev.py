import pytest

from app.pipeline import jev
from app.pipeline import service
from app.pipeline.shaping import ShapedCard


def _card(name, *, legal=True, combo=(), brainmap=None, rate=None):
    return ShapedCard(
        name=name, oracle_id=name.lower(), mana_cost="{1}{B}", cmc=2.0,
        type_line="Instant", oracle_text=f"{name} does a thing.",
        color_identity=["B"], keywords=[], power=None, toughness=None,
        loyalty=None, rarity="common", edhrec_rank=100,
        edhrec={"inclusion_rate": rate} if rate is not None else None,
        brainmap=brainmap, completes_combo_with=list(combo),
        legal_in_deck=legal,
    )


class FakeJev:
    """Answers from a name -> (fit, asked) table, keyed back through the
    question's candidate facts so chunk-local indices are exercised."""

    model = "jev-test"

    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.calls = []

    def system_one(self, state, questions):
        self.calls.append({"state": state, "questions": questions})
        answers = {}
        for key, question in questions.items():
            name = question["instructions"]["candidate"]["name"]
            fit, asked = self.verdicts[name]
            if key.startswith("fit_"):
                answers[key] = {"type": "score", "score": fit, "confidence": 0.9,
                                "probabilities": {}}
            else:
                answers[key] = {"type": "noul", "noul": asked}
        return {"model": self.model, "answers": answers, "usage": {}}


def test_ranks_by_fit_gated_by_the_ask():
    pool = [_card("Filler"), _card("Engine"), _card("Offtopic Bomb")]
    client = FakeJev({
        "Filler": (1.0, 0.9),
        "Engine": (3.6, 0.95),
        "Offtopic Bomb": (4.0, 0.05),
    })
    selection = jev.select_jev(client, pool, "more removal", max_picks=2)
    assert [p.name for p in selection.picks] == ["Engine", "Offtopic Bomb"]
    assert selection.raw["backend"] == "jev"


def test_illegal_cards_are_never_asked_about_or_picked():
    pool = [_card("Legal"), _card("Banned Thing", legal=False)]
    client = FakeJev({"Legal": (2.0, 0.5)})
    selection = jev.select_jev(client, pool, "anything")
    assert [p.name for p in selection.picks] == ["Legal"]
    names = {q["instructions"]["candidate"]["name"] for q in client.calls[0]["questions"].values()}
    assert names == {"Legal"}


def test_reason_is_built_from_facts_not_generated():
    pool = [_card("Piece", combo=["Commander X"], rate=0.42)]
    selection = jev.select_jev(FakeJev({"Piece": (3.8, 0.9)}), pool, "combo")
    reason = selection.picks[0].reason
    assert reason.startswith("Core piece (Jev fit 3.8/4")
    assert "completes a combo with Commander X" in reason
    assert "42% of this commander's decks" in reason


def test_state_carries_deck_context_and_the_players_words():
    client = FakeJev({"A": (2.0, 0.5)})
    jev.select_jev(client, [_card("A")], "ramp", deck_context="Commander: Korvold",
                   player_message="cheap,   nothing green")
    state = client.calls[0]["state"]
    assert "Commander: Korvold" in state
    assert "ramp" in state
    assert "cheap, nothing green" in state


def test_brain_map_breaks_ties_between_equal_judgments():
    pool = [_card("Low", brainmap={"total": 0.1}), _card("High", brainmap={"total": 0.9})]
    selection = jev.select_jev(FakeJev({"Low": (2.0, 0.5), "High": (2.0, 0.5)}), pool, "x")
    assert selection.picks[0].name == "High"


def test_large_pool_is_chunked_and_reassembled(monkeypatch):
    monkeypatch.setattr(jev, "_MAX_REQUEST_CHARS", 3000)
    pool = [_card(f"Card {i}") for i in range(12)]
    client = FakeJev({f"Card {i}": (i / 3, 0.5) for i in range(12)})
    selection = jev.select_jev(client, pool, "x", max_picks=3)
    assert len(client.calls) > 1
    assert [p.name for p in selection.picks] == ["Card 11", "Card 10", "Card 9"]


def test_missing_answer_raises_so_the_caller_can_fall_back():
    class Broken(FakeJev):
        def system_one(self, state, questions):
            return {"answers": {}}

    with pytest.raises(jev.JevError):
        jev.select_jev(Broken({}), [_card("A")], "x")


def test_client_refuses_to_start_without_a_key():
    with pytest.raises(jev.JevError):
        jev.JevClient("")


def test_run_selection_falls_back_to_the_llm_when_jev_fails():
    class FakeProvider:
        def complete_json(self, system, user, **kwargs):
            return '{"summary": "llm", "picks": [{"name": "A", "reason": "fits"}]}'

    class Exploding:
        def system_one(self, state, questions):
            raise jev.JevError("down")

    prepared = service.PreparedPool(
        snapshot={}, spec=None, gathered=None, local_pool=[], stage1_skipped=True,
        pool=[], shaped=[_card("A")], deck_context="", timings={},
    )
    selection, used = service.run_selection(
        prepared, "x", FakeProvider(), backend="jev", model="m", jev_client=Exploding(),
    )
    assert used == "llm"
    assert selection.summary == "llm"
