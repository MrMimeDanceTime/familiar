from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Only "deepseek" is implemented. Kept as a setting so a second backend can
    # slot in behind ChatProvider without a config change of shape.
    llm_provider: str = "deepseek"

    deepseek_api_key: str = ""
    # Default to Pro everywhere: V4 pricing makes the Flash/Pro gap negligible, so
    # we pay for the better model unless a call genuinely benefits from Flash (bulk
    # extraction). Thinking mode is no longer implied by the ID (the retired
    # deepseek-reasoner alias always thought) — the provider enables it explicitly
    # in send(), preserving reasoner behavior across the ID change.
    deepseek_model: str = "deepseek-v4-pro"
    # The Flash seam: passed as a per-call model override where high throughput
    # beats reasoning depth. Not used by the chat loop, which runs on the default.
    deepseek_model_fast: str = "deepseek-v4-flash"

    # How hard the selection stage is allowed to think: "low", "medium", "high",
    # or empty for the provider default.
    #
    # Stage-4 latency is dominated by OUTPUT volume, not prompt size — measured
    # over repeated samples, a thinking call emits a median 9,476 completion
    # tokens against 489 without, and at ~90 tok/s that is the entire wait.
    # Shrinking the prompt does nothing (halving it measured slightly slower).
    # On an identical pool: provider default 93.2s median, "medium" 73.7s,
    # "low" 40.8s — and "low" returned the same theme-aware picks, including
    # both changelings that trigger the commander twice.
    #
    # "low" is the default here because a suggestion is interactive and 40s beats
    # 93s for output that graded the same. Set empty to restore provider default.
    select_reasoning_effort: str = "low"

    # Which backend runs stage 4: "llm" (the thinking selection call above) or
    # "jev" (TypeSafe's System One model scores every candidate in under a
    # second; see app/pipeline/jev.py). "jev" falls back to "llm" on any error,
    # including a missing key, so switching it on cannot break suggestions.
    select_backend: str = "llm"
    typesafe_api_key: str = ""
    typesafe_model: str = "jev-1.13.0"
    # How Jev judges (blind | informed | verdict | choice | ensemble) and how
    # many samples it averages. verdict x3 measured best on held-out cards from
    # the player's own decks: 73% recall@10 against the LLM's 72%, and 9.5 of
    # its top 10 survive a pool shuffle against the LLM's 7.0. See PIPELINE.md.
    jev_mode: str = "verdict"
    jev_samples: int = 3
    # Batch shaping after ranking: at most this many interchangeable cards
    # (same primary type and roles; 0 disables), and stop below this verdict
    # probability (0 disables). Set from tools/jev_eval.py results.
    jev_max_similar: int = 0
    jev_min_probability: float = 0.0
    # One fast, non-thinking model call writes reasons, a summary, and cuts for
    # Jev's picks. It cannot change which cards were picked.
    jev_explain: bool = True

    # Per-request timeout (seconds) for LLM API calls. The OpenAI SDK defaults to
    # 600s, which reads as a total freeze from the UI when a call stalls (e.g. a
    # slow thinking-mode pipeline call). Cap it so a stalled request fails fast
    # and surfaces as a tool error the model/user can see, instead of hanging.
    llm_timeout_seconds: float = 90.0

    # How long a deck has to sit unchanged before the LLM power-level nuance is
    # recomputed for it. The panel refreshes stats after every approval, and
    # each approval changes the content hash, so without a settle window a
    # 60-card build fired 60 reasoning calls on decks that were about to change
    # again. 0 recomputes immediately (the tests use that).
    power_nuance_settle_seconds: float = 90.0

    # The chat loop thinks on the first send of a turn (where it decides what
    # the turn is for and what to hand the pipeline) and on the send after a
    # batch (where it decides which picks to stand behind); the tool-dispatch
    # sends between them stay fast. Off makes the first send fast too.
    chat_plan_thinking: bool = True
    # How hard those chat sends may think: low | medium | high | empty for the
    # provider default. The selection stage measured "low" at half the wait
    # for the same picks; the chat sends are a smaller decision still.
    chat_reasoning_effort: str = "low"

    # Whether a reply that describes a card the model never read is withdrawn
    # and rewritten. The card facts are injected either way; this is only the
    # repair round, which costs a send and visibly replaces the draft. Off
    # leaves the reply as written and logs the miss.
    chat_correct_ungrounded_replies: bool = True

    # Output cap for the chat sends that think. DeepSeek counts reasoning
    # against max_tokens in thinking mode, so the 1000-token reply cap below
    # starved the planning send: the reasoning spent the whole budget, the
    # reply came back empty, and the empty assistant message poisoned every
    # later call ("content or tool_calls must be set").
    chat_thinking_max_tokens: int = 8000

    # Cap on the chat model's response length. DeepSeek generates at ~40 tok/s,
    # so an unbounded final answer of 2000+ tokens takes ~50s purely to write —
    # measured as the dominant cause of slow turns. Bounding output both caps
    # worst-case latency and pushes the model toward concise, scannable replies.
    # Pipeline stages set their own limits and are unaffected.
    chat_max_tokens: int = 1000

    # App log level. INFO surfaces the pipeline's per-stage timing logs, which
    # are how a slow/stuck suggest_cards call gets diagnosed.
    log_level: str = "INFO"

    familiar_db_path: str = "familiar.db"

    # Refresh the local Scryfall card index on startup, in a background thread.
    # Turned off under test so the suite never reaches the network; a test that
    # needs an index builds one explicitly.
    card_index_refresh_on_startup: bool = True

    # Commander Spellbook's variant export, the source of the local combo
    # table. Blank disables combo detection entirely. Refreshed weekly in the
    # same background thread as the card index.
    combo_source_url: str = "https://json.commanderspellbook.com/variants.json"

    edhrec_cache_dir: str = "cache/edhrec"
    edhrec_cache_ttl_hours: int = 24

    # Scryfall oracle-tags bulk cache (~6MB gzipped, refetched every 24h). Split
    # out as a setting so a container can place it on the mounted volume;
    # otherwise it lands in the image layer and is re-downloaded on every deploy.
    # Stored exactly as downloaded — gzipped JSONL — hence the extension.
    oracle_tags_cache_path: str = "oracle_tags_cache.jsonl.gz"

    # Browser origins allowed to call the API. Only needed when the frontend is
    # served from somewhere other than this app (the Vite dev server, or a
    # reverse proxy on a different name); same-origin requests don't use CORS.
    cors_allow_origins: list[str] = ["http://localhost:5173"]

    # Built frontend to serve at "/". Empty means "use the repo layout"
    # (../../frontend/dist), which is wrong in a container that flattens the
    # tree, so the image sets this explicitly.
    frontend_dist_path: str = ""

    # Automatic DB backup on app startup. Modes:
    #   "off"    — disabled
    #   "folder" — write the snapshot into backup_dir (default). Point that at
    #              a synced folder (Google Drive / OneDrive / Dropbox / pCloud
    #              Drive) and that client uploads it off-machine. No API/token.
    #   "pcloud" — upload via the pCloud API (needs pcloud_auth_token).
    backup_mode: str = "folder"
    backup_keep: int = 10
    # Hours between periodic snapshots after the startup one. 0 keeps only the
    # startup snapshot, which is enough for a laptop that restarts daily and
    # not for a container that runs for weeks.
    backup_interval_hours: float = 24.0

    # folder mode: destination directory for snapshots (a synced folder).
    # Empty disables folder mode even if selected.
    backup_dir: str = ""

    # pcloud mode: host differs by data region — US accounts use
    # api.pcloud.com, EU accounts use eapi.pcloud.com.
    pcloud_auth_token: str = ""
    pcloud_folder_id: int = 0  # 0 = root; set to a folder id to nest backups
    pcloud_api_host: str = "eapi.pcloud.com"

    @property
    def db_path(self) -> Path:
        path = Path(self.familiar_db_path)
        if not path.is_absolute():
            path = BACKEND_DIR / path
        return path

    @property
    def edhrec_cache_path(self) -> Path:
        path = Path(self.edhrec_cache_dir)
        if not path.is_absolute():
            path = BACKEND_DIR / path
        return path

    @property
    def oracle_tags_path(self) -> Path:
        path = Path(self.oracle_tags_cache_path)
        if not path.is_absolute():
            path = BACKEND_DIR / path
        return path

    @property
    def frontend_dist(self) -> Path:
        if self.frontend_dist_path:
            return Path(self.frontend_dist_path)
        return BACKEND_DIR.parent / "frontend" / "dist"


settings = Settings()
