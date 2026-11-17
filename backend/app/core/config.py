from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All environment-specific values live here (loaded from ../.env)."""

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    app_env: str = "local"
    database_url: str
    redis_url: str = "redis://127.0.0.1:6380/0"

    ai_provider: str = "ollama"
    ai_base_url: str = "http://127.0.0.1:11434"
    ai_chat_model: str = "qwen3:4b"
    ai_embed_model: str = "nomic-embed-text"
    ai_num_ctx: int = 8192
    ai_api_key: str = ""

    jwt_secret: str = ""
    admin_username: str = "admin"
    admin_password_hash: str = ""
    access_token_minutes: int = 480

    adzuna_app_id: str = ""
    adzuna_app_key: str = ""

    generated_dir: str = "../data/generated"
    # Gmail drafts (IMAP APPEND only: this app has no code path that sends mail). Credentials come from the Keychain.
    gmail_imap_host: str = "imap.gmail.com"
    gmail_imap_port: int = 993
    gmail_imap_ssl: bool = True
    # Assisted form fill
    assist_wait_seconds: int = 600  # how long to wait for YOU (sign in, CAPTCHA, a choice) before handing over
    assist_max_minutes: int = 45  # ceiling for the automatic part of one run
    assist_leave_after_fill: bool = True  # once the form is filled, send nothing more to the page: the person takes over
    seek_client_id: str = ""  # only for a future, approved SEEK partner integration; nothing is called with it today
    seek_client_secret: str = ""
    indeed_client_id: str = ""  # same: Indeed's APIs are partner-only
    indeed_client_secret: str = ""
    jora_feed_url: str = ""  # only if Jora ever grants a feed in writing
    assist_headless: bool = False  # the real thing is a VISIBLE browser; only tests run headless
    assist_test_hosts: str = ""  # comma list of extra hosts treated as a fake ATS. Tests only: empty in real use
    storage_backend: str = "local"
    storage_local_path: str = "./data"
    browser_backend: str = "local"


settings = Settings()
