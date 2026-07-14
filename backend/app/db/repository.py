from datetime import datetime, timezone

from sqlmodel import Session, select

from app.db.models import Conversation, Deck, DeckCard, DeckProposal, Message, UserPreferences


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Conversations ---------------------------------------------------------


def create_conversation(session: Session, title: str = "New conversation") -> Conversation:
    conversation = Conversation(title=title)
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def get_conversation(session: Session, conversation_id: int) -> Conversation | None:
    return session.get(Conversation, conversation_id)


def list_conversations(session: Session) -> list[Conversation]:
    statement = select(Conversation).order_by(Conversation.updated_at.desc())
    return list(session.exec(statement))


def touch_conversation(session: Session, conversation_id: int) -> None:
    conversation = session.get(Conversation, conversation_id)
    if conversation:
        conversation.updated_at = _utcnow()
        session.add(conversation)
        session.commit()


def set_conversation_title(session: Session, conversation_id: int, title: str) -> None:
    conversation = session.get(Conversation, conversation_id)
    if conversation:
        conversation.title = title
        session.add(conversation)
        session.commit()


def delete_conversation(session: Session, conversation_id: int) -> None:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        return
    for message in list_messages(session, conversation_id):
        session.delete(message)
    for proposal in list_proposals(session, conversation_id):
        session.delete(proposal)
    session.delete(conversation)
    session.commit()


def latest_conversation_for_deck(session: Session, deck_id: int) -> Conversation | None:
    statement = (
        select(Conversation)
        .where(Conversation.deck_id == deck_id)
        .order_by(Conversation.updated_at.desc())
    )
    return session.exec(statement).first()


def set_conversation_deck(session: Session, conversation_id: int, deck_id: int) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise ValueError(f"Conversation {conversation_id} not found")
    conversation.deck_id = deck_id
    conversation.updated_at = _utcnow()
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


# --- Messages ---------------------------------------------------------------


def add_message(
    session: Session,
    conversation_id: int,
    role: str,
    sequence: int,
    text_content: str | None = None,
    provider_native: list | None = None,
    tool_calls: list | None = None,
    tool_results: list | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role=role,
        sequence=sequence,
        text_content=text_content,
        provider_native=provider_native or [],
        tool_calls=tool_calls,
        tool_results=tool_results,
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def list_messages(session: Session, conversation_id: int) -> list[Message]:
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.sequence.asc())
    )
    return list(session.exec(statement))


def next_sequence(session: Session, conversation_id: int) -> int:
    messages = list_messages(session, conversation_id)
    return (messages[-1].sequence + 1) if messages else 0


# --- Decks -------------------------------------------------------------------


def create_deck(
    session: Session,
    name: str = "Untitled Deck",
    commander: str | None = None,
    format: str = "commander",
) -> Deck:
    deck = Deck(name=name, commander=commander, format=format)
    session.add(deck)
    session.commit()
    session.refresh(deck)
    return deck


def get_deck(session: Session, deck_id: int) -> Deck | None:
    return session.get(Deck, deck_id)


def list_decks(session: Session) -> list[Deck]:
    statement = select(Deck).order_by(Deck.updated_at.desc())
    return list(session.exec(statement))


def set_deck_power_nuance(
    session: Session, deck_id: int, adjustment: float, reason: str, content_key: str
) -> None:
    """Persist the cached power-level nuance adjustment + reason + the content
    hash it was computed for. Deliberately does NOT touch updated_at — caching a
    derived value is not a deck edit, and bumping updated_at would ripple into
    anything that keys off it."""
    deck = session.get(Deck, deck_id)
    if not deck:
        return
    deck.power_nuance_adj = adjustment
    deck.power_nuance_reason = reason
    deck.power_nuance_key = content_key
    session.add(deck)
    session.commit()


def update_deck(
    session: Session,
    deck_id: int,
    name: str | None = None,
    commander: str | None = None,
    partner_commander: str | None = None,
    notes: str | None = None,
    power_level: str | None = None,
    format: str | None = None,
) -> Deck:
    deck = session.get(Deck, deck_id)
    if not deck:
        raise ValueError(f"Deck {deck_id} not found")
    if name is not None:
        deck.name = name
    if commander is not None:
        deck.commander = commander
    if partner_commander is not None:
        deck.partner_commander = partner_commander
    if notes is not None:
        deck.notes = notes
    if power_level is not None:
        deck.power_level = power_level
    if format is not None:
        deck.format = format
    deck.updated_at = _utcnow()
    session.add(deck)
    session.commit()
    session.refresh(deck)
    return deck


def set_deck_commander_fields(
    session: Session,
    deck_id: int,
    commander: str | None,
    partner_commander: str | None,
) -> Deck:
    """Set both commander slots directly, allowing either to be cleared.

    Unlike update_deck (where None means "leave unchanged"), this writes the
    given values verbatim so a commander can be removed by passing None.
    """
    deck = session.get(Deck, deck_id)
    if not deck:
        raise ValueError(f"Deck {deck_id} not found")
    deck.commander = commander
    deck.partner_commander = partner_commander
    deck.updated_at = _utcnow()
    session.add(deck)
    session.commit()
    session.refresh(deck)
    return deck


def delete_deck(session: Session, deck_id: int) -> None:
    deck = session.get(Deck, deck_id)
    if not deck:
        return
    for card in list_deck_cards(session, deck_id):
        session.delete(card)
    for proposal in list_proposals_by_deck_id(session, deck_id):
        session.delete(proposal)
    session.delete(deck)
    session.commit()


# --- Deck cards ---------------------------------------------------------------


def list_deck_cards(session: Session, deck_id: int) -> list[DeckCard]:
    statement = select(DeckCard).where(DeckCard.deck_id == deck_id).order_by(DeckCard.added_at.asc())
    return list(session.exec(statement))


def get_deck_card(session: Session, deck_id: int, card_name: str) -> DeckCard | None:
    statement = select(DeckCard).where(
        DeckCard.deck_id == deck_id, DeckCard.card_name == card_name
    )
    return session.exec(statement).first()


def add_deck_card(
    session: Session,
    deck_id: int,
    card_name: str,
    quantity: int | None = None,
    category: str | None = None,
    mana_value: float | None = None,
    color_identity: str | None = None,
    type_line: str | None = None,
    oracle_text: str | None = None,
    oracle_id: str | None = None,
    tags: list | None = None,
    notes: str | None = None,
) -> DeckCard:
    existing = get_deck_card(session, deck_id, card_name)
    if existing:
        # quantity=None means the caller didn't specify a count (e.g. a
        # tag/metadata-only refresh) — don't let that clobber an existing
        # stack (e.g. 34 Swamps). An explicit quantity adds to the existing
        # stack (mirrors remove_deck_card subtracting) — e.g. adding 6 more
        # Swamps to 8 already in the deck results in 14, not 6.
        if quantity is not None:
            existing.quantity += quantity
        existing.category = category if category is not None else existing.category
        existing.mana_value = mana_value if mana_value is not None else existing.mana_value
        existing.color_identity = color_identity if color_identity is not None else existing.color_identity
        existing.type_line = type_line if type_line is not None else existing.type_line
        existing.oracle_text = oracle_text if oracle_text is not None else existing.oracle_text
        existing.oracle_id = oracle_id if oracle_id is not None else existing.oracle_id
        existing.tags = tags if tags is not None else existing.tags
        existing.notes = notes if notes is not None else existing.notes
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    card = DeckCard(
        deck_id=deck_id,
        card_name=card_name,
        quantity=quantity if quantity is not None else 1,
        category=category,
        mana_value=mana_value,
        color_identity=color_identity,
        type_line=type_line,
        oracle_text=oracle_text,
        oracle_id=oracle_id,
        tags=tags,
        notes=notes,
    )
    session.add(card)
    session.commit()
    session.refresh(card)
    return card


def remove_deck_card(
    session: Session, deck_id: int, card_name: str, quantity: int | None = None
) -> bool:
    card = get_deck_card(session, deck_id, card_name)
    if not card:
        return False
    # quantity=None means "remove the whole stack" — the common case in
    # singleton formats where there's only ever 1 copy.  An explicit
    # quantity less than the current stack just shrinks it (e.g. cutting
    # 2 of 34 Swamps) instead of deleting the row outright.
    if quantity is not None and quantity < card.quantity:
        card.quantity -= quantity
        session.add(card)
        session.commit()
        return True
    session.delete(card)
    session.commit()
    return True


def get_conversation_by_deck_id(session: Session, deck_id: int) -> Conversation | None:
    statement = (
        select(Conversation)
        .where(Conversation.deck_id == deck_id)
        .order_by(Conversation.updated_at.desc())
    )
    return session.exec(statement).first()


def list_conversations_by_deck_id(session: Session, deck_id: int) -> list[Conversation]:
    statement = (
        select(Conversation)
        .where(Conversation.deck_id == deck_id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(session.exec(statement))


def deck_snapshot(session: Session, deck_id: int) -> dict:
    deck = get_deck(session, deck_id)
    if not deck:
        raise ValueError(f"Deck {deck_id} not found")
    cards = list_deck_cards(session, deck_id)
    linked = get_conversation_by_deck_id(session, deck_id)

    # Display category is DERIVED from Scryfall community tags, not stored or
    # hand-set — the same tags that drive scoring, so display and scoring can
    # never disagree. Commander(s) are a deck designation, not a functional
    # tag, so they override to "Commander".
    from app.tools.deck_tools import category_for_card
    commander_names = {
        n.lower() for n in (deck.commander, deck.partner_commander) if n
    }

    return {
        "id": deck.id,
        "name": deck.name,
        "commander": deck.commander,
        "partner_commander": deck.partner_commander,
        "notes": deck.notes,
        "power_level": deck.power_level,
        "format": deck.format,
        "conversation_id": linked.id if linked else None,
        "cards": [
            {
                "name": c.card_name,
                "quantity": c.quantity,
                "category": (
                    "Commander" if c.card_name.lower() in commander_names
                    else category_for_card(c.type_line, c.oracle_text or "", c.oracle_id, c.tags)
                ),
                "mana_value": c.mana_value,
                "color_identity": c.color_identity,
                "type_line": c.type_line,
                "oracle_text": c.oracle_text,
                "tags": c.tags or [],
                "notes": c.notes,
            }
            for c in cards
        ],
    }


# --- Proposals --------------------------------------------------------------


def get_proposal(session: Session, proposal_id: int) -> DeckProposal | None:
    return session.get(DeckProposal, proposal_id)


def list_proposals(session: Session, conversation_id: int) -> list[DeckProposal]:
    statement = (
        select(DeckProposal)
        .where(DeckProposal.conversation_id == conversation_id)
        .order_by(DeckProposal.created_at.asc())
    )
    return list(session.exec(statement))


def anchor_proposals_to_message(
    session: Session, proposal_ids: list[int], message_id: int
) -> None:
    """Point a turn's freshly-created proposals at the assistant message whose
    tool call produced them, so the UI can render each batch inline at the spot
    it happened instead of pooling them all at the bottom of the transcript.
    Proposals are created mid-tool-loop, before that message row exists, so the
    anchor is back-filled once the message has been persisted."""
    if not proposal_ids:
        return
    for pid in proposal_ids:
        proposal = session.get(DeckProposal, pid)
        if proposal is not None:
            proposal.message_id = message_id
            session.add(proposal)
    session.commit()


def list_proposals_by_deck_id(session: Session, deck_id: int) -> list[DeckProposal]:
    statement = (
        select(DeckProposal)
        .where(DeckProposal.deck_id == deck_id)
        .order_by(DeckProposal.created_at.asc())
    )
    return list(session.exec(statement))


def apply_proposal(session: Session, proposal_id: int) -> dict | None:
    """Execute a pending proposal and return the updated deck snapshot."""
    from app.tools import deck_tools

    proposal = get_proposal(session, proposal_id)
    if not proposal or proposal.status != "pending":
        return None

    if proposal.action == "add":
        deck_tools.deck_add_card(
            session,
            deck_id=proposal.deck_id,
            card_name=proposal.card_name,
            qty=proposal.quantity,
            category=proposal.category,
        )
    elif proposal.action == "remove":
        deck_tools.deck_remove_card(
            session,
            deck_id=proposal.deck_id,
            card_name=proposal.card_name,
            quantity=proposal.quantity,
        )
    elif proposal.action == "set_commander":
        deck_tools.deck_set_commander(
            session, deck_id=proposal.deck_id, commander_name=proposal.commander_name
        )

    proposal.status = "approved"
    session.add(proposal)
    session.commit()
    return deck_snapshot(session, proposal.deck_id)


def deny_proposal(session: Session, proposal_id: int) -> bool:
    proposal = get_proposal(session, proposal_id)
    if not proposal or proposal.status != "pending":
        return False
    proposal.status = "denied"
    session.add(proposal)
    session.commit()
    return True


# --- User preferences -------------------------------------------------------


def get_or_create_preferences(session: Session) -> UserPreferences:
    prefs = session.get(UserPreferences, 1)
    if prefs is None:
        prefs = UserPreferences(id=1)
        session.add(prefs)
        session.commit()
        session.refresh(prefs)
    return prefs


def update_preferences(
    session: Session,
    preferred_bracket: str | None = None,
    preferred_power: str | None = None,
    budget: str | None = None,
    rule0_notes: str | None = None,
    build_preferences: str | None = None,
) -> UserPreferences:
    prefs = get_or_create_preferences(session)
    if preferred_bracket is not None:
        prefs.preferred_bracket = preferred_bracket
    if preferred_power is not None:
        prefs.preferred_power = preferred_power
    if budget is not None:
        prefs.budget = budget
    if rule0_notes is not None:
        prefs.rule0_notes = rule0_notes
    if build_preferences is not None:
        prefs.build_preferences = build_preferences
    session.add(prefs)
    session.commit()
    session.refresh(prefs)
    return prefs
