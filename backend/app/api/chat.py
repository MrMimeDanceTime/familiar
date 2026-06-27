from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.chat.engine import run_chat_turn
from app.db import repository as repo
from app.db.session import get_engine
from app.llm.factory import get_provider

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatIn(BaseModel):
    conversation_id: int | str  # "new" or an existing conversation id
    message: str


@router.post("")
def post_chat(body: ChatIn):
    session = Session(get_engine())

    if body.conversation_id == "new":
        conversation = repo.create_conversation(session)
        deck = repo.create_deck(session)
        repo.set_conversation_deck(session, conversation.id, deck.id)
        conversation_id = conversation.id
        deck_id = deck.id
    else:
        conversation_id = int(body.conversation_id)
        conversation = repo.get_conversation(session, conversation_id)
        if not conversation:
            session.close()
            raise HTTPException(status_code=404, detail="Conversation not found")
        deck_id = conversation.deck_id

    provider = get_provider()

    def event_stream():
        try:
            for event in run_chat_turn(
                session, provider, conversation_id, body.message, deck_id
            ):
                yield event
        finally:
            session.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
