import type { Conversation, ConversationDetail, Deck } from '../types/api'

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

  listDecks: (): Promise<Deck[]> => fetch('/api/decks').then((r) => asJson<Deck[]>(r)),

  getDeck: (id: number): Promise<Deck> =>
    fetch(`/api/decks/${id}`).then((r) => asJson<Deck>(r)),

  updateDeck: (id: number, body: Partial<Pick<Deck, 'name' | 'commander' | 'partner_commander' | 'notes' | 'power_level'>>): Promise<Deck> =>
    fetch(`/api/decks/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => asJson<Deck>(r)),

  deleteDeck: (id: number): Promise<{ ok: boolean }> =>
    fetch(`/api/decks/${id}`, { method: 'DELETE' }).then((r) => asJson<{ ok: boolean }>(r)),
}
