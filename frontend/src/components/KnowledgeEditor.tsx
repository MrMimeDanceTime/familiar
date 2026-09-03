import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { KnowledgeEntry, KnowledgeEntryIn } from '../types/api'

const EMPTY: KnowledgeEntryIn = { title: '', body: '', category: 'playgroup' }

/**
 * The player's own knowledge-base entries.
 *
 * The seeded entries are what the assistant cites for deckbuilding advice.
 * Entries written here sit beside them, carry source "user", and win when
 * they disagree with the seed, so house rules and a playgroup's expectations
 * become part of the advice rather than something to repeat every chat.
 */
export function KnowledgeEditor() {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([])
  const [categories, setCategories] = useState<string[]>(['playgroup'])
  const [draft, setDraft] = useState<KnowledgeEntryIn>(EMPTY)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const refresh = useCallback(() => {
    api.listKnowledge('user').then(setEntries).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    api.listKnowledgeCategories().then((r) => setCategories(r.categories)).catch(() => {})
  }, [refresh])

  const startEdit = (entry: KnowledgeEntry) => {
    setEditingId(entry.id)
    setDraft({ title: entry.title, body: entry.body, category: entry.category })
    setError(null)
  }

  const cancel = () => {
    setEditingId(null)
    setDraft(EMPTY)
    setError(null)
  }

  const save = async () => {
    if (!draft.title.trim() || !draft.body.trim()) {
      setError('A title and a body are both needed.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (editingId === null) await api.createKnowledge(draft)
      else await api.updateKnowledge(editingId, draft)
      cancel()
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const remove = async (id: number) => {
    if (!window.confirm('Delete this entry?')) return
    await api.deleteKnowledge(id).catch(() => {})
    if (editingId === id) cancel()
    refresh()
  }

  return (
    <div className="knowledge-editor">
      <div className="preferences-panel__field">
        Your knowledge
        <span className="preferences-panel__hint">
          Notes the assistant treats as authoritative: house rules, your playgroup&apos;s
          expectations, conclusions you do not want to repeat every chat.
        </span>
      </div>

      {entries.length > 0 && (
        <ul className="knowledge-editor__list">
          {entries.map((entry) => (
            <li key={entry.id} className={`knowledge-editor__item ${editingId === entry.id ? 'knowledge-editor__item--editing' : ''}`}>
              <div className="knowledge-editor__item-head">
                <span className="knowledge-editor__title">{entry.title}</span>
                <span className="knowledge-editor__category">{entry.category}</span>
                <button className="btn btn--chip" onClick={() => startEdit(entry)}>Edit</button>
                <button className="btn btn--chip btn--chip-muted" onClick={() => remove(entry.id)}>Delete</button>
              </div>
              <p className="knowledge-editor__body">{entry.body}</p>
            </li>
          ))}
        </ul>
      )}

      <div className="knowledge-editor__form">
        <input
          className="knowledge-editor__input"
          placeholder="Title, e.g. Our table's combo rule"
          value={draft.title}
          onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
        />
        <select
          className="knowledge-editor__input"
          value={draft.category}
          onChange={(e) => setDraft((d) => ({ ...d, category: e.target.value }))}
        >
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <textarea
          className="preferences-panel__textarea"
          placeholder="What the assistant should know, in a sentence or three."
          value={draft.body}
          onChange={(e) => setDraft((d) => ({ ...d, body: e.target.value }))}
          rows={3}
        />
        {error && <div className="knowledge-editor__error">{error}</div>}
        <div className="knowledge-editor__actions">
          <button className="btn btn--primary" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : editingId === null ? 'Add entry' : 'Save changes'}
          </button>
          {editingId !== null && (
            <button className="btn btn--secondary" onClick={cancel} disabled={saving}>Cancel</button>
          )}
        </div>
      </div>
    </div>
  )
}
