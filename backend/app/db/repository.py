from datetime import datetime, timezone

from sqlmodel import Session, select

from app.db.models import Conversation, Deck, DeckCard, Message


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
) -> Deck:
    deck = Deck(name=name, commander=commander)
    session.add(deck)
    session.commit()
    session.refresh(deck)
    return deck


def get_deck(session: Session, deck_id: int) -> Deck | None:
    return session.get(Deck, deck_id)


def list_decks(session: Session) -> list[Deck]:
    statement = select(Deck).order_by(Deck.updated_at.desc())
    return list(session.exec(statement))


def update_deck(
    session: Session,
    deck_id: int,
    name: str | None = None,
    commander: str | None = None,
    partner_commander: str | None = None,
    notes: str | None = None,
    power_level: str | None = None,
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
    quantity: int = 1,
    category: str | None = None,
    mana_value: float | None = None,
    color_identity: str | None = None,
    notes: str | None = None,
) -> DeckCard:
    existing = get_deck_card(session, deck_id, card_name)
    if existing:
        existing.quantity = quantity
        existing.category = category if category is not None else existing.category
        existing.mana_value = mana_value if mana_value is not None else existing.mana_value
        existing.color_identity = color_identity if color_identity is not None else existing.color_identity
        existing.notes = notes if notes is not None else existing.notes
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    card = DeckCard(
        deck_id=deck_id,
        card_name=card_name,
        quantity=quantity,
        category=category,
        mana_value=mana_value,
        color_identity=color_identity,
        notes=notes,
    )
    session.add(card)
    session.commit()
    session.refresh(card)
    return card


def remove_deck_card(session: Session, deck_id: int, card_name: str) -> bool:
    card = get_deck_card(session, deck_id, card_name)
    if not card:
        return False
    session.delete(card)
    session.commit()
    return True


def deck_snapshot(session: Session, deck_id: int) -> dict:
    deck = get_deck(session, deck_id)
    if not deck:
        raise ValueError(f"Deck {deck_id} not found")
    cards = list_deck_cards(session, deck_id)
    return {
        "id": deck.id,
        "name": deck.name,
        "commander": deck.commander,
        "partner_commander": deck.partner_commander,
        "notes": deck.notes,
        "power_level": deck.power_level,
        "cards": [
            {
                "name": c.card_name,
                "quantity": c.quantity,
                "category": c.category,
                "mana_value": c.mana_value,
                "color_identity": c.color_identity,
                "notes": c.notes,
            }
            for c in cards
        ],
    }
