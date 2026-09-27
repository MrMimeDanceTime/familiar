"""Format staples a deck should have regardless of what it is doing.

Reported from a real build: Sol Ring took five rounds to surface. It never lost
on merit — it scored rank 11 in a pool where stage 4 takes ten picks, so it sat
just outside the cut every batch. A card in ~90% of decks should not be
competing for slots in a themed batch.

The distinction that makes this safe is EDHREC's own, measured on a Myrkul pool:

    Sol Ring            78% play, synergy -0.01   <- staple
    Arcane Signet       66% play, synergy  0.00   <- staple
    Eidolon of Blossoms 69% play, synergy +0.59   <- commander-specific
    Sanctum Weaver      67% play, synergy +0.57   <- commander-specific

High play rate alone would pull the last two out of the suggestion pipeline,
hollowing batches of their best picks. Synergy is what separates them.
"""

from app.autoincludes import find_missing


class FakeEdhrec:
    def __init__(self, cards, exc=None):
        self._cards = cards
        self._exc = exc

    def commander_recs(self, commander_name):
        if self._exc:
            raise self._exc
        return {
            "commander": commander_name,
            "categories": {
                "topcards": {
                    "header": "Top Cards",
                    "cards": [
                        {
                            "name": name,
                            "synergy": synergy,
                            "num_decks": int(rate * 1000),
                            "potential_decks": 1000,
                        }
                        for name, rate, synergy in self._cards
                    ],
                }
            },
        }


STAPLES_AND_SYNERGY = [
    ("Sol Ring", 0.78, -0.01),
    ("Arcane Signet", 0.66, 0.00),
    ("Eidolon of Blossoms", 0.69, 0.59),
    ("Sanctum Weaver", 0.67, 0.57),
    ("Fringe Card", 0.20, 0.00),
]


def test_finds_staples_the_deck_is_missing():
    found = find_missing("Myrkul", set(), edhrec=FakeEdhrec(STAPLES_AND_SYNERGY))
    assert [c["name"] for c in found] == ["Sol Ring", "Arcane Signet"]


def test_high_synergy_cards_are_not_auto_includes():
    """Eidolon of Blossoms is in 69% of Myrkul decks BECAUSE of the commander.
    Pulling it out of the pipeline would strip batches of their best picks."""
    found = {c["name"] for c in find_missing(
        "Myrkul", set(), edhrec=FakeEdhrec(STAPLES_AND_SYNERGY)
    )}
    assert "Eidolon of Blossoms" not in found
    assert "Sanctum Weaver" not in found


def test_cards_already_in_the_deck_are_skipped():
    found = find_missing(
        "Myrkul", {"Sol Ring"}, edhrec=FakeEdhrec(STAPLES_AND_SYNERGY)
    )
    assert [c["name"] for c in found] == ["Arcane Signet"]


def test_owned_check_is_case_insensitive():
    found = find_missing(
        "Myrkul", {"sol ring"}, edhrec=FakeEdhrec(STAPLES_AND_SYNERGY)
    )
    assert "Sol Ring" not in {c["name"] for c in found}


def test_low_play_rate_is_not_a_staple():
    found = {c["name"] for c in find_missing(
        "Myrkul", set(), edhrec=FakeEdhrec(STAPLES_AND_SYNERGY)
    )}
    assert "Fringe Card" not in found


def test_basic_lands_are_excluded():
    """They clear both bars trivially and belong to the manabase, not to a card
    recommendation."""
    cards = [("Forest", 0.97, 0.00), ("Swamp", 0.96, 0.01), ("Sol Ring", 0.78, -0.01)]
    found = {c["name"] for c in find_missing("Myrkul", set(), edhrec=FakeEdhrec(cards))}
    assert found == {"Sol Ring"}


def test_ordered_most_played_first():
    found = find_missing("Myrkul", set(), edhrec=FakeEdhrec(STAPLES_AND_SYNERGY))
    rates = [c["play_rate"] for c in found]
    assert rates == sorted(rates, reverse=True)


def test_no_commander_yields_nothing():
    assert find_missing(None, set(), edhrec=FakeEdhrec(STAPLES_AND_SYNERGY)) == []


def test_edhrec_failure_degrades_quietly():
    """A missing staple list is a lost convenience, never a reason for a deck
    read to fail."""
    broken = FakeEdhrec([], exc=RuntimeError("EDHREC down"))
    assert find_missing("Myrkul", set(), edhrec=broken) == []


def test_result_explains_itself():
    found = find_missing("Myrkul", set(), edhrec=FakeEdhrec(STAPLES_AND_SYNERGY))
    assert "78% of Myrkul decks" in found[0]["why"]


def test_deck_read_exposes_missing_staples(tmp_path):
    from sqlmodel import Session, SQLModel, create_engine

    from app.db import repository as repo
    from app.tools.deck_tools import deck_get_current

    engine = create_engine(f"sqlite:///{tmp_path / 'auto.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        deck = repo.create_deck(session, name="T", commander="Myrkul")
        snapshot = deck_get_current(session, deck.id)

    # The key is present even when EDHREC is unreachable in a test environment.
    assert "missing_auto_includes" in snapshot["plan"]


# ── Folding staples into a role batch ────────────────────────────────────


def _fold(monkeypatch, picks, request_roles, restrictions=None):
    from app import autoincludes
    from app.cards import store
    from app.pipeline.selection import Pick, Selection
    from app.pipeline.shaping import DeckContext

    cards = {
        "sol ring": {"name": "Sol Ring", "oracle_id": "sol", "type_line": "Artifact", "color_identity": []},
        "command tower": {"name": "Command Tower", "oracle_id": "tow", "type_line": "Land", "color_identity": []},
        "swords to plowshares": {"name": "Swords to Plowshares", "oracle_id": "stp",
                                 "type_line": "Instant", "color_identity": ["W"]},
    }
    monkeypatch.setattr(store, "by_names", lambda names: {n.lower(): cards[n.lower()] for n in names})
    monkeypatch.setattr(store, "tags_for_many", lambda ids: {
        "sol": {"mana-rock"}, "tow": {"land"}, "stp": {"removal"},
    })
    missing = [{"name": "Sol Ring", "why": "in 90% of X decks"}, {"name": "Command Tower"},
               {"name": "Swords to Plowshares"}]
    ctx = DeckContext(identity=frozenset("WB"), card_names_lower=frozenset(), restrictions=restrictions or {})
    selection = Selection(picks=[Pick(name=n) for n in picks], summary="s")
    return autoincludes.fold_into(selection, missing, request_roles, ctx, "X")


def test_a_ramp_batch_leads_with_the_missing_ramp_staple_and_keeps_its_size(monkeypatch):
    out = _fold(monkeypatch, ["Rakdos Signet", "Talisman of Indulgence", "Fellwar Stone"], {"ramp"})
    assert [p.name for p in out.picks] == ["Sol Ring", "Rakdos Signet", "Talisman of Indulgence"]
    assert "Format staple" in out.picks[0].reason


def test_staples_outside_the_requested_role_stay_out(monkeypatch):
    out = _fold(monkeypatch, ["Mortify", "Anguished Unmaking"], {"card-draw"})
    assert [p.name for p in out.picks] == ["Mortify", "Anguished Unmaking"]


def test_a_staple_the_player_excluded_is_not_folded_in(monkeypatch):
    out = _fold(monkeypatch, ["Rakdos Signet"], {"ramp"}, restrictions={"exclude_cards": ["Sol Ring"]})
    assert [p.name for p in out.picks] == ["Rakdos Signet"]
