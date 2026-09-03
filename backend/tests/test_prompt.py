"""The prompt is an interface, and its phase blocks follow the deck."""

from app.chat.prompt import NOTECARD_FROM_CARDS, build_system_prompt


def test_no_state_includes_every_block():
    text = build_system_prompt("commander")
    for tag in ("<identity>", "<format_rules>", "<power_guide>", "<tools>", "<grounding>",
                "<the_plan>", "<batches>", "<deck_complete>", "<pacing>", "<posture>"):
        assert tag in text, tag
    assert "No plan is recorded yet" in text


def test_plan_block_shrinks_once_the_plan_is_set():
    unset = build_system_prompt("commander", plan_is_set=False, total_cards=10)
    set_ = build_system_prompt("commander", plan_is_set=True, total_cards=10)
    assert "No plan is recorded yet" in unset
    assert "The deck has a plan" in set_
    assert len(set_) < len(unset)


def test_notecard_only_near_completion():
    early = build_system_prompt("commander", plan_is_set=True, total_cards=40)
    late = build_system_prompt("commander", plan_is_set=True, total_cards=NOTECARD_FROM_CARDS)
    assert "<deck_complete>" not in early
    assert "<deck_complete>" in late


def test_rules_the_code_enforces_are_described_as_automatic_not_commanded():
    text = build_system_prompt("commander")
    assert "What the app does on its own" in text
    # The old prompt commanded a withdraw-before-propose dance that the engine
    # now performs; it must not come back as an instruction.
    assert "withdraw_pending_proposals FIRST" not in text


def test_preferences_render_when_present():
    text = build_system_prompt(
        "commander", preferred_bracket="3", budget="mid", build_preferences="creature decks",
    )
    assert "preferred bracket: 3" in text
    assert "budget: mid" in text
    assert "creature decks" in text
    assert "<player_preferences>" not in build_system_prompt("commander")


def test_unknown_format_falls_back_to_generic_rules():
    assert "Constructed" in build_system_prompt("cube")


def test_prompt_is_materially_shorter_than_the_rulebook_it_replaced():
    # The previous prompt was ~540 lines / ~30k characters.
    assert len(build_system_prompt("commander", plan_is_set=True, total_cards=40)) < 16000
