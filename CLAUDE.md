# Familiar

Personal, local-only AI assistant for Magic: The Gathering Commander/EDH
deckbuilding.

The architecture, conventions, and known gotchas for this project are
written as tool-agnostic docs in [`docs/`](docs/) so any coding assistant
(or human) can use them — not just Claude:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — stack, run/test commands, code layout, data model
- [docs/PRODUCT.md](docs/PRODUCT.md) — product philosophy behind the chat behavior
- [docs/PROVIDERS.md](docs/PROVIDERS.md) — DeepSeek vs Anthropic, which is primary and why
- [docs/PROVIDER_SHAPES.md](docs/PROVIDER_SHAPES.md) — the most important implementation detail in this codebase; read before touching `app/chat/engine.py`

Read those first. This file only has things specific to working with
Claude Code on this repo.

## Conventions

- No comments explaining *what* code does; only for non-obvious *why* (see
  the `provider_native` slicing comment in `engine.py` as the model).
- Don't add migration tooling (Alembic etc.) — single-user local SQLite,
  schema changes during development are expected to mean a manual DB wipe.
- `.env` is gitignored and must never be committed; `.env.example` is the
  tracked template — keep them in sync when adding new settings.
- The user prefers one launch command (`run.ps1`) over running separate
  backend/frontend dev servers as the default workflow — only suggest the
  two-process hot-reload setup when they're actively iterating on frontend UI.
