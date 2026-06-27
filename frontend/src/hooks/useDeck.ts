import { useCallback, useState } from 'react'
import { api } from '../api/client'
import type { Deck } from '../types/api'

export function useDeck() {
  const [deck, setDeck] = useState<Deck | null>(null)

  const loadDeck = useCallback(async (deckId: number) => {
    const fresh = await api.getDeck(deckId)
    setDeck(fresh)
  }, [])

  const clearDeck = useCallback(() => setDeck(null), [])

  return { deck, setDeck, loadDeck, clearDeck }
}
