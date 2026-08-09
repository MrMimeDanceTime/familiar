"""EDHREC recommendations must carry real card text.

The model kept misremembering what cards DO, then theorising from the wrong
text. The cause is an asymmetry between the two suggestion paths:

  * ``suggest_cards`` (the retrieval pipeline) shapes candidates WITH their
    oracle text, so the model reasons from the real card.
  * ``edhrec_commander_recs`` returned bare names plus synergy numbers. Asked
    "what should I add for this commander", the model got a list of names and
    filled in the rules text from training data.

Names alone are an invitation to guess. These tests pin that every recommended
card arrives with the fields needed to reason about it.
"""

from unittest.mock import MagicMock

import pytest

from app.tools import dispatch as dispatch_mod
from app.tools.dispatch import dispatch

RECS = {
    "commander": "Atraxa, Praetors' Voice",
    "categories": {
        "highsynergycards": {
            "header": "High Synergy",
            "cards": [
                {"name": "Deepglow Skate", "synergy": 0.42, "inclusion": 0.31},
                {"name": "Doubling Season", "synergy": 0.38, "inclusion": 0.44},
            ],
        },
        "topcards": {
            "header": "Top Cards",
            "cards": [{"name": "Sol Ring", "synergy": 0.01, "inclusion": 0.92}],
        },
    },
}

ORACLE = {
    "Deepglow Skate": {
        "name": "Deepglow Skate",
        "oracle_text": "When Deepglow Skate enters, double the number of each "
        "kind of counter on any number of target permanents.",
        "type_line": "Creature — Fish",
        "mana_cost": "{4}{U}",
        "cmc": 5.0,
        "color_identity": ["U"],
    },
    "Doubling Season": {
        "name": "Doubling Season",
        "oracle_text": "If an effect would create one or more tokens under your "
        "control, it creates twice that many instead. If an effect would put "
        "one or more counters on a permanent you control, it puts twice that "
        "many of those counters on that permanent instead.",
        "type_line": "Enchantment",
        "mana_cost": "{4}{G}",
        "cmc": 5.0,
        "color_identity": ["G"],
    },
    "Sol Ring": {
        "name": "Sol Ring",
        "oracle_text": "{T}: Add {C}{C}.",
        "type_line": "Artifact",
        "mana_cost": "{1}",
        "cmc": 1.0,
        "color_identity": [],
    },
}


@pytest.fixture(autouse=True)
def _clear_memo():
    """Enrichment memoises by name-set; tests must not inherit each other's."""
    from app.tools.dispatch import _ENRICHED_RECS

    _ENRICHED_RECS.clear()
    yield
    _ENRICHED_RECS.clear()


@pytest.fixture
def grounded(monkeypatch):
    """Stub EDHREC + Scryfall so the enrichment path runs without network."""
    edhrec = MagicMock()
    edhrec.commander_recs.return_value = RECS
    monkeypatch.setattr("app.tools.dispatch.get_edhrec_client", lambda: edhrec)

    scryfall = MagicMock()
    scryfall.collection.side_effect = lambda names: {
        "found": [ORACLE[n] for n in names if n in ORACLE],
        "not_found": [n for n in names if n not in ORACLE],
    }
    monkeypatch.setattr("app.tools.dispatch.get_scryfall_client", lambda: scryfall)
    monkeypatch.setattr("app.tools.dispatch.get_tags_for_card", lambda oid: [])
    return scryfall


def _all_cards(payload):
    return [c for cat in payload["categories"].values() for c in cat["cards"]]


def test_recommended_cards_carry_oracle_text(grounded):
    """THE regression: a recommendation without rules text invites a guess."""
    result = dispatch("edhrec_commander_recs", {"commander_name": "Atraxa"}, MagicMock())
    assert result.ok, result.content

    cards = _all_cards(result.content)
    assert cards, "no cards in payload"
    for card in cards:
        assert card.get("oracle_text"), f"{card['name']} has no oracle_text"


def test_recommended_cards_carry_type_and_cost(grounded):
    """Type line and mana cost are load-bearing for curve/colour reasoning."""
    result = dispatch("edhrec_commander_recs", {"commander_name": "Atraxa"}, MagicMock())
    for card in _all_cards(result.content):
        assert card.get("type_line"), f"{card['name']} has no type_line"
        assert card.get("mana_cost") is not None, f"{card['name']} has no mana_cost"


def test_synergy_data_is_preserved(grounded):
    """Enrichment must not drop what EDHREC is actually for."""
    result = dispatch("edhrec_commander_recs", {"commander_name": "Atraxa"}, MagicMock())
    skate = next(c for c in _all_cards(result.content) if c["name"] == "Deepglow Skate")
    assert skate["synergy"] == 0.42
    assert skate["inclusion"] == 0.31


def test_categories_and_headers_survive(grounded):
    result = dispatch("edhrec_commander_recs", {"commander_name": "Atraxa"}, MagicMock())
    assert set(result.content["categories"]) == {"highsynergycards", "topcards"}
    assert result.content["categories"]["highsynergycards"]["header"] == "High Synergy"
    assert result.content["commander"] == "Atraxa, Praetors' Voice"


def test_enrichment_is_one_batched_call(grounded):
    """Per-card lookups would be dozens of round-trips against a rate limit."""
    dispatch("edhrec_commander_recs", {"commander_name": "Atraxa"}, MagicMock())
    assert grounded.collection.call_count == 1
    requested = grounded.collection.call_args[0][0]
    assert len(requested) == len(set(requested)), "names should be de-duped"


def test_unresolvable_card_is_marked_not_invented(grounded, monkeypatch):
    """A card Scryfall can't find must be flagged, never silently passed through
    with no text — that is exactly the state that invites a guess."""
    recs = {
        "commander": "Test",
        "categories": {
            "x": {"header": "X", "cards": [{"name": "Totally Fake Card", "synergy": 0.1}]}
        },
    }
    edhrec = MagicMock()
    edhrec.commander_recs.return_value = recs
    monkeypatch.setattr("app.tools.dispatch.get_edhrec_client", lambda: edhrec)

    result = dispatch("edhrec_commander_recs", {"commander_name": "Test"}, MagicMock())
    card = _all_cards(result.content)[0]
    assert card.get("unverified") is True, "unresolved card not flagged"


def test_edhrec_failure_still_returns_names(grounded, monkeypatch):
    """If Scryfall enrichment fails, degrade to the old behaviour rather than
    losing the recommendations entirely."""
    scryfall = MagicMock()
    scryfall.collection.side_effect = RuntimeError("scryfall down")
    monkeypatch.setattr("app.tools.dispatch.get_scryfall_client", lambda: scryfall)

    result = dispatch("edhrec_commander_recs", {"commander_name": "Atraxa"}, MagicMock())
    assert result.ok, "enrichment failure must not sink the whole tool"
    assert _all_cards(result.content), "recommendations lost on enrichment failure"


def test_long_category_tail_is_marked_needs_lookup(grounded, monkeypatch):
    """Only the head of each category is enriched, to keep the call bounded.

    The tail must be explicitly flagged: an unenriched name with no marker is
    indistinguishable from one whose text the model already has, which is the
    exact ambiguity that lets it fall back on memory.
    """
    from app.tools.dispatch import _ENRICH_PER_CATEGORY

    big = {
        "commander": "Test",
        "categories": {
            "creatures": {
                "header": "Creatures",
                "cards": [
                    {"name": f"Card {i}", "synergy": 0.1}
                    for i in range(_ENRICH_PER_CATEGORY + 5)
                ],
            }
        },
    }
    edhrec = MagicMock()
    edhrec.commander_recs.return_value = big
    monkeypatch.setattr("app.tools.dispatch.get_edhrec_client", lambda: edhrec)

    result = dispatch("edhrec_commander_recs", {"commander_name": "Test"}, MagicMock())
    cards = result.content["categories"]["creatures"]["cards"]

    head, tail = cards[:_ENRICH_PER_CATEGORY], cards[_ENRICH_PER_CATEGORY:]
    assert all(not c.get("needs_lookup") for c in head)
    assert tail and all(c.get("needs_lookup") is True for c in tail)
    # The tail is never sent to Scryfall, which is the point of the cap.
    requested = grounded.collection.call_args[0][0]
    assert len(requested) == _ENRICH_PER_CATEGORY


def test_repeat_calls_reuse_the_memo(grounded):
    """A second call in the same session must not re-pay the Scryfall cost."""
    dispatch("edhrec_commander_recs", {"commander_name": "Atraxa"}, MagicMock())
    dispatch("edhrec_commander_recs", {"commander_name": "Atraxa"}, MagicMock())
    assert grounded.collection.call_count == 1
