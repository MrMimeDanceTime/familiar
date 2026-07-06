export interface DeckCard {
  name: string
  quantity: number
  category: string | null
  mana_value: number | null
  color_identity: string | null
  type_line: string | null
  tags: string[]
  notes: string | null
}

export interface DeckStats {
  mana_curve: { mv: string; count: number }[]
  avg_mv: number
  color_distribution: { color: string; count: number; pct: number }[]
  type_breakdown: { type: string; count: number; pct: number }[]
  land_count: number
  land_pct: number
  ramp_count: number
  draw_count: number
  removal_count: number
  total_cards: number
  power_level: number
  power_factors: string[]
  bracket: number
  bracket_factors: string[]
  untagged: string[]
  deficiencies: {
    category: string
    count: number
    target_low: number
    target_high: number
    status: 'LOW' | 'OK' | 'HIGH'
  }[]
}

export interface Deck {
  id: number
  name: string
  commander: string | null
  partner_commander: string | null
  notes: string | null
  power_level: string | null
  format: string
  conversation_id: number | null
  cards: DeckCard[]
}

export interface ImportResult extends Deck {
  _import?: { imported: number; errors: string[] }
  _source?: {
    provider: string
    url: string | null
    name: string
    commanders: string[]
  }
}

export interface DeckProvider {
  name: string
  display_name: string
  supports_fetch: boolean
  supports_push: boolean
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

export interface DeckProposal {
  id: number
  deck_id: number
  message_id: number | null
  status: 'pending' | 'approved' | 'denied'
  action: 'add' | 'remove' | 'set_commander'
  card_name: string | null
  quantity: number
  category: string | null
  commander_name: string | null
  reasoning: string
  created_at?: string
}

export interface ConversationDetail {
  conversation: Conversation
  messages: ChatMessage[]
  proposals: DeckProposal[]
}

export interface UserPreferences {
  preferred_bracket: string | null
  preferred_power: string | null
  budget: string | null
  rule0_notes: string | null
  build_preferences: string | null
}

export type SseEvent =
  | { event: 'token'; data: { text: string } }
  | { event: 'tool_call'; data: { name: string; arguments: Record<string, unknown> } }
  | { event: 'deck_proposal'; data: { ok: boolean; summary: string; proposals: DeckProposal[] } }
  | { event: 'deck_updated'; data: Deck }
  | { event: 'done'; data: { message_id: number; conversation_id: number } }
  | { event: 'error'; data: { message: string } }
