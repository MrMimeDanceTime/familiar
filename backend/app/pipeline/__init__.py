"""Deterministic retrieval pipeline for card suggestions.

Python owns the retrieval/shaping/validation flow; the LLM is called only in
two bounded roles — stage 1 (emit Scryfall query specs) and stage 4 (select
cards over a pre-retrieved, cleaned, tagged pool). See
``mtg-deckbuilder-overhaul-spec.md`` and ``docs/`` for the design.
"""
