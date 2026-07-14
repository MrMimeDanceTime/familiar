import httpx
import pytest
import respx

from app.tools.scryfall_client import (
    SCRYFALL_BASE_URL,
    ScryfallClient,
    ScryfallError,
    ScryfallNotFoundError,
)

RAW_CARD = {
    "name": "Atraxa, Grand Unifier",
    "mana_cost": "{4}{W}{U}{B}{G}",
    "cmc": 8.0,
    "type_line": "Legendary Creature — Phyrexian Angel",
    "oracle_text": "Flying, vigilance, deathtouch, lifelink...",
    "color_identity": ["W", "U", "B", "G"],
    "image_uris": {"normal": "https://example.com/atraxa.jpg"},
    "legalities": {"commander": "legal"},
    "scryfall_uri": "https://scryfall.com/card/atraxa",
}


@pytest.fixture
def client():
    c = ScryfallClient(client=httpx.Client(base_url=SCRYFALL_BASE_URL))
    yield c
    c.close()


@respx.mock
def test_search_normalizes_and_limits(client):
    respx.get(f"{SCRYFALL_BASE_URL}/cards/search").mock(
        return_value=httpx.Response(200, json={"data": [RAW_CARD, RAW_CARD, RAW_CARD]})
    )
    results = client.search("c:wubg t:angel", limit=2)
    assert len(results) == 2
    assert results[0]["name"] == "Atraxa, Grand Unifier"
    assert results[0]["legal_commander"] is True
    assert results[0]["image_url"] == "https://example.com/atraxa.jpg"


@respx.mock
def test_search_omits_order_and_unique_by_default(client):
    route = respx.get(f"{SCRYFALL_BASE_URL}/cards/search").mock(
        return_value=httpx.Response(200, json={"data": [RAW_CARD]})
    )
    client.search("c:wubg")
    params = route.calls.last.request.url.params
    assert "order" not in params
    assert "unique" not in params


@respx.mock
def test_search_passes_order_and_unique_when_given(client):
    route = respx.get(f"{SCRYFALL_BASE_URL}/cards/search").mock(
        return_value=httpx.Response(200, json={"data": [RAW_CARD]})
    )
    client.search("c:wubg", order="edhrec", unique="cards")
    params = route.calls.last.request.url.params
    assert params["order"] == "edhrec"
    assert params["unique"] == "cards"


@respx.mock
def test_search_pipeline_projection_and_params(client):
    raw = dict(
        RAW_CARD,
        keywords=["Flying", "Vigilance"],
        power="7",
        toughness="5",
        rarity="mythic",
        edhrec_rank=42,
    )
    route = respx.get(f"{SCRYFALL_BASE_URL}/cards/search").mock(
        return_value=httpx.Response(200, json={"data": [raw]})
    )
    results = client.search_pipeline("id<=wubg otag:ramp f:commander", limit=10)
    params = route.calls.last.request.url.params
    assert params["order"] == "edhrec"
    assert params["unique"] == "cards"
    card = results[0]
    # richer fields the pipeline needs, absent from the tool-loop projection
    assert card["keywords"] == ["Flying", "Vigilance"]
    assert card["power"] == "7"
    assert card["toughness"] == "5"
    assert card["rarity"] == "mythic"
    assert card["edhrec_rank"] == 42
    # still carries everything the base projection has
    assert card["name"] == "Atraxa, Grand Unifier"
    assert card["legal_commander"] is True


@respx.mock
def test_search_pipeline_returns_empty_on_no_match(client):
    respx.get(f"{SCRYFALL_BASE_URL}/cards/search").mock(
        return_value=httpx.Response(404, json={"details": "no cards found"})
    )
    assert client.search_pipeline("id<=w otag:bogus-tag f:commander") == []


@respx.mock
def test_named_fuzzy(client):
    route = respx.get(f"{SCRYFALL_BASE_URL}/cards/named").mock(
        return_value=httpx.Response(200, json=RAW_CARD)
    )
    result = client.named("atraxa grand unifier")
    assert result["name"] == "Atraxa, Grand Unifier"
    assert route.calls.last.request.url.params["fuzzy"] == "atraxa grand unifier"


@respx.mock
def test_named_not_found_raises(client):
    respx.get(f"{SCRYFALL_BASE_URL}/cards/named").mock(
        return_value=httpx.Response(404, json={"details": "No card found"})
    )
    with pytest.raises(ScryfallNotFoundError):
        client.named("not a real card name xyz")


@respx.mock
def test_collection_chunks_at_75(client):
    names = [f"Card {i}" for i in range(150)]
    route = respx.post(f"{SCRYFALL_BASE_URL}/cards/collection").mock(
        return_value=httpx.Response(200, json={"data": [RAW_CARD], "not_found": []})
    )
    result = client.collection(names)
    assert route.call_count == 2  # 150 names -> two chunks of 75
    assert len(result["found"]) == 2


@respx.mock
def test_collection_reports_not_found(client):
    respx.post(f"{SCRYFALL_BASE_URL}/cards/collection").mock(
        return_value=httpx.Response(
            200, json={"data": [], "not_found": [{"name": "Nonexistent Card"}]}
        )
    )
    result = client.collection(["Nonexistent Card"])
    assert result["not_found"] == ["Nonexistent Card"]


@respx.mock
def test_429_retries_then_succeeds(client, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    route = respx.get(f"{SCRYFALL_BASE_URL}/cards/named")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json=RAW_CARD),
    ]
    result = client.named("Atraxa, Grand Unifier", fuzzy=False)
    assert result["name"] == "Atraxa, Grand Unifier"
    assert route.call_count == 2


@respx.mock
def test_429_exhausts_retries_raises(client, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    respx.get(f"{SCRYFALL_BASE_URL}/cards/named").mock(
        return_value=httpx.Response(429)
    )
    with pytest.raises(ScryfallError):
        client.named("anything")


@respx.mock
def test_autocomplete(client):
    respx.get(f"{SCRYFALL_BASE_URL}/cards/autocomplete").mock(
        return_value=httpx.Response(
            200, json={"data": ["Atraxa, Grand Unifier", "Atraxa, Praetors' Voice"]}
        )
    )
    results = client.autocomplete("Atraxa")
    assert "Atraxa, Grand Unifier" in results
