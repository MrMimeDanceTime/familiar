"""Golden test for the deterministic shaping output.

Shaping (strip -> precompute -> dedupe -> sort -> render) is the pipeline's pure
core: given the same raw pool + deck context + tags, its rendered block must be
byte-stable, because that block is exactly what the selection model reasons over.
This test re-runs shaping on a stored input and diffs the render against the
captured golden. If shaping changes intentionally, regenerate the fixture (see
scripts note in the pipeline docs) and eyeball the diff.
"""

import json
from pathlib import Path

from app.pipeline.shaping import DeckContext, render_pool, shape

FIXTURE = Path(__file__).parent / "fixtures" / "golden_rakdos_removal.json"


def test_rakdos_removal_render_matches_golden():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    inp = golden["input"]

    ctx = DeckContext(
        identity=frozenset(inp["identity"]),
        card_names_lower=frozenset(inp["in_deck"]),
    )
    tags = {k: set(v) for k, v in inp["tags"].items()}
    rendered = render_pool(shape(inp["pool"], ctx, tags))

    assert rendered == golden["rendered"]


def test_golden_render_orders_legal_first_then_by_rank():
    # Guards the ordering contract independently of the exact text, so an
    # accidental sort-key change is caught even if someone regenerates the text.
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    inp = golden["input"]
    ctx = DeckContext(
        identity=frozenset(inp["identity"]),
        card_names_lower=frozenset(inp["in_deck"]),
    )
    shaped = shape(inp["pool"], ctx, {k: set(v) for k, v in inp["tags"].items()})

    legal_flags = [c.legal_in_deck for c in shaped]
    # all legal cards precede all illegal ones
    assert legal_flags == sorted(legal_flags, reverse=True)
    # Kroxa (rank 150) is the best-ranked legal card and comes first
    assert shaped[0].name.startswith("Kroxa")
    assert shaped[-1].name == "Terminate"  # illegal (already in deck), sorts last
