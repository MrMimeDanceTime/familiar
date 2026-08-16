from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.db import repository as repo
from app.db.session import get_engine

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationOut(BaseModel):
    id: int
    title: str
    deck_id: int | None
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: int
    role: str
    text_content: str | None
    tool_calls: list | None
    tool_results: list | None
    sequence: int
    created_at: str


def _conversation_out(c) -> ConversationOut:
    return ConversationOut(
        id=c.id,
        title=c.title,
        deck_id=c.deck_id,
        created_at=c.created_at.isoformat(),
        updated_at=c.updated_at.isoformat(),
    )


@router.get("", response_model=list[ConversationOut])
def list_conversations():
    with Session(get_engine()) as session:
        return [_conversation_out(c) for c in repo.list_conversations(session)]


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: int):
    with Session(get_engine()) as session:
        repo.delete_conversation(session, conversation_id)
        return {"ok": True}


@router.get("/{conversation_id}")
def get_conversation(conversation_id: int):
    with Session(get_engine()) as session:
        conversation = repo.get_conversation(session, conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = repo.list_messages(session, conversation_id)
        proposals_raw = repo.list_proposals(session, conversation_id)
        return {
            "conversation": _conversation_out(conversation),
            "messages": [
                MessageOut(
                    id=m.id,
                    role=m.role,
                    text_content=m.text_content,
                    tool_calls=m.tool_calls,
                    tool_results=m.tool_results,
                    sequence=m.sequence,
                    created_at=m.created_at.isoformat(),
                )
                for m in messages
            ],
            "proposals": [
                {
                    "id": p.id,
                    "deck_id": p.deck_id,
                    "message_id": p.message_id,
                    "status": p.status,
                    "action": p.action,
                    "card_name": p.card_name,
                    "quantity": p.quantity,
                    "category": p.category,
                    "commander_name": p.commander_name,
                    "reasoning": p.reasoning,
                    "scores": p.scores,
                    "denial_reason": p.denial_reason,
                    "created_at": p.created_at.isoformat(),
                }
                for p in proposals_raw
            ],
        }
