from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = "deepseek"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-reasoner"

    familiar_db_path: str = "familiar.db"

    edhrec_cache_dir: str = "cache/edhrec"
    edhrec_cache_ttl_hours: int = 24

    # Automatic DB backup on app startup. Modes:
    #   "off"    — disabled
    #   "folder" — write the snapshot into backup_dir (default). Point that at
    #              a synced folder (Google Drive / OneDrive / Dropbox / pCloud
    #              Drive) and that client uploads it off-machine. No API/token.
    #   "pcloud" — upload via the pCloud API (needs pcloud_auth_token).
    backup_mode: str = "folder"
    backup_keep: int = 10

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


settings = Settings()
