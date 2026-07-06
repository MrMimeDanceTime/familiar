import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { cardImageUrl } from '../api/cardImage'
import { useCardPins } from './CardPinContext'

// A card name that reveals the card's image on hover and pins it (into the
// shared bottom tray) on click. The hover image is portaled to <body> and
// positioned beside the name, clamped to the viewport. Pinning is app-level
// state (see CardPinContext) so multiple cards can be pinned for comparison.

// Scryfall "normal" images are 488×680 (aspect ~0.717). We render at a fixed
// width and let height follow the aspect so the box is sized before the image
// loads (no layout jump).
const PREVIEW_W = 244
const PREVIEW_H = Math.round(PREVIEW_W / 0.717)
const HOVER_DELAY_MS = 120

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
    setHovering(false) // hand off to the tray; drop the floating hover preview
    toggle(name)
  }, [name, toggle])

  // Clean up a scheduled show on unmount.
  useEffect(() => () => window.clearTimeout(showTimer.current), [])

  // Only float the hover preview when not pinned — pinned cards live in the tray.
  const showImage = hovering && !pinned && !failed

  return (
    <>
      <span
        ref={anchorRef}
        className={`card-preview-anchor ${pinned ? 'card-preview-anchor--pinned' : ''} ${className ?? ''}`}
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
    </>
  )
}
