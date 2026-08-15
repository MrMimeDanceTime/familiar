from app.pipeline.candidates import gather_candidates, gather_candidates_detailed
from app.pipeline.spec import QuerySpec
from app.tools.edhrec_client import EdhrecNotFoundError


class FakeScryfall:
    """Maps query -> list of pipeline-projected cards."""

    def __init__(self, results_by_query):
        self._results = results_by_query
        self.queries_run = []

    def search_pipeline(self, query, limit=50):
        self.queries_run.append(query)
        return list(self._results.get(query, []))


class FakeEdhrec:
    def __init__(self, recs=None, exc=None):
        self._recs = recs
        self._exc = exc

    def commander_recs(self, commander_name):
        if self._exc:
            raise self._exc
        return self._recs


def _card(name, oid, rank):
    return {"name": name, "oracle_id": oid, "edhrec_rank": rank, "color_identity": ["B"]}


def test_merges_and_dedupes_across_queries():
    sf = FakeScryfall({
        "q1": [_card("A", "a", 10), _card("B", "b", 20)],
        "q2": [_card("B", "b", 20), _card("C", "c", 5)],  # B duplicated
    })
    spec = QuerySpec(queries=["q1", "q2"])
    pool = gather_candidates(spec, None, scryfall=sf, edhrec=FakeEdhrec(recs=None))
    names = [c["name"] for c in pool]
    assert names.count("B") == 1
    assert set(names) == {"A", "B", "C"}


def test_pool_sorted_by_edhrec_rank():
    sf = FakeScryfall({"q1": [_card("A", "a", 10), _card("B", "b", 20), _card("C", "c", 5)]})
    pool = gather_candidates(QuerySpec(queries=["q1"]), None, scryfall=sf, edhrec=FakeEdhrec())
    assert [c["name"] for c in pool] == ["C", "A", "B"]


def test_unranked_cards_sort_last():
    sf = FakeScryfall({"q1": [_card("Ranked", "r", 50), _card("Unranked", "u", None)]})
    pool = gather_candidates(QuerySpec(queries=["q1"]), None, scryfall=sf, edhrec=FakeEdhrec())
    assert [c["name"] for c in pool] == ["Ranked", "Unranked"]


def test_cap_applied_after_sort():
    sf = FakeScryfall({"q1": [_card(f"C{i}", f"c{i}", i) for i in range(200)]})
    pool = gather_candidates(QuerySpec(queries=["q1"]), None, scryfall=sf,
                             edhrec=FakeEdhrec(), cap=10)
    assert len(pool) == 10
    assert [c["edhrec_rank"] for c in pool] == list(range(10))


def test_edhrec_synergy_annotated_when_available():
    sf = FakeScryfall({"q1": [_card("Sol Ring", "sr", 1)]})
    recs = {
        "commander": "Cmd",
        "categories": {
            "ramp": {"header": "Ramp", "cards": [
                {"name": "Sol Ring", "synergy": 0.42, "inclusion": 0.9},
            ]},
        },
    }
    pool = gather_candidates(QuerySpec(queries=["q1"]), "Cmd", scryfall=sf,
                             edhrec=FakeEdhrec(recs=recs))
    assert pool[0]["edhrec"] == {"synergy": 0.42, "inclusion": 0.9, "category": "Ramp"}


def test_edhrec_annotation_is_none_when_card_absent():
    sf = FakeScryfall({"q1": [_card("Obscure Card", "oc", 1)]})
    recs = {"commander": "Cmd", "categories": {}}
    pool = gather_candidates(QuerySpec(queries=["q1"]), "Cmd", scryfall=sf,
                             edhrec=FakeEdhrec(recs=recs))
    assert pool[0]["edhrec"] is None


def test_edhrec_failure_degrades_to_no_synergy():
    sf = FakeScryfall({"q1": [_card("A", "a", 1)]})
    pool = gather_candidates(
        QuerySpec(queries=["q1"]), "Missing Cmdr", scryfall=sf,
        edhrec=FakeEdhrec(exc=EdhrecNotFoundError("no page")),
    )
    # retrieval still succeeds; synergy is simply absent
    assert pool[0]["edhrec"] is None
    assert pool[0]["name"] == "A"


def test_no_commander_skips_edhrec_entirely():
    sf = FakeScryfall({"q1": [_card("A", "a", 1)]})

    class Boom:
        def commander_recs(self, name):
            raise AssertionError("should not be called without a commander")

    pool = gather_candidates(QuerySpec(queries=["q1"]), None, scryfall=sf, edhrec=Boom())
    assert pool[0]["edhrec"] is None


# ── Broadening integration ──────────────────────────────────────────────
#
# Stage 1 emits queries blind — it never sees a result count — so an over-tight
# query used to contribute nothing with no way to recover. Stage 2 now retries
# with one constraint relaxed. These pin that it happens, that it is reported,
# and that it never relaxes legality.


def test_underfilled_query_is_broadened():
    sf = FakeScryfall({
        # The original returns one card; the relaxed form (mv dropped) returns more.
        "id<=br otag:ramp mv<=1 f:commander": [_card("A", "a", 10)],
        "id<=br otag:ramp f:commander": [_card(f"C{i}", f"c{i}", i) for i in range(12)],
    })
    spec = QuerySpec(queries=["id<=br otag:ramp mv<=1 f:commander"])
    result = gather_candidates_detailed(spec, None, scryfall=sf, edhrec=FakeEdhrec())
    assert len(result.cards) == 12
    assert "id<=br otag:ramp mv<=1 f:commander" in result.broadened


def test_broadening_reports_the_trail():
    sf = FakeScryfall({"id<=br otag:ramp mv<=1 f:commander": []})
    spec = QuerySpec(queries=["id<=br otag:ramp mv<=1 f:commander"])
    result = gather_candidates_detailed(spec, None, scryfall=sf, edhrec=FakeEdhrec())
    trail = result.broadened["id<=br otag:ramp mv<=1 f:commander"]
    assert trail  # at least one relaxed query was attempted
    for attempted in trail:
        assert "id<=br" in attempted
        assert "f:commander" in attempted


def test_well_filled_query_is_not_broadened():
    sf = FakeScryfall({"q1": [_card(f"C{i}", f"c{i}", i) for i in range(20)]})
    result = gather_candidates_detailed(
        QuerySpec(queries=["q1"]), None, scryfall=sf, edhrec=FakeEdhrec()
    )
    assert result.broadened == {}
    assert sf.queries_run == ["q1"]


def test_broadening_can_be_disabled():
    sf = FakeScryfall({"id<=br otag:ramp mv<=1 f:commander": [_card("A", "a", 1)]})
    spec = QuerySpec(queries=["id<=br otag:ramp mv<=1 f:commander"])
    result = gather_candidates_detailed(
        spec, None, scryfall=sf, edhrec=FakeEdhrec(), broaden=False
    )
    assert sf.queries_run == ["id<=br otag:ramp mv<=1 f:commander"]
    assert result.broadened == {}


def test_gather_candidates_wrapper_still_returns_a_list():
    """The original signature is load-bearing for existing callers."""
    sf = FakeScryfall({"q1": [_card("A", "a", 1)]})
    pool = gather_candidates(QuerySpec(queries=["q1"]), None, scryfall=sf,
                             edhrec=FakeEdhrec())
    assert isinstance(pool, list)
    assert pool[0]["name"] == "A"


# ── EDHREC as a candidate source ────────────────────────────────────────


def _edhrec_page(**lists):
    return {
        "commander": "Cmd",
        "categories": {
            tag: {"header": tag.title(), "cards": cards}
            for tag, cards in lists.items()
        },
    }


def _view(name, synergy, num_decks, potential=20000):
    return {"name": name, "synergy": synergy, "num_decks": num_decks,
            "potential_decks": potential}


class FakeCardStore:
    def __init__(self, names):
        self._names = names

    def by_names(self, names):
        return {
            n.lower(): {
                "name": n, "oracle_id": f"oid-{n.lower()}",
                "color_identity": ["B"], "type_line": "Creature",
                "edhrec_rank": 9999,
            }
            for n in names if n in self._names
        }


def test_edhrec_contributes_candidates_no_query_matched(monkeypatch):
    """The gap this closes: a card EDHREC recommends that no Scryfall query
    returned could not previously enter the pool at all."""
    from app.pipeline import candidates as candidates_module

    monkeypatch.setattr(
        candidates_module, "card_store", FakeCardStore({"Mayhem Devil"})
    )
    sf = FakeScryfall({"q1": [_card("Unrelated", "u", 10)]})
    edh = FakeEdhrec(recs=_edhrec_page(
        highsynergycards=[_view("Mayhem Devil", 0.48, 15000)]
    ))

    result = gather_candidates_detailed(
        QuerySpec(queries=["q1"]), "Cmd", scryfall=sf, edhrec=edh,
    )
    names = [c["name"] for c in result.cards]
    assert "Mayhem Devil" in names


def test_edhrec_sourced_cards_sort_ahead_of_query_hits(monkeypatch):
    """Regression: the sort keyed off the presence of an `edhrec` dict, but
    query hits carry one too (from the synergy map), so both groups landed in
    the same bucket and generic staples outranked the high-synergy picks."""
    from app.pipeline import candidates as candidates_module

    monkeypatch.setattr(
        candidates_module, "card_store", FakeCardStore({"Mayhem Devil"})
    )
    # A query hit that IS on the commander's page, so it gets an annotation.
    sf = FakeScryfall({"q1": [_card("Sol Ring", "sol", 1)]})
    edh = FakeEdhrec(recs=_edhrec_page(
        highsynergycards=[_view("Mayhem Devil", 0.48, 15000)],
        topcards=[_view("Sol Ring", 0.02, 18000)],
    ))

    result = gather_candidates_detailed(
        QuerySpec(queries=["q1"]), "Cmd", scryfall=sf, edhrec=edh, off_meta=1.0,
    )
    assert result.cards[0]["name"] == "Mayhem Devil"


def test_internal_ordering_key_does_not_leak(monkeypatch):
    from app.pipeline import candidates as candidates_module

    monkeypatch.setattr(
        candidates_module, "card_store", FakeCardStore({"Mayhem Devil"})
    )
    edh = FakeEdhrec(recs=_edhrec_page(
        highsynergycards=[_view("Mayhem Devil", 0.48, 15000)]
    ))
    result = gather_candidates_detailed(
        QuerySpec(queries=["q1"]), "Cmd",
        scryfall=FakeScryfall({}), edhrec=edh,
    )
    for card in result.cards:
        assert "_edhrec_position" not in card


def test_edhrec_failure_still_yields_query_results(monkeypatch):
    """EDHREC is the best signal available but must never be a hard dependency."""
    from app.pipeline import candidates as candidates_module

    monkeypatch.setattr(candidates_module, "card_store", FakeCardStore(set()))
    sf = FakeScryfall({"q1": [_card("A", "a", 1)]})
    result = gather_candidates_detailed(
        QuerySpec(queries=["q1"]), "Cmd", scryfall=sf,
        edhrec=FakeEdhrec(exc=EdhrecNotFoundError("no page")),
    )
    assert [c["name"] for c in result.cards] == ["A"]
