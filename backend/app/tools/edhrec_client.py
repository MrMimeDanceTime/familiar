"""Thin client for EDHREC's unofficial JSON endpoint (json.edhrec.com).

EDHREC has no official public API. Their own site fetches structured JSON
from json.edhrec.com to render pages, and that JSON is what we hit here.
This is an undocumented endpoint that can change shape without notice, so
all parsing is defensive (missing/renamed keys degrade gracefully rather
than raising), and responses are cached aggressively to disk to avoid
hammering EDHREC's servers.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

EDHREC_BASE_URL = "https://json.edhrec.com"


class EdhrecError(Exception):
    pass


class EdhrecNotFoundError(EdhrecError):
    pass


def commander_name_to_slug(name: str) -> str:
    """Convert a commander name (or 'A / B' partner pair) to an EDHREC slug.

    Examples (verified against live EDHREC URLs):
      "Atraxa, Grand Unifier" -> "atraxa-grand-unifier"
      "The Gitrog Monster" -> "the-gitrog-monster"  (leading "The" is kept)
      "Atraxa, Praetors' Voice" -> "atraxa-praetors-voice"  (apostrophes dropped)
      "Thrasios, Triton Hero / Tymna the Weaver"
        -> "thrasios-triton-hero-tymna-the-weaver"  (partners joined with '-')
    """
    parts = re.split(r"\s*/\s*", name)
    slugs = []
    for part in parts:
        slug = part.lower()
        slug = slug.replace("'", "")
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        slugs.append(slug)
    return "-".join(slugs)


def _cache_path(slug: str) -> Path:
    cache_dir = settings.edhrec_cache_path
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{slug.replace('/', '--')}.json"


def _get_or_fetch(slug: str, fetch_fn) -> dict[str, Any]:
    path = _cache_path(slug)
    ttl_seconds = settings.edhrec_cache_ttl_hours * 3600

    if path.exists():
        age = time.time() - path.stat().st_mtime
        if age < ttl_seconds:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("EDHREC cache file %s unreadable, refetching: %s", path, exc)

    data = fetch_fn()
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write EDHREC cache file %s: %s", path, exc)
    return data


def _normalize_cardview(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": raw.get("name"),
        "synergy": raw.get("synergy"),
        "inclusion": raw.get("inclusion"),
        "num_decks": raw.get("num_decks"),
        "potential_decks": raw.get("potential_decks"),
    }


class EdhrecClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            base_url=EDHREC_BASE_URL,
            headers={"User-Agent": "familiar/0.1", "Accept": "application/json"},
            timeout=15.0,
        )
        # Shared singleton across the threadpool (get_edhrec_client); guard the
        # shared httpx.Client so concurrent requests don't race it. Cache hits
        # (the common path) don't reach here — only live fetches take the lock.
        self._lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def _fetch_commander_page(self, slug: str) -> dict[str, Any]:
        # ``slug`` may carry a theme ("korlash-heir-to-blackblade/voltron"):
        # EDHREC serves a commander's theme pages at the same path shape.
        with self._lock:
            response = self._client.get(f"/pages/commanders/{slug}.json")
        if response.status_code in (403, 404):
            raise EdhrecNotFoundError(f"No EDHREC page found for commander slug '{slug}'")
        if response.status_code >= 400:
            raise EdhrecError(f"EDHREC error {response.status_code} for slug '{slug}'")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise EdhrecError(f"EDHREC returned non-JSON response for '{slug}'") from exc

    def commander_themes(self, commander_name: str) -> list[dict[str, Any]]:
        """The commander's themes as EDHREC counts them, most decks first:
        ``[{"slug": "voltron", "value": "Voltron", "count": 111}, ...]``. Read
        from the commander page already cached, so this costs no request."""
        slug = commander_name_to_slug(commander_name)
        data = _get_or_fetch(slug, lambda: self._fetch_commander_page(slug))
        raw = (data.get("panels") or {}).get("taglinks") or data.get("tag_counts") or []
        themes = [
            {"slug": t["slug"], "value": t.get("value") or t["slug"], "count": int(t.get("count") or 0)}
            for t in raw if isinstance(t, dict) and t.get("slug")
        ]
        return sorted(themes, key=lambda t: t["count"], reverse=True)

    def commander_recs(self, commander_name: str, theme: str | None = None) -> dict[str, Any]:
        """Return EDHREC's cardlists for a commander, grouped by category.
        With ``theme`` (a slug from ``commander_themes``), the same lists
        computed over only that theme's decks.

        Shape: {"commander": str, "categories": {tag: {"header": str, "cards": [...]}}}
        Degrades gracefully (empty categories) if EDHREC's response shape
        has changed in a way we don't recognize, rather than raising.
        """
        slug = commander_name_to_slug(commander_name)
        if theme:
            slug = f"{slug}/{theme}"
        data = _get_or_fetch(slug, lambda: self._fetch_commander_page(slug))

        categories: dict[str, Any] = {}
        try:
            cardlists = data.get("container", {}).get("json_dict", {}).get("cardlists", [])
        except AttributeError:
            cardlists = []

        if not isinstance(cardlists, list):
            logger.warning("EDHREC cardlists for '%s' was not a list; shape may have changed", slug)
            cardlists = []

        for entry in cardlists:
            if not isinstance(entry, dict):
                continue
            tag = entry.get("tag") or entry.get("header") or "unknown"
            cardviews = entry.get("cardviews") or []
            if not isinstance(cardviews, list):
                cardviews = []
            categories[tag] = {
                "header": entry.get("header", tag),
                "cards": [_normalize_cardview(c) for c in cardviews if isinstance(c, dict)],
            }

        return {"commander": commander_name, "categories": categories}

    def card_synergy(self, card_name: str, commander_name: str) -> dict[str, Any] | None:
        """Look up a single card's synergy stats within a commander's page.

        EDHREC has no dedicated single-card-synergy endpoint, so this is
        derived by scanning the commander's cardlists for a name match.
        """
        recs = self.commander_recs(commander_name)
        target = card_name.strip().lower()
        for category in recs["categories"].values():
            for card in category["cards"]:
                if (card.get("name") or "").strip().lower() == target:
                    return card
        return None


@lru_cache(maxsize=1)
def get_edhrec_client() -> EdhrecClient:
    return EdhrecClient()
