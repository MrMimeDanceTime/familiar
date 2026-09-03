"""Tool results become compact text, and a shape the renderer does not know
falls back to JSON rather than failing."""

import json

from app.tools.render import render_result

SNAPSHOT = {
    "name": "Elves", "format": "commander", "total_cards": 5,
    "commander": "Ezuri, Renegade Leader", "partner_commander": None, "notes": "go wide",
    "plan": {
        "themes": ["elves"], "notes": None, "off_meta": 0.25, "max_card_price": 5.0,
        "role_counts": {"land": {"current": 2, "target": 36}},
        "still_needs": {"land": 34}, "missing_auto_includes": [{"name": "Sol Ring"}],
        "is_set": True, "counts_overlap": "overlap note",
    },
    "cards": [
        {"name": "Ezuri, Renegade Leader", "quantity": 1, "category": "Commander", "mana_value": 3,
         "type_line": "Legendary Creature — Elf Warrior", "oracle_text": "Regenerate.\nOverrun.",
         "tags": ["lord"], "notes": None},
        {"name": "Forest", "quantity": 2, "category": "Land", "mana_value": 0, "type_line": "Basic Land", "tags": []},
        {"name": "Llanowar Elves", "quantity": 1, "category": "Creature", "mana_value": 1,
         "type_line": "Creature — Elf Druid", "tags": ["ramp"], "notes": "one-drop dork"},
        {"name": "Cultivate", "quantity": 1, "category": "Sorcery", "mana_value": 3, "type_line": "Sorcery", "tags": ["ramp"]},
    ],
    "pending_proposals": {"count": 2, "proposals": [
        {"id": 7, "action": "add", "card_name": "Elvish Mystic", "quantity": 1},
        {"id": 8, "action": "set_commander", "commander_name": "Marwyn"},
    ]},
    "oracle_text_note": "commander only",
}


def test_deck_render_is_compact_and_complete():
    text = render_result("deck_get_current", SNAPSHOT)
    assert 'Deck "Elves" (commander) · 5 cards.' in text
    assert "Commander: Ezuri, Renegade Leader." in text
    assert "Plan (set): themes: elves · off_meta 0.25 · budget $5.00/card." in text
    assert "Role counts: land 2/36." in text
    assert "Still needs: land (short 34)." in text
    assert "Missing format staples: Sol Ring." in text
    assert "Commander (1):" in text and "text: Regenerate. Overrun." in text
    assert "Land (2):\n  Forest x2" in text
    assert "- Llanowar Elves · MV 1 · Creature — Elf Druid · tags: ramp · why: one-drop dork" in text
    assert "- #7 add Elvish Mystic" in text and "- #8 set commander Marwyn" in text
    assert "(commander only)" in text
    assert len(text) < len(json.dumps(SNAPSHOT))


def test_deck_render_orders_commander_first_and_lands_last():
    text = render_result("deck_get_current", SNAPSHOT)
    assert text.index("Commander (1)") < text.index("Creature (1)") < text.index("Land (2)")


def test_stats_render_reads_as_lines():
    stats = {
        "total_cards": 40, "avg_mv": 3.1, "land_count": 15, "land_pct": 38, "ramp_count": 4,
        "draw_count": 5, "removal_count": 3, "bracket": 2, "bracket_factors": ["Bracket 2: precon"],
        "power_level": 5.5, "power_level_base": 5.0, "power_nuance_adj": 0.5,
        "power_nuance_reason": "tight", "power_nuance_pending": False,
        "power_factors": ["curve ok"], "mana_curve": [{"mv": "2", "count": 8}],
        "color_distribution": [{"color": "G", "count": 20, "pct": 100}],
        "type_breakdown": [{"type": "Creature", "count": 20, "pct": 50}],
        "deficiencies": [{"category": "ramp", "count": 4, "status": "LOW", "target_low": 8, "target_high": 12}],
        "mana_sources": [{"color": "G", "sources": 15, "pips": 30, "source_pct": 100, "pip_pct": 100, "status": "OK"}],
        "total_price_usd": 42.5, "priced_cards": 38,
        "combos": [{"cards": ["A", "B"], "produces": ["Infinite mana"], "description": "tap", "card_count": 2}],
        "untagged": ["Weird Card"],
    }
    text = render_result("deck_get_stats", stats)
    assert "Total 40 cards · average MV 3.1 · lands 15 (38%)" in text
    assert "Bracket 2: Bracket 2: precon" in text
    assert "Power 5.5 (base 5.0, nuance +0.5): curve ok" in text
    assert "Against targets: ramp 4 LOW (want 8-12)" in text
    assert "Price: $42.50 across 38 priced cards." in text
    assert "- A + B → Infinite mana (tap)" in text
    assert "Untagged (1, so role counts are floors): Weird Card" in text


def test_card_render_carries_text_tags_and_rulings():
    card = {
        "name": "Counterspell", "mana_cost": "{U}{U}", "cmc": 2, "type_line": "Instant",
        "oracle_text": "Counter target spell.", "color_identity": ["U"], "legal_commander": True,
        "tags": ["counterspell-hard"],
        "rulings": [{"published_at": "2020-01-01", "comment": "It counters."}],
    }
    text = render_result("scryfall_card_by_name", card)
    assert text.startswith("Counterspell · {U}{U} · MV 2 · Instant · identity U · tags: counterspell-hard")
    assert "text: Counter target spell." in text
    assert "ruling: (2020-01-01) It counters." in text


def test_search_render_counts_and_marks_illegal():
    content = {"cards": [
        {"name": "Black Lotus", "cmc": 0, "type_line": "Artifact", "legal_commander": False, "color_identity": []},
    ], "count": 1}
    text = render_result("search_card_index", content)
    assert text.startswith("1 card(s):")
    assert "Black Lotus · MV 0 · Artifact · identity C · NOT commander-legal" in text
    assert "0 card(s):" in render_result("scryfall_search", [])


def test_collection_render_lists_missing_names():
    text = render_result("scryfall_card_collection", {"found": [{"name": "Sol Ring"}], "not_found": ["Nope"]})
    assert "Sol Ring" in text and "Not found: Nope" in text


def test_edhrec_render_flags_unfetched_text():
    recs = {"commander": "Ezuri", "categories": {"highsynergycards": {"header": "High Synergy", "cards": [
        {"name": "Elvish Archdruid", "synergy": 0.42, "inclusion": 0.61, "oracle_text": "Elves get +1/+1.",
         "type_line": "Creature — Elf Druid", "tags": ["lord"]},
        {"name": "Later Card", "synergy": 0.1, "inclusion": 0.2, "needs_lookup": True},
        {"name": "Ghost", "unverified": True},
    ]}}}
    text = render_result("edhrec_commander_recs", recs)
    assert "High Synergy (3):" in text
    assert "- Elvish Archdruid · synergy +42% · in 61% of decks · Creature — Elf Druid · tags: lord" in text
    assert "text: Elves get +1/+1." in text
    assert "Later Card · synergy +10% · in 20% of decks · [text not fetched]" in text
    assert "Ghost · [unverified]" in text


def test_knowledge_render_uses_headings():
    hits = [{"title": "Land count", "body": "Run 36.", "category": "land-base", "format": "commander", "source": "seed"}]
    text = render_result("search_deckbuilding_knowledge", hits)
    assert text == "## Land count (land-base, commander, seed)\nRun 36."
    assert render_result("search_deckbuilding_knowledge", []) == "No knowledge entries matched."


def test_proposal_render_shows_ids_prices_scores_and_reasons():
    content = {"ok": True, "summary": "the ramp package", "proposals": [
        {"id": 12, "action": "add", "card_name": "Cultivate", "quantity": 1, "category": "Sorcery",
         "price_usd": 0.5, "scores": {"total": 0.81, "consensus": 0.9, "mechanical": 0.7, "explain": "fits"},
         "reasoning": "Ramp that fixes."},
        {"id": 13, "action": "remove", "card_name": "Dud", "quantity": None, "reasoning": ""},
        {"id": 14, "action": "set_commander", "commander_name": "Ezuri"},
    ]}
    text = render_result("suggest_cards", content)
    assert text.startswith("the ramp package\n3 proposal(s) now awaiting the player's decision:")
    assert "- #12 add Cultivate · Sorcery · $0.50 · fit 0.81, consensus 0.90, mechanical 0.70, fits — Ramp that fixes." in text
    assert "- #13 remove Dud" in text
    assert "- #14 set commander Ezuri" in text
    assert "No proposals were created." in render_result("propose_deck_changes", {"ok": True, "summary": "", "proposals": []})


def test_withdraw_render():
    text = render_result("withdraw_pending_proposals", {
        "ok": True, "withdrawn": ["A"], "preserved_commander": "Ezuri", "not_found": ["Z"],
    })
    assert text == "Withdrawn: A.\nThe commander proposal (Ezuri) was kept.\nNot pending, so not withdrawn: Z."


def test_unknown_tool_and_bad_shape_fall_back_to_json():
    assert render_result("mystery_tool", {"a": 1}) == '{"a": 1}'
    assert render_result("deck_get_stats", "not a dict") == '"not a dict"'
