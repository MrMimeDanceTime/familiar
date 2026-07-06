import { useEffect } from 'react'
import { cardImageUrl } from '../api/cardImage'
import { useCardPins } from './CardPinContext'

// A bottom tray that collects all pinned card images side by side for
// comparison. Each card has its own remove button; the whole tray clears on
// Escape or the "Clear all" button.
export function PinnedTray() {
  const { pinned, unpin, clear } = useCardPins()

  // Let CSS know the tray is visible so scrollable deck areas can add
  // bottom padding — otherwise the fixed tray covers the last few cards.
  useEffect(() => {
    document.body.classList.toggle('has-pinned-tray', pinned.length > 0)
    return () => document.body.classList.remove('has-pinned-tray')
  }, [pinned.length])

  useEffect(() => {
    if (pinned.length === 0) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') clear()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [pinned.length, clear])

  if (pinned.length === 0) return null

  return (
    <div className="pinned-tray">
      <div className="pinned-tray__head">
        <span className="micro-label">Pinned · {pinned.length}</span>
        <button className="pinned-tray__clear" onClick={clear}>
          Clear all
        </button>
      </div>
      <div className="pinned-tray__cards">
        {pinned.map((name) => (
          <div key={name} className="pinned-card">
            <img
              className="pinned-card__img"
              src={cardImageUrl(name)}
              alt={name}
              loading="lazy"
            />
            <button
              className="pinned-card__close"
              onClick={() => unpin(name)}
              title={`Unpin ${name}`}
              aria-label={`Unpin ${name}`}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
