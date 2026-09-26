import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import type { DeckSummary, Grade, GradingBackendSummary, GradingBatch } from '../types/api'
import { CardPinProvider } from './CardPinContext'
import { CardPreview } from './CardPreview'

// Grading real suggestion batches: the eval can only score agreement with
// cards already in a deck, so a good card the player never ran counts as a
// miss. This measures how many picks are actually bad. Batches come from the
// Jev or the LLM selector at random, and the server withholds which until the
// batch is fully graded.

const GRADES: { value: Grade; label: string }[] = [
  { value: 'good', label: 'Good' },
  { value: 'fine', label: 'Fine' },
  { value: 'bad', label: 'Bad' },
]
const SET_SIZE = 20

function pct(value: number | null | undefined): string {
  return value == null ? '–' : `${Math.round(value * 100)}%`
}

function Summary({ rows }: { rows: Record<string, GradingBackendSummary> }) {
  const labels: Record<string, string> = { jev: 'Jev', llm: 'LLM' }
  const keys = Object.keys(rows)
  if (!keys.length) {
    return <p className="grading__muted">Grade every pick in a batch to see its selector and the running totals.</p>
  }
  return (
    <table className="grading__summary">
      <thead>
        <tr><th>Selector</th><th>Batches</th><th>Picks</th><th>Bad</th><th>Good</th><th>Avg time</th></tr>
      </thead>
      <tbody>
        {keys.map((k) => (
          <tr key={k}>
            <td>{labels[k] ?? k}</td>
            <td>{rows[k].batches}</td>
            <td>{rows[k].picks}</td>
            <td className="grading__bad">{pct(rows[k].bad_rate)}</td>
            <td>{pct(rows[k].good_rate)}</td>
            <td>{rows[k].mean_seconds == null ? '–' : `${rows[k].mean_seconds.toFixed(1)}s`}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function BatchCard({ batch, onChange, onDelete }: {
  batch: GradingBatch
  onChange: (b: GradingBatch) => void
  onDelete: (id: number) => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const graded = batch.picks.filter((p) => p.grade).length

  const grade = async (name: string, value: Grade) => {
    setBusy(name)
    try {
      onChange(await api.gradePick(batch.id, name, value))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className={`grading__batch ${batch.complete ? 'grading__batch--done' : ''}`}>
      <header className="grading__batch-head">
        <div>
          <div className="grading__deck">{batch.deck_name ?? `Deck ${batch.deck_id}`}</div>
          <div className="grading__intent">“{batch.intent}”</div>
        </div>
        <div className="grading__batch-meta">
          <span>{graded}/{batch.picks.length} graded</span>
          {batch.backend && (
            <span className="grading__chip grading__chip--reveal">
              {batch.backend === 'jev' ? 'Jev' : 'LLM'}{batch.seconds != null ? ` · ${batch.seconds}s` : ''}
            </span>
          )}
          <button className="btn btn--ghost" onClick={() => onDelete(batch.id)} title="Delete batch">Delete</button>
        </div>
      </header>
      {batch.picks.length === 0 && <p className="grading__muted">This batch came back empty.</p>}
      <ol className="grading__picks">
        {batch.picks.map((p) => (
          <li key={p.name} className={`grading__pick ${p.grade ? `grading__pick--${p.grade}` : ''}`}>
            <div className="grading__pick-main">
              <div className="grading__pick-title">
                <CardPreview name={p.name} className="grading__name" />
                {p.mana_cost && <span className="grading__cost">{p.mana_cost}</span>}
                {p.off_page && <span className="grading__chip" title="Not on this commander's EDHREC page">Off EDHREC</span>}
              </div>
              {p.type_line && <div className="grading__type">{p.type_line}</div>}
              {p.oracle_text && <div className="grading__oracle">{p.oracle_text}</div>}
              {p.reason && <div className="grading__reason">{p.reason}</div>}
            </div>
            <div className="grading__buttons" role="group" aria-label={`Grade ${p.name}`}>
              {GRADES.map((g) => (
                <button
                  key={g.value}
                  className={`btn btn--chip grading__grade grading__grade--${g.value} ${p.grade === g.value ? 'grading__grade--on' : ''}`}
                  disabled={busy === p.name}
                  aria-pressed={p.grade === g.value}
                  onClick={() => grade(p.name, g.value)}
                >
                  {g.label}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}

export function GradingPage() {
  const [decks, setDecks] = useState<DeckSummary[]>([])
  const [requests, setRequests] = useState<string[]>([])
  const [batches, setBatches] = useState<GradingBatch[]>([])
  const [summary, setSummary] = useState<Record<string, GradingBackendSummary>>({})
  const [deckId, setDeckId] = useState<number | null>(null)
  const [intent, setIntent] = useState('')
  const [progress, setProgress] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hideDone, setHideDone] = useState(true)
  const cancelled = useRef(false)

  const refreshSummary = useCallback(() => {
    api.gradingSummary().then((s) => setSummary(s.by_backend)).catch(() => {})
  }, [])

  useEffect(() => {
    api.listDecks().then((d) => {
      const usable = d.filter((x) => x.commander)
      setDecks(usable)
      if (usable.length) setDeckId((cur) => cur ?? usable[0].id)
    }).catch((e) => setError(String(e)))
    api.gradingRequests().then((r) => {
      setRequests(r.requests)
      setIntent((cur) => cur || r.requests[0] || '')
    }).catch(() => {})
    api.listGradingBatches().then(setBatches).catch((e) => setError(String(e)))
    refreshSummary()
  }, [refreshSummary])

  const onChange = useCallback((b: GradingBatch) => {
    setBatches((cur) => cur.map((x) => (x.id === b.id ? b : x)))
    if (b.complete) refreshSummary()
  }, [refreshSummary])

  const onDelete = useCallback(async (id: number) => {
    await api.deleteGradingBatch(id)
    setBatches((cur) => cur.filter((x) => x.id !== id))
    refreshSummary()
  }, [refreshSummary])

  const generateOne = async (deck: number, request: string) => {
    const batch = await api.createGradingBatch(deck, request)
    setBatches((cur) => [batch, ...cur])
  }

  const generate = async () => {
    if (deckId == null || !intent.trim()) return
    setError(null)
    setProgress('Generating…')
    try {
      await generateOne(deckId, intent.trim())
    } catch (e) {
      setError(String(e))
    } finally {
      setProgress(null)
    }
  }

  // A varied set in one go: random deck and request pairs, one at a time so a
  // slow LLM batch shows progress instead of one long silent wait.
  const generateSet = async () => {
    if (!decks.length || !requests.length) return
    cancelled.current = false
    setError(null)
    const pairs = decks.flatMap((d) => requests.map((r) => [d.id, r] as const))
    pairs.sort(() => Math.random() - 0.5)
    const chosen = pairs.slice(0, SET_SIZE)
    for (let i = 0; i < chosen.length; i++) {
      if (cancelled.current) break
      setProgress(`Generating ${i + 1} of ${chosen.length}…`)
      try {
        await generateOne(chosen[i][0], chosen[i][1])
      } catch (e) {
        setError(String(e))
      }
    }
    setProgress(null)
  }

  const shown = useMemo(() => (hideDone ? batches.filter((b) => !b.complete) : batches), [batches, hideDone])
  const doneCount = batches.filter((b) => b.complete).length

  return (
    <CardPinProvider>
      <div className="grading">
        <header className="grading__top">
          <div>
            <h1 className="grading__title">Grade picks</h1>
            <p className="grading__muted">
              Mark each suggested card good, fine, or bad for its deck and request. Each batch comes from Jev or the
              LLM selector at random, revealed once the batch is fully graded.
            </p>
          </div>
          <a className="btn btn--secondary" href="#">Back to Familiar</a>
        </header>

        <Summary rows={summary} />

        <div className="grading__controls">
          <select className="grading__input" value={deckId ?? ''} onChange={(e) => setDeckId(Number(e.target.value))}>
            {decks.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
          <input
            className="grading__input grading__input--wide"
            list="grading-requests"
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
            placeholder="What to ask for"
          />
          <datalist id="grading-requests">
            {requests.map((r) => <option key={r} value={r} />)}
          </datalist>
          <button className="btn btn--primary" disabled={!!progress} onClick={generate}>Generate batch</button>
          <button className="btn btn--secondary" disabled={!!progress} onClick={generateSet}>
            Generate a set of {SET_SIZE}
          </button>
          {progress && (
            <>
              <span className="grading__muted">{progress}</span>
              <button className="btn btn--ghost" onClick={() => { cancelled.current = true }}>Stop</button>
            </>
          )}
        </div>
        {error && <p className="grading__error">{error}</p>}

        <label className="grading__toggle">
          <input type="checkbox" checked={hideDone} onChange={(e) => setHideDone(e.target.checked)} />
          Hide graded batches ({doneCount})
        </label>

        {shown.length === 0 && !progress && (
          <p className="grading__muted">No batches to grade. Generate one, or a set of {SET_SIZE}.</p>
        )}
        {shown.map((b) => <BatchCard key={b.id} batch={b} onChange={onChange} onDelete={onDelete} />)}
      </div>
    </CardPinProvider>
  )
}
