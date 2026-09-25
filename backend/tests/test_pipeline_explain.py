import json

from app.pipeline import explain
from app.pipeline.selection import Pick, Selection
from tests.test_pipeline_jev import _card


def _selection():
    return Selection(picks=[Pick("A", "fact a"), Pick("B", "fact b")], raw={"backend": "jev"})


def test_explanation_rewrites_reasons_without_changing_the_picks():
    raw = json.dumps({
        "summary": "Two pieces.",
        "picks": [{"name": "b", "reason": "B works with the commander."},
                  {"name": "Intruder", "reason": "should be ignored"}],
        "cuts": [{"name": "Old Card", "reason": "weakest"}, {"name": "A", "reason": "no"}],
    })
    result = explain.apply_explanation(_selection(), raw)
    assert [p.name for p in result.picks] == ["A", "B"]
    assert result.picks[0].reason == "fact a"
    assert result.picks[1].reason == "B works with the commander."
    assert [c.name for c in result.cuts] == ["Old Card"]
    assert result.summary == "Two pieces."


def test_explain_calls_the_model_with_thinking_off():
    class Provider:
        def complete_json(self, system, user, **kwargs):
            self.kwargs, self.user = kwargs, user
            return json.dumps({"summary": "s", "picks": [], "cuts": []})

    provider = Provider()
    explain.explain(provider, _selection(), [_card("A"), _card("B")], "ramp")
    assert provider.kwargs["thinking"] is False
    assert "A does a thing." in provider.user


def test_run_selection_keeps_fact_reasons_when_the_explainer_fails():
    from app.pipeline import service
    from tests.test_pipeline_jev import FakeJev

    class Broken:
        def complete_json(self, *a, **k):
            raise RuntimeError("down")

    prepared = service.PreparedPool(
        snapshot={}, spec=None, gathered=None, local_pool=[], stage1_skipped=True,
        pool=[], shaped=[_card("A")], deck_context="", timings={},
    )
    selection, used = service.run_selection(
        prepared, "x", Broken(), backend="jev", model="m",
        jev_client=FakeJev({"A": (3.0, 0.9)}), jev_mode="verdict", jev_samples=1, jev_explain=True,
    )
    assert used == "jev"
    assert selection.picks[0].reason.startswith("Jev:")
