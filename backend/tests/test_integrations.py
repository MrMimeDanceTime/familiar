from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.db import repository as repo
from app.integrations.archidekt import ArchidektProvider
from app.integrations.base import (
    NotSupportedError,
    ProviderError,
    get_provider,
    list_providers,
)
from app.integrations.moxfield import MoxfieldProvider

# Registering happens on import; ensure the moxfield module is imported so the
# provider is in the registry for the registry/capability tests.
import app.integrations.moxfield  # noqa: F401,E402


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


_SAMPLE = {
    "name": "Sak + Krark",
    "deckFormat": 3,
    "categories": [
        {"name": "Maybeboard", "includedInDeck": False},
        {"name": "Ramp", "includedInDeck": True},
    ],
    "cards": [
        {"quantity": 1, "categories": ["Commander"], "card": {"oracleCard": {"name": "Krark, the Thumbless"}}},
        {"quantity": 1, "categories": ["Commander"], "card": {"oracleCard": {"name": "Sakashima of a Thousand Faces"}}},
        {"quantity": 10, "categories": ["Ramp"], "card": {"oracleCard": {"name": "Island"}}},
        {"quantity": 1, "categories": ["Maybeboard"], "card": {"oracleCard": {"name": "Cut This One"}}},
    ],
}


# ── registry / capabilities ─────────────────────────────────────────────────

def test_archidekt_registered_fetch_only():
    p = get_provider("archidekt")
    assert p.supports_fetch is True
    assert p.supports_push is False
    names = {row["name"] for row in list_providers()}
    assert "archidekt" in names


def test_unknown_provider_raises():
    with pytest.raises(ProviderError):
        get_provider("nope")


def test_push_on_fetch_only_provider_raises_not_supported():
    from app.integrations.base import NormalizedDeck
    with pytest.raises(NotSupportedError):
        ArchidektProvider().push_deck(NormalizedDeck(name="x"))


# ── id extraction ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ref", [
    "https://archidekt.com/decks/2087352",
    "archidekt.com/decks/2087352#Edgar_Markov",
    "2087352",
])
def test_extract_id(ref):
    assert ArchidektProvider()._extract_id(ref) == "2087352"


def test_extract_id_rejects_garbage():
    with pytest.raises(ProviderError):
        ArchidektProvider()._extract_id("not-a-deck")


# ── normalization ────────────────────────────────────────────────────────────

def test_normalize_detects_commanders_and_excludes_maybeboard():
    nd = ArchidektProvider()._normalize(_SAMPLE, "2087352", "ref")
    names = [c.name for c in nd.cards]
    assert nd.commanders == ["Krark, the Thumbless", "Sakashima of a Thousand Faces"]
    assert "Cut This One" not in names  # maybeboard excluded
    assert ("Island" in names)
    assert nd.source_url == "https://archidekt.com/decks/2087352"


def test_normalize_empty_deck_raises():
    with pytest.raises(ProviderError):
        ArchidektProvider()._normalize({"name": "empty", "cards": []}, "1", "ref")


# ── fetch (mocked HTTP) ──────────────────────────────────────────────────────

def _mock_httpx(status=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body or _SAMPLE
    client = MagicMock()
    client.get.return_value = resp
    client.__enter__ = lambda s: client
    client.__exit__ = lambda *a: False
    return client


def test_fetch_404_gives_clear_error():
    with patch("app.integrations.archidekt.httpx.Client", return_value=_mock_httpx(status=404)):
        with pytest.raises(ProviderError) as exc:
            ArchidektProvider().fetch_deck("2087352")
    assert "not found" in str(exc.value).lower()


# ── Moxfield ─────────────────────────────────────────────────────────────────

_MOX_SAMPLE = {
    "name": "Sak + Krark",
    "format": "commander",
    "publicId": "BqA1Yx_p-01",
    "boards": {
        "commanders": {
            "count": 2,
            "cards": {
                "a": {"quantity": 1, "card": {"name": "Krark, the Thumbless"}},
                "b": {"quantity": 1, "card": {"name": "Sakashima of a Thousand Faces"}},
            },
        },
        "mainboard": {
            "count": 10,
            "cards": {
                "c": {"quantity": 10, "card": {"name": "Island"}},
            },
        },
        "maybeboard": {
            "count": 1,
            "cards": {
                "d": {"quantity": 1, "card": {"name": "Cut This One"}},
            },
        },
    },
}


def test_moxfield_registered_fetch_only():
    p = get_provider("moxfield")
    assert p.supports_fetch is True
    assert p.supports_push is False
    names = {row["name"] for row in list_providers()}
    assert "moxfield" in names


def test_moxfield_push_raises_not_supported():
    from app.integrations.base import NormalizedDeck
    with pytest.raises(NotSupportedError):
        MoxfieldProvider().push_deck(NormalizedDeck(name="x"))


@pytest.mark.parametrize("ref", [
    "https://www.moxfield.com/decks/BqA1Yx_p-01",
    "moxfield.com/decks/BqA1Yx_p-01",
    "https://moxfield.com/decks/BqA1Yx_p-01/some-slug",
    "BqA1Yx_p-01",
])
def test_moxfield_extract_id(ref):
    assert MoxfieldProvider()._extract_id(ref) == "BqA1Yx_p-01"


def test_moxfield_extract_id_rejects_garbage():
    with pytest.raises(ProviderError):
        MoxfieldProvider()._extract_id("has spaces/and slashes")


def test_moxfield_normalize_detects_commanders_and_excludes_maybeboard():
    nd = MoxfieldProvider()._normalize(_MOX_SAMPLE, "BqA1Yx_p-01", "ref")
    names = [c.name for c in nd.cards]
    assert set(nd.commanders) == {"Krark, the Thumbless", "Sakashima of a Thousand Faces"}
    assert "Cut This One" not in names  # maybeboard excluded
    assert "Island" in names
    assert nd.source_url == "https://moxfield.com/decks/BqA1Yx_p-01"
    assert nd.source_format == "commander"


def test_moxfield_normalize_empty_deck_raises():
    with pytest.raises(ProviderError):
        MoxfieldProvider()._normalize({"name": "empty", "boards": {}}, "1", "ref")


# Moxfield fetches via curl_cffi (not httpx), so its response mock is a plain
# object with status_code + json(), matching curl_cffi.requests.Response.
def _mock_curl_resp(status=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body if json_body is not None else _MOX_SAMPLE
    return resp


def test_moxfield_fetch_404_gives_clear_error():
    with patch("app.integrations.moxfield.curl_requests.get",
               return_value=_mock_curl_resp(status=404)):
        with pytest.raises(ProviderError) as exc:
            MoxfieldProvider().fetch_deck("BqA1Yx_p-01")
    assert "not found" in str(exc.value).lower()


def test_moxfield_fetch_normalizes(session):
    with patch("app.integrations.moxfield.curl_requests.get",
               return_value=_mock_curl_resp(json_body=_MOX_SAMPLE)):
        nd = MoxfieldProvider().fetch_deck("https://moxfield.com/decks/BqA1Yx_p-01")
    assert nd.name == "Sak + Krark"
    assert "Island" in [c.name for c in nd.cards]


def test_fetch_into_deck_imports_and_sets_commanders(session):
    deck = repo.create_deck(session, format="commander")

    # Mock Scryfall resolution used by import_decklist so no network is hit.
    # import_decklist batch-resolves via collection(); mirror that shape.
    def fake_card(name):
        return {
            "name": name, "cmc": 0.0, "color_identity": [],
            "type_line": "Creature" if "," in name else "Land",
            "oracle_text": "", "oracle_id": f"oid-{name}",
        }

    def fake_collection(names):
        return {"found": [fake_card(n) for n in names], "not_found": []}

    with patch("app.integrations.archidekt.httpx.Client", return_value=_mock_httpx()), \
         patch("app.tools.deck_tools.get_scryfall_client") as scry, \
         patch("app.knowledge.tag_lookup.get_tag_lookup", lambda: {}), \
         patch("app.tools.deck_tools.get_tags_for_card", return_value=[]):
        scry.return_value.collection.side_effect = fake_collection
        from app.integrations.service import fetch_into_deck
        result = fetch_into_deck(session, deck.id, "archidekt", "2087352")

    assert result["_source"]["provider"] == "archidekt"
    assert result["commander"] == "Krark, the Thumbless"
    assert result["partner_commander"] == "Sakashima of a Thousand Faces"
    # Maybeboard card must not have been imported.
    names = {c["name"] for c in result["cards"]}
    assert "Cut This One" not in names
