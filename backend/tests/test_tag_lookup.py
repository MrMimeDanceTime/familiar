"""Oracle-tag bulk download tests.

Pins the Scryfall bulk-data contract. In August 2026 Scryfall replaced the
``download_uri`` field (a plain JSON array) with ``jsonl_download_uri``
(gzipped JSONL, one tag object per line). The old key vanished from every
bulk type, so ``data["download_uri"]`` raised KeyError on the first card
tagged during any deck import.
"""

import gzip
import io
import json

import httpx
import pytest
import respx

from app.knowledge import tag_lookup

TAG_RECORDS = [
    {
        "object": "tag",
        "slug": "ramp",
        "label": "Ramp",
        "taggings": [
            {"oracle_id": "oid-sol-ring", "weight": 1},
            {"oracle_id": "oid-cultivate", "weight": 1},
        ],
    },
    {
        "object": "tag",
        "slug": "board-wipe",
        "label": "Board wipe",
        "taggings": [{"oracle_id": "oid-wrath", "weight": 1}],
    },
    # A tag with no taggings must be skipped, not crash the parse.
    {"object": "tag", "slug": "orphan-tag", "label": "Orphan", "taggings": []},
]


def _gzipped_jsonl(records) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for rec in records:
            gz.write((json.dumps(rec) + "\n").encode("utf-8"))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Point the cache at a temp file and clear the module-level memo."""
    monkeypatch.setattr(tag_lookup, "_CACHE_PATH", tmp_path / "oracle_tags.jsonl.gz")
    monkeypatch.setattr(tag_lookup, "_lookup", None)
    yield
    tag_lookup._lookup = None


@respx.mock
def test_download_uses_jsonl_download_uri():
    """The bulk-data response no longer carries ``download_uri``."""
    download_url = "https://data.scryfall.io/oracle-tags/oracle-tags-20260804.jsonl.gz"
    respx.get(tag_lookup.SCRYFALL_BULK).mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "bulk_data",
                "type": "oracle_tags",
                "jsonl_download_uri": download_url,
                "compressed_size": 5897759,
            },
        )
    )
    respx.get(download_url).mock(
        return_value=httpx.Response(200, content=_gzipped_jsonl(TAG_RECORDS))
    )

    lookup = tag_lookup.get_tag_lookup()

    assert lookup["oid-sol-ring"] == {"ramp"}
    assert lookup["oid-cultivate"] == {"ramp"}
    assert lookup["oid-wrath"] == {"board-wipe"}


@respx.mock
def test_roles_resolve_through_a_fresh_download():
    """End to end: a downloaded tag maps to an internal role."""
    download_url = "https://data.scryfall.io/oracle-tags/oracle-tags-20260804.jsonl.gz"
    respx.get(tag_lookup.SCRYFALL_BULK).mock(
        return_value=httpx.Response(
            200, json={"jsonl_download_uri": download_url}
        )
    )
    respx.get(download_url).mock(
        return_value=httpx.Response(200, content=_gzipped_jsonl(TAG_RECORDS))
    )

    assert tag_lookup.roles_from_tags("oid-sol-ring") == {"ramp"}
    assert tag_lookup.roles_from_tags("oid-wrath") == {"removal"}
    assert tag_lookup.get_tags_for_card("oid-wrath") == ["board-wipe"]


@respx.mock
def test_cache_round_trips_without_refetching():
    """A warm cache is reused, and parses back to the same lookup."""
    download_url = "https://data.scryfall.io/oracle-tags/oracle-tags-20260804.jsonl.gz"
    bulk_route = respx.get(tag_lookup.SCRYFALL_BULK).mock(
        return_value=httpx.Response(
            200, json={"jsonl_download_uri": download_url}
        )
    )
    respx.get(download_url).mock(
        return_value=httpx.Response(200, content=_gzipped_jsonl(TAG_RECORDS))
    )

    tag_lookup.get_tag_lookup()
    assert bulk_route.call_count == 1

    # Drop the in-process memo so the next call must hit the disk cache.
    tag_lookup._lookup = None
    lookup = tag_lookup.get_tag_lookup()

    assert bulk_route.call_count == 1, "warm cache should not refetch"
    assert lookup["oid-sol-ring"] == {"ramp"}


@respx.mock
def test_missing_download_uri_raises_a_clear_error():
    """A bulk-data payload without a usable URI fails loudly, not with KeyError."""
    respx.get(tag_lookup.SCRYFALL_BULK).mock(
        return_value=httpx.Response(200, json={"object": "bulk_data", "type": "oracle_tags"})
    )

    with pytest.raises(tag_lookup.TagLookupError, match="download URI"):
        tag_lookup.get_tag_lookup()
