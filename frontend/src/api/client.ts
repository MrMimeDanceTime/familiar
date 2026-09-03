import type { Conversation, ConversationDetail, Deck, DeckProvider, DeckStats, DeckStatsNuance, DeckSummary, ImportMode, ImportResult, UserPreferences } from '../types/api'

async function asJson<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    throw new Error(body.detail ?? `Request failed: ${resp.status}`)
  }
  return resp.json()
}

export const api = {
  listConversations: (): Promise<Conversation[]> =>
    fetch('/api/conversations').then((r) => asJson<Conversation[]>(r)),

  getConversation: (id: number): Promise<ConversationDetail> =>
    fetch(`/api/conversations/${id}`).then((r) => asJson<ConversationDetail>(r)),

  deleteConversation: (id: number): Promise<{ ok: boolean }> =>
    fetch(`/api/conversations/${id}`, { method: 'DELETE' }).then((r) => asJson<{ ok: boolean }>(r)),

  listDecks: (): Promise<DeckSummary[]> => fetch('/api/decks').then((r) => asJson<DeckSummary[]>(r)),

  getDeck: (id: number): Promise<Deck> =>
    fetch(`/api/decks/${id}`).then((r) => asJson<Deck>(r)),

  updateDeck: (id: number, body: Partial<Pick<Deck, 'name' | 'commander' | 'partner_commander' | 'notes' | 'power_level' | 'format'>>): Promise<Deck> =>
    fetch(`/api/decks/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => asJson<Deck>(r)),

  deleteDeck: (id: number): Promise<{ ok: boolean }> =>
    fetch(`/api/decks/${id}`, { method: 'DELETE' }).then((r) => asJson<{ ok: boolean }>(r)),

  createDeck: (name?: string, commander?: string): Promise<Deck> =>
    fetch('/api/decks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, commander }),
    }).then((r) => asJson<Deck>(r)),

  setCommanders: (
    deckId: number,
    body: { commander: string | null; partner_commander: string | null },
  ): Promise<Deck> =>
    fetch(`/api/decks/${deckId}/commanders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => asJson<Deck>(r)),

  importDecklist: (deckId: number, text: string, mode: ImportMode = 'merge'): Promise<ImportResult> =>
    fetch(`/api/decks/${deckId}/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, mode }),
    }).then((r) => asJson<ImportResult>(r)),

  listProviders: (): Promise<{ providers: DeckProvider[] }> =>
    fetch('/api/decks/providers').then((r) => asJson<{ providers: DeckProvider[] }>(r)),

  fetchDeckFrom: (deckId: number, provider: string, ref: string, mode: ImportMode = 'merge'): Promise<ImportResult> =>
    fetch(`/api/decks/${deckId}/fetch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, ref, mode }),
    }).then((r) => asJson<ImportResult>(r)),

  pushDeckTo: (deckId: number, provider: string): Promise<{ url: string; external_id: string }> =>
    fetch(`/api/decks/${deckId}/push`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider }),
    }).then((r) => asJson<{ url: string; external_id: string }>(r)),

  getDeckConversation: (deckId: number): Promise<Conversation> =>
    fetch(`/api/decks/${deckId}/conversation`).then((r) => asJson<Conversation>(r)),

  listDeckConversations: (deckId: number): Promise<Conversation[]> =>
    fetch(`/api/decks/${deckId}/conversations`).then((r) => asJson<Conversation[]>(r)),

  startDeckConversation: (deckId: number): Promise<Conversation> =>
    fetch(`/api/decks/${deckId}/start-conversation`, { method: 'POST' }).then((r) => asJson<Conversation>(r)),

  getDeckStats: (deckId: number): Promise<DeckStats> =>
    fetch(`/api/decks/${deckId}/stats`).then((r) => asJson<DeckStats>(r)),

  getDeckStatsNuance: (deckId: number): Promise<DeckStatsNuance> =>
    fetch(`/api/decks/${deckId}/stats/nuance`).then((r) => asJson<DeckStatsNuance>(r)),

  applyProposal: (proposalId: number): Promise<Deck> =>
    fetch(`/api/decks/proposals/${proposalId}/apply`, { method: 'POST' }).then((r) => asJson<Deck>(r)),

  revertProposal: (proposalId: number): Promise<Deck> =>
    fetch(`/api/decks/proposals/${proposalId}/revert`, { method: 'POST' }).then((r) => asJson<Deck>(r)),

  denyProposal: (proposalId: number, reason?: string): Promise<{ ok: boolean }> =>
    fetch(`/api/decks/proposals/${proposalId}/deny`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: reason ?? null }),
    }).then((r) => asJson<{ ok: boolean }>(r)),

  getPreferences: (): Promise<UserPreferences> =>
    fetch('/api/preferences').then((r) => asJson<UserPreferences>(r)),

  updatePreferences: (body: Partial<UserPreferences>): Promise<UserPreferences> =>
    fetch('/api/preferences', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => asJson<UserPreferences>(r)),
}
