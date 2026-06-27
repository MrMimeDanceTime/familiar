export interface DeckCard {
  name: string
  quantity: number
  category: string | null
  mana_value: number | null
  color_identity: string | null
  notes: string | null
}

export interface Deck {
  id: number
  name: string
  commander: string | null
  partner_commander: string | null
  notes: string | null
  power_level: string | null
  cards: DeckCard[]
}

export interface Conversation {
  id: number
  title: string
  deck_id: number | null
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: number
  role: string
  text_content: string | null
  tool_calls: { id: string; name: string; arguments: Record<string, unknown> }[] | null
  tool_results: { call_id: string; content: string }[] | null
  sequence: number
  created_at: string
}

export interface ConversationDetail {
  conversation: Conversation
  messages: ChatMessage[]
}

export type SseEvent =
  | { event: 'token'; data: { text: string } }
  | { event: 'tool_call'; data: { name: string; arguments: Record<string, unknown> } }
  | { event: 'deck_updated'; data: Deck }
  | { event: 'done'; data: { message_id: number; conversation_id: number } }
  | { event: 'error'; data: { message: string } }
