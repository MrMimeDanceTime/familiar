"""Local card index: import, schema, and query-surface tests.

Pins three things that were found by running the importer against live Scryfall
data rather than by reading the docs:

1. Non-playable layouts (art_series, tokens, emblems…) ship in the same bulk
   file as real cards and several SHARE A NAME with one. The art_series
   "Gigantosaurus // Gigantosaurus" has type line "Card // Card" and no rules
   text; without the ``playable`` filter a name lookup can return it instead of
   the real card.
2. Double-faced cards carry no top-level ``oracle_text`` — the rules text lives
   per-face, so it has to be joined or every DFC is blank and invisible to FTS.
3. Scryfall's bulk-data objects renamed ``size`` to ``compressed_size``, and
   earlier ``download_uri`` to ``jsonl_download_uri``. Nothing may depend on a
   size field being present.
"""

import gzip
import io
import json

import httpx
import pytest
import respx

from app.cards import importer, schema, store


CARD_RECORDS = [
    {
        "object": "card",
        "oracle_id": "oid-mayhem-devil",
        "name": "Mayhem Devil",
        "mana_cost": "{1}{B}{R}",
        "cmc": 3.0,
        "type_line": "Creature — Devil",
        "oracle_text": "Whenever a player sacrifices a permanent, "
                       "Mayhem Devil deals 1 damage to any target.",
        "color_identity": ["B", "R"],
        "colors": ["B", "R"],
        "keywords": [],
        "power": "3",
        "toughness": "3",
        "rarity": "uncommon",
        "edhrec_rank": 579,
        "layout": "normal",
        "legalities": {"commander": "legal"},
        "image_uris": {"normal": "https://img/mayhem.jpg"},
        "scryfall_uri": "https://scryfall.com/mayhem",
    },
    {
        "object": "card",
        "oracle_id": "oid-sol-ring",
        "name": "Sol Ring",
        "mana_cost": "{1}",
        "cmc": 1.0,
        "type_line": "Artifact",
        "oracle_text": "{T}: Add {C}{C}.",
        "color_identity": [],
        "keywords": [],
        "rarity": "uncommon",
        "edhrec_rank": 1,
        "layout": "normal",
        "produced_mana": ["C"],
        "legalities": {"commander": "legal"},
    },
    {
        "object": "card",
        "oracle_id": "oid-black-lotus",
        "name": "Black Lotus",
        "cmc": 0.0,
        "type_line": "Artifact",
        "oracle_text": "{T}, Sacrifice Black Lotus: Add three mana of any one color.",
        "color_identity": [],
        "keywords": [],
        "layout": "normal",
        "legalities": {"commander": "banned"},
    },
    # A DFC: no top-level oracle_text, rules text lives per-face.
    {
        "object": "card",
        "oracle_id": "oid-delver",
        "name": "Delver of Secrets // Insectile Aberration",
        "cmc": 1.0,
        "type_line": "Creature — Human Wizard // Creature — Human Insect",
        "color_identity": ["U"],
        "keywords": [],
        "layout": "transform",
        "legalities": {"commander": "legal"},
        "card_faces": [
            {
                "name": "Delver of Secrets",
                "mana_cost": "{U}",
                "oracle_text": "At the beginning of your upkeep, look at the top card.",
                "image_uris": {"normal": "https://img/delver.jpg"},
            },
            {
                "name": "Insectile Aberration",
                "mana_cost": "",
                "oracle_text": "Flying.",
            },
        ],
    },
    # The shadow card: same name as a real one, but an art_series object.
    {
        "object": "card",
        "oracle_id": "oid-gigantosaurus-art",
        "name": "Gigantosaurus",
        "type_line": "Card",
        "color_identity": [],
        "keywords": [],
        "layout": "art_series",
        "legalities": {"commander": "not_legal"},
    },
    {
        "object": "card",
        "oracle_id": "oid-gigantosaurus",
        "name": "Gigantosaurus",
        "mana_cost": "{G}{G}{G}{G}{G}",
        "cmc": 5.0,
        "type_line": "Creature — Dinosaur",
        "oracle_text": "",
        "color_identity": ["G"],
        "keywords": [],
        "power": "10",
        "toughness": "10",
        "layout": "normal",
        "edhrec_rank": 4000,
        "legalities": {"commander": "legal"},
    },
    # No oracle_id: must be skipped, not crash the import.
    {"object": "card", "name": "Broken Row", "layout": "normal"},
]

TAG_RECORDS = [
    {
        "object": "tag",
        "slug": "mana-rock",
        "taggings": [{"oracle_id": "oid-sol-ring", "weight": 1}],
    },
    {
        "object": "tag",
        "slug": "sacrifice-outlet-artifact",
        "taggings": [{"oracle_id": "oid-black-lotus", "weight": 1}],
    },
    {
        "object": "tag",
        "slug": "opponent-sacrifice-matters",
        "taggings": [
            {"oracle_id": "oid-mayhem-devil", "weight": 1},
            {"oracle_id": "oid-sol-ring", "weight": 1},
        ],
    },
    # No taggings: skipped rather than crashing the parse.
    {"object": "tag", "slug": "orphan-tag", "taggings": []},
]


def _gzipped_jsonl(records) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for rec in records:
            gz.write((json.dumps(rec) + "\n").encode("utf-8"))
    return buf.getvalue()


@pytest.fixture
def card_db(tmp_path, monkeypatch):
    """A fresh on-disk DB with the card-index schema applied."""
    from app.config import settings
    from app.db import session as db_session

    db_file = tmp_path / "cards_test.db"
    monkeypatch.setattr(settings, "familiar_db_path", str(db_file))
    db_session.get_engine.cache_clear()
    schema.ensure_schema()
    yield
    db_session.get_engine.cache_clear()


@pytest.fixture
def imported(card_db):
    """Run a full mocked import of both bulk files."""
    with respx.mock:
        respx.get("https://api.scryfall.com/bulk-data/oracle_cards").mock(
            return_value=httpx.Response(200, json={
                "jsonl_download_uri": "https://data.scryfall.io/cards.jsonl.gz",
                "updated_at": "2026-08-15T09:00:00.000+00:00",
                "compressed_size": 123,
            })
        )
        respx.get("https://data.scryfall.io/cards.jsonl.gz").mock(
            return_value=httpx.Response(200, content=_gzipped_jsonl(CARD_RECORDS))
        )
        respx.get("https://api.scryfall.com/bulk-data/oracle_tags").mock(
            return_value=httpx.Response(200, json={
                "jsonl_download_uri": "https://data.scryfall.io/tags.jsonl.gz",
                "updated_at": "2026-08-15T09:00:00.000+00:00",
            })
        )
        respx.get("https://data.scryfall.io/tags.jsonl.gz").mock(
            return_value=httpx.Response(200, content=_gzipped_jsonl(TAG_RECORDS))
        )
        client = httpx.Client()
        importer.import_cards(client)
        importer.import_tags(client)
        client.close()
    yield


def test_import_writes_cards_and_skips_malformed(imported):
    # 7 records in, 1 has no oracle_id and is skipped.
    assert schema.card_count() == 6


def test_import_writes_taggings(imported):
    # 4 tag records, one orphan with no taggings; 4 real taggings total.
    assert schema.tag_count() == 4


def test_name_lookup_skips_non_playable_shadow_card(imported):
    """The art_series 'Gigantosaurus' must never win a name lookup."""
    card = store.by_name("Gigantosaurus")
    assert card is not None
    assert card["oracle_id"] == "oid-gigantosaurus"
    assert card["type_line"] == "Creature — Dinosaur"


def test_dfc_oracle_text_joins_faces(imported):
    card = store.by_name("Delver of Secrets // Insectile Aberration")
    assert card is not None
    assert "look at the top card" in (card["oracle_text"] or "")
    assert "Flying." in (card["oracle_text"] or "")


def test_dfc_mana_cost_falls_back_to_faces(imported):
    card = store.by_name("Delver of Secrets // Insectile Aberration")
    assert card["mana_cost"] == "{U}"


def test_illegal_cards_stay_nameable(imported):
    """Banned cards are imported and findable — legality is a query-time filter,
    not an import filter, so the app can explain why a card can't be used."""
    card = store.by_name("Black Lotus")
    assert card is not None
    assert card["legal_commander"] is False


def test_card_shape_matches_pipeline_projection(imported):
    """The local store must return the same keys as the Scryfall client's
    pipeline projection, or it can't substitute for it."""
    from app.tools.scryfall_client import _project_pipeline_card

    api_shape = set(_project_pipeline_card(CARD_RECORDS[0]))
    local = store.by_name("Mayhem Devil")
    assert api_shape.issubset(set(local))
    assert local["color_identity"] == ["B", "R"]
    assert local["cmc"] == 3.0
    assert local["edhrec_rank"] == 579


def test_search_text_finds_by_oracle_text(imported):
    hits = store.search_text("sacrifices permanent", limit=10)
    assert any(c["name"] == "Mayhem Devil" for c in hits)


def test_search_text_excludes_illegal_by_default(imported):
    hits = store.search_text("sacrifice", limit=10)
    assert not any(c["name"] == "Black Lotus" for c in hits)


def test_search_text_identity_filter(imported):
    """Colourless cards are a subset of every identity, so Sol Ring survives a
    mono-green filter while the B/R card does not."""
    hits = store.search_text("Add", limit=10, identity="G")
    assert any(c["name"] == "Sol Ring" for c in hits)
    for card in hits:
        assert set(card["color_identity"]).issubset({"G"})


def test_search_text_ands_terms(imported):
    """Multi-term queries AND rather than OR, so an extra term narrows. Sol
    Ring's text is "{T}: Add {C}{C}." — it matches "Add" but not "Add mana"."""
    assert any(c["name"] == "Sol Ring" for c in store.search_text("Add", limit=10))
    assert not any(
        c["name"] == "Sol Ring" for c in store.search_text("Add mana", limit=10)
    )


def test_search_text_empty_query_returns_nothing(imported):
    assert store.search_text("   ") == []


def test_tags_for_many_bulk(imported):
    tags = store.tags_for_many(["oid-sol-ring", "oid-mayhem-devil"])
    assert tags["oid-sol-ring"] == {"mana-rock", "opponent-sacrifice-matters"}
    assert tags["oid-mayhem-devil"] == {"opponent-sacrifice-matters"}


def test_tags_for_many_missing_card_yields_empty_set(imported):
    tags = store.tags_for_many(["oid-nonexistent"])
    assert tags["oid-nonexistent"] == set()


def test_cards_with_any_tag(imported):
    hits = store.cards_with_any_tag(["mana-rock", "opponent-sacrifice-matters"])
    names = {c["name"] for c in hits}
    assert names == {"Sol Ring", "Mayhem Devil"}


def test_cards_with_any_tag_dedupes(imported):
    """Sol Ring carries both slugs; the join must not return it twice."""
    hits = store.cards_with_any_tag(["mana-rock", "opponent-sacrifice-matters"])
    assert len([c for c in hits if c["name"] == "Sol Ring"]) == 1


def test_tag_slugs_discovery(imported):
    found = dict(store.tag_slugs("sacrifice"))
    assert "sacrifice-outlet-artifact" in found


def test_raw_blob_round_trips(imported):
    """The raw blob is the whole point of over-collecting: a field that isn't an
    extracted column must still be reachable without a re-download."""
    raw = store.raw_card("oid-sol-ring")
    assert raw is not None
    assert raw["produced_mana"] == ["C"]


def test_by_names_bulk_lookup(imported):
    found = store.by_names(["Sol Ring", "Mayhem Devil", "Not A Card"])
    assert set(found) == {"sol ring", "mayhem devil"}


def test_resolve_bulk_accepts_legacy_download_uri(card_db):
    """Scryfall renamed download_uri -> jsonl_download_uri; the old key must
    still work so a stale mirror or a rollback doesn't break the import."""
    with respx.mock:
        respx.get("https://api.scryfall.com/bulk-data/oracle_cards").mock(
            return_value=httpx.Response(200, json={
                "download_uri": "https://data.scryfall.io/legacy.jsonl.gz",
                "updated_at": "2026-08-15T09:00:00.000+00:00",
            })
        )
        client = httpx.Client()
        url, updated = importer._resolve_bulk(client, "oracle_cards")
        client.close()
    assert url == "https://data.scryfall.io/legacy.jsonl.gz"
    assert updated.startswith("2026-08-15")


def test_resolve_bulk_raises_without_any_uri(card_db):
    with respx.mock:
        respx.get("https://api.scryfall.com/bulk-data/oracle_cards").mock(
            return_value=httpx.Response(200, json={"compressed_size": 1})
        )
        client = httpx.Client()
        with pytest.raises(importer.CardImportError):
            importer._resolve_bulk(client, "oracle_cards")
        client.close()


def test_refresh_failure_keeps_existing_index(imported):
    """A failed refresh must leave the previous index intact — a stale index is
    still usable, and the app has to serve either way."""
    before = schema.card_count()
    with respx.mock:
        respx.get("https://api.scryfall.com/bulk-data/oracle_cards").mock(
            side_effect=httpx.ConnectError("network down")
        )
        assert importer.refresh_if_stale(force=True) is False
    assert schema.card_count() == before


def test_import_does_not_hold_write_lock_during_download(card_db):
    """The import must not hold SQLite's single write lock across the network
    read.

    Regression: the first version wrapped the whole streaming parse in one
    `engine.begin()`. SQLite allows exactly one writer, so a background refresh
    at startup blocked every other write for the duration of the import —
    `database is locked` for any concurrent request. Here the mocked bulk
    response writes to the DB from another connection *while* the stream is
    being consumed; that write must succeed.
    """
    from sqlalchemy import text as sa_text

    from app.db.session import get_engine

    def _slow_stream(request):
        # Write from a separate connection mid-download. If the importer holds a
        # transaction open across the stream, this raises "database is locked".
        with get_engine().begin() as conn:
            conn.execute(sa_text(
                "INSERT INTO card_index_meta (key, value) VALUES ('probe', 'ok') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            ))
        return httpx.Response(200, content=_gzipped_jsonl(CARD_RECORDS))

    with respx.mock:
        respx.get("https://api.scryfall.com/bulk-data/oracle_cards").mock(
            return_value=httpx.Response(200, json={
                "jsonl_download_uri": "https://data.scryfall.io/cards.jsonl.gz",
                "updated_at": "2026-08-15T09:00:00.000+00:00",
            })
        )
        respx.get("https://data.scryfall.io/cards.jsonl.gz").mock(side_effect=_slow_stream)
        client = httpx.Client()
        importer.import_cards(client)
        client.close()

    assert schema.get_meta("probe") == "ok"
    assert schema.card_count() == 6


def test_is_stale_on_empty_index(card_db):
    assert importer.is_stale() is True


def test_is_stale_false_after_import(imported):
    assert importer.is_stale() is False


def test_pipeline_reads_tags_from_the_index(imported, monkeypatch):
    """The pipeline's tag lookup must come from the index once it exists, not
    from the gzip-backed dict that rebuilt ~229k entries on first call."""
    from app.knowledge import tag_lookup
    from app.pipeline import service

    def _boom():
        raise AssertionError("legacy bulk lookup should not be reached")

    monkeypatch.setattr(tag_lookup, "get_tag_lookup", _boom)
    monkeypatch.setattr(service, "get_tag_lookup", _boom)

    tags = service._tags_for_pool([
        {"oracle_id": "oid-sol-ring"}, {"oracle_id": "oid-mayhem-devil"},
    ])
    assert tags["oid-sol-ring"] == {"mana-rock", "opponent-sacrifice-matters"}


def test_pipeline_tag_lookup_falls_back_when_index_empty(card_db, monkeypatch):
    """A cold start with no index yet must still produce roles rather than an
    untagged pool."""
    from app.pipeline import service

    monkeypatch.setattr(
        service, "get_tag_lookup", lambda: {"oid-x": {"ramp"}}
    )
    tags = service._tags_for_pool([{"oracle_id": "oid-x"}])
    assert tags["oid-x"] == {"ramp"}
