import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import type { DeckProvider, ImportMode, ImportResult } from '../types/api'

interface ImportPanelProps {
  deckId: number
  anchorRef: React.RefObject<HTMLElement | null>
  onImported: (result: ImportResult) => void
  onClose: () => void
}

// `source` drives which input is shown: null = just the source chips,
// "text" = paste box, otherwise a provider name = that provider's URL box.
type Source = null | 'text' | string

const POP_WIDTH = 300

export function ImportPanel({ deckId, anchorRef, onImported, onClose }: ImportPanelProps) {
  const [providers, setProviders] = useState<DeckProvider[]>([])
  const [source, setSource] = useState<Source>(null)
  const [mode, setMode] = useState<ImportMode>('merge')
  const [text, setText] = useState('')
  const [ref, setRef] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rowErrors, setRowErrors] = useState<string[]>([])
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const popoverRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.listProviders()
      .then((r) => setProviders(r.providers.filter((p) => p.supports_fetch)))
      .catch(() => setProviders([]))
  }, [])

  // Position the fixed popover under the anchor button, right-aligned, and
  // clamped to the viewport. Recomputed on open, scroll, and resize so it
  // tracks the button even though it's portaled to <body>.
  useLayoutEffect(() => {
    const place = () => {
      const el = anchorRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      const width = Math.min(POP_WIDTH, window.innerWidth - 16)
      let left = r.right - width           // right-align to the button
      left = Math.max(8, Math.min(left, window.innerWidth - width - 8))
      setPos({ top: r.bottom + 6, left })
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [anchorRef])

  // Close on outside click (ignoring the anchor) or Escape.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (popoverRef.current?.contains(t)) return
      if (anchorRef.current?.contains(t)) return
      onClose()
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [onClose, anchorRef])

  const clearErrors = useCallback(() => { setError(null); setRowErrors([]) }, [])

  const finish = useCallback((result: ImportResult) => {
    onImported(result)
    const errs = result._import?.errors ?? []
    if (errs.length > 0) setRowErrors(errs)
    else onClose()
  }, [onImported, onClose])

  const run = useCallback(async (fn: () => Promise<ImportResult>) => {
    setBusy(true); clearErrors()
    try {
      finish(await fn())
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }, [finish, clearErrors])

  const providerLabel = source && source !== 'text'
    ? providers.find((p) => p.name === source)?.display_name ?? source
    : ''

  // Merge vs Replace: merge adds onto the deck's current cards, replace wipes
  // them first so the imported list becomes the whole deck. Reuses the source
  // chips' segmented styling.
  const modeToggle = (
    <div className="import-pop__chips" role="radiogroup" aria-label="Import mode">
      <button
        className={`import-pop__chip ${mode === 'merge' ? 'import-pop__chip--active' : ''}`}
        role="radio"
        aria-checked={mode === 'merge'}
        onClick={() => setMode('merge')}
      >
        Merge
      </button>
      <button
        className={`import-pop__chip ${mode === 'replace' ? 'import-pop__chip--active' : ''}`}
        role="radio"
        aria-checked={mode === 'replace'}
        onClick={() => setMode('replace')}
      >
        Replace
      </button>
    </div>
  )

  const modeHint = mode === 'replace'
    ? 'Replaces this deck — existing cards are cleared first.'
    : 'Merges into this deck — quantities add to existing cards.'

  const popover = (
    <div
      className="import-pop"
      ref={popoverRef}
      style={pos ? { top: pos.top, left: pos.left, width: Math.min(POP_WIDTH, window.innerWidth - 16) } : { visibility: 'hidden' }}
    >
      <div className="import-pop__head">Import from…</div>

      <div className="import-pop__chips">
        <button
          className={`import-pop__chip ${source === 'text' ? 'import-pop__chip--active' : ''}`}
          onClick={() => { setSource('text'); clearErrors() }}
        >
          Text
        </button>
        {providers.map((p) => (
          <button
            key={p.name}
            className={`import-pop__chip ${source === p.name ? 'import-pop__chip--active' : ''}`}
            onClick={() => { setSource(p.name); clearErrors() }}
          >
            {p.display_name}
          </button>
        ))}
      </div>

      {source === 'text' && (
        <div className="import-pop__body">
          <textarea
            className="import-pop__textarea"
            placeholder="Paste a decklist…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={6}
            autoFocus
          />
          {modeToggle}
          <button
            className="import-pop__go"
            onClick={() => run(() => api.importDecklist(deckId, text, mode))}
            disabled={busy || !text.trim()}
          >
            {busy ? 'Importing…' : mode === 'replace' ? 'Replace deck' : 'Import cards'}
          </button>
          <div className="import-pop__hint">{modeHint}</div>
        </div>
      )}

      {source && source !== 'text' && (
        <div className="import-pop__body">
          <input
            className="import-pop__ref"
            placeholder={`${providerLabel} deck URL or id`}
            value={ref}
            onChange={(e) => setRef(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && ref.trim() && !busy) run(() => api.fetchDeckFrom(deckId, source, ref.trim(), mode)) }}
            autoFocus
          />
          {modeToggle}
          <button
            className="import-pop__go"
            onClick={() => run(() => api.fetchDeckFrom(deckId, source, ref.trim(), mode))}
            disabled={busy || !ref.trim()}
          >
            {busy ? 'Fetching…' : `Fetch from ${providerLabel}`}
          </button>
          <div className="import-pop__hint">{modeHint} Maybeboard is skipped.</div>
        </div>
      )}

      {error && <div className="import-pop__error">{error}</div>}
      {rowErrors.length > 0 && (
        <div className="import-pop__error">
          {rowErrors.map((e, i) => <p key={i}>{e}</p>)}
        </div>
      )}
    </div>
  )

  return createPortal(popover, document.body)
}
