# Design package

The full design cycle for the Familiar UI visual refresh — the brief that was
sent out, and the approved **"Console"** system that came back.

## Layout

- **[`brief/`](brief/)** — the original UX design brief: the request
  ([`brief/README.md`](brief/README.md)), the token file shared as input
  (`theme-tokens.css`, a copy of the then-current `frontend/src/index.css`),
  and dark-mode screenshots of the app before the refresh (`01`–`05`).
- **[`handoff/`](handoff/)** — the returned design handoff (the winning
  **3A / "Console"** direction):
  - [`handoff/README.md`](handoff/README.md) — the authoritative spec: tokens,
    per-screen layout, reusable component primitives.
  - `Deck Manager Directions.dc.html` — the design reference prototype (open in
    a browser; needs the sibling `support.js`). **Not production code.**
  - `support.js` — prototype runtime only. **Do not port.**
  - `assets/familiar-mark.svg`, `assets/favicon.svg` — the brand logo assets.

## Status

The Console system has been implemented in `frontend/`. The logo assets ship at
`frontend/public/favicon.svg` (browser icon + sidebar brand tile) and
`frontend/public/familiar-mark.svg` (standalone transparent mark); the two files
under `handoff/assets/` are the design-source originals.

These folders are reference material — nothing here is built or imported by the
app.
