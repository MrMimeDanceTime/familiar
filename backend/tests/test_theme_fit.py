import pytest

from app.pipeline import theme_fit


def _page(rates, potential=1000):
    """A commander page whose one list carries each card at the given rate."""
    return {"categories": {"topcards": {"header": "Top Cards", "cards": [
        {"name": n, "synergy": 0.0, "num_decks": int(r * potential), "potential_decks": potential}
        for n, r in rates.items()
    ]}}}


class FakeEdhrec:
    def __init__(self, base, themes):
        self.base, self.themes = base, themes

    def commander_themes(self, commander):
        return [{"slug": s, "value": s, "count": 100} for s in self.themes]

    def commander_recs(self, commander, theme=None):
        return _page(self.themes[theme] if theme else self.base)


def _client():
    # "Staple" is everywhere; "Sword" marks voltron, "Crucible" marks lands.
    base = {"Staple": 0.9, "Sword": 0.3, "Crucible": 0.3, "Spare Sword": 0.2}
    return FakeEdhrec(base, {
        "voltron": {"Staple": 0.9, "Sword": 0.8, "Crucible": 0.05, "Spare Sword": 0.7},
        "lands": {"Staple": 0.9, "Sword": 0.05, "Crucible": 0.8, "Spare Sword": 0.02},
    })


def test_deck_is_matched_to_the_theme_of_its_distinctive_cards():
    profile = theme_fit.build_profile("Cmd", ["Staple", "Sword"], _client())
    assert profile.top_theme() == "voltron"
    assert profile.weights["voltron"] > 0.9


def test_theme_rate_reads_the_candidate_through_the_match():
    profile = theme_fit.build_profile("Cmd", ["Staple", "Sword"], _client())
    assert profile.rate("Spare Sword") == pytest.approx(0.7, abs=0.02)
    assert profile.lift("Spare Sword") == pytest.approx(0.5, abs=0.02)


def test_thin_theme_rates_fall_back_to_the_commander_page():
    client = _client()
    client.commander_recs = lambda commander, theme=None: (
        _page(client.themes[theme], potential=5) if theme else _page(client.base)
    )
    profile = theme_fit.build_profile("Cmd", ["Staple"], client)
    assert profile.is_empty()


def test_annotate_adds_theme_numbers_to_the_edhrec_block():
    profile = theme_fit.build_profile("Cmd", ["Staple", "Sword"], _client())
    pool = [{"name": "Spare Sword", "edhrec": {"inclusion_rate": 0.2}}, {"name": "Unknown"}]
    theme_fit.annotate(pool, profile)
    assert pool[0]["edhrec"]["inclusion_rate"] == 0.2
    assert pool[0]["edhrec"]["theme_rate"] == pytest.approx(0.7, abs=0.02)
    assert "edhrec" not in pool[1]


def test_no_commander_or_a_failing_client_gives_no_signal():
    assert theme_fit.build_profile(None, ["Sword"], _client()).is_empty()

    class Broken:
        def commander_recs(self, *a, **k):
            raise RuntimeError("down")

    assert theme_fit.build_profile("Cmd", ["Sword"], Broken()).is_empty()
