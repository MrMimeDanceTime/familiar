import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { cardImageUrl } from '../api/cardImage'
import { useCardPins } from './CardPinContext'

// A card name that reveals the card's image and pins it (into the shared bottom
// tray) for comparison. Pinning is app-level state (see CardPinContext) so
// multiple cards can be pinned at once.
//
// Two interaction models, because a phone has no hover:
//
//   Pointer (mouse):  hover shows the image, click pins.
//   Touch:            first tap shows the image, a second tap on the same name
//                     pins it. Tapping anywhere else dismisses.
//
// Without the touch path a phone could pin a card but never see it, which is
// backwards — the image is the whole point of the preview.

// Scryfall "normal" images are 488×680 (aspect ~0.717). We render at a fixed
// width and let height follow the aspect so the box is sized before the image
// loads (no layout jump).
const PREVIEW_W = 244
const PREVIEW_H = Math.round(PREVIEW_W / 0.717)
const HOVER_DELAY_MS = 120

/** True when the primary input can't hover — phones, most tablets. */
function isTouchPrimary(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(hover: none)').matches
}

interface CardPreviewProps {
  name: string
  className?: string
}

export function CardPreview({ name, className }: CardPreviewProps) {
  const { isPinned, toggle } = useCardPins()
  const pinned = isPinned(name)
  const anchorRef = useRef<HTMLSpanElement>(null)
  const showTimer = useRef<number | undefined>(undefined)
  const [hovering, setHovering] = useState(false)
  const [failed, setFailed] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  // Touch only: this name is showing its preview, so the next tap pins it.
  const [tapOpen, setTapOpen] = useState(false)

  const place = useCallback(() => {
    const el = anchorRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    // Prefer to the right of the name; flip left if it would overflow.
    let left = r.right + 12
    if (left + PREVIEW_W > window.innerWidth - 8) {
      left = r.left - PREVIEW_W - 12
    }
    left = Math.max(8, Math.min(left, window.innerWidth - PREVIEW_W - 8))
    // Vertically center on the name, clamped to the viewport.
    let top = r.top + r.height / 2 - PREVIEW_H / 2
    top = Math.max(8, Math.min(top, window.innerHeight - PREVIEW_H - 8))
    setPos({ top, left })
  }, [])

  useLayoutEffect(() => {
    if (!hovering) return
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [hovering, place])

  const show = useCallback(() => {
    window.clearTimeout(showTimer.current)
    showTimer.current = window.setTimeout(() => {
      setFailed(false)
      setHovering(true)
    }, HOVER_DELAY_MS)
  }, [])

  const hide = useCallback(() => {
    window.clearTimeout(showTimer.current)
    setHovering(false)
  }, [])

  const onClick = useCallback(() => {
    window.clearTimeout(showTimer.current)

    if (isTouchPrimary() && !pinned && !tapOpen) {
      // First tap on a touch device: reveal the card rather than pinning it
      // sight-unseen. The second tap (below) pins.
      setFailed(false)
      setTapOpen(true)
      return
    }

    setTapOpen(false)
    setHovering(false) // hand off to the tray; drop the floating preview
    toggle(name)
  }, [name, toggle, pinned, tapOpen])

  // Dismiss a tap-opened preview on the next tap anywhere else, on scroll, or
  // on Escape. Registered only while open so it costs nothing at rest.
  useEffect(() => {
    if (!tapOpen) return
    const dismiss = (event: Event) => {
      // A tap on the anchor itself is the "pin it" tap — onClick owns that.
      if (event.target instanceof Node && anchorRef.current?.contains(event.target)) return
      setTapOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setTapOpen(false)
    }
    // Deferred so the tap that opened this doesn't immediately close it.
    const id = window.setTimeout(() => {
      document.addEventListener('pointerdown', dismiss)
      document.addEventListener('keydown', onKey)
      window.addEventListener('scroll', () => setTapOpen(false), { once: true, capture: true })
    }, 0)
    return () => {
      window.clearTimeout(id)
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', onKey)
    }
  }, [tapOpen])

  // Clean up a scheduled show on unmount.
  useEffect(() => () => window.clearTimeout(showTimer.current), [])

  // Pinned cards live in the tray, so the floating hover preview stands down.
  const showImage = hovering && !pinned && !failed
  const showTapImage = tapOpen && !failed

  return (
    <>
      <span
        ref={anchorRef}
        className={`card-preview-anchor ${pinned ? 'card-preview-anchor--pinned' : ''} ${tapOpen ? 'card-preview-anchor--open' : ''} ${className ?? ''}`}
        onMouseEnter={show}
        onMouseLeave={hide}
        onClick={onClick}
        title={pinned ? 'Unpin' : 'Click to pin for comparison'}
      >
        {name}
      </span>

      {showImage && pos &&
        createPortal(
          <div
            className="card-preview"
            style={{ top: pos.top, left: pos.left, width: PREVIEW_W, height: PREVIEW_H }}
          >
            <img
              className="card-preview__img"
              src={cardImageUrl(name)}
              alt={name}
              width={PREVIEW_W}
              height={PREVIEW_H}
              onError={() => setFailed(true)}
            />
          </div>,
          document.body,
        )}

      {/* Touch: a centered overlay. Anchoring beside the name has nowhere to go
          on a ~390px screen — it would clamp to an edge and cover the text you
          just tapped. */}
      {showTapImage &&
        createPortal(
          <div className="card-tap-preview" onClick={() => setTapOpen(false)}>
            <figure className="card-tap-preview__frame">
              <img
                className="card-tap-preview__img"
                src={cardImageUrl(name, 'large')}
                alt={name}
                onError={() => setFailed(true)}
              />
              <figcaption className="card-tap-preview__hint">
                {pinned ? 'Tap the name again to unpin' : 'Tap the name again to pin'}
              </figcaption>
            </figure>
          </div>,
          document.body,
        )}
    </>
  )
}
