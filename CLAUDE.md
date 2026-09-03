# Familiar

Personal, local-only AI assistant for Magic: The Gathering Commander/EDH
deckbuilding.

The architecture, conventions, and known gotchas for this project are
written as tool-agnostic docs in [`docs/`](docs/) so any coding assistant
(or human) can use them — not just Claude:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — stack, run/test commands, code layout, data model
- [docs/PRODUCT.md](docs/PRODUCT.md) — product philosophy behind the chat behavior
- [docs/PROVIDERS.md](docs/PROVIDERS.md) — the DeepSeek backend, its model/thinking policy, and why it is the only one
- [docs/PROVIDER_SHAPES.md](docs/PROVIDER_SHAPES.md) — the most important implementation detail in this codebase; read before touching `app/chat/engine.py`
- [docs/PIPELINE.md](docs/PIPELINE.md) — the card-suggestion retrieval pipeline as built (stages, model/thinking policy, deck-aware selection, timing/timeout diagnostics)

Read those first. This file only has things specific to working with
Claude Code on this repo.

## Conventions

- **Touching scoring? Run the coverage report.** Any change to
  `app/brainmap/`, `app/cards/` (index, tags, co-occurrence), or
  `app/pipeline/candidates.py` changes how much of a candidate pool the brain
  map can actually score. Measure it:

  ```
  cd backend && .venv\Scripts\python.exe tools/coverage_report.py
  ```

  It compares against `tools/coverage_baseline.json`, prints a per-deck delta,
  and exits non-zero if mechanical coverage dropped on any deck. A single total
  hides the failure that matters — a change that helps one deck and craters
  another looks fine in aggregate. When a drop is intended, say why and re-run
  with `--save` to move the baseline; the diff of the numbers belongs in the
  commit. Never `--save` to silence a drop you have not explained.

  History for why this exists: the first mechanical layer used a hand-written
  theme list and scored **4%** of pooled cards, with five of eleven decks at
  exactly zero. Nothing caught that for two stages, because nobody was
  measuring.

- **Touching the prompt, the tool descriptions, or the engine's turn logic?
  Run the behaviour eval.** `backend/tools/behaviour_eval.py replay` re-runs
  stored user turns through the live loop against a scratch copy of the DB and
  scores each reply on the rules the prompt asks for and the code cannot
  enforce (role batches through the pipeline, plan set before the first batch,
  refusals, reply length, unbracketed card names, tokens). It calls the LLM
  and costs money; `score <trace.jsonl>` re-scores a saved trace for free.
  Compare against `tools/behaviour_baseline.json` and `--save` only when the
  change is understood. The prompt used to be edited on the strength of one
  live deck misbehaving; that is how it grew to 540 lines of scar tissue.

- No comments explaining *what* code does; only for non-obvious *why* (see
  the `provider_native` slicing comment in `engine.py` as the model).
- Don't add migration tooling (Alembic etc.) — single-user local SQLite.
  For a schema change, prefer a targeted, idempotent, non-destructive edit
  to the existing DB (e.g. `ALTER TABLE ... ADD COLUMN` guarded by a
  `PRAGMA table_info` check) so the user's decks and conversations survive.
  Only wipe `backend/familiar.db` when the change genuinely can't be applied
  in place (a column type change, a table restructure, dropped data), and
  say so before doing it.
- `.env` is gitignored and must never be committed; `.env.example` is the
  tracked template — keep them in sync when adding new settings.
- The user prefers one launch command (`run.ps1`) over running separate
  backend/frontend dev servers as the default workflow — only suggest the
  two-process hot-reload setup when they're actively iterating on frontend UI.
