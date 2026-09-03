from app.pipeline import local_retrieval, roles


def test_intent_roles_match_whole_phrases():
    assert local_retrieval.intent_roles("some cheap ramp under 3 mana") == {roles.RAMP}
    assert local_retrieval.intent_roles("board wipes and spot removal") == {roles.BOARD_WIPE, roles.SPOT_REMOVAL}
    assert local_retrieval.intent_roles("cards that care about rooms") == set()


def test_counters_the_noun_is_not_a_counterspell():
    assert roles.COUNTERSPELL not in local_retrieval.intent_roles("+1/+1 counters synergy")
    assert roles.COUNTERSPELL in local_retrieval.intent_roles("cheap counters for the stack")


def test_intent_terms_drop_filler():
    assert local_retrieval.intent_terms("some cheap ramp for my deck that fits the commander") == ["ramp"]
    assert local_retrieval.intent_terms("cards that care about rooms") == ["care", "about", "rooms"]


class FakeStore:
    def __init__(self):
        self.calls = []

    def cards_matching_slug_rules(self, exact, prefixes, suffixes, *, limit=200):
        self.calls.append(("rules", sorted(exact), prefixes, suffixes))
        return [
            {"name": "Rampant Growth", "oracle_id": "o-rg", "color_identity": ["G"]},
            {"name": "Sol Ring", "oracle_id": "o-sr", "color_identity": []},
            {"name": "Off Colour Rock", "oracle_id": "o-oc", "color_identity": ["U"]},
        ]

    def search_text(self, query, *, limit=50, identity=None):
        self.calls.append(("text", query, identity))
        return [{"name": "Sol Ring", "oracle_id": "o-sr", "color_identity": []},
                {"name": "Text Hit", "oracle_id": "o-th", "color_identity": ["G"]}]


def test_retrieve_dedupes_and_drops_off_identity_cards():
    store = FakeStore()
    pool = local_retrieval.retrieve("green ramp", frozenset({"G"}), store=store)
    assert [c["name"] for c in pool] == ["Rampant Growth", "Sol Ring", "Text Hit"]
    kinds = [c[0] for c in store.calls]
    assert kinds == ["rules", "text"]


def test_retrieve_with_no_role_and_no_terms_returns_nothing():
    store = FakeStore()
    assert local_retrieval.retrieve("a", frozenset({"G"}), store=store) == []
    assert store.calls == []
