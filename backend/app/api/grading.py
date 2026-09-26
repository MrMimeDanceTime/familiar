"""Grade real suggestion batches, pick by pick.

The offline eval measures agreement with the cards the player already ran, and
counts every good card they never chose as a miss. This measures what it
cannot: of the picks a batch actually makes, how many are bad.

Each batch is generated through the real pipeline in preview mode (no
proposals are written) by the Jev selector or the LLM selector, chosen at
random unless asked for. The selector is withheld from the response until
every pick in the batch is graded, so grading stays blind.
"""

from __future__ import annotations

import random
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db.models import GradingBatch, PickGrade
from app.db.session import get_engine

router = APIRouter(prefix="/api/grading", tags=["grading"])

GRADES = ("good", "fine", "bad")
DEFAULT_REQUESTS = [
    "more ramp",
    "more card draw",
    "more removal",
    "a board wipe or two",
    "cards that advance my deck's gameplan",
]


class BatchIn(BaseModel):
    deck_id: int
    intent: str
    backend: Literal["jev", "llm", "random"] = "random"


class GradeIn(BaseModel):
    card_name: str
    grade: Literal["good", "fine", "bad"]
    note: str | None = None


def _edhrec_page(commander: str | None) -> set[str]:
    if not commander:
        return set()
    try:
        from app.pipeline import edhrec_source
        from app.tools.edhrec_client import get_edhrec_client

        recs = get_edhrec_client().commander_recs(commander)
        return {c.name.lower() for c in edhrec_source.collect(recs, per_list_cap=10_000)}
    except Exception:  # noqa: BLE001 - novelty is a label, not a requirement
        return set()


def _render(batch: GradingBatch, grades: list[PickGrade], deck_name: str | None) -> dict[str, Any]:
    by_card = {g.card_name: g for g in grades}
    picks = []
    for pick in batch.picks or []:
        grade = by_card.get(pick["name"])
        picks.append({**pick, "grade": grade.grade if grade else None,
                      "note": grade.note if grade else None})
    complete = bool(picks) and all(p["grade"] for p in picks)
    return {
        "id": batch.id, "deck_id": batch.deck_id, "deck_name": deck_name,
        "intent": batch.intent, "picks": picks, "complete": complete,
        # Withheld until graded, so the grader cannot lean on it.
        "backend": batch.backend if complete else None,
        "seconds": batch.seconds, "created_at": batch.created_at.isoformat(),
    }


def _deck_names(session: Session) -> dict[int, str]:
    from app.db import repository as repo

    return {d.id: d.name for d in repo.list_decks(session)}


@router.get("/requests")
def default_requests() -> dict[str, list[str]]:
    return {"requests": DEFAULT_REQUESTS}


@router.post("/batches")
def create_batch(body: BatchIn) -> dict[str, Any]:
    from app.cards import store as card_store
    from app.db import repository as repo
    from app.llm.factory import get_provider
    from app.pipeline.service import build_suggestions

    backend = body.backend if body.backend != "random" else random.choice(("jev", "llm"))
    with Session(get_engine()) as session:
        deck = repo.get_deck(session, body.deck_id)
        if deck is None:
            raise HTTPException(404, "deck not found")
        start = time.monotonic()
        result = build_suggestions(
            session, body.deck_id, body.intent, get_provider(), select_backend=backend,
        )
        seconds = round(time.monotonic() - start, 1)
        used = (result.debug or {}).get("select_backend", backend)
        picks = result.selection.picks if result.selection else []
        cards = card_store.by_names([p.name for p in picks])
        page = _edhrec_page(deck.commander)
        batch = GradingBatch(
            deck_id=body.deck_id, intent=body.intent, backend=used, seconds=seconds,
            picks=[{
                "name": p.name, "reason": p.reason,
                "mana_cost": (cards.get(p.name.lower()) or {}).get("mana_cost"),
                "type_line": (cards.get(p.name.lower()) or {}).get("type_line"),
                "oracle_text": (cards.get(p.name.lower()) or {}).get("oracle_text"),
                "off_page": bool(page) and p.name.lower() not in page,
            } for p in picks],
        )
        session.add(batch)
        session.commit()
        session.refresh(batch)
        return _render(batch, [], deck.name)


@router.get("/batches")
def list_batches() -> list[dict[str, Any]]:
    with Session(get_engine()) as session:
        names = _deck_names(session)
        batches = session.exec(select(GradingBatch).order_by(GradingBatch.id.desc())).all()
        grades = session.exec(select(PickGrade)).all()
        by_batch: dict[int, list[PickGrade]] = {}
        for g in grades:
            by_batch.setdefault(g.batch_id, []).append(g)
        return [_render(b, by_batch.get(b.id, []), names.get(b.deck_id)) for b in batches]


@router.put("/batches/{batch_id}/grades")
def grade_pick(batch_id: int, body: GradeIn) -> dict[str, Any]:
    with Session(get_engine()) as session:
        batch = session.get(GradingBatch, batch_id)
        if batch is None:
            raise HTTPException(404, "batch not found")
        if body.card_name not in {p["name"] for p in batch.picks or []}:
            raise HTTPException(400, "card is not in this batch")
        existing = session.exec(select(PickGrade).where(
            PickGrade.batch_id == batch_id, PickGrade.card_name == body.card_name,
        )).first()
        if existing:
            existing.grade, existing.note = body.grade, body.note
            session.add(existing)
        else:
            session.add(PickGrade(batch_id=batch_id, card_name=body.card_name,
                                  grade=body.grade, note=body.note))
        session.commit()
        grades = session.exec(select(PickGrade).where(PickGrade.batch_id == batch_id)).all()
        return _render(batch, list(grades), _deck_names(session).get(batch.deck_id))


@router.delete("/batches/{batch_id}")
def delete_batch(batch_id: int) -> dict[str, bool]:
    with Session(get_engine()) as session:
        batch = session.get(GradingBatch, batch_id)
        if batch is None:
            raise HTTPException(404, "batch not found")
        for g in session.exec(select(PickGrade).where(PickGrade.batch_id == batch_id)).all():
            session.delete(g)
        session.delete(batch)
        session.commit()
    return {"ok": True}


@router.get("/summary")
def summary() -> dict[str, Any]:
    """Grade shares per selector, over completed batches only (a half-graded
    batch would weight the picks graded first)."""
    with Session(get_engine()) as session:
        batches = {b.id: b for b in session.exec(select(GradingBatch)).all()}
        grades = session.exec(select(PickGrade)).all()
    by_batch: dict[int, list[str]] = {}
    for g in grades:
        by_batch.setdefault(g.batch_id, []).append(g.grade)
    out: dict[str, Any] = {}
    for batch_id, batch in batches.items():
        got = by_batch.get(batch_id, [])
        if not batch.picks or len(got) < len(batch.picks):
            continue
        row = out.setdefault(batch.backend, {"batches": 0, "picks": 0, **{k: 0 for k in GRADES},
                                             "seconds": 0.0})
        row["batches"] += 1
        row["picks"] += len(got)
        row["seconds"] += batch.seconds or 0.0
        for grade in got:
            row[grade] += 1
    for row in out.values():
        row["bad_rate"] = row["bad"] / row["picks"] if row["picks"] else None
        row["good_rate"] = row["good"] / row["picks"] if row["picks"] else None
        row["mean_seconds"] = row["seconds"] / row["batches"] if row["batches"] else None
    return {"by_backend": out}
