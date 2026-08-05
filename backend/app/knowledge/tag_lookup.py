"""Scryfall Tagger oracle-tag lookup.

Downloads the Oracle Tags bulk-data file from Scryfall (community-vetted
functional tags — ramp, draw, removal, board-wipe, etc.) and builds an
in-memory ``oracle_id → set of tag slugs`` dictionary.  The file is cached
to disk and refreshed at most once every 24 hours.

The bulk file is gzipped JSONL: one tag object per line, each with a
``slug`` and a list of ``taggings``.  It is streamed to disk rather than
held in memory — the compressed download is ~6MB and expands well past
that.
"""

from __future__ import annotations

import gzip
import json
import time
from typing import Any, Iterator

import httpx

from app.config import settings

SCRYFALL_BULK = "https://api.scryfall.com/bulk-data/oracle_tags"


class TagLookupError(RuntimeError):
    """Raised when the oracle-tag bulk data cannot be fetched or parsed."""

# Where the cached tags JSON lives.
_CACHE_PATH = settings.oracle_tags_path

# Map Scryfall tag slugs to our internal role names.
def _tag_to_role(tag: str) -> str | None:
    """Map a Scryfall oracle tag slug to one of our internal roles."""
    # Ramp: mana rocks, dorks, land-search, rituals, cost reduction.
    # Excludes: mana-sink (outlet, not acceleration), mana-fix (color
    # fixing, not ramp), mana-filter (same).
    if (
        tag == "ramp"
        or tag.endswith("-ramp")
        or tag in ("mana-rock", "mana-dork", "mana-egg", "mana-producer",
                    "mana-dork-egg", "utility-mana-rock",
                    "mana-increaser", "mana-storage",
                    "adds-multiple-mana", "cost-reducer-colored-mana",
                    "mana-source-type", "mana-ability-with-extra-effect",
                    "non-mana-ability-mana", "donate-rampant-growth",
                    "powerstone-mana", "mana-rock-with-set-s-mechanic",
                    "ramp-with-set-s-mechanic", "mana-gorger",
                    "buff-mana", "donate-mana", "theft-mana",
                    "offcolor-mana-generation", "gives-mana-ability",
                    "ritual", "ritual-untap")
        or tag.startswith("tutor-land-")
        or tag in ("tutor-land", "tutor-land-any", "tutor-land-to-battlefield",
                    "tutor-to-battlefield")
    ):
        return "ramp"

    # Draw: any card advantage, wheels, impulse draw
    if (
        tag in ("draw", "card-draw", "pure-draw", "burst-draw",
                 "impulsive-draw", "long-term-impulsive-draw",
                 "repeatable-draw", "repeatable-pure-draw",
                 "repeatable-impulsive-draw", "draw-engine",
                 "force-draw", "draw-to-seven", "extra-draw-step",
                 "draw-matters", "second-draw-matters", "third-draw-matters",
                 "wheel", "wheel-one-sided", "wheel-symmetrical",
                 "wheel-symmetrical-optional", "miniwheel")
    ):
        return "draw"

    # Removal: destroy, exile, damage, counters, bounce, sweepers,
    # sacrifice-forcing, fight effects
    if (
        tag == "removal"
        or tag.startswith("removal-")
        or tag.startswith("counterspell")
        or tag.startswith("sweeper")
        or tag.endswith("-sweeper")
        or tag in ("board-wipe", "bounce", "multi-removal",
                    "spot-removal", "repeatable-removal",
                    "opponent-sacrifices", "land-destruction",
                    "exile-on-resolution", "exile-with-tax",
                    "mass-fight", "one-sided-fight", "old-fight",
                    "buttfight", "swap-removal", "exiletouch",
                    "typal-killbot", "unstable-killbot")
    ):
        return "removal"

    return None

# Tag slugs that always mean "land"
_LAND_TAGS = {"land"}

_lookup: dict[str, set[str]] | None = None


def _resolve_download_url(data: dict[str, Any]) -> str:
    """Pull the bulk-file URL out of a Scryfall bulk-data response.

    Scryfall moved from ``download_uri`` (a plain JSON array) to
    ``jsonl_download_uri`` (gzipped JSONL) in August 2026.  The old key is
    still accepted so a stale mirror or a rollback keeps working.
    """
    url = data.get("jsonl_download_uri") or data.get("download_uri")
    if not url:
        raise TagLookupError(
            "Scryfall bulk-data response carried no download URI "
            f"(keys: {sorted(data)})"
        )
    return url


def _download_tags() -> None:
    """Fetch the bulk file and stream it to the on-disk cache."""
    client = httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        # Get the download URL (changes daily)
        resp = client.get(SCRYFALL_BULK)
        resp.raise_for_status()
        download_url = _resolve_download_url(resp.json())

        # Stream the gzipped JSONL straight to disk. Writing to a temp file
        # first keeps a failed download from leaving a truncated cache that
        # later reads would treat as valid.
        tmp_path = _CACHE_PATH.with_suffix(_CACHE_PATH.suffix + ".part")
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with client.stream("GET", download_url) as resp:
            resp.raise_for_status()
            with tmp_path.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        tmp_path.replace(_CACHE_PATH)
    finally:
        client.close()


def _iter_cached_records() -> Iterator[dict[str, Any]]:
    """Yield tag objects from the gzipped JSONL cache, one line at a time."""
    with gzip.open(_CACHE_PATH, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _build_lookup() -> dict[str, set[str]]:
    """Parse the cached bulk file into ``oracle_id → set of tag slugs``."""
    lookup: dict[str, set[str]] = {}
    for entry in _iter_cached_records():
        tag_slug = entry.get("slug", "")
        if not entry.get("taggings"):
            continue
        for t in entry["taggings"]:
            oid = t.get("oracle_id")
            if oid:
                lookup.setdefault(oid, set()).add(tag_slug)
    return lookup


def _load_tags() -> dict[str, set[str]]:
    """Load the oracle_id → tag-slugs mapping, refreshing if stale."""
    global _lookup

    fresh = (
        _CACHE_PATH.exists()
        and (time.time() - _CACHE_PATH.stat().st_mtime) < 86400  # 24 hours
    )
    if not fresh:
        _download_tags()

    try:
        lookup = _build_lookup()
    except (OSError, gzip.BadGzipFile, json.JSONDecodeError) as exc:
        # A corrupt cache is recoverable: drop it and pull a clean copy once.
        if fresh:
            _CACHE_PATH.unlink(missing_ok=True)
            _download_tags()
            lookup = _build_lookup()
        else:
            raise TagLookupError(f"Could not parse oracle-tag bulk data: {exc}") from exc

    _lookup = lookup
    return lookup


def get_tag_lookup() -> dict[str, set[str]]:
    """Return the ``oracle_id → set of tag slugs`` mapping.

    Downloads and caches on first call; reuses the cache for 24 hours.
    """
    if _lookup is not None:
        return _lookup
    return _load_tags()


def get_tags_for_card(oracle_id: str | None) -> list[str]:
    """Return the list of oracle tag slugs for a card."""
    if not oracle_id:
        return []
    return sorted(get_tag_lookup().get(oracle_id, set()))


def roles_from_tags(oracle_id: str | None) -> set[str]:
    """Map a card's Scryfall oracle tags to our internal role names."""
    if not oracle_id:
        return set()
    tags = get_tag_lookup().get(oracle_id, set())
    roles: set[str] = set()
    for tag in tags:
        if tag in _LAND_TAGS:
            roles.add("land")
        role = _tag_to_role(tag)
        if role:
            roles.add(role)
    return roles
