"""Suite-wide test setup.

The app refreshes the local Scryfall card index on startup in a background
thread. Any test that builds a `TestClient` runs the lifespan, so without this
the suite would hit the live Scryfall API and race the DB writer — which showed
up as `database is locked` in the proposal tests, not as an obvious network
failure. Tests that want an index build one explicitly (see test_card_index).
"""

import pytest

from app.config import settings
from app.knowledge import tag_lookup


@pytest.fixture(autouse=True, scope="session")
def _disable_card_index_refresh():
    settings.card_index_refresh_on_startup = False
    # The nuance settle window is a production latency knob; the tests that
    # exercise the nuance call want it to fire immediately. One test sets the
    # window explicitly to check the debounce itself.
    settings.power_nuance_settle_seconds = 0.0
    # Connection-error retries back off in production; a test that exercises
    # the retry path should not wait for it.
    from app.tools import scryfall_client

    scryfall_client.RETRY_BACKOFF_SECONDS = 0.0
    # Jev is the default selector and the developer's .env carries a real key,
    # so without this every pipeline test using a fake provider would make a
    # live, billed Jev call. Tests that exercise Jev pass a fake client.
    settings.select_backend = "llm"
    settings.typesafe_api_key = ""
    yield


@pytest.fixture(autouse=True)
def _no_oracle_tag_download(request):
    """Keep the oracle-tag lookup off the network.

    Any path that derives a card's display category (deck_snapshot, and so
    every apply/import/stats test) falls through to ``get_tag_lookup`` when the
    card carries no stored tags. That downloads Scryfall's bulk file unless a
    cache already sits in ``backend/``, so fifteen tests passed only on a
    machine that happened to have one and failed on a fresh clone. An empty
    in-memory lookup is the honest fixture: "no tags known".

    ``test_tag_lookup`` exercises the download itself against mocked HTTP and
    resets the module state it needs, so it is left alone.
    """
    if request.node.fspath.basename == "test_tag_lookup.py":
        yield
        return
    previous = tag_lookup._lookup
    tag_lookup._lookup = {}
    try:
        yield
    finally:
        tag_lookup._lookup = previous
