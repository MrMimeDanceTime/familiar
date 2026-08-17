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
