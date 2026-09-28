"""Commander discovery over the local index (find_commanders)."""

import json

import pytest
from sqlalchemy import text as sql_text

from app.cards import schema as card_schema
from app.cards import store
from app.db.session import get_engine
from app.tools.dispatch import _find_commanders
from app.tools.render import render_commanders


def _card(oracle_id, name, type_line, identity, rank, set_name, text="", released="2014-05-02"):
    return {
        "oracle_id": oracle_id, "name": name, "mana_cost": "{3}", "cmc": 3.0,
        "type_line": type_line, "oracle_text": text, "color_identity": identity,
        "legal_commander": 1, "edhrec_rank": rank,
        "raw": {"set_name": set_name, "released_at": released},
    }


CARDS = [
    _card("mac", "King Macar, the Gold-Cursed", "Legendary Creature — Human Noble", "B", 10835,
          "Journey into Nyx", "Inspired — Whenever King Macar, the Gold-Cursed becomes untapped, exile target creature."),
    _card("pur", "Purphoros, God of the Forge", "Legendary Enchantment Creature — God", "R", 686,
          "Commander Masters", "Whenever another creature enters, Purphoros deals 2 damage to each opponent."),
    _card("kro", "Kroxa, Titan of Death's Hunger", "Legendary Creature — Elder Giant", "BR", 5000,
          "Theros Beyond Death", "Whenever Kroxa enters or attacks, each opponent discards a card.", "2020-01-24"),
    _card("sol", "Sol Ring", "Artifact", "", 1, "Commander Masters", "{T}: Add {C}{C}."),
    _card("tal", "Talion's Messenger", "Creature — Faerie", "U", 9000, "Wilds of Eldraine"),
    _card("gra", "Grist, the Hunger Tide", "Legendary Planeswalker — Grist", "BG", 900, "Modern Horizons 2",
          "Grist, the Hunger Tide can be your commander.", "2021-06-18"),
]


@pytest.fixture
def index(tmp_path, monkeypatch):
    from app.config import settings
    from app.db import session as db_session

    monkeypatch.setattr(settings, "familiar_db_path", str(tmp_path / "cards.db"))
    db_session.get_engine.cache_clear()
    card_schema.ensure_schema()
    with get_engine().begin() as conn:
        for card in CARDS:
            conn.execute(sql_text(
                "INSERT INTO cards (oracle_id, name, mana_cost, cmc, type_line, oracle_text,"
                " color_identity, legal_commander, edhrec_rank, playable, raw) VALUES"
                " (:oracle_id, :name, :mana_cost, :cmc, :type_line, :oracle_text,"
                " :color_identity, :legal_commander, :edhrec_rank, 1, :raw)"
            ), {**card, "raw": json.dumps(card["raw"])})
        conn.execute(sql_text(
            "INSERT INTO cards_fts (rowid, name, oracle_text, type_line) "
            "SELECT rowid, name, oracle_text, type_line FROM cards"
        ))
    yield
    db_session.get_engine.cache_clear()


def _names(**kw):
    return [c["name"] for c in store.find_commanders(**kw)]


def test_only_cards_that_can_lead_a_deck(index):
    assert _names() == ["Purphoros, God of the Forge", "Grist, the Hunger Tide",
                        "Kroxa, Titan of Death's Hunger", "King Macar, the Gold-Cursed"]


def test_colours_within_and_exact(index):
    assert _names(identity="BR") == ["Purphoros, God of the Forge", "Kroxa, Titan of Death's Hunger",
                                     "King Macar, the Gold-Cursed"]
    assert _names(identity="BR", exact_identity=True) == ["Kroxa, Titan of Death's Hunger"]


def test_popularity_split_at_the_rank_cutoff(index):
    assert _names(popularity="less_popular") == ["Kroxa, Titan of Death's Hunger", "King Macar, the Gold-Cursed"]
    assert _names(popularity="popular") == ["Purphoros, God of the Forge", "Grist, the Hunger Tide"]


def test_text_type_and_set_filters(index):
    assert _names(words="exile") == ["King Macar, the Gold-Cursed"]
    assert _names(creature_type="God") == ["Purphoros, God of the Forge"]
    assert _names(set_name="Theros") == ["Kroxa, Titan of Death's Hunger"]
    assert _names(set_name="Theros, Journey into Nyx") == [
        "Kroxa, Titan of Death's Hunger", "King Macar, the Gold-Cursed"]


def test_tool_result_labels_popularity_and_set(index):
    result = _find_commanders(set_name="Nyx")
    assert result["commanders"][0]["popularity"] == "less played"
    rendered = render_commanders(result)
    assert "King Macar, the Gold-Cursed" in rendered and "Journey into Nyx (2014)" in rendered


def test_printings_find_a_reprinted_legend_by_its_original_set_and_block(index):
    with get_engine().begin() as conn:
        conn.execute(sql_text(
            "INSERT INTO card_sets (code, name, block_code, block, released_at, set_type) VALUES "
            "('ths', 'Theros', 'ths', 'Theros', '2013-09-27', 'expansion'), "
            "('jou', 'Journey into Nyx', 'ths', 'Theros', '2014-05-02', 'expansion'), "
            "('cmm', 'Commander Masters', NULL, NULL, '2023-08-04', 'masters')"
        ))
        conn.execute(sql_text(
            "INSERT INTO card_printings (oracle_id, set_code) VALUES "
            "('pur', 'ths'), ('pur', 'cmm'), ('mac', 'jou')"
        ))
    # Purphoros's own printing is Commander Masters; its first was Theros.
    assert _names(set_name="Theros") == [
        "Purphoros, God of the Forge", "Kroxa, Titan of Death's Hunger", "King Macar, the Gold-Cursed"]
    assert _names(set_name="jou") == ["King Macar, the Gold-Cursed"]


def test_set_search_works_before_printings_exist(index):
    """Until the first refresh after upgrading there is no printings table."""
    with get_engine().begin() as conn:
        conn.execute(sql_text("DROP TABLE card_printings"))
    assert _names(set_name="Theros") == ["Kroxa, Titan of Death's Hunger"]
