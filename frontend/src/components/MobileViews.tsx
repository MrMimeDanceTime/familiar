import { useCardPins } from './CardPinContext'
import type { Conversation, DeckSummary } from '../types/api'

/** The mobile Chats tab's list. */
export function MobileConversationList({
  conversations, activeId, onSelect,
}: { conversations: Conversation[]; activeId: number | null; onSelect: (id: number) => void }) {
  return (
    <div className="mobile-list">
      {conversations.length === 0 && (
        <p className="mobile-list__empty">No conversations yet. Start a new one.</p>
      )}
      {conversations.map((c) => (
        <button
          key={c.id}
          className={`mobile-list__item ${c.id === activeId ? 'mobile-list__item--active' : ''}`}
          onClick={() => onSelect(c.id)}
        >
          <span className="mobile-list__item-title">{c.title || 'Untitled'}</span>
          <span className="mobile-list__item-date">
            {new Date(c.updated_at).toLocaleDateString()}
          </span>
        </button>
      ))}
    </div>
  )
}

/** The mobile Decks tab's list. */
export function MobileDeckList({
  decks, activeId, onSelect,
}: { decks: DeckSummary[]; activeId: number | null; onSelect: (deck: DeckSummary) => void }) {
  return (
    <div className="mobile-list">
      {decks.length === 0 && (
        <p className="mobile-list__empty">No decks yet. Create one to get started.</p>
      )}
      {decks.map((d) => (
        <button
          key={d.id}
          className={`mobile-list__item ${d.id === activeId ? 'mobile-list__item--active' : ''}`}
          onClick={() => onSelect(d)}
        >
          <span className="mobile-list__item-title">{d.name}</span>
          {d.commander && <span className="mobile-list__item-deck">{d.commander}</span>}
        </button>
      ))}
    </div>
  )
}

/** Full-screen pinned-card view for the mobile Pins tab. */
export function MobilePinsView() {
  const { pinned, unpin, clear } = useCardPins()

  if (pinned.length === 0) {
    return (
      <div className="mobile-list">
        <p className="mobile-list__empty">
          No cards pinned yet. Tap a card name in a message or deck list to pin it for comparison.
        </p>
      </div>
    )
  }

  return (
    <div className="mobile-pins">
      <div className="mobile-pins__head">
        <span className="micro-label">Pinned · {pinned.length}</span>
        <button className="pinned-tray__clear" onClick={clear}>Clear all</button>
      </div>
      <div className="mobile-pins__grid">
        {pinned.map((name) => (
          <div key={name} className="mobile-pins__card">
            <img
              src={`https://api.scryfall.com/cards/named?exact=${encodeURIComponent(name)}&format=image`}
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

