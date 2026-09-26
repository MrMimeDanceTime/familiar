import json

import pytest

from app.pipeline import gameplan, local_retrieval
from app.pipeline.service import deck_brief
from app.pipeline.spec import build_prompt


def test_parse_keeps_the_plan_and_dedupes_themes():
    raw = json.dumps({"plan": "Win by wither.", "themes": ["wither", "Wither", "-1/-1 counter", "", 3]})
    assert gameplan.parse(raw) == {"plan": "Win by wither.", "themes": ["wither", "-1/-1 counter"]}


def test_parse_rejects_a_draft_without_a_plan():
    with pytest.raises(ValueError):
        gameplan.parse(json.dumps({"plan": " ", "themes": ["x"]}))


def test_prompt_carries_the_commander_text_and_the_players_notes():
    snapshot = {"commander": "Cmd", "notes": "go wide",
                "cards": [{"name": "Cmd", "oracle_text": "Creatures you control have wither."},
                          {"name": "Other", "category": "Creature"}]}
    _, user = gameplan.build_prompt(snapshot)
    assert "Creatures you control have wither." in user
    assert "Other" in user and "go wide" in user


def test_deck_brief_and_stage_one_see_the_commander_and_plan():
    snapshot = {"commander": "Cmd", "plan_notes": "Shrink boards.", "themes": ["wither"],
                "cards": [{"name": "Cmd", "oracle_text": "Wither."}]}
    brief = deck_brief(snapshot)
    assert "Cmd | Wither." in brief and "Shrink boards." in brief and "wither" in brief
    _, user = build_prompt("what fits my deck", frozenset("B"), deck_brief=brief)
    assert user.startswith("Commander: Cmd")


class _Store:
    def __init__(self):
        self.text_queries, self.tag_queries = [], []

    def cards_matching_slug_rules(self, *a, **k):
        return []

    def search_text(self, query, *, limit=50, identity=None):
        self.text_queries.append(query)
        return [{"name": f"Hit {query}", "oracle_id": f"o-{query}", "color_identity": ["B"]}]

    def cards_with_any_tag(self, slugs, *, limit=200):
        self.tag_queries.append(slugs)
        return [{"name": "Tagged", "oracle_id": "o-tag", "color_identity": ["B"]}]


def test_a_roleless_request_searches_the_plan_themes_and_commander_tags():
    store = _Store()
    pool = local_retrieval.retrieve(
        "what fits my deck", frozenset("B"), store=store,
        themes=["wither", "proliferate"], commander_tags={"synergy-wither"},
    )
    names = {c["name"] for c in pool}
    assert {"Hit wither", "Hit proliferate", "Tagged"} <= names
    assert store.tag_queries == [["synergy-wither"]]


def test_a_role_request_does_not_widen_into_the_theme():
    store = _Store()
    local_retrieval.retrieve("more ramp", frozenset("B"), store=store, themes=["wither"])
    assert "wither" not in store.text_queries


def test_background_draft_fills_an_empty_plan_and_never_replaces_one(tmp_path, monkeypatch):
    from sqlmodel import Session, SQLModel, create_engine

    import app.db.session as db_session
    import app.llm.factory as factory
    from app.db import repository as repo
    from app.tools import deck_tools

    engine = create_engine(f"sqlite:///{tmp_path / 'g.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_session, "get_engine", lambda: engine)

    class Provider:
        def complete_json(self, system, user, **kwargs):
            return json.dumps({"plan": "Win with wither.", "themes": ["wither"]})

    monkeypatch.setattr(factory, "get_provider", lambda: Provider())
    monkeypatch.setattr(deck_tools, "lookup_card",
                        lambda client, name: {"name": name, "oracle_text": "Wither."})
    with Session(engine) as s:
        empty = repo.create_deck(s, name="A").id
        planned = repo.create_deck(s, name="B").id
        repo.update_deck(s, planned, plan_notes="Mine.", themes=["mine"])

    gameplan._draft_and_store(empty, "Massacre Girl, Known Killer")
    gameplan._draft_and_store(planned, "Massacre Girl, Known Killer")
    with Session(engine) as s:
        assert repo.deck_snapshot(s, empty)["plan_notes"] == "Win with wither."
        assert repo.deck_snapshot(s, planned)["plan_notes"] == "Mine."


def test_suggest_cards_accepts_goal_as_intent(monkeypatch):
    from app.tools import dispatch as d

    seen = {}
    monkeypatch.setitem(d.PROVIDER_SESSION_TOOLS, "suggest_cards",
                        lambda session, provider, **kw: seen.update(kw) or {"ok": True})
    result = d.dispatch("suggest_cards", {"goal": "ramp", "deck_id": 1}, session=None, provider=object())
    assert result.ok and seen["intent"] == "ramp" and "goal" not in seen


def test_pending_adds_count_as_in_the_deck():
    from app.pipeline.shaping import DeckContext

    ctx = DeckContext.from_snapshot(
        {"cards": [], "pending_proposals": {"proposals": [
            {"action": "add", "card_name": "Arcane Signet"},
            {"action": "set_commander", "card_name": None},
        ]}},
        frozenset("BR"),
    )
    assert "arcane signet" in ctx.card_names_lower
