from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = "http://localhost:8000"
    tz: str = "Asia/Shanghai"

    watchlist: str = "600519,000858,300750"

    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    wechat_push_enabled: bool = False

    cron_minute: int = 35
    cron_hour: int = 15

    data_dir: Path = Field(default=Path("./data"))

    @property
    def watchlist_codes(self) -> list[str]:
        return [c.strip() for c in self.watchlist.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
