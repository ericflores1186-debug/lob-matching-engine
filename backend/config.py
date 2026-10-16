from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.database import DEFAULT_DATABASE_URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOB_", env_file=".env", extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL
    comp_id: str = "LOBX"
    collar_bps: int = Field(default=1_000, gt=0)
    max_order_qty: int = Field(default=1_000_000, gt=0)
    max_notional_cents: int = Field(default=10_000_000_00, gt=0)
    max_clock_skew_seconds: int = Field(default=120, gt=0)
    # Prior-close references used for collars until a symbol prints
    reference_prices_cents: dict[str, int] = {"AAPL": 22850, "MSFT": 43120, "NVDA": 13840}
    max_batch_messages: int = Field(default=5_000, gt=0)
    max_payload_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    book_depth: int = Field(default=10, gt=0, le=100)
    tape_size: int = Field(default=500, gt=0)
    broadcast_event_cap: int = Field(default=400, gt=0)
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
