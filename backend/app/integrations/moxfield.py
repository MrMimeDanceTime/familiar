"""Moxfield integration — fetch only.

Moxfield's write API requires authenticated, per-partner access, so we can pull
a deck FROM Moxfield but do not push. The public deck endpoint is undocumented,
so this parses defensively and fails with a clear message rather than crashing.

Deck JSON shape (the parts we use), from ``/v3/decks/all/{publicId}``::

    { "name": str, "format": str, "publicId": str,
      "boards": {
        "mainboard":  { "count": int, "cards": { <id>: {"quantity": int, "card": {"name": str}} } },
        "commanders": { ...same shape... },
        "maybeboard": { ... },   # and sideboard/companions/etc — all ignored
      } }

Only ``mainboard`` and ``commanders`` are imported; every other board
(maybeboard, sideboard, companions, ...) is dropped so it doesn't pollute the
imported list.
"""

from __future__ import annotations

import re

from curl_cffi import requests as curl_requests

from app.integrations.base import (
    DeckProvider,
    NormalizedCard,
    NormalizedDeck,
    ProviderError,
    register,
)

# A Moxfield publicId is a base64url-ish slug (letters, digits, - and _).
# Match it either bare or embedded in a /decks/<id> URL. Anchored on the
# /decks/ segment when a URL is given so we don't grab a username or host.
_URL_ID_RE = re.compile(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", re.IGNORECASE)
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Boards that make up the imported deck. Everything else Moxfield exposes
# (maybeboard, sideboard, companions, attractions, ...) is intentionally omitted.
_INCLUDED_BOARDS = ("mainboard", "commanders")


class MoxfieldProvider(DeckProvider):
    name = "moxfield"
    display_name = "Moxfield"
    supports_fetch = True
    supports_push = False

    api_base = "https://api2.moxfield.com/v3/decks/all"

    def _extract_id(self, ref: str) -> str:
        ref = ref.strip()
        m = _URL_ID_RE.search(ref)
        if m:
            return m.group(1)
        if _BARE_ID_RE.match(ref):
            return ref
        raise ProviderError(
            f"Couldn't find a Moxfield deck id in {ref!r}. "
            "Paste a deck URL like moxfield.com/decks/AbC123 or the id."
        )

    def fetch_deck(self, ref: str) -> NormalizedDeck:
        deck_id = self._extract_id(ref)
        url = f"{self.api_base}/{deck_id}"
        try:
            # Moxfield's API is behind Cloudflare and TLS-fingerprints plain
            # Python HTTP clients (httpx → 403). curl_cffi impersonates a real
            # Chrome TLS handshake so the request gets through.
            resp = curl_requests.get(
                url, impersonate="chrome", timeout=30.0
            )
        except curl_requests.RequestsError as exc:
            raise ProviderError(f"Could not reach Moxfield: {exc}") from exc

        if resp.status_code == 404:
            raise ProviderError(
                f"Moxfield deck {deck_id} not found (it may be private or deleted)."
            )
        if resp.status_code != 200:
            raise ProviderError(f"Moxfield returned HTTP {resp.status_code} for deck {deck_id}.")

        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError("Moxfield returned a non-JSON response.") from exc

        return self._normalize(data, deck_id, ref)

    def _normalize(self, data: dict, deck_id: str, ref: str) -> NormalizedDeck:
        boards = data.get("boards") or {}

        cards: list[NormalizedCard] = []
        commanders: list[str] = []
        for board_name in _INCLUDED_BOARDS:
            board = boards.get(board_name) or {}
            is_commander_board = board_name == "commanders"
            for entry in (board.get("cards") or {}).values():
                name = (entry.get("card") or {}).get("name")
                if not name:
                    continue
                qty = entry.get("quantity") or 1
                if is_commander_board:
                    commanders.append(name)
                cards.append(
                    NormalizedCard(name=name, quantity=qty, is_commander=is_commander_board)
                )

        if not cards:
            raise ProviderError(
                f"Moxfield deck {deck_id} has no importable cards "
                "(it may be empty or entirely maybeboard)."
            )

        public_id = data.get("publicId") or deck_id
        return NormalizedDeck(
            name=data.get("name") or f"Moxfield deck {deck_id}",
            cards=cards,
            commanders=commanders,
            source_format=data.get("format"),
            source_url=f"https://moxfield.com/decks/{public_id}",
        )


register(MoxfieldProvider())
