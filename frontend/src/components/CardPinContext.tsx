import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

// Shared state for pinned card previews. Pins live at the app level (not inside
// each CardPreview) so multiple cards can be pinned at once and collected into a
// single tray for side-by-side comparison.

interface CardPinContextValue {
  pinned: string[]
  isPinned: (name: string) => boolean
  toggle: (name: string) => void
  unpin: (name: string) => void
  clear: () => void
}

const CardPinContext = createContext<CardPinContextValue | null>(null)

export function CardPinProvider({ children }: { children: ReactNode }) {
  // Ordered list, newest last. A card is pinned at most once (keyed by name).
  const [pinned, setPinned] = useState<string[]>([])

  const toggle = useCallback((name: string) => {
    setPinned((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    )
  }, [])

  const unpin = useCallback((name: string) => {
    setPinned((prev) => prev.filter((n) => n !== name))
  }, [])

  const clear = useCallback(() => setPinned([]), [])

  const value = useMemo<CardPinContextValue>(
    () => ({
      pinned,
      isPinned: (name: string) => pinned.includes(name),
      toggle,
      unpin,
      clear,
    }),
    [pinned, toggle, unpin, clear],
  )

  return <CardPinContext.Provider value={value}>{children}</CardPinContext.Provider>
}

export function useCardPins(): CardPinContextValue {
  const ctx = useContext(CardPinContext)
  if (!ctx) {
    throw new Error('useCardPins must be used within a CardPinProvider')
  }
  return ctx
}
