// Scryfall's image-redirect endpoint serves a card image directly from a card
// name — no JSON round-trip, and Scryfall's CDN handles caching. Our decklist
// names are canonical (resolved via Scryfall on import), so `exact` is safe;
// double-faced cards return their front face at this endpoint.
//
// https://scryfall.com/docs/api/cards/named
export function cardImageUrl(name: string, version: 'normal' | 'large' = 'normal'): string {
  const q = encodeURIComponent(name)
  return `https://api.scryfall.com/cards/named?exact=${q}&format=image&version=${version}`
}
