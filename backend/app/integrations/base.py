"""Deck-provider interface and registry.

A provider integrates one external platform. Capabilities are explicit:
``supports_fetch`` (pull a deck FROM the platform) and ``supports_push``
(publish a deck TO it). A provider may support one, both, or neither — e.g.
Archidekt is fetch-only because its public API is read-only, while a platform
with a write API (Moxfield) could support push too.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field


class ProviderError(Exception):
    """A provider failed to fetch/push — message is safe to surface to the user."""


class NotSupportedError(ProviderError):
    """The requested direction (fetch or push) isn't supported by this provider."""


@dataclass
class NormalizedCard:
    """One card line in a fetched deck, platform-agnostic."""
    name: str
    quantity: int = 1
    is_commander: bool = False


@dataclass
class NormalizedDeck:
    """A deck fetched from a platform, reduced to what Familiar can import.

    Only mainboard + commanders — maybeboard/sideboard/considering cards are
    dropped by the provider so they don't pollute the imported list.
    """
    name: str
    cards: list[NormalizedCard] = field(default_factory=list)
    commanders: list[str] = field(default_factory=list)
    source_format: str | None = None
    source_url: str | None = None


@dataclass
class PushResult:
    """Outcome of pushing a deck to a platform."""
    url: str
    external_id: str


class DeckProvider(ABC):
    """Base class for a third-party deck platform integration."""

    #: Stable machine name used in API calls and the registry key.
    name: str = ""
    #: Human-facing label.
    display_name: str = ""
    #: Capability flags — the API layer checks these before dispatching.
    supports_fetch: bool = False
    supports_push: bool = False

    def fetch_deck(self, ref: str) -> NormalizedDeck:
        """Fetch a deck from the platform given a URL or id.

        Override in fetch-capable providers. *ref* may be a full deck URL or a
        bare id; the provider is responsible for extracting the id.
        """
        raise NotSupportedError(f"{self.display_name} does not support fetching decks.")

    def push_deck(self, deck: NormalizedDeck, *, credentials: dict | None = None) -> PushResult:
        """Publish a deck to the platform.

        Override in push-capable providers. *credentials* carries whatever auth
        the platform needs (token, session, etc.).
        """
        raise NotSupportedError(f"{self.display_name} does not support pushing decks.")


_REGISTRY: dict[str, DeckProvider] = {}


def register(provider: DeckProvider) -> DeckProvider:
    _REGISTRY[provider.name] = provider
    return provider


def get_provider(name: str) -> DeckProvider:
    provider = _REGISTRY.get(name)
    if provider is None:
        raise ProviderError(f"Unknown deck provider: {name!r}")
    return provider


def list_providers() -> list[dict]:
    """Return each registered provider's name, label, and capabilities."""
    return [
        {
            "name": p.name,
            "display_name": p.display_name,
            "supports_fetch": p.supports_fetch,
            "supports_push": p.supports_push,
        }
        for p in _REGISTRY.values()
    ]
