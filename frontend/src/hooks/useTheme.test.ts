import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useTheme } from './useTheme'

/**
 * Two toggles now exist at once: the desktop sidebar's and the mobile header's.
 * Both are always mounted (CSS decides which is visible), so each holds its own
 * instance of this hook. Without cross-instance sync, toggling one flips the
 * theme while the other's sun/moon icon keeps showing the old state.
 */

function mockSystemTheme(dark: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: dark && query.includes('dark'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  mockSystemTheme(false)
})

describe('useTheme', () => {
  it('follows the OS when the user has never chosen', () => {
    mockSystemTheme(true)
    const { result } = renderHook(() => useTheme())

    expect(result.current.isDark).toBe(true)
    // No explicit choice: the attribute stays off so CSS drives the theme.
    expect(document.documentElement.getAttribute('data-theme')).toBeNull()
  })

  it('stamps an explicit choice and persists it', () => {
    const { result } = renderHook(() => useTheme())
    expect(result.current.isDark).toBe(false)

    act(() => result.current.toggle())

    expect(result.current.isDark).toBe(true)
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(localStorage.getItem('familiar-theme')).toBe('dark')
  })

  it('keeps a second instance in sync when the first toggles', () => {
    // THE regression: desktop sidebar + mobile header both mounted.
    const sidebar = renderHook(() => useTheme())
    const mobileHeader = renderHook(() => useTheme())

    expect(sidebar.result.current.isDark).toBe(false)
    expect(mobileHeader.result.current.isDark).toBe(false)

    act(() => sidebar.result.current.toggle())

    expect(sidebar.result.current.isDark).toBe(true)
    expect(mobileHeader.result.current.isDark).toBe(true)
  })

  it('syncs in both directions', () => {
    const sidebar = renderHook(() => useTheme())
    const mobileHeader = renderHook(() => useTheme())

    act(() => mobileHeader.result.current.toggle())
    expect(sidebar.result.current.isDark).toBe(true)

    act(() => sidebar.result.current.toggle())
    expect(mobileHeader.result.current.isDark).toBe(false)
  })

  it('does not leave a stale subscriber after unmount', () => {
    const sidebar = renderHook(() => useTheme())
    const transient = renderHook(() => useTheme())

    transient.unmount()

    // Toggling must not throw by writing into an unmounted instance.
    act(() => sidebar.result.current.toggle())
    expect(sidebar.result.current.isDark).toBe(true)
  })

  it('reads the stored choice on mount', () => {
    localStorage.setItem('familiar-theme', 'dark')
    const { result } = renderHook(() => useTheme())
    expect(result.current.isDark).toBe(true)
  })
})
