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

export interface DeckStatsNuance {
  power_level: number
  power_level_base: number
  power_nuance_adj: number
  power_nuance_reason: string
  // True when the deck changed too recently for the LLM nuance to be worth
  // computing yet; ask again after settle_seconds.
  power_nuance_pending: boolean
  settle_seconds: number
  power_factors: string[]
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
  power_level_base: number
  power_nuance_adj: number
  power_nuance_reason: string
  power_nuance_pending?: boolean
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
  // Deck price at the card index's representative printing; null when the
  // index has no prices yet. priced_cards says how many cards the sum covers.
  total_price_usd?: number | null
  priced_cards?: number
  // Per colour: share of mana sources against share of coloured pips.
  mana_sources?: {
    color: string
    sources: number
    pips: number
    source_pct: number
    pip_pct: number
    status: 'LOW' | 'OK'
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
  max_card_price?: number | null
  cards: DeckCard[]
}

/** A deck as the list endpoint returns it: enough for a sidebar row and to
 *  route to its conversation, without the card list. */
export interface DeckSummary {
  id: number
  name: string
  commander: string | null
  partner_commander: string | null
  format: string
  power_level: string | null
  conversation_id: number | null
  total_cards: number
  updated_at: string
}

export type ImportMode = 'merge' | 'replace'

export interface ImportResult extends Deck {
  _import?: { imported: number; errors: string[]; mode?: ImportMode; cleared?: number }
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
  sequence: number
  created_at: string
}

/** The brain map's verdict for one card: per-layer scores plus the one-line why.
 *
 * The SHAPE matters more than the total. High mechanical with low consensus is
 * an underplayed card that genuinely works with this commander; high consensus
 * with low mechanical is a staple every deck in these colours runs. Showing
 * only a single number throws that distinction away. */
export interface ProposalScores {
  total: number
  consensus: number
  mechanical: number
  personal: number
  explain: string
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
  scores?: ProposalScores | null
  denial_reason?: string | null
  price_usd?: number | null
  created_at?: string
}

/** Why a card was passed on. A bare "no" says nothing generalisable; these say
 *  what to do differently, which is the only thing worth learning from. */
export const DENIAL_REASONS = [
  { key: 'wrong-slot', label: "Don't need this role" },
  { key: 'too-generic', label: 'Too generic' },
  { key: 'too-expensive', label: 'Too expensive' },
  { key: 'off-theme', label: 'Off-theme' },
  { key: 'dislike', label: 'Just don’t like it' },
] as const

export interface ConversationDetail {
  conversation: Conversation
  messages: ChatMessage[]
  proposals: DeckProposal[]
}

/** A knowledge-base entry. Seeded entries are read-only; the player's own
 *  carry source "user" and the model treats them as authoritative. */
export interface KnowledgeEntry {
  id: number
  title: string
  body: string
  category: string
  format: string
  source: 'seed' | 'user'
}

export type KnowledgeEntryIn = Pick<KnowledgeEntry, 'title' | 'body' | 'category'> & { format?: string }

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
