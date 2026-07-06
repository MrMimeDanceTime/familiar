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
export function useTheme() {
  const [choice, setChoice] = useState<Theme | null>(() => readStored())
  const [system, setSystem] = useState<Theme>(() => systemTheme())

  useEffect(() => {
    apply(choice)
  }, [choice])

  // Track OS changes so the icon stays correct while following the system.
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => setSystem(mq.matches ? 'dark' : 'light')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const resolved: Theme = choice ?? system

  const toggle = useCallback(() => {
    setChoice((prev) => {
      const next: Theme = (prev ?? systemTheme()) === 'dark' ? 'light' : 'dark'
      localStorage.setItem(STORAGE_KEY, next)
      return next
    })
  }, [])

  return { theme: resolved, isDark: resolved === 'dark', toggle }
}
