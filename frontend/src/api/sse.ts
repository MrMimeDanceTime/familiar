import type { SseEvent } from '../types/api'

export interface StartedTurn {
  turn_id: string
  conversation_id: number
}

/**
 * Start a chat turn. Returns as soon as the server has accepted it.
 *
 * The turn then runs on the server independently of this browser, so closing
 * the tab, locking the phone, or losing the network no longer kills it. Read it
 * back with streamTurnEvents.
 */
export async function startTurn(
  conversationId: number | 'new',
  message: string,
  signal?: AbortSignal,
): Promise<StartedTurn> {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId, message }),
    signal,
  })
  if (!resp.ok) {
    throw new Error(`Chat request failed: ${resp.status}`)
  }
  return resp.json()
}

/**
 * Read a turn's events from `after`, then follow it live.
 *
 * Pure reader — calling it again with the last seq seen is always safe, which
 * is what makes reconnect, refresh, and cold load a single code path. onEvent
 * receives every event with its `seq`; the caller is responsible for ignoring
 * anything it has already applied.
 */
/**
 * Thrown when the connection drops mid-stream rather than the request failing.
 *
 * A backgrounded tab, a sleeping phone, or a flaky network kills the response
 * body while it is being read. The browser surfaces that as a generic TypeError
 * ("error in input stream", "network error", "Load failed" — the wording is
 * per-engine), which is indistinguishable from a real failure unless it is
 * classified here. It is not an error the user should see: the turn is still
 * running on the server and the caller can simply reconnect from its cursor.
 */
export class StreamInterrupted extends Error {
  constructor(cause: unknown) {
    super('Turn stream interrupted', { cause })
    this.name = 'StreamInterrupted'
  }
}

/** HTTP statuses worth retrying: the server is up but momentarily unhappy. */
function isTransientStatus(status: number): boolean {
  return status === 429 || (status >= 500 && status < 600)
}

export async function streamTurnEvents(
  turnId: string,
  after: number,
  onEvent: (event: SseEvent & { seq: number }) => void,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response
  try {
    resp = await fetch(
      `/api/chat/turns/${turnId}/events?after=${after}`,
      { headers: { Accept: 'text/event-stream' }, signal },
    )
  } catch (err) {
    // Could not even connect (offline, phone asleep). Retryable, not fatal.
    if (signal?.aborted) throw err
    throw new StreamInterrupted(err)
  }

  if (!resp.ok || !resp.body) {
    if (isTransientStatus(resp.status)) throw new StreamInterrupted(resp.status)
    throw new Error(`Turn stream failed: ${resp.status}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    let chunk: ReadableStreamReadResult<Uint8Array>
    try {
      chunk = await reader.read()
    } catch (err) {
      // The body died underneath us. Our own aborts re-throw so the caller can
      // tell "I cancelled this" from "the network cut out".
      if (signal?.aborted) throw err
      throw new StreamInterrupted(err)
    }
    const { done, value } = chunk
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''

    for (const chunk of chunks) {
      const lines = chunk.split('\n')
      let eventName = ''
      let dataLine = ''
      for (const line of lines) {
        if (line.startsWith('event: ')) eventName = line.slice('event: '.length)
        else if (line.startsWith('data: ')) dataLine = line.slice('data: '.length)
      }
      if (!eventName || !dataLine) continue

      // The server folds `seq` into the data payload, so lift it onto the
      // envelope the caller reads. Without this `evt.seq` is undefined, the
      // cursor never advances, and every reconnect replays the turn from 0 —
      // duplicating text the client had already rendered.
      const data = JSON.parse(dataLine) as Record<string, unknown>
      const seq = typeof data.seq === 'number' ? data.seq : undefined
      onEvent({ event: eventName, data, seq } as unknown as SseEvent & { seq: number })
    }
  }
}

/**
 * Ask the server to stop a running turn. The engine finishes the turn with
 * whatever it has at its next checkpoint, so the stream still ends with a
 * `done` event; the caller keeps reading until then.
 */
export async function cancelTurn(turnId: string): Promise<void> {
  const resp = await fetch(`/api/chat/turns/${turnId}/cancel`, { method: 'POST' })
  if (!resp.ok) throw new Error(`Cancel failed: ${resp.status}`)
}

export async function getTurnStatus(
  turnId: string,
): Promise<{ status: string; conversation_id: number; error: string | null }> {
  const resp = await fetch(`/api/chat/turns/${turnId}`)
  if (!resp.ok) throw new Error(`Turn status failed: ${resp.status}`)
  return resp.json()
}
