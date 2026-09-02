import { useCallback, useState } from 'react'
import { api } from '../api/client'
import type { Deck } from '../types/api'

export function useDeck() {
  const [deck, setDeck] = useState<Deck | null>(null)

  const loadDeck = useCallback(async (deckId: number) => {
    try {
      const fresh = await api.getDeck(deckId)
      setDeck(fresh)
    } catch {
      // A conversation can outlive its deck. Showing no deck is the truthful
      // state; an unhandled rejection here left the previous deck on screen.
      setDeck(null)
    }
  }, [])

  const clearDeck = useCallback(() => setDeck(null), [])

  return { deck, setDeck, loadDeck, clearDeck }
}
