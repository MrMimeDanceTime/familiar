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

    def __init__(self, verdicts, cuts=None):
        self.verdicts = verdicts
        self.cuts = cuts or {}
        self.calls = []

    def system_one(self, state, questions):
        self.calls.append({"state": state, "questions": questions})
        answers = {}
        for key, question in questions.items():
            if question["type"] == "choice":
                weights = {label: self.verdicts[facts["name"]][0]
                           for label, facts in question["criteria"].items()}
                total = sum(weights.values()) or 1.0
                probs = {label: w / total for label, w in weights.items()}
                answers[key] = {"type": "choice", "choice": max(probs, key=probs.get),
                                "confidence": 0.5, "probabilities": probs}
                continue
            if key.startswith("cut_"):
                name = question["instructions"]["card"]["name"]
                answers[key] = {"type": "noul", "noul": self.cuts.get(name, 0.1)}
                continue
            name = question["instructions"]["candidate"]["name"]
            fit, asked = self.verdicts[name]
            if key.startswith("add_"):
                answers[key] = {"type": "noul", "noul": fit / 4}
            elif key.startswith("fit_"):
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


def test_informed_mode_shows_the_evidence_to_the_verdict_only():
    card = _card("Piece", rate=0.42, brainmap={"total": 0.7, "explain": "mechanical 0.70"})
    card.fine_roles = {"ramp"}
    questions = jev.build_questions([card], "more ramp", mode="informed")
    verdict = questions["fit_0"]["instructions"]["candidate"]
    assert verdict["role_tags"] == ["ramp"]
    assert verdict["played_in_share_of_decks_with_this_commander"] == "42%"
    assert verdict["brain_map_fit"]["total_0_to_1"] == 0.7
    assert questions["fit_0"]["criteria"] == jev.VERDICT_LEVELS
    for kind in ("ask_0", "dup_0"):
        assert "brain_map_fit" not in questions[kind]["instructions"]["candidate"]
    assert "more ramp" in questions["ask_0"]["instructions"]["statement"]


def test_blind_mode_withholds_the_evidence():
    card = _card("Piece", rate=0.42, brainmap={"total": 0.7})
    questions = jev.build_questions([card], "x", mode="blind")
    assert "brain_map_fit" not in questions["fit_0"]["instructions"]["candidate"]
    assert "dup_0" not in questions


def test_informed_verdict_is_not_blended_with_the_brain_map_again():
    pool = [_card("Low", brainmap={"total": 1.0}), _card("High", brainmap={"total": 0.0})]
    client = FakeJev({"Low": (2.0, 0.5), "High": (2.1, 0.5)})
    selection = jev.select_jev(client, pool, "x", mode="informed")
    assert selection.picks[0].name == "High"
    assert selection.picks[0].reason.startswith("Reasonable add (Jev verdict 2.1/4")


def test_verdict_mode_ranks_on_one_calibrated_probability():
    pool = [_card("Maybe"), _card("Yes"), _card("No")]
    client = FakeJev({"Maybe": (2.0, 0.9), "Yes": (3.6, 0.1), "No": (0.4, 0.9)})
    selection = jev.select_jev(client, pool, "more ramp", mode="verdict")
    assert [p.name for p in selection.picks] == ["Yes", "Maybe", "No"]
    question = client.calls[0]["questions"]["add_0"]["instructions"]
    assert "more ramp" in question["statement"]
    assert selection.picks[0].reason.endswith("Jev 90%.")


def test_choice_mode_averages_the_pool_wide_question_over_orderings():
    pool = [_card("A"), _card("B", legal=False), _card("C"), _card("D")]
    client = FakeJev({"A": (1.0, 0), "C": (3.0, 0), "D": (2.0, 0)})
    selection = jev.select_jev(client, pool, "x", mode="choice", max_picks=2, samples=3)
    assert len(client.calls) == 3
    orders = {tuple(f["name"] for f in call["questions"]["best"]["criteria"].values())
              for call in client.calls}
    assert all(len(order) == 3 for order in orders)
    assert len(orders) > 1
    assert [p.name for p in selection.picks] == ["C", "D"]
    assert selection.picks[1].reason.startswith("Jev ranked it #2 of 3")


def test_choice_mode_refuses_a_pool_it_cannot_ask_about_in_one_question():
    pool = [_card(f"Card {i}") for i in range(300)]
    with pytest.raises(jev.JevError):
        jev.build_choice_question(pool, "x")


def test_client_retries_transient_errors_then_succeeds(monkeypatch):
    import httpx

    monkeypatch.setattr(jev.time, "sleep", lambda s: None)
    replies = iter([
        httpx.Response(503, text="upstream connect error"),
        httpx.Response(429, headers={"retry-after": "0"}),
        httpx.Response(200, json={"answers": {"x": {"type": "noul", "noul": 0.5}}}),
    ])
    client = jev.JevClient("k", retries=3)
    client._client = httpx.Client(
        base_url="https://api.test", transport=httpx.MockTransport(lambda r: next(replies)),
    )
    assert client.system_one("s", {"x": {"type": "noul"}})["answers"]["x"]["noul"] == 0.5


def test_client_gives_up_after_its_retries():
    import httpx

    client = jev.JevClient("k", retries=0)
    client._client = httpx.Client(
        base_url="https://api.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(503, text="down")),
    )
    with pytest.raises(jev.JevError, match="503"):
        client.system_one("s", {"x": {"type": "noul"}})


def test_verdict_samples_are_averaged():
    class Wobbly(FakeJev):
        def __init__(self):
            super().__init__({"A": (2.0, 0.5), "B": (2.0, 0.5)})
            self.flip = False

        def system_one(self, state, questions):
            self.flip = not self.flip
            self.verdicts = {"A": (3.6, 0.5), "B": (2.0, 0.5)} if self.flip else {"A": (0.0, 0.5), "B": (2.4, 0.5)}
            return super().system_one(state, questions)

    client = Wobbly()
    selection = jev.select_jev(client, [_card("A"), _card("B")], "x", mode="verdict", samples=2)
    ranking = {j["name"]: j["score"] for j in selection.raw["judgments"]}
    assert ranking["A"] == pytest.approx(0.45)
    assert ranking["B"] == pytest.approx(0.55)


def test_ensemble_averages_verdict_and_choice_rank_positions():
    pool = [_card("A"), _card("B"), _card("C")]
    client = FakeJev({"A": (3.0, 0.5), "B": (2.0, 0.5), "C": (1.0, 0.5)})
    selection = jev.select_jev(client, pool, "x", mode="ensemble", samples=1)
    assert [p.name for p in selection.picks] == ["A", "B", "C"]
    assert selection.raw["usage"]["requests"] == 2


def _land(name, roles=("fixing",)):
    card = _card(name)
    card.type_line = "Land"
    card.fine_roles = set(roles)
    return card


def test_batch_caps_interchangeable_cards():
    pool = [_land("Fetch A"), _land("Fetch B"), _land("Fetch C"), _card("Rock")]
    client = FakeJev({"Fetch A": (4.0, 1), "Fetch B": (3.9, 1), "Fetch C": (3.8, 1), "Rock": (1.0, 1)})
    selection = jev.select_jev(client, pool, "ramp", mode="verdict", max_similar=2)
    assert [p.name for p in selection.picks] == ["Fetch A", "Fetch B", "Rock"]


def test_roleless_cards_are_never_capped_as_similar():
    pool = [_card("A"), _card("B"), _card("C")]
    client = FakeJev({"A": (3.0, 1), "B": (2.0, 1), "C": (1.0, 1)})
    selection = jev.select_jev(client, pool, "x", mode="verdict", max_similar=1)
    assert len(selection.picks) == 3


def test_verdict_threshold_returns_a_short_batch():
    pool = [_card("Sure"), _card("Maybe"), _card("Nope")]
    client = FakeJev({"Sure": (3.6, 1), "Maybe": (2.4, 1), "Nope": (1.0, 1)})
    selection = jev.select_jev(client, pool, "x", mode="verdict", min_probability=0.5)
    assert [p.name for p in selection.picks] == ["Sure", "Maybe"]


def test_verdict_reason_is_built_from_the_pipelines_facts():
    card = _card("Engine", combo=["Commander X"], brainmap={
        "total": 0.9, "explain": "consensus 0.95, mechanical 1.00 — 54% of decks, synergy +0.47; "
                                 "works with the commander via synergy-swamp",
    })
    card.fine_roles = {"ramp"}
    selection = jev.select_jev(FakeJev({"Engine": (3.6, 1)}), [card], "ramp", mode="verdict")
    assert selection.picks[0].reason == (
        "Ramp; completes a combo with Commander X; 54% of decks, synergy +0.47; "
        "works with the commander via synergy-swamp; Jev 90%."
    )


_DECK = [
    {"name": "Commander", "type_line": "Legendary Creature"},
    {"name": "Weak Rock", "type_line": "Artifact", "oracle_text": "T: Add C."},
    {"name": "Key Engine", "type_line": "Enchantment"},
    {"name": "Swamp", "type_line": "Basic Land - Swamp"},
]


def test_cut_candidates_skip_the_commander_and_lands():
    names = [c["name"] for c in jev.cut_candidates({"commander": "Commander", "cards": _DECK})]
    assert names == ["Weak Rock", "Key Engine"]


def test_cuts_fill_exactly_the_overflow_past_the_deck_limit():
    pool = [_card("A"), _card("B")]
    client = FakeJev({"A": (3.0, 1), "B": (2.0, 1)}, cuts={"Weak Rock": 0.8, "Key Engine": 0.1})
    candidates = jev.cut_candidates({"commander": "Commander", "cards": _DECK})
    selection = jev.select_jev(client, pool, "x", mode="verdict",
                               cut_cards=candidates, deck_total=99)
    assert [c.name for c in selection.cuts] == ["Weak Rock"]
    assert selection.cuts[0].reason.endswith("Jev 80% that cutting it costs the deck little.")
    cut_state = next(call["state"] for call in client.calls if "cut_0" in call["questions"])
    assert "Cards being added in this batch: A, B" in cut_state


def test_no_cuts_while_the_deck_has_room():
    client = FakeJev({"A": (3.0, 1)}, cuts={"Weak Rock": 0.9})
    candidates = jev.cut_candidates({"commander": "Commander", "cards": _DECK})
    selection = jev.select_jev(client, [_card("A")], "x", mode="verdict",
                               cut_cards=candidates, deck_total=60)
    assert selection.cuts == []
    assert not any("cut_0" in call["questions"] for call in client.calls)
