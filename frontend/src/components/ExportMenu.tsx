import { useEffect, useRef, useState } from 'react'
import { EXPORT_FORMATS, exportDecklist } from '../lib/exportDecklist'
import type { ExportFormat } from '../lib/exportDecklist'
import type { Deck } from '../types/api'

/** The Export button: one click copies the plain list, the caret picks a format. */
export function ExportMenu({ deck }: { deck: Deck }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState<ExportFormat | null>(null)
  const wrapRef = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  const copy = (format: ExportFormat) => {
    navigator.clipboard.writeText(exportDecklist(deck, format))
    setCopied(format)
    setOpen(false)
    setTimeout(() => setCopied(null), 2000)
  }

  return (
    <span className="export-menu" ref={wrapRef}>
      <button className="btn btn--secondary btn--mono" onClick={() => copy('plain')}>
        {copied ? 'Copied!' : 'Export'}
      </button>
      <button
        className="btn btn--secondary btn--mono export-menu__caret"
        onClick={() => setOpen((v) => !v)}
        aria-label="Choose export format"
        aria-expanded={open}
      >
        ▾
      </button>
      {open && (
        <div className="export-menu__list" role="menu">
          {EXPORT_FORMATS.map((f) => (
            <button key={f.value} className="export-menu__item" role="menuitem" onClick={() => copy(f.value)}>
              <span className="export-menu__label">{f.label}</span>
              <span className="export-menu__hint">{f.hint}</span>
            </button>
          ))}
        </div>
      )}
    </span>
  )
}
