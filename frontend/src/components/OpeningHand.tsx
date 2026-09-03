import { useEffect, useMemo, useState } from 'react'
import type { DeckCard } from '../types/api'
import { DeckCardRow } from './DeckCardRow'

// Seven cards from the library (commander excluded, quantities honoured), the
// way a real shuffle would deal them. Pure and seeded by a counter so "Draw
// again" gives a fresh hand while a rerender does not.
function drawHand(cards: DeckCard[], seed: number, size = 7): DeckCard[] {
  const library: DeckCard[] = []
  for (const c of cards) {
    if (c.category === 'Commander') continue
    for (let i = 0; i < c.quantity; i++) library.push(c)
  }
  // Mulberry32: small, deterministic, good enough for a goldfish hand.
  let t = seed + 0x6d2b79f5
  const rand = () => {
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  for (let i = library.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    ;[library[i], library[j]] = [library[j], library[i]]
  }
  return library.slice(0, Math.min(size, library.length))
}


/** Deal a seven-card hand from the decklist and mulligan it. No model, no server. */
export function OpeningHand({ cards, deckId }: { cards: DeckCard[]; deckId: number }) {
  // A seed of 0 means "not drawn yet"; each draw bumps it, and mulligans
  // count so the hand can say how many cards go to the bottom.
  const [seed, setSeed] = useState(0)
  const [mulligans, setMulligans] = useState(0)
  const hand = useMemo(() => (seed ? drawHand(cards, seed) : []), [cards, seed])
  const lands = hand.filter((c) => (c.type_line ?? '').toLowerCase().includes('land')).length

  useEffect(() => {
    setSeed(0)
    setMulligans(0)
  }, [deckId])

  if (!cards.some((c) => c.category !== 'Commander')) return null

  return (
    <div className="deck-detail__section">
      <div className="deck-detail__notes-head">
        <div className="micro-label">
          Opening hand{seed ? ` · ${lands} land${lands === 1 ? '' : 's'}` : ''}
          {mulligans > 0 && ` · mulligan ${mulligans}, bottom ${mulligans}`}
        </div>
        <span className="deck-detail__chips">
          <button
            className="btn btn--ghost btn--chip"
            onClick={() => { setSeed(Date.now() & 0x7fffffff); setMulligans(0) }}
          >
            {seed ? 'Draw again' : 'Draw seven'}
          </button>
          {seed > 0 && (
            <button
              className="btn btn--ghost btn--chip"
              onClick={() => { setSeed((s) => (s * 31 + 7) & 0x7fffffff); setMulligans((m) => m + 1) }}
            >
              Mulligan
            </button>
          )}
        </span>
      </div>
      {seed > 0 && (
        <div className="opening-hand">
          {hand.map((c, i) => (
            <DeckCardRow key={`${c.name}-${i}`} card={{ ...c, quantity: 1 }} />
          ))}
        </div>
      )}
    </div>
  )
}
