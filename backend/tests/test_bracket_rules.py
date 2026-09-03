"""Bracket estimation against the current official rules.

Two things drifted from the rules as they stand after the October 2025 update:

1. **Tutors no longer set a bracket.** Tutor limits were removed from every
   bracket. Only tutors that are themselves on the Game Changers list (Demonic
   Tutor, Vampiric Tutor, and so on) move a deck, and they move it because they
   are Game Changers, not because they are tutors. The estimator promoted any
   deck with 3+ tutors to Bracket 3 and any deck with a single tutor to Bracket
   2, which penalised thematic or low-power tutors the rules explicitly stopped
   counting.

2. **The Bracket 3 ceiling is stated in Game Changers.** Bracket 3 allows up to
   3; 4 or more is Bracket 4. The scorer already did this correctly, but the
   seeded knowledge described Bracket 3 as "up to 3 game changer cards (or 3+
   tutors)", so the model quoted the tutor clause back at the player.

Bracket definitions used here: 1 and 2 allow zero Game Changers, 3 allows up to
3, 4 and 5 are unlimited (4 additionally gated by mass land denial and fast
combo profiles).
"""

import pytest

from app.tools.deck_stats import _estimate_bracket

# Real cards, so the name matching in _estimate_bracket does real work.
GAME_CHANGERS = [
    "Rhystic Study",
    "Smothering Tithe",
    "Cyclonic Rift",
    "Thassa's Oracle",
    "Demonic Tutor",
    "Vampiric Tutor",
]
# Cards the estimator tracks as tutors that are NOT Game Changers. These are the
# ones the October 2025 update stopped counting: unremarkable, often slow tutors
# that used to drag a casual deck up two brackets on their own.
PLAIN_TUTORS = ["Diabolic Tutor", "Expedition Map", "Fabricate", "Merchant Scroll"]


def _bracket(cards, avg_mv=3.2, land_count=30, ramp_count=4, interaction_count=3):
    """Score a deck, padding to a realistic size with filler basics.

    Defaults sit deliberately BELOW the Bracket 2 "solid precon fundamentals"
    rule (35+ lands, 8+ ramp, low curve). That rule is legitimate and unrelated
    to tutors, so leaving it satisfied would mask whether a tutor was the thing
    doing the promoting.
    """
    names = set(cards)
    filler = {f"Filler Card {i}" for i in range(max(0, 99 - len(names)))}
    bracket, factors = _estimate_bracket(
        names | filler,
        avg_mv,
        land_count,
        ramp_count,
        interaction_count,
        {},
        99,
    )
    return bracket, factors


# --- the Game Changer ladder ------------------------------------------------


@pytest.mark.parametrize("count,expected", [(0, 1), (1, 3), (2, 3), (3, 3), (4, 4)])
def test_game_changer_count_sets_the_bracket(count, expected):
    """0 → B1/B2, 1-3 → B3, 4+ → B4. This is the load-bearing rule."""
    bracket, factors = _bracket(GAME_CHANGERS[:count])
    assert bracket == expected, f"{count} GCs → B{bracket}, expected B{expected}: {factors[:2]}"


def test_three_game_changers_is_still_bracket_three():
    """The exact boundary the player hit: 3 is the Bracket 3 ceiling."""
    bracket, _ = _bracket(GAME_CHANGERS[:3])
    assert bracket == 3


def test_four_game_changers_crosses_into_bracket_four():
    bracket, factors = _bracket(GAME_CHANGERS[:4])
    assert bracket == 4
    assert any("Game Changer" in f for f in factors)


# --- tutors no longer move a deck -------------------------------------------


def test_plain_tutors_do_not_promote_out_of_bracket_one():
    """Tutor limits were removed in October 2025.

    A deck whose only "spicy" cards are ordinary tutors is still an exhibition
    deck. Previously 3+ tutors forced Bracket 3 and a single tutor forced
    Bracket 2.
    """
    bracket, factors = _bracket(PLAIN_TUTORS)
    assert bracket == 1, f"plain tutors promoted to B{bracket}: {factors[:2]}"


def test_a_single_plain_tutor_is_not_bracket_two():
    bracket, _ = _bracket(PLAIN_TUTORS[:1])
    assert bracket == 1


def test_tutors_on_the_game_changers_list_still_count():
    """Demonic Tutor moves a deck because it is a Game Changer, not a tutor."""
    bracket, factors = _bracket(["Demonic Tutor"])
    assert bracket == 3
    assert any("Game Changer" in f for f in factors)


def test_bracket_reason_never_uses_a_tutor_count_as_the_cause():
    """The explanation the player reads must not quote a retired rule.

    Saying "no tutors" while describing an exhibition deck is fine — that is
    descriptive. Saying "3 tutors" as the REASON for Bracket 3 is the retired
    rule leaking into the UI.
    """
    for cards in ([], PLAIN_TUTORS, GAME_CHANGERS[:2], GAME_CHANGERS[:4]):
        _, factors = _bracket(cards)
        reason = next((f for f in factors if f.lower().startswith("bracket ")), "")
        assert "tutor(s)," not in reason.lower(), f"bracket reason cites tutors: {reason}"
        assert "tutors, mv" not in reason.lower(), f"bracket reason cites tutors: {reason}"


# --- the upper brackets are unaffected --------------------------------------


def test_mass_land_denial_still_forces_bracket_four():
    bracket, factors = _bracket(["Armageddon"])
    assert bracket >= 4
    assert any("MLD" in f or "land" in f.lower() for f in factors)


def test_cedh_profile_still_reaches_bracket_five():
    bracket, _ = _bracket(GAME_CHANGERS, avg_mv=1.6, ramp_count=14, interaction_count=12)
    assert bracket == 5


def test_game_changers_come_from_the_card_index_when_it_has_them(monkeypatch):
    """Scryfall flags Game Changers per card and the index stores the flag; the
    hand-typed list is only the cold-start fallback."""
    from unittest.mock import patch

    from app.tools import card_lists

    monkeypatch.setattr(card_lists, "_gc_cache", None)
    with patch("app.cards.store.game_changer_names", return_value=["Brand New Bomb"]):
        names = card_lists.game_changer_names()
    assert names == frozenset({"Brand New Bomb"})

    monkeypatch.setattr(card_lists, "_gc_cache", None)
    with patch("app.cards.store.game_changer_names", return_value=[]):
        fallback = card_lists.game_changer_names()
    assert "Rhystic Study" in fallback
