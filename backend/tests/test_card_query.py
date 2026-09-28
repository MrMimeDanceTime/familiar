"""Scryfall syntax answered from the local index (app.cards.query)."""

import json

import pytest
from sqlalchemy import text as sql_text

from app.cards import query
from tests.test_find_commanders import index  # noqa: F401 - the fixture

from app.db.session import get_engine


@pytest.fixture
def cards(index):  # noqa: F811
    """The finder's index plus reminder text and a double-faced card."""
    with get_engine().begin() as conn:
        conn.execute(sql_text(
            "INSERT INTO cards (oracle_id, name, cmc, type_line, oracle_text, oracle_plain, colors,"
            " color_identity, legal_commander, edhrec_rank, playable, rarity, power, raw) VALUES"
            " ('jes', 'Jeskai Student', 2, 'Creature — Human Monk',"
            "  'Prowess (Whenever you cast a noncreature spell, this creature gets +1/+1.)', 'Prowess',"
            "  'W', 'W', 1, 800, 1, 'common', '1', :raw),"
            " ('jad', 'Jadzi, Oracle of Arcavios // Journey to the Oracle', 8, 'Legendary Creature // Sorcery',"
            "  'Whenever you cast a noncreature spell, draw.', 'Whenever you cast a noncreature spell, draw.',"
            "  'U', 'U', 1, 900, 1, 'mythic', '5', :raw)"
        ), {"raw": json.dumps({})})
    yield


def _names(q):
    return [c["name"] for c in query.search(q, limit=20)]


def test_rules_text_ignores_reminder_text(cards):
    assert _names('o:"noncreature spell"') == ["Jadzi, Oracle of Arcavios // Journey to the Oracle"]


@pytest.mark.parametrize("q,expected", [
    ("c<=wb o:noncreature", []),
    ("id:br t:legendary", ["Purphoros, God of the Forge", "Kroxa, Titan of Death's Hunger",
                           "King Macar, the Gold-Cursed"]),
    ("id=br", ["Kroxa, Titan of Death's Hunger"]),
    ("t:creature -t:legendary", ["Jeskai Student", "Talion's Messenger"]),
    ("(o:exile OR o:discards) is:commander", ["Kroxa, Titan of Death's Hunger", "King Macar, the Gold-Cursed"]),
    ('!"sol ring"', ["Sol Ring"]),
    ("pow>=5 r:mythic", ["Jadzi, Oracle of Arcavios // Journey to the Oracle"]),
    ("o:~ t:noble", ["King Macar, the Gold-Cursed"]),
    ("t:creature game:paper -st:sticker id:w", ["Jeskai Student"]),
])
def test_syntax(cards, q, expected):
    assert _names(q) == expected


@pytest.mark.parametrize("q", ["lore:theros", "is:funny", "f:modern", "c=2", "set:ths"])
def test_what_the_index_cannot_answer_is_unsupported(cards, q):
    # set: without card_printings imported is unsupported too: the web knows.
    with pytest.raises(query.Unsupported):
        query.search(q)
