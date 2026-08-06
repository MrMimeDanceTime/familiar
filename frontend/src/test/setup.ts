import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  sessionStorage.clear()
  setVisibility('visible')
})

/**
 * Drive `document.visibilityState`, which jsdom exposes as a read-only getter.
 *
 * The reconnect logic keys off visibility (a backgrounded tab can't run timers
 * reliably, so backoff only runs in the foreground), and that branch is exactly
 * where the shipped bugs lived — so tests have to be able to background a tab.
 */
export function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => state,
  })
}

export function fireVisibilityChange(state: DocumentVisibilityState) {
  setVisibility(state)
  document.dispatchEvent(new Event('visibilitychange'))
}
