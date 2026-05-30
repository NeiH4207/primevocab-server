"""Application settings loaded from environment via pydantic-settings.

A single source of truth for connection strings, secrets and feature flags.
Anything env-driven goes through `Settings` so we never sprinkle
`os.getenv` calls across the codebase.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import List, Literal, Optional, Self
from urllib.parse import urlparse

from pydantic import (
    AliasChoices,
    Field,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    app_name: str = "Aiforen API"
    app_env: Literal["dev", "staging", "production"] = "dev"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    frontend_base_url: str = "http://localhost:3000"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value):
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                return json.loads(raw)
            return [part.strip() for part in raw.split(",") if part.strip()]
        return value

    @field_validator("frontend_base_url")
    @classmethod
    def _strip_frontend_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _merge_frontend_into_cors(self) -> Self:
        """Always allow the configured frontend origin (+ www/bare pair)."""
        origins: List[str] = list(self.cors_origins or [])
        base = (self.frontend_base_url or "").rstrip("/")
        if base:
            if base not in origins:
                origins.append(base)
            if base.startswith("https://www."):
                bare = "https://" + base.removeprefix("https://www.")
                if bare not in origins:
                    origins.append(bare)
            elif base.startswith("https://") and "://" in base:
                host = base.split("://", 1)[1]
                www = f"https://www.{host}"
                if www not in origins:
                    origins.append(www)
        object.__setattr__(self, "cors_origins", origins)
        return self

    @model_validator(mode="after")
    def _validate_production_safety(self) -> Self:
        if self.app_env == "production":
            if self.debug:
                raise ValueError("DEBUG must be false in production")
            if self.jwt_secret == "dev-secret-change-me":
                raise ValueError("JWT_SECRET must be changed in production")
            if not self.cors_origins or "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS must be explicit in production")
        return self

    # ---- Postgres (transactional data) ----
    database_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("database_url", "DATABASE_URL"),
    )
    pg_host: str = "postgres"
    pg_port: int = 5432
    pg_user: str = "aiforen"
    pg_password: str = "aiforen_dev"
    pg_db: str = "aiforen"

    @staticmethod
    def _pg_dsn(
        driver: str,
        url: Optional[str],
        user: str,
        password: str,
        host: str,
        port: int,
        db: str,
    ) -> str:
        if url:
            raw = url.strip()
            if raw.startswith("postgres://"):
                raw = "postgresql://" + raw[len("postgres://") :]
            parsed = urlparse(raw)
            if parsed.scheme not in (
                "postgresql",
                "postgresql+asyncpg",
                "postgresql+psycopg2",
            ):
                raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")
            user = parsed.username or user
            password = parsed.password or password
            host = parsed.hostname or host
            port = parsed.port or port
            db = (parsed.path or f"/{db}").lstrip("/") or db
        return f"{driver}://{user}:{password}@{host}:{port}/{db}"

    @computed_field  # type: ignore[misc]
    @property
    def pg_dsn_async(self) -> str:
        return self._pg_dsn(
            "postgresql+asyncpg",
            self.database_url,
            self.pg_user,
            self.pg_password,
            self.pg_host,
            self.pg_port,
            self.pg_db,
        )

    @computed_field  # type: ignore[misc]
    @property
    def pg_dsn_sync(self) -> str:
        return self._pg_dsn(
            "postgresql+psycopg2",
            self.database_url,
            self.pg_user,
            self.pg_password,
            self.pg_host,
            self.pg_port,
            self.pg_db,
        )

    # ---- Redis (cache, queue, pub/sub, quota) ----
    redis_url: str = "redis://redis:6379/0"

    # ---- JWT ----
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = Field(
        default=60 * 24 * 7,  # 7 days — avoids frequent re-login in dev
        validation_alias=AliasChoices(
            "jwt_access_ttl_minutes",
            "access_token_expire_minutes",
        ),
    )
    jwt_refresh_ttl_days: int = Field(
        default=365,
        validation_alias=AliasChoices(
            "jwt_refresh_ttl_days",
            "refresh_token_expire_days",
        ),
    )

    # ---- OAuth ----
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:3000/auth/callback"

    # ---- Integrations (mock-friendly) ----
    llm_provider: Literal["mock", "anthropic", "openai", "gemini"] = "mock"
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-opus-4-7"
    anthropic_vocab_eval_model: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "anthropic_vocab_eval_model",
            "ANTHROPIC_VOCAB_EVAL_MODEL",
        ),
    )
    openai_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("openai_api_key", "OPENAI_API_KEY"),
    )
    openai_model: str = Field(
        default="gpt-4.1-mini",
        validation_alias=AliasChoices("openai_model", "OPENAI_MODEL"),
    )
    openai_vocab_eval_model: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "openai_vocab_eval_model",
            "OPENAI_VOCAB_EVAL_MODEL",
        ),
    )
    gemini_api_key: Optional[str] = None
    google_translate_api_key: Optional[str] = None
    transipy_chunk_size: int = 16  # parallel workers for transipy.translate

    payment_provider: Literal["mock", "payos", "stripe"] = "mock"
    payos_client_id: Optional[str] = None
    payos_api_key: Optional[str] = None
    payos_checksum_key: Optional[str] = None

    storage_provider: Literal["local", "s3"] = "local"
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = None

    # ---- Rate limits ----
    rate_limit_default: str = "60/minute"
    rate_limit_auth: str = "10/minute"
    rate_limit_assessment: str = "5/minute"

    # ---- Quotas (fallback if plan doesn't override) ----
    free_assessments_per_month: int = 2
    free_vocab_ai_eval_total: int = 10

    # ---- Worker ----
    worker_concurrency: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
