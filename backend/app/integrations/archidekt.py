"""Archidekt integration — fetch only.

Archidekt's public API is read-only (GET), so we can pull a deck FROM Archidekt
but cannot push one TO it. The endpoint is undocumented and in open beta, so
this parses defensively and fails with a clear message rather than crashing.

Deck JSON shape (the parts we use):
    { "name": str, "deckFormat": int,
      "categories": [ { "name": str, "includedInDeck": bool, "isPremier": bool } ],
      "cards": [ { "quantity": int, "categories": [str],
                   "card": { "oracleCard": { "name": str } } } ] }
"""

from __future__ import annotations

import re

import httpx

from app.integrations.base import (
    DeckProvider,
    NormalizedCard,
    NormalizedDeck,
    ProviderError,
    register,
)

# Matches a bare id, or an id embedded in an archidekt deck URL.
_ID_RE = re.compile(r"(?:archidekt\.com/decks/)?(\d+)", re.IGNORECASE)

# Categories that mean "not in the 100" regardless of platform config.
_EXCLUDED_CATEGORIES = {"maybeboard", "sideboard", "considering"}


class ArchidektProvider(DeckProvider):
    name = "archidekt"
    display_name = "Archidekt"
    supports_fetch = True
    supports_push = False

    api_base = "https://archidekt.com/api/decks"

    def _extract_id(self, ref: str) -> str:
        m = _ID_RE.search(ref.strip())
        if not m:
            raise ProviderError(
                f"Couldn't find an Archidekt deck id in {ref!r}. "
                "Paste a deck URL like archidekt.com/decks/123456 or the id."
            )
        return m.group(1)

    def fetch_deck(self, ref: str) -> NormalizedDeck:
        deck_id = self._extract_id(ref)
        url = f"{self.api_base}/{deck_id}/"
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": "Familiar/1.0"})
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Archidekt: {exc}") from exc

        if resp.status_code == 404:
            raise ProviderError(
                f"Archidekt deck {deck_id} not found (it may be private or deleted)."
            )
        if resp.status_code != 200:
            raise ProviderError(f"Archidekt returned HTTP {resp.status_code} for deck {deck_id}.")

        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError("Archidekt returned a non-JSON response.") from exc

        return self._normalize(data, deck_id, ref)

    def _normalize(self, data: dict, deck_id: str, ref: str) -> NormalizedDeck:
        # Category defs flagged includedInDeck=False (e.g. a custom Maybeboard)
        # mark cards to exclude; combine with the well-known excluded names.
        excluded_cat_names = {
            (c.get("name") or "").lower()
            for c in (data.get("categories") or [])
            if c.get("includedInDeck") is False
        } | _EXCLUDED_CATEGORIES

        cards: list[NormalizedCard] = []
        commanders: list[str] = []
        for entry in data.get("cards") or []:
            name = (
                (entry.get("card") or {})
                .get("oracleCard", {})
                .get("name")
            )
            if not name:
                continue
            cats = [c.lower() for c in (entry.get("categories") or [])]
            if any(c in excluded_cat_names for c in cats):
                continue

            qty = entry.get("quantity") or 1
            is_commander = "commander" in cats
            if is_commander:
                commanders.append(name)
            cards.append(NormalizedCard(name=name, quantity=qty, is_commander=is_commander))

        if not cards:
            raise ProviderError(
                f"Archidekt deck {deck_id} has no importable cards "
                "(it may be empty or entirely maybeboard)."
            )

        return NormalizedDeck(
            name=data.get("name") or f"Archidekt deck {deck_id}",
            cards=cards,
            commanders=commanders,
            source_format=str(data.get("deckFormat")) if data.get("deckFormat") is not None else None,
            source_url=f"https://archidekt.com/decks/{deck_id}",
        )


register(ArchidektProvider())
