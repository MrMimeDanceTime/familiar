import { useCallback, useEffect, useRef, useState } from 'react'
import { getTurnStatus, startTurn, streamTurnEvents } from '../api/sse'
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

interface ActiveTurn {
  turnId: string
  assistantId: string
  lastSeq: number
}

function loadActiveTurn(): ActiveTurn | null {
  try {
    const raw = sessionStorage.getItem(ACTIVE_TURN_KEY)
    return raw ? (JSON.parse(raw) as ActiveTurn) : null
  } catch {
    return null
  }
}

function saveActiveTurn(turn: ActiveTurn | null) {
  try {
    if (turn) sessionStorage.setItem(ACTIVE_TURN_KEY, JSON.stringify(turn))
    else sessionStorage.removeItem(ACTIVE_TURN_KEY)
  } catch {
    // sessionStorage can be unavailable (private mode, quota). Resume is a
    // nicety; the turn still completes server-side regardless.
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

  const optionsRef = useRef(options)
  optionsRef.current = options

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
      saveActiveTurn(turn)
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
      const finalMessageId = evt.data.message_id
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, serverId: finalMessageId } : m)),
      )
      optionsRef.current.onTurnComplete?.(finalMessageId)
      optionsRef.current.onConversationCreated?.(evt.data.conversation_id)
    } else if (evt.event === 'error') {
      setError(evt.data.message)
    }
  }, [])

  /** Read a turn from its cursor until it ends. Safe to call repeatedly. */
  const consumeTurn = useCallback(
    async (turnId: string, from: number) => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
      setIsStreaming(true)
      try {
        await streamTurnEvents(turnId, from, handleEvent, controller.signal)
      } catch (err) {
        // An aborted read is a tab-switch or a new turn, not a failure.
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
        activeTurn.current = { turnId: started.turn_id, assistantId, lastSeq: 0 }
        saveActiveTurn(activeTurn.current)
        await consumeTurn(started.turn_id, 0)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
        setIsStreaming(false)
        setActiveTool(null)
      }
    },
    [consumeTurn],
  )

  // Resume on tab-return. Mobile browsers suspend a backgrounded tab's fetch
  // body, so the reader dies even though the turn keeps running server-side;
  // reconnecting from the cursor replays whatever was missed.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      const turn = activeTurn.current
      if (turn) void consumeTurn(turn.turnId, turn.lastSeq)
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [consumeTurn])

  // Resume across a full refresh, using the pointer left in sessionStorage.
  useEffect(() => {
    const stored = loadActiveTurn()
    if (!stored) return
    let cancelled = false
    void (async () => {
      try {
        const status = await getTurnStatus(stored.turnId)
        if (cancelled) return
        if (status.status === 'running') {
          activeTurn.current = stored
          assistantText.current = ''
          // Replay from scratch: the rendered bubble did not survive the reload.
          void consumeTurn(stored.turnId, 0)
        } else {
          saveActiveTurn(null)
        }
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
