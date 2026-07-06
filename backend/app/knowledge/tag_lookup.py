"""Scryfall Tagger oracle-tag lookup.

Downloads the Oracle Tags bulk-data file from Scryfall (community-vetted
functional tags — ramp, draw, removal, board-wipe, etc.) and builds an
in-memory ``oracle_id → set of tag slugs`` dictionary.  The file is cached
to disk and refreshed at most once every 24 hours.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

SCRYFALL_BULK = "https://api.scryfall.com/bulk-data/oracle_tags"

# Where the cached tags JSON lives.
_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "oracle_tags_cache.json"

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


def _download_tags() -> dict[str, Any]:
    """Fetch the bulk-data download URL, download the file, cache it."""
    client = httpx.Client(timeout=30.0)
    try:
        # Get the download URL (changes daily)
        resp = client.get(SCRYFALL_BULK)
        resp.raise_for_status()
        data = resp.json()
        download_url = data["download_uri"]

        # Download the actual tags file
        resp = client.get(download_url)
        resp.raise_for_status()
        tags_data = resp.json()
    finally:
        client.close()

    # Cache to disk
    _CACHE_PATH.write_text(json.dumps(tags_data), encoding="utf-8")
    return tags_data


def _load_tags() -> dict[str, set[str]]:
    """Load the oracle_id → tag-slugs mapping, refreshing if stale."""
    global _lookup

    # Try cache first
    if _CACHE_PATH.exists():
        age = time.time() - _CACHE_PATH.stat().st_mtime
        if age < 86400:  # 24 hours
            tags_data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            lookup: dict[str, set[str]] = {}
            for entry in tags_data:
                tag_slug = entry.get("slug", "")
                if not entry.get("taggings"):
                    continue
                for t in entry["taggings"]:
                    oid = t.get("oracle_id")
                    if oid:
                        lookup.setdefault(oid, set()).add(tag_slug)
            _lookup = lookup
            return lookup

    # Download fresh
    tags_data = _download_tags()
    lookup = {}
    for entry in tags_data:
        tag_slug = entry.get("slug", "")
        if not entry.get("taggings"):
            continue
        for t in entry["taggings"]:
            oid = t.get("oracle_id")
            if oid:
                lookup.setdefault(oid, set()).add(tag_slug)
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
