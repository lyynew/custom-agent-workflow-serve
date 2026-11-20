"""Application settings.

Environment variables win over `.env`, which keeps tests and containers in
control of the database URL and secrets.
"""

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# `secret_ref` resolution reads os.environ directly, so load `.env` once here.
# Real environment variables keep precedence (override=False).
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "postgresql+asyncpg://aegra:aegra@127.0.0.1:5432/ai_service"
    app_env: str = "local"
    log_level: str = "INFO"

    self_url: str = "http://127.0.0.1:2026"

    auth_cache_ttl_seconds: int = Field(default=60, ge=0)
    jwt_default_leeway_seconds: int = Field(default=30, ge=0)

    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout_seconds: float = Field(default=30.0, gt=0)

    # Workflow execution. The run timeout must stay well below the access-token
    # lifetime: a background poller reuses the caller's token to read the run.
    workflow_sync_timeout_seconds: float = Field(default=30.0, gt=0)
    workflow_run_timeout_seconds: float = Field(default=600.0, gt=0)
    workflow_poll_interval_seconds: float = Field(default=0.3, gt=0)
    workflow_max_concurrent_runs: int = Field(default=8, ge=1)

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
