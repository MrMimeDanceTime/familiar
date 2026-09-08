import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from behaviour_eval import aggregate, score_turn  # noqa: E402


def _record(**over):
    base = {
        "user_text": "suggest some ramp",
        "final_text": "Here is the ramp package: [[Sol Ring]] and [[Cultivate]].",
        "tool_calls": [
            {"name": "deck_get_current", "arguments": {}, "ok": True, "result": "{}"},
            {"name": "deck_set_plan", "arguments": {}, "ok": True, "result": "{}"},
            {"name": "suggest_cards", "arguments": {"intent": "ramp"}, "ok": True, "result": "{}"},
        ],
        "rounds": 3,
        "plan_was_set": False,
        "deck_card_names": ["Sol Ring"],
        "proposed_names": ["Cultivate"],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    base.update(over)
    return base


def test_well_behaved_turn_scores_clean():
    s = score_turn(_record())
    assert s["hand_pick_refusals"] == 0
    assert s["tool_errors"] == 0
    assert s["role_batch_via_pipeline"] is True
    assert s["plan_set_before_batch"] is True
    assert s["unbracketed_card_names"] == []


def test_hand_picked_role_batch_is_caught():
    s = score_turn(_record(tool_calls=[
        {"name": "propose_deck_changes", "ok": True, "result": "REFUSED: 3 hand-picked cards",
         "arguments": {"changes": [{"action": "add", "card_name": n} for n in "ABC"]}},
    ]))
    assert s["hand_pick_refusals"] == 1
    assert s["role_batch_via_pipeline"] is False
    assert s["plan_set_before_batch"] is False


def test_player_named_batch_is_not_a_hand_pick():
    s = score_turn(_record(user_text="add Sol Ring and Arcane Signet", tool_calls=[
        {"name": "propose_deck_changes", "ok": True, "result": "{}",
         "arguments": {"changes": [{"action": "add", "card_name": "Sol Ring", "player_named": True}]}},
    ]))
    assert s["role_batch_via_pipeline"] is None


def test_unbracketed_known_names_are_listed():
    s = score_turn(_record(final_text="Sol Ring is great, as is [[Cultivate]]."))
    assert s["unbracketed_card_names"] == ["sol ring"]


def test_aggregate_rates_skip_not_applicable_turns():
    scores = [score_turn(_record()), score_turn(_record(user_text="what is a mulligan?", tool_calls=[]))]
    summary = aggregate(scores)
    assert summary["turns"] == 2
    assert summary["role_batch_via_pipeline_rate"] == 1.0
    assert summary["prompt_tokens"] == 200


def test_ungrounded_mentions_count_cards_absent_from_every_tool_result():
    score = score_turn({
        "user_text": "what should I add?",
        "final_text": "[[Krosan Grip]] handles it; [[Sol Ring]] is already in.",
        "tool_calls": [
            {"name": "search_card_index", "ok": True,
             "result": "Sol Ring · {1} · Artifact\n  text: {T}: Add {C}{C}."},
        ],
    })
    assert score["ungrounded_card_mentions"] == ["krosan grip"]
    assert aggregate([score])["ungrounded_mentions_per_turn"] == 1.0
