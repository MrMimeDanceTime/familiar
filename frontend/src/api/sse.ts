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
export async function streamTurnEvents(
  turnId: string,
  after: number,
  onEvent: (event: SseEvent & { seq: number }) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(
    `/api/chat/turns/${turnId}/events?after=${after}`,
    { headers: { Accept: 'text/event-stream' }, signal },
  )

  if (!resp.ok || !resp.body) {
    throw new Error(`Turn stream failed: ${resp.status}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
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
      onEvent({ event: eventName, data: JSON.parse(dataLine) } as SseEvent & { seq: number })
    }
  }
}

export async function getTurnStatus(
  turnId: string,
): Promise<{ status: string; conversation_id: number; error: string | null }> {
  const resp = await fetch(`/api/chat/turns/${turnId}`)
  if (!resp.ok) throw new Error(`Turn status failed: ${resp.status}`)
  return resp.json()
}
