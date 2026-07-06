"""Bridge between deck providers and Familiar's deck storage.

Fetching a deck from a provider reuses the existing import pipeline (Scryfall
resolution + tag-based categorization) rather than duplicating it: the
normalized deck is rendered to the same decklist text ``import_decklist``
already accepts, imported, then its commander(s) are designated.
"""

from __future__ import annotations

from sqlmodel import Session

from app.db import repository as repo
from app.integrations.base import NormalizedDeck, get_provider
from app.tools.deck_tools import import_decklist, set_deck_commanders


def _to_decklist_text(deck: NormalizedDeck) -> str:
    return "\n".join(f"{c.quantity} {c.name}" for c in deck.cards)


def fetch_into_deck(session: Session, deck_id: int, provider_name: str, ref: str) -> dict:
    """Fetch a deck from *provider_name* (by URL/id *ref*) into an existing deck.

    Returns the deck snapshot with an ``_import`` summary (imported count +
    per-card errors) and a ``_source`` block describing the origin.
    """
    provider = get_provider(provider_name)
    normalized = provider.fetch_deck(ref)  # raises ProviderError on failure

    result = import_decklist(session, deck_id, _to_decklist_text(normalized))
    import_summary = result["_import"]

    # Designate commander(s) now that the cards are in the deck. Partner pairs
    # come through as two commanders; take the first two. Only designate names
    # that actually resolved into the deck (a failed import shouldn't leave a
    # dangling commander pointer). set_deck_commanders re-snapshots, so refresh
    # and re-attach the import summary afterward.
    resolved = {c.card_name.lower() for c in repo.list_deck_cards(session, deck_id)}
    valid_commanders = [c for c in normalized.commanders if c.lower() in resolved]
    if valid_commanders:
        result = set_deck_commanders(
            session,
            deck_id,
            commander_name=valid_commanders[0],
            partner_commander_name=valid_commanders[1] if len(valid_commanders) > 1 else None,
        )
        result["_import"] = import_summary

    result["_source"] = {
        "provider": provider.name,
        "url": normalized.source_url,
        "name": normalized.name,
        "commanders": valid_commanders,
    }
    return result
