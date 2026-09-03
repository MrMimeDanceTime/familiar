import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { Deck, DeckStats } from '../types/api'

/**
 * The stats panel's data: deterministic stats at once, the LLM power nuance
 * when the server is willing to compute it.
 *
 * Every response is checked against the deck currently wanted before it is
 * applied, so a slow reply for a deck the user has navigated away from is
 * dropped instead of overwriting the current panel.
 */
export function useDeckStats(deck: Deck | null) {
  const [stats, setStats] = useState<DeckStats | null>(null)
  const [nuanceLoading, setNuanceLoading] = useState(false)
  const wantedDeck = useRef<number | null>(null)
  // The one scheduled nuance retry, so a burst of approvals collapses into a
  // single follow-up once the deck settles rather than one timer per click.
  const nuanceRetry = useRef<ReturnType<typeof setTimeout> | null>(null)

  const loadNuance = useCallback((deckId: number) => {
    if (nuanceRetry.current) {
      clearTimeout(nuanceRetry.current)
      nuanceRetry.current = null
    }
    setNuanceLoading(true)
    api.getDeckStatsNuance(deckId)
      .then((n) => {
        if (wantedDeck.current !== deckId) return
        setStats((prev) => (prev ? { ...prev, ...n } : prev))
        // The server declines to score a deck that is still changing. Come
        // back once the settle window has passed; approvals in between just
        // push that single retry further out.
        if (n.power_nuance_pending) {
          nuanceRetry.current = setTimeout(
            () => { if (wantedDeck.current === deckId) loadNuance(deckId) },
            Math.max(1, n.settle_seconds) * 1000 + 500,
          )
        }
      })
      .catch(() => {})
      .finally(() => { if (wantedDeck.current === deckId) setNuanceLoading(false) })
  }, [])

  const loadStats = useCallback((deckId: number) => {
    wantedDeck.current = deckId
    api.getDeckStats(deckId)
      .then((s) => { if (wantedDeck.current === deckId) setStats(s) })
      .catch(() => { if (wantedDeck.current === deckId) setStats(null) })
    loadNuance(deckId)
  }, [loadNuance])

  useEffect(() => {
    if (deck) {
      loadStats(deck.id)
    } else {
      wantedDeck.current = null
      setStats(null)
      setNuanceLoading(false)
    }
  }, [deck, loadStats])

  return { stats, nuanceLoading, loadStats }
}
