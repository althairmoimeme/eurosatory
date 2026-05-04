from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
EXPORT_DIR = DATA_DIR / "exports"
LOG_DIR = DATA_DIR / "logs"
RAW_DIR = DATA_DIR / "raw"

for d in (DATA_DIR, EXPORT_DIR, LOG_DIR, RAW_DIR):
    d.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    finderr_base_url: str = "https://eurosatory.finderr.cloud/api/"
    finderr_api_key: str = (
        "55d2a5d23487234d2a83fc3c50b94e5bbfd66e8595d847230af8ec4cbb9f4a8c"
    )
    finderr_language: Literal["en", "fr"] = "en"

    http_user_agent: str = (
        "eurosatory-scraper/0.1 (+contact: a.bertantoine@gmail.com)"
    )

    scrape_per_host_delay: float = 1.0
    scrape_timeout: int = 20
    scrape_max_retries: int = 3
    scrape_concurrency: int = 8

    # When the full dev DB is absent (e.g. on Streamlit Cloud) the slim
    # read-only deploy DB at ``data/eurosatory_deploy.db`` is used as a
    # fallback automatically (computed in ``__post_init__``-style hook
    # below via ``model_post_init`` for pydantic v2).
    database_url: str = Field(default="sqlite:///data/eurosatory.db")

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    streamlit_port: int = 8501

    deep_crawl_max_pages: int = 15
    deep_crawl_max_pdfs: int = 3
    deep_crawl_max_depth: int = 2
    deep_crawl_per_host_delay: float = 2.0
    deep_crawl_timeout: int = 20
    deep_crawl_concurrency: int = 4
    deep_crawl_max_page_bytes: int = 2_000_000

    anthropic_api_key: str = ""
    llm_intelligence_model: str = "claude-haiku-4-5"
    llm_intelligence_budget_tokens: int = 2500

    fullenrich_api_key: str = ""
    rocketreach_api_key: str = ""

    log_level: str = "INFO"
    log_dir: str = str(LOG_DIR)


settings = Settings()

# Fallback for deploy environments (Streamlit Cloud / VPS) where the
# heavy dev DB isn't shipped — point at the slim ``eurosatory_deploy.db``
# automatically. Skipped when ``DATABASE_URL`` was explicitly overridden.
import os as _os
from pathlib import Path as _Path
_DEFAULT_DB = "sqlite:///data/eurosatory.db"
if (
    settings.database_url == _DEFAULT_DB
    and not _os.getenv("DATABASE_URL")
    and not _Path("data/eurosatory.db").exists()
    and _Path("data/eurosatory_deploy.db").exists()
):
    settings.database_url = "sqlite:///data/eurosatory_deploy.db"
