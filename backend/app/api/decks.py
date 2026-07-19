from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.db import repository as repo
from app.db.session import get_engine
from app.integrations import archidekt as _archidekt  # noqa: F401 - registers provider
from app.integrations import moxfield as _moxfield  # noqa: F401 - registers provider
from app.llm.factory import get_provider as get_llm_provider
from app.chat.engine import _generate_deck_name
from app.integrations.base import (
    ProviderError,
    get_provider,
    list_providers,
)
from app.integrations.service import fetch_into_deck
from app.tools.deck_tools import compute_deck_stats, import_decklist, set_deck_commanders

router = APIRouter(prefix="/api/decks", tags=["decks"])


def _deck_user_intent(session: Session, deck_id: int, max_chars: int = 1500) -> str | None:
    """The player's own words about what this deck should do, pulled from the
    conversation that's building it — the strongest signal for naming a deck
    that has nothing in it yet but a freshly-approved commander."""
    conversation = repo.latest_conversation_for_deck(session, deck_id)
    if conversation is None or conversation.id is None:
        return None
    user_texts = [
        m.text_content.strip()
        for m in repo.list_messages(session, conversation.id)
        if m.role == "user" and m.text_content and m.text_content.strip()
    ]
    if not user_texts:
        return None
    intent = "\n".join(user_texts)
    if len(intent) > max_chars:
        intent = intent[-max_chars:]
    return intent


class CreateDeckIn(BaseModel):
    name: str = "Untitled Deck"
    commander: str | None = None
    format: str = "commander"


class UpdateDeckIn(BaseModel):
    name: str | None = None
    commander: str | None = None
    partner_commander: str | None = None
    notes: str | None = None
    power_level: str | None = None
    format: str | None = None


class ImportDecklistIn(BaseModel):
    text: str
    mode: str = "merge"  # "merge" onto existing cards, or "replace" the deck


class SetCommandersIn(BaseModel):
    commander: str | None = None
    partner_commander: str | None = None


class FetchDeckIn(BaseModel):
    provider: str
    ref: str  # deck URL or id on the provider
    mode: str = "merge"  # "merge" onto existing cards, or "replace" the deck


class PushDeckIn(BaseModel):
    provider: str


@router.post("")
def create_deck(body: CreateDeckIn):
    with Session(get_engine()) as session:
        deck = repo.create_deck(session, name=body.name, commander=body.commander, format=body.format)
        return repo.deck_snapshot(session, deck.id)


@router.get("")
def list_decks():
    with Session(get_engine()) as session:
        decks = repo.list_decks(session)
        return [repo.deck_snapshot(session, d.id) for d in decks]


# Must precede "/{deck_id}" — otherwise "providers" is parsed as a deck id.
@router.get("/providers")
def get_providers():
    """List integration providers and which directions each supports."""
    return {"providers": list_providers()}


@router.get("/{deck_id}")
def get_deck(deck_id: int):
    with Session(get_engine()) as session:
        try:
            return repo.deck_snapshot(session, deck_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/{deck_id}")
def update_deck(deck_id: int, body: UpdateDeckIn):
    with Session(get_engine()) as session:
        try:
            repo.update_deck(
                session,
                deck_id,
                name=body.name,
                commander=body.commander,
                partner_commander=body.partner_commander,
                notes=body.notes,
                power_level=body.power_level,
                format=body.format,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return repo.deck_snapshot(session, deck_id)


@router.delete("/{deck_id}")
def delete_deck(deck_id: int):
    with Session(get_engine()) as session:
        repo.delete_deck(session, deck_id)
        return {"ok": True}


@router.post("/{deck_id}/import")
def import_deck(deck_id: int, body: ImportDecklistIn):
    with Session(get_engine()) as session:
        if not repo.get_deck(session, deck_id):
            raise HTTPException(status_code=404, detail=f"Deck {deck_id} not found")
        try:
            return import_decklist(session, deck_id, body.text, mode=body.mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{deck_id}/fetch")
def fetch_deck(deck_id: int, body: FetchDeckIn):
    """Pull a deck from an external provider into this deck."""
    with Session(get_engine()) as session:
        if not repo.get_deck(session, deck_id):
            raise HTTPException(status_code=404, detail=f"Deck {deck_id} not found")
        provider = get_provider_or_404(body.provider)
        if not provider.supports_fetch:
            raise HTTPException(
                status_code=501,
                detail=f"{provider.display_name} does not support fetching decks.",
            )
        try:
            return fetch_into_deck(session, deck_id, body.provider, body.ref, mode=body.mode)
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc))


@router.post("/{deck_id}/push")
def push_deck(deck_id: int, body: PushDeckIn):
    """Publish this deck to an external provider (if that provider supports it)."""
    with Session(get_engine()) as session:
        if not repo.get_deck(session, deck_id):
            raise HTTPException(status_code=404, detail=f"Deck {deck_id} not found")
        provider = get_provider_or_404(body.provider)
        if not provider.supports_push:
            raise HTTPException(
                status_code=501,
                detail=f"{provider.display_name} does not support pushing decks.",
            )
        # No push-capable provider is wired yet; the framework is ready for one.
        raise HTTPException(status_code=501, detail="Deck push is not implemented yet.")


def get_provider_or_404(name: str):
    try:
        return get_provider(name)
    except ProviderError:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name!r}")


@router.post("/{deck_id}/commanders")
def set_commanders(deck_id: int, body: SetCommandersIn):
    """Designate the deck's commander(s) from cards already in the deck."""
    with Session(get_engine()) as session:
        try:
            return set_deck_commanders(
                session,
                deck_id,
                commander_name=body.commander,
                partner_commander_name=body.partner_commander,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{deck_id}/conversation")
def get_deck_conversation(deck_id: int):
    with Session(get_engine()) as session:
        conversation = repo.get_conversation_by_deck_id(session, deck_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="No conversation linked to this deck")
        from app.api.conversations import _conversation_out
        return _conversation_out(conversation)


@router.get("/{deck_id}/conversations")
def list_deck_conversations(deck_id: int):
    with Session(get_engine()) as session:
        conversations = repo.list_conversations_by_deck_id(session, deck_id)
        from app.api.conversations import _conversation_out
        return [_conversation_out(c) for c in conversations]


@router.post("/{deck_id}/start-conversation")
def start_conversation(deck_id: int):
    """Create a new conversation linked to this deck."""
    with Session(get_engine()) as session:
        if not repo.get_deck(session, deck_id):
            raise HTTPException(status_code=404, detail=f"Deck {deck_id} not found")
        conversation = repo.create_conversation(session)
        linked = repo.set_conversation_deck(session, conversation.id, deck_id)
        from app.api.conversations import _conversation_out
        return _conversation_out(linked)


@router.get("/{deck_id}/stats")
def get_deck_stats(deck_id: int):
    """Deterministic deck stats — no LLM call, always fast.

    The panel loads this on deck-select and paints immediately. The LLM-refined
    power-level nuance is fetched separately via /stats/nuance so a cold nuance
    cache never makes this endpoint (and the whole panel) hang. If a fresh
    nuance is already cached, compute_deck_stats folds it in for free; if not,
    the base power level is returned and /stats/nuance sharpens it."""
    with Session(get_engine()) as session:
        if not repo.get_deck(session, deck_id):
            raise HTTPException(status_code=404, detail=f"Deck {deck_id} not found")
        return compute_deck_stats(session, deck_id)


@router.get("/{deck_id}/stats/nuance")
def get_deck_stats_nuance(deck_id: int):
    """The LLM-refined power level, resolved on request (cache-first).

    Split out from /stats so the panel can render deterministic stats instantly
    and show a small loading state on just the power level while this call
    runs. A cache hit returns immediately; only a cold miss pays the LLM
    round-trip, which the frontend spinner covers. Returns just the power-level
    fields the panel needs to swap the base number for the nuanced one."""
    with Session(get_engine()) as session:
        if not repo.get_deck(session, deck_id):
            raise HTTPException(status_code=404, detail=f"Deck {deck_id} not found")
        try:
            provider = get_llm_provider()
        except Exception:
            provider = None
        stats = compute_deck_stats(session, deck_id, provider)
        return {
            "power_level": stats["power_level"],
            "power_level_base": stats["power_level_base"],
            "power_nuance_adj": stats["power_nuance_adj"],
            "power_nuance_reason": stats["power_nuance_reason"],
            "power_factors": stats["power_factors"],
        }


def _autoname_deck(deck_id: int) -> None:
    """Generate and store a deck name via the LLM. Runs as a background task
    after apply_proposal has already responded, because the naming call takes
    several seconds and must not block the player's "Approve" click. Opens its
    own session — the request's session is closed by the time this runs.
    Best-effort: any failure just leaves the deck named "Untitled Deck"."""
    try:
        with Session(get_engine()) as session:
            snapshot = repo.deck_snapshot(session, deck_id)
            # Re-check under the fresh session: a racing edit may have already
            # named it, or removed the commander.
            if snapshot.get("name") != "Untitled Deck" or not snapshot.get("commander"):
                return
            provider = get_llm_provider()
            user_intent = _deck_user_intent(session, deck_id)
            new_name = _generate_deck_name(
                provider, snapshot, snapshot.get("format", "commander"),
                user_intent=user_intent,
            )
            repo.update_deck(session, deck_id, name=new_name)
    except Exception:
        pass


@router.post("/proposals/{proposal_id}/apply")
def apply_proposal(proposal_id: int, background_tasks: BackgroundTasks):
    with Session(get_engine()) as session:
        result = repo.apply_proposal(session, proposal_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Proposal not found or not pending")

        # Auto-name an "Untitled Deck" once it has a commander — that's when the
        # deck's identity becomes meaningful. The naming is a multi-second LLM
        # call, so it runs AFTER this response is sent (BackgroundTasks) rather
        # than blocking the Approve click; the client picks up the new name on
        # its next deck refresh.
        if result.get("name") == "Untitled Deck" and result.get("commander"):
            background_tasks.add_task(_autoname_deck, result["id"])

        return result


@router.post("/proposals/{proposal_id}/deny")
def deny_proposal(proposal_id: int):
    with Session(get_engine()) as session:
        ok = repo.deny_proposal(session, proposal_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Proposal not found or not pending")
        return {"ok": True}


@router.get("/{deck_id}/cards")
def get_deck_cards(deck_id: int):
    with Session(get_engine()) as session:
        try:
            return repo.deck_snapshot(session, deck_id)["cards"]
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
