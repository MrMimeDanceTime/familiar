import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'familiar-theme'

// The user's stored choice, or null when they've never toggled (→ follow OS).
function readStored(): Theme | null {
  const v = localStorage.getItem(STORAGE_KEY)
  return v === 'light' || v === 'dark' ? v : null
}

function systemTheme(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

// Applies (or clears) the data-theme attribute. No stored choice → remove the
// attribute entirely so index.css's prefers-color-scheme block drives the
// theme; a choice → stamp it explicitly, overriding the OS.
function apply(choice: Theme | null) {
  const root = document.documentElement
  if (choice) {
    root.setAttribute('data-theme', choice)
  } else {
    root.removeAttribute('data-theme')
  }
}

/**
 * Persisted light/dark theme. `resolved` is the effective theme (falling back
 * to the OS when the user hasn't chosen); `toggle` flips to the opposite of
 * whatever is currently showing and persists it.
 */
// Instances subscribe here so a toggle in one updates all of them. The desktop
// sidebar and the mobile header both mount their own toggle (both are always in
// the DOM; CSS decides which is visible), and without this each would hold its
// own copy of `choice` — toggling one would flip the theme while leaving the
// other's sun/moon icon showing the wrong state until a reload.
const subscribers = new Set<(choice: Theme | null) => void>()

function broadcast(choice: Theme | null) {
  for (const fn of subscribers) fn(choice)
}

export function useTheme() {
  const [choice, setChoice] = useState<Theme | null>(() => readStored())
  const [system, setSystem] = useState<Theme>(() => systemTheme())

  useEffect(() => {
    apply(choice)
  }, [choice])

  useEffect(() => {
    subscribers.add(setChoice)
    return () => {
      subscribers.delete(setChoice)
    }
  }, [])

  // Track OS changes so the icon stays correct while following the system.
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => setSystem(mq.matches ? 'dark' : 'light')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const resolved: Theme = choice ?? system

  const toggle = useCallback(() => {
    const next: Theme = (readStored() ?? systemTheme()) === 'dark' ? 'light' : 'dark'
    localStorage.setItem(STORAGE_KEY, next)
    broadcast(next)
  }, [])

  return { theme: resolved, isDark: resolved === 'dark', toggle }
}
