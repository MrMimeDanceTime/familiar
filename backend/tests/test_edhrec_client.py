import json

import httpx
import pytest
import respx

from app.tools.edhrec_client import (
    EDHREC_BASE_URL,
    EdhrecClient,
    EdhrecNotFoundError,
    commander_name_to_slug,
)

SAMPLE_PAGE = {
    "container": {
        "json_dict": {
            "cardlists": [
                {
                    "tag": "highsynergycards",
                    "header": "High Synergy Cards",
                    "cardviews": [
                        {
                            "name": "Doubling Season",
                            "synergy": 0.05,
                            "inclusion": 40,
                            "num_decks": 40,
                            "potential_decks": 2000,
                        }
                    ],
                },
                {
                    "tag": "creatures",
                    "header": "Creatures",
                    "cardviews": [
                        {
                            "name": "Solemn Simulacrum",
                            "synergy": 0.01,
                            "inclusion": 800,
                            "num_decks": 800,
                            "potential_decks": 2000,
                        }
                    ],
                },
            ]
        }
    }
}


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Atraxa, Grand Unifier", "atraxa-grand-unifier"),
        ("The Gitrog Monster", "the-gitrog-monster"),
        ("Atraxa, Praetors' Voice", "atraxa-praetors-voice"),
        (
            "Thrasios, Triton Hero / Tymna the Weaver",
            "thrasios-triton-hero-tymna-the-weaver",
        ),
    ],
)
def test_commander_name_to_slug(name, expected):
    assert commander_name_to_slug(name) == expected


@pytest.fixture
def client():
    c = EdhrecClient(client=httpx.Client(base_url=EDHREC_BASE_URL))
    yield c
    c.close()


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "edhrec_cache_dir", str(tmp_path))
    yield tmp_path


@respx.mock
def test_commander_recs_parses_categories(client, isolated_cache):
    respx.get(f"{EDHREC_BASE_URL}/pages/commanders/atraxa-grand-unifier.json").mock(
        return_value=httpx.Response(200, json=SAMPLE_PAGE)
    )
    result = client.commander_recs("Atraxa, Grand Unifier")
    assert "highsynergycards" in result["categories"]
    assert result["categories"]["highsynergycards"]["cards"][0]["name"] == "Doubling Season"
    assert result["categories"]["creatures"]["cards"][0]["num_decks"] == 800


@respx.mock
def test_commander_recs_caches_to_disk_and_skips_second_network_call(client, isolated_cache):
    route = respx.get(f"{EDHREC_BASE_URL}/pages/commanders/atraxa-grand-unifier.json").mock(
        return_value=httpx.Response(200, json=SAMPLE_PAGE)
    )
    client.commander_recs("Atraxa, Grand Unifier")
    assert route.call_count == 1

    cache_file = isolated_cache / "atraxa-grand-unifier.json"
    assert cache_file.exists()

    # Second call within TTL must not hit the network again.
    client.commander_recs("Atraxa, Grand Unifier")
    assert route.call_count == 1


@respx.mock
def test_commander_recs_404_raises_not_found(client, isolated_cache):
    respx.get(f"{EDHREC_BASE_URL}/pages/commanders/not-a-real-commander.json").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(EdhrecNotFoundError):
        client.commander_recs("Not A Real Commander")


@respx.mock
def test_commander_recs_403_treated_as_not_found(client, isolated_cache):
    respx.get(f"{EDHREC_BASE_URL}/pages/commanders/not-a-real-commander.json").mock(
        return_value=httpx.Response(403)
    )
    with pytest.raises(EdhrecNotFoundError):
        client.commander_recs("Not A Real Commander")


@respx.mock
def test_commander_recs_degrades_gracefully_on_unexpected_shape(client, isolated_cache):
    respx.get(f"{EDHREC_BASE_URL}/pages/commanders/atraxa-grand-unifier.json").mock(
        return_value=httpx.Response(200, json={"container": {}})
    )
    result = client.commander_recs("Atraxa, Grand Unifier")
    assert result["categories"] == {}


@respx.mock
def test_card_synergy_finds_match_case_insensitive(client, isolated_cache):
    respx.get(f"{EDHREC_BASE_URL}/pages/commanders/atraxa-grand-unifier.json").mock(
        return_value=httpx.Response(200, json=SAMPLE_PAGE)
    )
    result = client.card_synergy("doubling season", "Atraxa, Grand Unifier")
    assert result is not None
    assert result["num_decks"] == 40


@respx.mock
def test_card_synergy_returns_none_when_not_present(client, isolated_cache):
    respx.get(f"{EDHREC_BASE_URL}/pages/commanders/atraxa-grand-unifier.json").mock(
        return_value=httpx.Response(200, json=SAMPLE_PAGE)
    )
    result = client.card_synergy("Some Unrelated Card", "Atraxa, Grand Unifier")
    assert result is None
