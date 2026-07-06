// Pure CSS/SVG deck-console primitives shared across the deck side-panel,
// deck manager, and chat. All colors come from tokens (var(--mana-*), etc.)
// so light and dark modes both work.

import type { Deck } from '../types/api'

const MANA_VAR: Record<string, string> = {
  W: 'var(--mana-w)',
  U: 'var(--mana-u)',
  B: 'var(--mana-b)',
  R: 'var(--mana-r)',
  G: 'var(--mana-g)',
  C: 'var(--mana-c)',
}

// Fill for a single circle representing a (possibly multi-color) commander or
// card. One color → solid; two+ → equal vertical bands with hard stops. Bands
// read far better than a conic pie at small pip sizes: each color is a solid
// full-height block, whereas pie wedges converge to a muddy point at the
// center and each color only shows at the rim. A vertical (90deg) split also
// avoids the "muddy diagonal" of the earlier 135deg gradient.
function identityFill(colorIdentity: string | null): string {
  const colors = (colorIdentity ?? '').replace(/[^WUBRG]/g, '')
  if (colors.length === 0) return MANA_VAR.C
  if (colors.length === 1) return MANA_VAR[colors[0]] ?? MANA_VAR.C
  const letters = colors.split('')
  const stops = letters
    .map((c, i) => {
      const from = (i / letters.length) * 100
      const to = ((i + 1) / letters.length) * 100
      const v = MANA_VAR[c] ?? MANA_VAR.C
      return `${v} ${from}% ${to}%`
    })
    .join(', ')
  return `linear-gradient(90deg, ${stops})`
}

// One color-identity string per commander (commander + partner/background),
// matched to the deck card carrying its color identity. Prefers matching by
// name; falls back to Commander-category cards for imports that didn't set
// deck.commander / deck.partner_commander.
export function commanderColorIdentities(deck: Deck): (string | null)[] {
  const names = [deck.commander, deck.partner_commander].filter(
    (n): n is string => !!n,
  )

  if (names.length > 0) {
    return names.map(
      (name) =>
        deck.cards.find((c) => c.name === name)?.color_identity ?? null,
    )
  }

  return deck.cards
    .filter((c) => c.category === 'Commander')
    .map((c) => c.color_identity ?? null)
}

export function ManaPip({ colorIdentity, size = 9 }: { colorIdentity: string | null; size?: number }) {
  // Multicolor cards render as a color pie (same as the commander pip) rather
  // than one flat blend — a GU card shows green + blue, not purple.
  return (
    <span
      className="mana-pip"
      style={{ width: size, height: size, background: identityFill(colorIdentity) }}
    />
  )
}

// Commander identity pip. `identities` holds one color-identity string per
// commander card (1 for a solo commander, 2 for partner/background pairs).
// Two commanders → a 2-circle Venn, one circle per commander's own colors,
// with an overlap lens (README §Commander Venn pip). One commander → a single
// circle filled with its (possibly multi-color) identity.
export function CommanderPip({ identities }: { identities: (string | null)[] }) {
  const present = identities.filter((id) => id !== null && id !== undefined)

  if (present.length >= 2) {
    return (
      <span className="venn-pip">
        <span className="venn-pip__a" style={{ background: identityFill(present[0]) }} />
        <span className="venn-pip__b" style={{ background: identityFill(present[1]) }} />
        <span className="venn-pip__lens" />
      </span>
    )
  }

  return (
    <span
      className="mana-pip"
      style={{ width: 18, height: 18, background: identityFill(present[0] ?? null) }}
    />
  )
}

// Conic power dial (README §Power dial). `surface` sets the inner fill so the
// hole matches whatever card it sits on.
export function PowerDial({
  value,
  size = 56,
  surface = 'var(--bg-soft)',
}: {
  value: number
  size?: number
  surface?: string
}) {
  const turn = Math.max(0, Math.min(10, value)) / 10
  return (
    <div
      className="power-dial"
      style={{
        width: size,
        height: size,
        background: `conic-gradient(var(--accent) 0turn ${turn}turn, var(--border) ${turn}turn 1turn)`,
      }}
    >
      <div className="power-dial__hole" style={{ background: surface }}>
        <span className="power-dial__num">{value}</span>
      </div>
    </div>
  )
}

// Row of 5 bracket diamonds, `filled` = current bracket (README §Bracket diamonds).
export function BracketDiamonds({ filled, size = 14 }: { filled: number; size?: number }) {
  return (
    <div className="bracket-diamonds">
      {[1, 2, 3, 4, 5].map((i) => (
        <span
          key={i}
          className={`bracket-diamond ${i <= filled ? 'bracket-diamond--on' : ''}`}
          style={{ width: size, height: size }}
        />
      ))}
    </div>
  )
}

// Vertical mana-curve bars (README §Mana-curve bars).
export function ManaCurve({ buckets }: { buckets: { mv: string; count: number }[] }) {
  const max = Math.max(1, ...buckets.map((b) => b.count))
  return (
    <div className="mana-curve">
      {buckets.map((b) => (
        <div key={b.mv} className="mana-curve__bar-wrap">
          <span className="mana-curve__count">{b.count}</span>
          <div
            className="mana-curve__bar"
            style={{ height: `${Math.max(4, (b.count / max) * 100)}%` }}
          />
          <span className="mana-curve__label">{b.mv}</span>
        </div>
      ))}
    </div>
  )
}

const ROLE_COLOR: Record<string, string> = {
  Lands: 'var(--mana-c)',
  Ramp: 'var(--mana-g)',
  Draw: 'var(--mana-u)',
  Removal: 'var(--mana-r)',
}

// Horizontal role-balance bars (README §Role-balance bars).
export function RoleBalance({ roles }: { roles: { label: string; count: number }[] }) {
  const max = Math.max(1, ...roles.map((r) => r.count))
  return (
    <div className="role-balance">
      {roles.map((r) => (
        <div key={r.label} className="role-balance__row">
          <div className="role-balance__head">
            <span className="role-balance__label">{r.label}</span>
            <span className="role-balance__count">{r.count}</span>
          </div>
          <div className="role-balance__track">
            <div
              className="role-balance__fill"
              style={{
                width: `${(r.count / max) * 100}%`,
                background: ROLE_COLOR[r.label] ?? 'var(--mana-c)',
              }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}
