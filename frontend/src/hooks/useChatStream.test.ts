import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useChatStream } from './useChatStream'
import { installFetchMock, type StreamHandle } from '../test/sseFixture'
import { fireVisibilityChange } from '../test/setup'

/**
 * Covers the streaming/reconnect path, which is where every shipped bug in this
 * feature has lived. Three of them are pinned here directly:
 *
 *  1. A dropped response body arrives as a generic TypeError, not an abort. It
 *     was being rendered as a user-visible error.
 *  2. An interrupted read gave up and waited for a visibilitychange that never
 *     came if the tab was already visible.
 *  3. Reconnecting used the seq the read STARTED at rather than the last seq
 *     applied, replaying events already rendered.
 */

let stream: StreamHandle

beforeEach(() => {
  stream = installFetchMock()
})

/** Start a turn and wait until the client is reading it. */
async function startTurn(hook: { current: ReturnType<typeof useChatStream> }) {
  act(() => {
    void hook.current.sendMessage('new', 'build me a deck')
  })
  await stream.waitForConnect(1)
}

describe('happy path', () => {
  it('renders streamed tokens into one assistant message', async () => {
    const { result } = renderHook(() => useChatStream())
    await startTurn(result)

    act(() => {
      stream.send('token', { text: 'Looking ', seq: 1 })
      stream.send('token', { text: 'at your deck.', seq: 2 })
    })

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant')
      expect(assistant?.text).toBe('Looking at your deck.')
    })
    expect(result.current.error).toBeNull()
  })

  it('surfaces the active tool, then clears it when text resumes', async () => {
    const { result } = renderHook(() => useChatStream())
    await startTurn(result)

    act(() => {
      stream.send('tool_call', { name: 'scryfall_search', arguments: {}, seq: 1 })
    })
    await waitFor(() => expect(result.current.activeTool).toBe('scryfall_search'))

    act(() => {
      stream.send('token', { text: 'Found some.', seq: 2 })
    })
    await waitFor(() => expect(result.current.activeTool).toBeNull())
  })

  it('stops streaming when the turn completes', async () => {
    const onTurnComplete = vi.fn()
    const { result } = renderHook(() => useChatStream({ onTurnComplete }))
    await startTurn(result)

    act(() => {
      stream.send('token', { text: 'Done.', seq: 1 })
      stream.send('done', { message_id: 42, conversation_id: 1, seq: 2 })
      stream.close()
    })

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(onTurnComplete).toHaveBeenCalledWith(42)
    // The resume pointer is cleared once the turn is terminal.
    expect(sessionStorage.getItem('familiar.activeTurn')).toBeNull()
  })

  it('ignores keep-alive comments', async () => {
    const { result } = renderHook(() => useChatStream())
    await startTurn(result)

    act(() => {
      stream.sendRaw(': ping\n\n')
      stream.send('token', { text: 'still here', seq: 1 })
    })

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant')
      expect(assistant?.text).toBe('still here')
    })
    expect(result.current.error).toBeNull()
  })
})

describe('dropped connections', () => {
  it('does not surface an error when the body dies mid-stream', async () => {
    // THE bug: a backgrounded tab kills the body, the browser throws a generic
    // TypeError, and signal.aborted is false — so it went straight to setError
    // and the user saw "error in input stream".
    const { result } = renderHook(() => useChatStream())
    await startTurn(result)

    act(() => {
      stream.send('token', { text: 'partial', seq: 1 })
    })
    await waitFor(() => expect(result.current.messages.length).toBeGreaterThan(1))

    act(() => {
      stream.killStream()
    })

    // It reconnects instead of erroring.
    await stream.waitForConnect(2)
    expect(result.current.error).toBeNull()
  })

  it('reconnects from the last applied seq, not the seq it started at', async () => {
    const { result } = renderHook(() => useChatStream())
    await startTurn(result)

    act(() => {
      stream.send('token', { text: 'one ', seq: 1 })
      stream.send('token', { text: 'two ', seq: 2 })
      stream.send('token', { text: 'three ', seq: 3 })
    })
    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant')
      expect(assistant?.text).toBe('one two three ')
    })

    act(() => {
      stream.killStream()
    })
    await stream.waitForConnect(2)

    // Resuming from 0 would replay everything already rendered.
    expect(stream.lastAfter).toBe(3)
  })

  it('does not duplicate events replayed across a reconnect', async () => {
    const { result } = renderHook(() => useChatStream())
    await startTurn(result)

    act(() => {
      stream.send('token', { text: 'alpha ', seq: 1 })
      stream.send('token', { text: 'beta ', seq: 2 })
    })
    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant')
      expect(assistant?.text).toBe('alpha beta ')
    })

    act(() => {
      stream.killStream()
    })
    await stream.waitForConnect(2)

    // An overlapping replay: the server re-sends seq 2 alongside seq 3.
    act(() => {
      stream.send('token', { text: 'beta ', seq: 2 })
      stream.send('token', { text: 'gamma', seq: 3 })
    })

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant')
      expect(assistant?.text).toBe('alpha beta gamma')
    })
  })

  it('gives up with a clear message after repeated failures', async () => {
    vi.useFakeTimers()
    try {
      const { result } = renderHook(() => useChatStream())
      act(() => {
        void result.current.sendMessage('new', 'hi')
      })
      await vi.waitFor(() => expect(stream.connectCount).toBeGreaterThanOrEqual(1))

      // Kill every reconnection attempt; backoff runs to exhaustion.
      for (let i = 0; i < 8; i += 1) {
        act(() => {
          stream.killStream()
        })
        await act(async () => {
          await vi.advanceTimersByTimeAsync(5000)
        })
      }

      await vi.waitFor(() => expect(result.current.error).toBeTruthy())
      expect(result.current.error).toMatch(/still be working/i)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not spin on backoff while the tab is hidden', async () => {
    const { result } = renderHook(() => useChatStream())
    await startTurn(result)

    // A backgrounded tab can't run timers reliably, so the loop must stand down
    // and let visibilitychange own the resume.
    fireVisibilityChange('hidden')
    act(() => {
      stream.killStream()
    })

    await new Promise((r) => setTimeout(r, 50))
    expect(stream.connectCount).toBe(1)
    expect(result.current.error).toBeNull()
  })
})

describe('tab return', () => {
  it('reconnects from the cursor when the tab becomes visible', async () => {
    const { result } = renderHook(() => useChatStream())
    await startTurn(result)

    act(() => {
      stream.send('token', { text: 'before ', seq: 1 })
    })
    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant')
      expect(assistant?.text).toBe('before ')
    })

    fireVisibilityChange('hidden')
    act(() => {
      stream.killStream()
    })
    fireVisibilityChange('visible')

    await stream.waitForConnect(2)
    expect(stream.lastAfter).toBe(1)

    act(() => {
      stream.send('token', { text: 'after', seq: 2 })
    })
    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant')
      expect(assistant?.text).toBe('before after')
    })
  })
})

describe('resume across a refresh', () => {
  it('restores the rendered text and continues from its cursor', async () => {
    // Simulates what a reload finds in sessionStorage.
    sessionStorage.setItem(
      'familiar.activeTurn',
      JSON.stringify({
        turnId: 'turn-abc',
        assistantId: 'local-1',
        lastSeq: 2,
        text: 'restored text ',
      }),
    )

    const { result } = renderHook(() => useChatStream())

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant')
      expect(assistant?.text).toBe('restored text ')
    })
    await stream.waitForConnect(1)
    // Continues where the render left off rather than replaying the turn.
    expect(stream.lastAfter).toBe(2)

    act(() => {
      stream.send('token', { text: 'and more', seq: 3 })
    })
    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant')
      expect(assistant?.text).toBe('restored text and more')
    })
  })

  it('drops the pointer when the turn already finished', async () => {
    stream = installFetchMock({
      turnStatus: () => ({ status: 'done', conversation_id: 1, error: null }),
    })
    sessionStorage.setItem(
      'familiar.activeTurn',
      JSON.stringify({ turnId: 'turn-abc', assistantId: 'local-1', lastSeq: 5, text: 'old' }),
    )

    renderHook(() => useChatStream())

    await waitFor(() => {
      expect(sessionStorage.getItem('familiar.activeTurn')).toBeNull()
    })
    // No point tailing a turn that is already over.
    expect(stream.connectCount).toBe(0)
  })
})

describe('resume record', () => {
  it('keeps the saved cursor consistent with the saved text', async () => {
    // The invariant that a wrong write order breaks: `text` must be exactly the
    // tokens through `lastSeq`. Saving before applying an event pairs a new seq
    // with old text, and the resume then skips tokens it never rendered.
    const { result } = renderHook(() => useChatStream())
    await startTurn(result)

    act(() => {
      stream.send('token', { text: 'aaa ', seq: 1 })
      stream.send('token', { text: 'bbb ', seq: 2 })
      stream.send('token', { text: 'ccc', seq: 3 })
    })

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant')
      expect(assistant?.text).toBe('aaa bbb ccc')
    })

    // Force the throttled write to land.
    fireVisibilityChange('hidden')

    await waitFor(() => {
      const saved = JSON.parse(sessionStorage.getItem('familiar.activeTurn') ?? '{}')
      expect(saved.lastSeq).toBe(3)
      expect(saved.text).toBe('aaa bbb ccc')
    })
  })
})

describe('stopping a turn', () => {
  it('asks the server to cancel and keeps reading until done', async () => {
    const { result } = renderHook(() => useChatStream())
    await startTurn(result)

    act(() => {
      stream.send('token', { text: 'Half a ', seq: 1 })
    })
    await act(async () => {
      await result.current.stopTurn()
    })
    expect(stream.cancelled).toEqual(['turn-abc'])
    expect(result.current.isStreaming).toBe(true)

    act(() => {
      stream.send('token', { text: '(stopped)', seq: 2 })
      stream.send('done', { message_id: 3, conversation_id: 1, seq: 3 })
      stream.close()
    })
    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.messages.find((m) => m.role === 'assistant')?.text).toBe('Half a (stopped)')
  })

  it('is a no-op with no turn running', async () => {
    const { result } = renderHook(() => useChatStream())
    await act(async () => {
      await result.current.stopTurn()
    })
    expect(stream.cancelled).toEqual([])
  })
})
