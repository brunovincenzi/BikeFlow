from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    mongo_uri: str = "mongodb://localhost:27017/?replicaSet=rs0&retryWrites=true"
    mongo_database: str = "bikeflow"
    mongo_collection: str = "telemetry"
    mongo_server_selection_timeout_ms: int = Field(default=2_000, ge=100, le=60_000)
    mongo_connect_timeout_ms: int = Field(default=2_000, ge=100, le=60_000)
    mongo_write_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    log_level: str = "INFO"
    api_title: str = "BikeFlow API"
    api_version: str = "1.0.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
