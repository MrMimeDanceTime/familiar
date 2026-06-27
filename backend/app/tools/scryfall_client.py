"""Thin client for the Scryfall REST API (https://api.scryfall.com).

No auth required. Self-throttles to stay within Scryfall's documented
10 req/s guidance and backs off on 429s.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

import httpx

SCRYFALL_BASE_URL = "https://api.scryfall.com"
MIN_REQUEST_INTERVAL = 0.11  # ~9 req/s, safely under the 10 req/s ceiling
MAX_RETRIES = 3


class ScryfallError(Exception):
    pass


class ScryfallNotFoundError(ScryfallError):
    pass


def _normalize_card(raw: dict[str, Any]) -> dict[str, Any]:
    image_uris = raw.get("image_uris") or {}
    if not image_uris and raw.get("card_faces"):
        image_uris = (raw["card_faces"][0] or {}).get("image_uris") or {}

    return {
        "name": raw.get("name"),
        "mana_cost": raw.get("mana_cost"),
        "cmc": raw.get("cmc"),
        "type_line": raw.get("type_line"),
        "oracle_text": raw.get("oracle_text"),
        "color_identity": raw.get("color_identity"),
        "image_url": image_uris.get("normal"),
        "legal_commander": (raw.get("legalities") or {}).get("commander") == "legal",
        "scryfall_uri": raw.get("scryfall_uri"),
    }


class ScryfallClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            base_url=SCRYFALL_BASE_URL,
            headers={"User-Agent": "familiar/0.1", "Accept": "application/json"},
            timeout=10.0,
        )
        self._last_request_time: float = 0.0

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            self._last_request_time = time.monotonic()
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                continue

            if response.status_code == 404:
                raise ScryfallNotFoundError(
                    (response.json().get("details") if response.content else None)
                    or "Not found"
                )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else 2**attempt
                if attempt < MAX_RETRIES:
                    time.sleep(wait)
                    continue
                raise ScryfallError("Rate limited by Scryfall and retries exhausted")

            if response.status_code >= 400:
                detail = response.json().get("details") if response.content else None
                raise ScryfallError(detail or f"Scryfall error {response.status_code}")

            return response.json()

        raise ScryfallError(f"Request failed after retries: {last_error}")

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        data = self._request("GET", "/cards/search", params={"q": query})
        cards = data.get("data", [])[:limit]
        return [_normalize_card(c) for c in cards]

    def named(self, name: str, fuzzy: bool = True) -> dict[str, Any]:
        param = "fuzzy" if fuzzy else "exact"
        data = self._request("GET", "/cards/named", params={param: name})
        return _normalize_card(data)

    def autocomplete(self, prefix: str) -> list[str]:
        data = self._request("GET", "/cards/autocomplete", params={"q": prefix})
        return data.get("data", [])

    def collection(self, names: list[str]) -> dict[str, list[Any]]:
        found: list[dict[str, Any]] = []
        not_found: list[str] = []
        for i in range(0, len(names), 75):
            chunk = names[i : i + 75]
            identifiers = [{"name": n} for n in chunk]
            data = self._request(
                "POST", "/cards/collection", json={"identifiers": identifiers}
            )
            found.extend(_normalize_card(c) for c in data.get("data", []))
            not_found.extend(
                nf.get("name", "") for nf in data.get("not_found", [])
            )
        return {"found": found, "not_found": not_found}


@lru_cache(maxsize=1)
def get_scryfall_client() -> ScryfallClient:
    return ScryfallClient()
