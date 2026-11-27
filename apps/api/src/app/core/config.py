"""Environment-driven application configuration."""

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    app_env: Literal["development", "staging", "production"] = "development"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    api_max_request_bytes: int = Field(default=524_288, ge=1024, le=4_194_304)
    connection_test_timeout_seconds: float = Field(default=10.0, gt=0.0, le=120.0)
    connection_test_max_file_bytes: int = Field(default=262_144, ge=64, le=2_097_152)
    rest_api_max_response_bytes: int = Field(default=65_536, ge=1024, le=1_048_576)
    enable_api_docs: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ALLOWED_ORIGINS JSON must be a list of origins.")
                return [str(item).strip().rstrip("/") for item in parsed if str(item).strip()]
            return [origin.strip().rstrip("/") for origin in stripped.split(",") if origin.strip()]
        if isinstance(value, list):
            return [str(item).strip().rstrip("/") for item in value if str(item).strip()]
        raise ValueError("Invalid CORS_ALLOWED_ORIGINS format.")

    @field_validator("cors_allowed_origins")
    @classmethod
    def _validate_cors_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            if origin == "*":
                raise ValueError("Wildcard '*' CORS origin is not allowed.")
            if not (origin.startswith("http://") or origin.startswith("https://")):
                raise ValueError("CORS origins must start with http:// or https://.")
        return origins

    @model_validator(mode="after")
    def _enforce_production_cors(self) -> "Settings":
        # In production, do not keep localhost defaults unless explicitly configured.
        if self.app_env == "production" and self.cors_allowed_origins == [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]:
            # Only clear if CORS_ALLOWED_ORIGINS was not explicitly provided in env.
            import os

            if "CORS_ALLOWED_ORIGINS" not in os.environ:
                self.cors_allowed_origins = []
        return self


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
