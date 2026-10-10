from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "Will There Be a Bike API"
    app_version: str = "0.1.0"
    app_env: Literal["development", "test", "production"] = "development"
    app_debug: bool = False
    app_log_level: str = "INFO"
    app_cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    database_url: str = Field(
        default="postgresql+asyncpg://bike:change-me-for-production@db:5432/bike_forecast",
        repr=False,
    )
    gbfs_discovery_url: str = "https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json"
    gbfs_poll_seconds: int = Field(default=60, ge=30)
    app_public_origin: str = "http://localhost:5173"
    auth_cookie_secure: bool = False
    auth_session_hours: int = Field(default=168, ge=1, le=720)
    auth_rate_limit: int = Field(default=10, ge=1, le=100)
    db_pool_size: int = Field(default=4, ge=1, le=10)
    background_jobs_enabled: bool = False
    snapshot_retention_days: int = Field(default=30, ge=7, le=365)
    job_retention_days: int = Field(default=90, ge=7, le=365)
    docs_enabled: bool = True
    demo_mode: bool = False
    frontend_dist_path: Path | None = None

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        # Managed providers return PostgreSQL URLs, not SQLAlchemy driver names.
        try:
            parts = urlsplit(value)
        except ValueError:
            # urllib errors can embed the entire netloc, including passwords.
            raise ValueError("DATABASE_URL must be a valid PostgreSQL URL") from None
        if parts.scheme in {"postgres", "postgresql", "postgresql+asyncpg"}:
            query = dict(parse_qsl(parts.query))
            if "sslmode" in query:
                query["ssl"] = query.pop("sslmode")
            return urlunsplit(parts._replace(scheme="postgresql+asyncpg", query=urlencode(query)))
        if parts.scheme != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use PostgreSQL with the asyncpg driver")
        return value

    @model_validator(mode="after")
    def production_security(self) -> Settings:
        if self.demo_mode and self.background_jobs_enabled:
            raise ValueError("DEMO_MODE requires background provider jobs disabled")
        try:
            origin = urlsplit(self.app_public_origin)
        except ValueError:
            raise ValueError("APP_PUBLIC_ORIGIN must be a valid HTTP(S) origin") from None
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path
            or origin.query
            or origin.fragment
        ):
            raise ValueError("APP_PUBLIC_ORIGIN must be an exact HTTP(S) origin without a path")
        if self.app_env == "production":
            if "app_cors_origins" not in self.model_fields_set:
                self.app_cors_origins = [self.app_public_origin]
            if not self.auth_cookie_secure or not self.app_public_origin.startswith("https://"):
                raise ValueError("Production requires HTTPS APP_PUBLIC_ORIGIN and secure cookies")
            if self.app_debug or "change-me" in self.database_url:
                raise ValueError("Production requires debug disabled and a private database secret")
        if "*" in self.app_cors_origins:
            raise ValueError("Credentialed CORS requires explicit origins")
        return self

    ml_artifact_path: Path = (
        Path(__file__).resolve().parents[1] / "modules" / "ml" / "artifacts" / "current.json"
    )

    @field_validator("app_cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("app_log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache
def get_settings() -> Settings:
    return Settings()
