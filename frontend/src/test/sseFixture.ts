import { vi } from 'vitest'

/**
 * A controllable fake of the SSE endpoints, so tests can reproduce the failures
 * that actually shipped.
 *
 * The important capability is `killStream()`: a real backgrounded tab has its
 * response body destroyed underneath the reader, and the browser surfaces that
 * as a generic TypeError, not an abort. Two bugs hid in that distinction
 * (a dropped body being reported as a user-visible error, and an interrupted
 * read never retrying), and neither is reachable without being able to break a
 * stream mid-flight.
 */

export interface StreamHandle {
  /** Push one SSE event to the connected reader. */
  send(event: string, data: Record<string, unknown>): void
  /** Push a raw frame, e.g. a `: ping` keep-alive comment. */
  sendRaw(frame: string): void
  /** Close cleanly, as the server does when a turn goes terminal. */
  close(): void
  /** Destroy the body mid-read, as a backgrounded tab does. */
  killStream(): void
  /** How many times the client has connected to this turn. */
  readonly connectCount: number
  /** The `after` cursor of the most recent connection. */
  readonly lastAfter: number
  /** Resolves once the client has connected at least `n` times. */
  waitForConnect(n?: number): Promise<void>
}

interface Controller {
  enqueue(chunk: Uint8Array): void
  error(err: unknown): void
  close(): void
}

export function installFetchMock(options: {
  turnId?: string
  turnStatus?: () => { status: string; conversation_id: number; error: string | null }
} = {}): StreamHandle {
  const turnId = options.turnId ?? 'turn-abc'
  const encoder = new TextEncoder()

  let controller: Controller | null = null
  let connectCount = 0
  let lastAfter = 0
  const connectWaiters: Array<{ n: number; resolve: () => void }> = []

  function notifyConnect() {
    for (let i = connectWaiters.length - 1; i >= 0; i -= 1) {
      if (connectCount >= connectWaiters[i].n) {
        connectWaiters[i].resolve()
        connectWaiters.splice(i, 1)
      }
    }
  }

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()

      if (url === '/api/chat' && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ turn_id: turnId, conversation_id: 1 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }

      const statusMatch = url.match(/^\/api\/chat\/turns\/([^/?]+)$/)
      if (statusMatch) {
        const body = options.turnStatus?.() ?? {
          status: 'running',
          conversation_id: 1,
          error: null,
        }
        return new Response(JSON.stringify(body), { status: 200 })
      }

      const eventsMatch = url.match(/^\/api\/chat\/turns\/([^/?]+)\/events\?after=(\d+)$/)
      if (eventsMatch) {
        connectCount += 1
        lastAfter = Number(eventsMatch[2])
        notifyConnect()

        const body = new ReadableStream<Uint8Array>({
          start(c) {
            controller = c as unknown as Controller
          },
          cancel() {
            controller = null
          },
        })

        // Mirror the real endpoint's abort behavior: aborting the request
        // rejects the read with an AbortError rather than a TypeError.
        if (init?.signal) {
          const signal = init.signal
          const onAbort = () => {
            try {
              controller?.error(new DOMException('Aborted', 'AbortError'))
            } catch {
              /* already closed */
            }
          }
          if (signal.aborted) onAbort()
          else signal.addEventListener('abort', onAbort, { once: true })
        }

        return new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }

      throw new Error(`Unexpected fetch in test: ${url}`)
    }),
  )

  return {
    send(event, data) {
      controller?.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`))
    },
    sendRaw(frame) {
      controller?.enqueue(encoder.encode(frame))
    },
    close() {
      controller?.close()
      controller = null
    },
    killStream() {
      // What a browser actually does to a backgrounded tab's response body:
      // a generic TypeError, NOT an AbortError.
      controller?.error(new TypeError('error in input stream'))
      controller = null
    },
    get connectCount() {
      return connectCount
    },
    get lastAfter() {
      return lastAfter
    },
    waitForConnect(n = 1) {
      if (connectCount >= n) return Promise.resolve()
      return new Promise<void>((resolve) => connectWaiters.push({ n, resolve }))
    },
  }
}
