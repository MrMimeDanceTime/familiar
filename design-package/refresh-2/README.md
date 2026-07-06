# Familiar — UIX Overhaul Bundle (current state)

This is a **self-contained snapshot of the Familiar frontend as it exists
today**, assembled to hand to a designer (Claude Design or otherwise) as raw
material for a UI/UX overhaul. Everything here is the *real, shipping* code —
not a prototype and not the older design cycle in `../brief` / `../handoff`
(that one is historical; its `theme-tokens.css` is stale).

**Start here:** [`FAMILIAR-DESIGN-BUNDLE.md`](FAMILIAR-DESIGN-BUNDLE.md) — a
single file that inlines every stylesheet and component below, so it can be
pasted whole into a design chat. The folders exist if you'd rather browse real
files.

---

## What Familiar is

A personal, **local-only** AI assistant for **Magic: The Gathering
Commander/EDH** deckbuilding. Single-user desktop web app, served locally. The
user chats with an AI deckbuilding partner; the AI proposes deck changes the
user approves/denies via inline **proposal cards**, and a live **deck panel +
stats** keep pace with the build.

## Hard tech constraints (please design within these)

- **React 19 + TypeScript 6 (strict)**, **Vite 8** build, **oxlint** lint.
- **No component/UI library** — every component is hand-rolled.
- **No CSS framework** (no Tailwind/MUI). Plain CSS in exactly two files:
  - `current-src/index.css` — **design tokens** (light default +
    `@media (prefers-color-scheme: dark)` override) and the base reset.
  - `current-src/styles/global.css` — one global stylesheet, **BEM-ish** class
    names (`.deck-detail__header`, `.import-pop__chip`). ~2,000 lines.
- Only runtime UI deps: **react-markdown** + **remark-gfm** (assistant messages
  render markdown incl. GFM tables — style those, don't restructure them).
- **Keep new dependencies to a minimum.** If a design needs a new lib, call it
  out explicitly.
- Both **light and dark** must keep working → **design against the tokens, not
  hardcoded colors.** New colors should become new tokens in *both* blocks.

## Layout

Three-column CSS grid **`240px | 1fr | 320px`**, with a separate single-column
mobile layout (`≤768px`) that swaps to bottom-tab navigation:

- **Left (240px):** sidebar — brand header, Conversations/Decks segmented tabs,
  list, settings gear.
- **Center (1fr):** chat view (message stream + composer) **OR** the
  full-screen **deck manager** (`DeckDetail`) when the Decks tab + a deck are
  active.
- **Right (320px):** deck side-panel (`DeckPanel`) — live deck contents while
  chatting.

## Key surfaces / components

| Component | Surface |
|-----------|---------|
| `ConversationSidebar` | Brand, Conversations/Decks tabs, list, gear |
| `ChatView` + `MessageBubble` | Streaming AI chat, markdown incl. tables, user bubbles |
| `ToolActivityIndicator` | Pulsing "tool running" row (only motion in the UI) |
| `ProposalCard` | Inline approve/deny AI deck-change suggestions |
| `DeckPanel` | 320px live deck view during chat (mini stat strip + card list) |
| `DeckDetail` | Full deck manager — **the signature surface** (metric strip, stat grid, quick actions, decklist) |
| `deckViz` | Pure-CSS/SVG primitives: mana pip, commander Venn pip, power dial, bracket diamonds, mana curve, role balance |
| `DeckCardRow` | qty · pip · name · MV row (shared) |
| `ImportPanel` | Portaled import popover (paste text / fetch Archidekt) |
| `PreferencesPanel` | Modal: bracket / power / budget / rule-0 notes |
| `CardPreview` + `PinnedTray` + `CardPinContext` | Hover card image; click to pin into a compare tray |
| `MobileHeader` / `MobileNav` | Mobile-only chrome |

## The "Console" visual language (current)

The app currently uses a restrained **"deck-console"** system: monospace
micro-labels for section headers, tabular-nums for all data, hairline `1px
var(--border)` dividers instead of shadows between cells, purple accent
(`#aa3bff` light / `#c084fc` dark), a purple→blue brand gradient
(`#863bff → #47bfff`), and pure-CSS data viz (power dial = conic-gradient,
bracket diamonds = rotated squares, mana pips = color-banded circles). Motion is
limited to one pulsing dot. This is the baseline to evolve — keep, refine, or
reimagine as the overhaul calls for.

## Where the design effort is most valuable

- The **deck manager** (`DeckDetail`) stat grid + decklist — users spend the
  most time here.
- **Hierarchy / rhythm in chat** — message bubbles, proposal cards, results
  tables.
- **Consistent, reusable component primitives** (buttons, chips, inputs, cards,
  modals) expressed as CSS classes on the tokens.
- Any **layout improvements** to the three-column desktop grid.

## Files in this bundle

```
refresh-2/
├── README.md                      ← you are here
├── FAMILIAR-DESIGN-BUNDLE.md      ← everything inlined into one pasteable file
├── assets/
│   ├── favicon.svg                ← brand tile (browser icon + sidebar mark)
│   └── familiar-mark.svg          ← standalone transparent mark
└── current-src/                   ← verbatim copies of the live frontend
    ├── index.html
    ├── index.css                  ← tokens + reset
    ├── styles/global.css          ← the whole stylesheet
    ├── App.tsx
    ├── components/*.tsx
    ├── types/api.ts               ← data shapes the UI renders
    └── api/cardImage.ts
```

> The `hooks/` directory in the tree is intentionally empty here — the chat
> streaming hook (`useChatStream`) and `useDeck` are behavioral, not visual, and
> were left out to keep the bundle focused on UI. The component prop types they
> feed (`DisplayMessage`, `ProposalBatch`) are described where used.
