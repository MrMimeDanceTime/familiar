import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CardPreview } from './CardPreview'
import { CardPinProvider } from './CardPinContext'
import { PinnedTray } from './PinnedTray'

/**
 * Two interaction models share one component, and a phone only ever exercises
 * one of them — so the touch path shipped broken and invisible: pins worked,
 * previews never appeared, because everything hung off onMouseEnter.
 *
 * The branch is decided by `matchMedia('(hover: none)')` read at click time,
 * which is why these tests drive matchMedia directly rather than firing touch
 * events (jsdom reports hover-capable regardless of the events you send).
 */

function setHoverCapability(canHover: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      // '(hover: none)' is true on touch devices.
      matches: query.includes('hover: none') ? !canHover : canHover,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    })),
  )
}

function renderCard(name = 'Sol Ring') {
  // The tray is what makes pin state observable — pins live in context and are
  // rendered by PinnedTray, not by CardPreview itself.
  return render(
    <CardPinProvider>
      <CardPreview name={name} />
      <PinnedTray />
    </CardPinProvider>,
  )
}

// Comfortably past the component's 120ms hover delay.
const HOVER_SETTLE_MS = 220

const anchor = (name = 'Sol Ring') => screen.getByText(name)
const tapPreview = () => document.querySelector('.card-tap-preview')
const hoverPreview = () => document.querySelector('.card-preview')
const trayHas = (name: string) =>
  Array.from(document.querySelectorAll('.pinned-tray img')).some(
    (img) => (img as HTMLImageElement).alt === name,
  )

beforeEach(() => {
  vi.useRealTimers()
})

describe('touch (no hover)', () => {
  beforeEach(() => setHoverCapability(false))

  it('first tap opens the preview instead of pinning', () => {
    // THE regression: on a phone this used to pin a card you had not seen.
    renderCard()
    fireEvent.click(anchor())

    expect(tapPreview()).not.toBeNull()
    expect(trayHas('Sol Ring')).toBe(false)
  })

  it('second tap on the same name pins it and closes the preview', () => {
    renderCard()
    fireEvent.click(anchor())
    fireEvent.click(anchor())

    expect(tapPreview()).toBeNull()
    expect(trayHas('Sol Ring')).toBe(true)
  })

  it('shows the card image at large resolution', () => {
    renderCard()
    fireEvent.click(anchor())

    const img = document.querySelector('.card-tap-preview__img') as HTMLImageElement
    expect(img).not.toBeNull()
    expect(img.src).toContain('Sol%20Ring')
    expect(img.src).toContain('version=large')
  })

  it('tapping the overlay dismisses without pinning', () => {
    renderCard()
    fireEvent.click(anchor())
    fireEvent.click(tapPreview()!)

    expect(tapPreview()).toBeNull()
    expect(trayHas('Sol Ring')).toBe(false)
  })

  it('tapping elsewhere dismisses without pinning', async () => {
    renderCard()
    fireEvent.click(anchor())
    // The dismiss listener is attached on a deferred tick so the opening tap
    // doesn't immediately close it.
    await new Promise((r) => setTimeout(r, 5))
    fireEvent.pointerDown(document.body)

    expect(tapPreview()).toBeNull()
    expect(trayHas('Sol Ring')).toBe(false)
  })

  it('Escape dismisses without pinning', async () => {
    renderCard()
    fireEvent.click(anchor())
    await new Promise((r) => setTimeout(r, 5))
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(tapPreview()).toBeNull()
    expect(trayHas('Sol Ring')).toBe(false)
  })

  it('an already-pinned card unpins on a single tap', () => {
    // Preview-first only applies to cards you have not pinned; a pinned card
    // is already visible in the tray, so tap goes straight to unpin.
    renderCard()
    fireEvent.click(anchor())
    fireEvent.click(anchor())
    expect(trayHas('Sol Ring')).toBe(true)

    fireEvent.click(anchor())
    expect(trayHas('Sol Ring')).toBe(false)
  })

  it('never opens the hover preview', () => {
    renderCard()
    fireEvent.mouseEnter(anchor())
    expect(hoverPreview()).toBeNull()
  })
})

describe('pointer (hover capable)', () => {
  beforeEach(() => setHoverCapability(true))

  it('click pins immediately, with no intermediate preview step', () => {
    // Desktop behaviour must not regress into a two-click pin.
    renderCard()
    fireEvent.click(anchor())

    expect(trayHas('Sol Ring')).toBe(true)
    expect(tapPreview()).toBeNull()
  })

  it('hover opens the anchored preview after the delay', async () => {
    // Real timers: the preview positions itself in a useLayoutEffect that runs
    // off the show timeout, and driving that with fake timers leaves the layout
    // pass unflushed so the portal never mounts.
    renderCard()
    fireEvent.mouseEnter(anchor())

    await new Promise((r) => setTimeout(r, HOVER_SETTLE_MS))
    expect(hoverPreview()).not.toBeNull()

    fireEvent.mouseLeave(anchor())
    expect(hoverPreview()).toBeNull()
  })
})
