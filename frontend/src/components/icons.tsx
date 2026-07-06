// Inline stroke SVGs (Feather-style) + the Familiar mascot. Kept together so
// every surface draws the same marks. All use currentColor so they inherit
// the token color of whatever they sit in.

type IconProps = { size?: number }

export function SunIcon({ size = 15 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" />
    </svg>
  )
}

export function MoonIcon({ size = 15 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 14.5A8 8 0 019.5 4 6.5 6.5 0 1020 14.5z" />
    </svg>
  )
}

export function GearIcon({ size = 15 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.6 1.6 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.6 1.6 0 00-2.7 1.1V21a2 2 0 01-4 0v-.1A1.6 1.6 0 006 19.4a1.6 1.6 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1A1.6 1.6 0 003 15a1.6 1.6 0 00-1-1.5H2a2 2 0 010-4h.1A1.6 1.6 0 003 6a1.6 1.6 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1A1.6 1.6 0 009 3a1.6 1.6 0 001-1.5V2a2 2 0 014 0v.1A1.6 1.6 0 0018 4.6a1.6 1.6 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1A1.6 1.6 0 0021 9v.1" />
    </svg>
  )
}

export function PlusIcon({ size = 15 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

/** Resting/flourish state of the living mascot. */
export type FamiliarState = 'breathing' | 'glimmer' | 'curious'

/**
 * The Familiar mascot — a jagged brass/teal sigil with two watching eyes.
 *
 * `outline` renders a static stroke-only silhouette for empty states. The
 * filled avatar is a *living* mark: it breathes (float + blink) at rest,
 * glimmers while the assistant channels a reply, and does a curious
 * tilt-and-glance flourish when `state` is 'curious'. All motion lives in
 * global.css keyed off `data-state`; nested wrappers let the flourishes
 * compose over the always-on breathe.
 *
 * `flourishKey` should change each time a one-shot 'curious' flourish should
 * (re)play — the caller bumps it to remount the tilt/eye wrappers so the
 * animation restarts. See ChatView.
 */
export function FamiliarMark({
  size = 28,
  outline = false,
  state = 'breathing',
  flourishKey,
}: IconProps & { outline?: boolean; state?: FamiliarState; flourishKey?: number }) {
  if (outline) {
    return (
      <svg width={size} height={size} viewBox="0 0 100 100" style={{ flex: 'none' }} aria-hidden="true">
        <polygon
          points="12,34 24,4 42,30 58,30 76,4 88,34 82,62 64,88 50,96 36,88 18,62"
          fill="none"
          stroke="var(--accent)"
          strokeWidth={4}
          strokeLinejoin="round"
        />
        <ellipse cx="36" cy="55" rx="5.5" ry="8.5" fill="var(--accent)" transform="rotate(20 36 55)" />
        <ellipse cx="64" cy="55" rx="5.5" ry="8.5" fill="var(--accent)" transform="rotate(-20 64 55)" />
      </svg>
    )
  }

  return (
    <span className="fam-mark" data-state={state} style={{ display: 'inline-flex', flex: 'none' }} aria-hidden="true">
      <span className="fam-mark__float">
        <span key={flourishKey} className="fam-mark__tilt">
          <svg className="fam-mark__svg" width={size} height={size} viewBox="0 0 100 100" style={{ display: 'block' }}>
            <polygon
              points="12,34 24,4 42,30 58,30 76,4 88,34 82,62 64,88 50,96 36,88 18,62"
              fill="var(--accent)"
            />
            <g className="fam-mark__eyes">
              <ellipse cx="36" cy="55" rx="5.5" ry="8.5" fill="var(--bg)" transform="rotate(20 36 55)" />
              <ellipse cx="64" cy="55" rx="5.5" ry="8.5" fill="var(--bg)" transform="rotate(-20 64 55)" />
            </g>
          </svg>
        </span>
      </span>
    </span>
  )
}
