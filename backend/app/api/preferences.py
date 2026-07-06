from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import Session

from app.db import repository as repo
from app.db.session import get_engine

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


class PreferencesOut(BaseModel):
    preferred_bracket: str | None = None
    preferred_power: str | None = None
    budget: str | None = None
    rule0_notes: str | None = None
    build_preferences: str | None = None


class PreferencesIn(BaseModel):
    preferred_bracket: str | None = None
    preferred_power: str | None = None
    budget: str | None = None
    rule0_notes: str | None = None
    build_preferences: str | None = None


@router.get("")
def get_preferences():
    with Session(get_engine()) as session:
        prefs = repo.get_or_create_preferences(session)
    return PreferencesOut(
        preferred_bracket=prefs.preferred_bracket,
        preferred_power=prefs.preferred_power,
        budget=prefs.budget,
        rule0_notes=prefs.rule0_notes,
        build_preferences=prefs.build_preferences,
    )


@router.put("")
def put_preferences(body: PreferencesIn):
    with Session(get_engine()) as session:
        prefs = repo.update_preferences(
            session,
            preferred_bracket=body.preferred_bracket,
            preferred_power=body.preferred_power,
            budget=body.budget,
            rule0_notes=body.rule0_notes,
            build_preferences=body.build_preferences,
        )
    return PreferencesOut(
        preferred_bracket=prefs.preferred_bracket,
        preferred_power=prefs.preferred_power,
        budget=prefs.budget,
        rule0_notes=prefs.rule0_notes,
        build_preferences=prefs.build_preferences,
    )
