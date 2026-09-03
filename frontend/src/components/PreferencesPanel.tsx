import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { UserPreferences } from '../types/api'
import { KnowledgeEditor } from './KnowledgeEditor'

interface PreferencesPanelProps {
  open: boolean
  onClose: () => void
}

export function PreferencesPanel({ open, onClose }: PreferencesPanelProps) {
  const [draft, setDraft] = useState<UserPreferences | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (open) {
      api.getPreferences().then(setDraft).catch(() => {})
    }
  }, [open])

  const save = useCallback(async () => {
    if (!draft) return
    await api.updatePreferences(draft)
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }, [draft])

  if (!open) return null

  return (
    <div className="preferences-overlay" onClick={onClose}>
      <div className="preferences-panel" onClick={(e) => e.stopPropagation()}>
        <div className="preferences-panel__header">
          <h3>Preferences</h3>
          <button className="preferences-panel__close" onClick={onClose}>×</button>
        </div>

        <label className="preferences-panel__field">
          Preferred bracket
          <select
            value={draft?.preferred_bracket ?? ''}
            onChange={(e) => setDraft((d) => d ? { ...d, preferred_bracket: e.target.value || null } : d)}
          >
            <option value="">Any</option>
            <option value="1">Bracket 1 — Exhibition</option>
            <option value="2">Bracket 2 — Core</option>
            <option value="3">Bracket 3 — Upgraded</option>
            <option value="4">Bracket 4 — Optimized</option>
            <option value="5">Bracket 5 — cEDH</option>
          </select>
        </label>

        <label className="preferences-panel__field">
          Preferred power level
          <select
            value={draft?.preferred_power ?? ''}
            onChange={(e) => setDraft((d) => d ? { ...d, preferred_power: e.target.value || null } : d)}
          >
            <option value="">Any</option>
            {Array.from({ length: 10 }, (_, i) => (
              <option key={i + 1} value={String(i + 1)}>{i + 1}</option>
            ))}
          </select>
        </label>

        <label className="preferences-panel__field">
          Budget
          <select
            value={draft?.budget ?? ''}
            onChange={(e) => setDraft((d) => d ? { ...d, budget: e.target.value || null } : d)}
          >
            <option value="">Any</option>
            <option value="budget">Budget</option>
            <option value="mid">Mid-range</option>
            <option value="unlimited">Unlimited</option>
          </select>
        </label>

        <label className="preferences-panel__field">
          Personal build preferences
          <span className="preferences-panel__hint">
            How you like your decks built — the assistant follows this by default.
          </span>
          <textarea
            className="preferences-panel__textarea"
            placeholder="e.g. I like to stay on flavor and theme, especially in tribal decks; I avoid infinite combos and prefer creature-based strategies."
            value={draft?.build_preferences ?? ''}
            onChange={(e) => setDraft((d) => d ? { ...d, build_preferences: e.target.value || null } : d)}
            rows={4}
          />
        </label>

        <label className="preferences-panel__field">
          Rule 0 notes
          <span className="preferences-panel__hint">
            Table/social-contract expectations for your playgroup.
          </span>
          <textarea
            className="preferences-panel__textarea"
            placeholder="e.g. proxies welcome, no mass land denial, casual takebacks"
            value={draft?.rule0_notes ?? ''}
            onChange={(e) => setDraft((d) => d ? { ...d, rule0_notes: e.target.value || null } : d)}
            rows={3}
          />
        </label>

        <button className="preferences-panel__save" onClick={save}>
          {saved ? 'Saved ✓' : 'Save'}
        </button>

        <KnowledgeEditor />
      </div>
    </div>
  )
}
