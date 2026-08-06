import { describe, expect, it, vi } from 'vitest'

import { StreamInterrupted, streamTurnEvents } from './sse'

/**
 * Pins the wire contract between the SSE endpoint and the client.
 *
 * The server folds `seq` into the data payload (`{**event.data, "seq": ...}`),
 * and the client lifts it onto the event envelope. That mismatch shipped once:
 * `evt.seq` was undefined, so the cursor never advanced and every reconnect
 * replayed the whole turn, duplicating rendered text.
 */

function streamOf(...frames: string[]): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const frame of frames) c.enqueue(encoder.encode(frame))
      c.close()
    },
  })
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('streamTurnEvents', () => {
  it('lifts seq out of the data payload onto the event', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => streamOf(
      'event: token\ndata: {"text": "hi", "seq": 1}\n\n',
      'event: done\ndata: {"message_id": 7, "conversation_id": 1, "seq": 2}\n\n',
    )))

    const seen: Array<{ event: string; seq: number }> = []
    await streamTurnEvents('t1', 0, (e) => seen.push({ event: e.event, seq: e.seq }))

    expect(seen).toEqual([
      { event: 'token', seq: 1 },
      { event: 'done', seq: 2 },
    ])
  })

  it('parses events split across chunk boundaries', async () => {
    // A real socket does not respect frame boundaries.
    vi.stubGlobal('fetch', vi.fn(async () => streamOf(
      'event: tok',
      'en\ndata: {"text": "spl',
      'it", "seq": 1}\n\n',
    )))

    const seen: Array<Record<string, unknown>> = []
    await streamTurnEvents('t1', 0, (e) => seen.push(e.data as Record<string, unknown>))

    expect(seen).toEqual([{ text: 'split', seq: 1 }])
  })

  it('ignores keep-alive comments', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => streamOf(
      ': open\n\n',
      ': ping\n\n',
      'event: token\ndata: {"text": "real", "seq": 1}\n\n',
    )))

    const seen: string[] = []
    await streamTurnEvents('t1', 0, (e) => seen.push(e.event))

    expect(seen).toEqual(['token'])
  })

  it('sends the cursor as the after parameter', async () => {
    const fetchMock = vi.fn(async () => streamOf())
    vi.stubGlobal('fetch', fetchMock)

    await streamTurnEvents('turn-xyz', 12, () => {})

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/chat/turns/turn-xyz/events?after=12',
      expect.anything(),
    )
  })

  it('classifies a body that dies mid-read as interrupted, not failed', async () => {
    const encoder = new TextEncoder()
    vi.stubGlobal('fetch', vi.fn(async () => {
      const body = new ReadableStream<Uint8Array>({
        start(c) {
          c.enqueue(encoder.encode('event: token\ndata: {"text": "partial", "seq": 1}\n\n'))
          // What a browser does to a backgrounded tab: a generic TypeError.
          c.error(new TypeError('error in input stream'))
        },
      })
      return new Response(body, { status: 200 })
    }))

    await expect(streamTurnEvents('t1', 0, () => {})).rejects.toBeInstanceOf(StreamInterrupted)
  })

  it('classifies a 5xx as interrupted so the caller retries', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 503 })))
    await expect(streamTurnEvents('t1', 0, () => {})).rejects.toBeInstanceOf(StreamInterrupted)
  })

  it('treats a missing turn as a real failure, not something to retry', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('gone', { status: 404 })))

    const err = await streamTurnEvents('t1', 0, () => {}).catch((e) => e)
    expect(err).toBeInstanceOf(Error)
    expect(err).not.toBeInstanceOf(StreamInterrupted)
  })

  it('re-throws our own aborts rather than masking them as interruptions', async () => {
    const controller = new AbortController()
    vi.stubGlobal('fetch', vi.fn(async () => {
      controller.abort()
      throw new DOMException('Aborted', 'AbortError')
    }))

    const err = await streamTurnEvents('t1', 0, () => {}, controller.signal).catch((e) => e)
    expect(err).not.toBeInstanceOf(StreamInterrupted)
  })
})
