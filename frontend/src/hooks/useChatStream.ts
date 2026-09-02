import { useCallback, useEffect, useRef, useState } from 'react'
import { StreamInterrupted, getTurnStatus, startTurn, streamTurnEvents } from '../api/sse'
import type { Deck, DeckProposal } from '../types/api'

export interface DisplayMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  serverId?: number
}

export interface ProposalBatch {
  summary: string
  proposals: DeckProposal[]
  // The server id of the assistant message this batch belongs to, so it can
  // render inline where it happened. null for a live batch not yet anchored
  // (its assistant message id isn't known until the turn's `done` event).
  anchorMessageId: number | null
}

interface UseChatStreamOptions {
  onDeckUpdated?: (deck: Deck) => void
  onDeckProposal?: (batch: ProposalBatch) => void
  onConversationCreated?: (conversationId: number) => void
  // Fires when a turn finishes, with the persisted id of its final assistant
  // message — used to anchor this turn's proposal batches to that bubble.
  onTurnComplete?: (finalMessageId: number) => void
}

// Survives a refresh, so a turn started before the reload can be resumed. The
// server is executing it either way; this is just the pointer back to it.
const ACTIVE_TURN_KEY = 'familiar.activeTurn'

// Foreground reconnect attempts before giving up and telling the user. Six
// attempts with exponential backoff spans roughly 15s, which comfortably
// outlasts a network blip without spinning forever on a genuinely dead server.
const MAX_RECONNECT_ATTEMPTS = 6

interface ActiveTurn {
  turnId: string
  assistantId: string
  lastSeq: number
  // Text rendered so far. Kept alongside the cursor so a refresh resumes the
  // bubble where it was rather than re-streaming the turn from seq 0. The pair
  // must stay consistent: `text` is exactly the tokens up to `lastSeq`.
  text: string
}

function loadActiveTurn(): ActiveTurn | null {
  try {
    const raw = sessionStorage.getItem(ACTIVE_TURN_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<ActiveTurn>
    if (!parsed.turnId || typeof parsed.lastSeq !== 'number') return null
    return {
      turnId: parsed.turnId,
      assistantId: parsed.assistantId ?? 'local-assistant',
      lastSeq: parsed.lastSeq,
      text: parsed.text ?? '',
    }
  } catch {
    return null
  }
}

function saveActiveTurn(turn: ActiveTurn | null) {
  try {
    if (turn) sessionStorage.setItem(ACTIVE_TURN_KEY, JSON.stringify(turn))
    else sessionStorage.removeItem(ACTIVE_TURN_KEY)
  } catch {
    // sessionStorage can be unavailable (private mode, quota) or full. Resume
    // is a nicety; the turn still completes server-side regardless, and a
    // failed write just means resuming replays from seq 0 as before.
  }
}

export function useChatStream(options: UseChatStreamOptions = {}) {
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [activeTool, setActiveTool] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const nextId = useRef(0)

  // Mutable turn state, in refs because the visibilitychange handler and the
  // in-flight reader both read it outside React's render cycle.
  const activeTurn = useRef<ActiveTurn | null>(null)
  const assistantText = useRef('')
  const abortRef = useRef<AbortController | null>(null)
  // Whether the current turn has delivered its `done` or `error` event. A
  // stream can close cleanly without one (the server caps how long a single
  // stream stays open), and that close must not be mistaken for the end.
  const terminalSeen = useRef(false)

  const optionsRef = useRef(options)
  optionsRef.current = options

  const persistAt = useRef(0)

  /**
   * Write the resume record (cursor + rendered text) at most every 250ms.
   *
   * Tokens arrive in ~120ms batches, so an unthrottled write would stringify
   * the whole accumulated message several times a second. Losing the last
   * fraction of a second costs nothing: the seq saved with it is equally stale,
   * so a resume just replays those few events from the log and re-renders them.
   */
  const persistTurn = useCallback((force = false) => {
    const turn = activeTurn.current
    if (!turn) return
    const now = Date.now()
    if (!force && now - persistAt.current < 250) return
    persistAt.current = now
    saveActiveTurn({ ...turn, text: assistantText.current })
  }, [])

  const reset = useCallback((initial: DisplayMessage[] = []) => {
    setMessages(initial)
    nextId.current = initial.length
  }, [])

  const handleEvent = useCallback((evt: { event: string; data: any; seq: number }) => {
    const turn = activeTurn.current
    // Idempotency: a reconnect can overlap the events already applied. Without
    // this a resumed turn would duplicate tokens.
    if (turn && typeof evt.seq === 'number') {
      if (evt.seq <= turn.lastSeq) return
      turn.lastSeq = evt.seq
      // NB: the resume record is written at the END of this function, once the
      // text for this event has been applied. Saving here would pair the new
      // seq with the previous text, and a resume would then start after tokens
      // it never rendered — losing them permanently.
    }
    const assistantId = turn?.assistantId ?? 'local-assistant'

    if (evt.event === 'tool_call') {
      setActiveTool(evt.data.name)
    } else if (evt.event === 'token') {
      assistantText.current += evt.data.text
      setActiveTool(null)
      const text = assistantText.current
      setMessages((prev) => {
        const existing = prev.find((m) => m.id === assistantId)
        if (existing) {
          return prev.map((m) => (m.id === assistantId ? { ...m, text } : m))
        }
        return [...prev, { id: assistantId, role: 'assistant', text }]
      })
    } else if (evt.event === 'deck_proposal') {
      if (evt.data.ok) {
        optionsRef.current.onDeckProposal?.({
          summary: evt.data.summary,
          proposals: evt.data.proposals,
          anchorMessageId: null,
        })
      }
    } else if (evt.event === 'deck_updated') {
      optionsRef.current.onDeckUpdated?.(evt.data)
    } else if (evt.event === 'done') {
      terminalSeen.current = true
      const finalMessageId = evt.data.message_id
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, serverId: finalMessageId } : m)),
      )
      optionsRef.current.onTurnComplete?.(finalMessageId)
      optionsRef.current.onConversationCreated?.(evt.data.conversation_id)
    } else if (evt.event === 'error') {
      terminalSeen.current = true
      setError(evt.data.message)
    }

    // Cursor and text are saved together, after both have been updated, so the
    // pair is always consistent: `text` is exactly the tokens through `lastSeq`.
    persistTurn()
  }, [persistTurn])

  /**
   * Read a turn from its cursor until it ends, reconnecting through drops.
   *
   * A backgrounded tab or a sleeping phone kills the response body, which the
   * browser reports as a generic TypeError. That is not a failure worth showing
   * anyone — the turn is still executing server-side — so an interrupted read
   * reconnects from the last applied seq instead of surfacing an error. Only a
   * genuine failure (a 404 for the turn, a parse error) reaches setError.
   *
   * Safe to call repeatedly: it aborts any previous reader first, and the seq
   * cursor makes overlapping reads idempotent.
   */
  const consumeTurn = useCallback(
    async (turnId: string, from: number) => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
      setIsStreaming(true)

      let cursor = from
      let attempt = 0

      try {
        while (!controller.signal.aborted) {
          try {
            await streamTurnEvents(turnId, cursor, handleEvent, controller.signal)
            if (terminalSeen.current) break // ran to the end of the turn
            // A clean close with no terminal event: the server caps how long
            // one stream stays open and closes it exactly like a finished
            // turn. Ask, and pick a still-running turn back up from the cursor.
            const status = await getTurnStatus(turnId).catch(() => null)
            if (status?.status === 'running' && !controller.signal.aborted) {
              cursor = activeTurn.current?.lastSeq ?? cursor
              continue
            }
            break
          } catch (err) {
            if (controller.signal.aborted) return
            if (!(err instanceof StreamInterrupted)) throw err

            // Resume from wherever the render actually got to, which may be
            // well past `from` if the drop happened late in the turn.
            cursor = activeTurn.current?.lastSeq ?? cursor

            // A backgrounded tab cannot run timers reliably, so this loop is
            // really for foreground blips; visibilitychange handles the rest.
            if (document.visibilityState !== 'visible') return

            attempt += 1
            if (attempt > MAX_RECONNECT_ATTEMPTS) {
              setError('Lost connection to the assistant. It may still be working — reopen this conversation to catch up.')
              return
            }
            const delay = Math.min(250 * 2 ** (attempt - 1), 4000)
            await new Promise((r) => setTimeout(r, delay))
          }
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : String(err))
        }
        return
      } finally {
        if (!controller.signal.aborted) {
          setIsStreaming(false)
          setActiveTool(null)
        }
      }

      if (controller.signal.aborted) return
      // The stream ends when the turn is terminal, so the resume pointer has
      // done its job.
      activeTurn.current = null
      saveActiveTurn(null)
    },
    [handleEvent],
  )

  const sendMessage = useCallback(
    async (conversationId: number | 'new', text: string) => {
      setError(null)
      setMessages((prev) => [...prev, { id: `local-${nextId.current++}`, role: 'user', text }])
      setIsStreaming(true)
      setActiveTool(null)

      const assistantId = `local-${nextId.current++}`
      assistantText.current = ''

      try {
        const started = await startTurn(conversationId, text)
        terminalSeen.current = false
        activeTurn.current = { turnId: started.turn_id, assistantId, lastSeq: 0, text: '' }
        persistTurn(true)
        await consumeTurn(started.turn_id, 0)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
        setIsStreaming(false)
        setActiveTool(null)
      }
    },
    [consumeTurn, persistTurn],
  )

  // Resume on tab-return. Mobile browsers suspend a backgrounded tab's fetch
  // body, so the reader dies even though the turn keeps running server-side;
  // reconnecting from the cursor replays whatever was missed.
  useEffect(() => {
    const onVisibilityChange = () => {
      const turn = activeTurn.current
      if (!turn) return
      if (document.visibilityState === 'visible') {
        // Coming back is a fresh attempt: drop any "lost connection" notice
        // from the previous drop so a successful resume doesn't sit under a
        // stale error.
        setError(null)
        void consumeTurn(turn.turnId, turn.lastSeq)
      } else {
        // Going away is the moment the throttled write matters: a backgrounded
        // tab can be discarded outright, and this is the last chance to record
        // where the render got to.
        persistTurn(true)
      }
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [consumeTurn, persistTurn])

  // Resume across a full refresh, using the pointer left in sessionStorage.
  useEffect(() => {
    const stored = loadActiveTurn()
    if (!stored) return
    let cancelled = false
    void (async () => {
      try {
        const status = await getTurnStatus(stored.turnId)
        if (cancelled) return
        if (status.status !== 'running') {
          // Already finished while away. The message is in conversation history,
          // which the view loads on its own, so there is nothing to resume.
          saveActiveTurn(null)
          return
        }
        terminalSeen.current = false
        activeTurn.current = stored
        // Restore the bubble instead of re-streaming from seq 0. The text and
        // the cursor were written together, so picking up at stored.lastSeq
        // continues exactly where the render left off.
        assistantText.current = stored.text
        if (stored.text) {
          setMessages((prev) =>
            prev.some((m) => m.id === stored.assistantId)
              ? prev
              : [...prev, { id: stored.assistantId, role: 'assistant', text: stored.text }],
          )
        }
        void consumeTurn(stored.turnId, stored.lastSeq)
      } catch {
        saveActiveTurn(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [consumeTurn])

  useEffect(() => () => abortRef.current?.abort(), [])

  return { messages, sendMessage, reset, activeTool, isStreaming, error }
}
