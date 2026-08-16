"""Suite-wide test setup.

The app refreshes the local Scryfall card index on startup in a background
thread. Any test that builds a `TestClient` runs the lifespan, so without this
the suite would hit the live Scryfall API and race the DB writer — which showed
up as `database is locked` in the proposal tests, not as an obvious network
failure. Tests that want an index build one explicitly (see test_card_index).
"""

import pytest

from app.config import settings


@pytest.fixture(autouse=True, scope="session")
def _disable_card_index_refresh():
    settings.card_index_refresh_on_startup = False
    yield
