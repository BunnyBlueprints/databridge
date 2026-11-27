"""Environment-driven application configuration."""

import base64
import binascii
import json
import os
import re
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_AES_256_KEY_BYTES = 32


def parse_credential_keyring_json(raw_json: str) -> dict[str, bytes]:
    """Parse and validate Base64-encoded 32-byte keys from CREDENTIAL_ENCRYPTION_KEYS."""
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError("CREDENTIAL_ENCRYPTION_KEYS must be valid JSON.") from exc

    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("CREDENTIAL_ENCRYPTION_KEYS must be a non-empty JSON object.")

    keyring: dict[str, bytes] = {}
    for key_id, raw_key in parsed.items():
        if not isinstance(key_id, str) or not _KEY_ID_RE.match(key_id):
            raise ValueError("CREDENTIAL_ENCRYPTION_KEYS contains an invalid key ID.")
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError("CREDENTIAL_ENCRYPTION_KEYS values must be Base64 strings.")
        try:
            decoded = base64.b64decode(raw_key.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                "CREDENTIAL_ENCRYPTION_KEYS contains invalid Base64 key material."
            ) from exc
        if len(decoded) != _AES_256_KEY_BYTES:
            raise ValueError(
                "Each key in CREDENTIAL_ENCRYPTION_KEYS must decode to exactly 32 bytes."
            )
        keyring[key_id] = decoded

    return keyring


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
    schema_discovery_timeout_seconds: float = Field(default=15.0, gt=0.0, le=60.0)
    schema_discovery_max_objects: int = Field(default=250, ge=1, le=2000)
    schema_discovery_max_fields_per_object: int = Field(default=500, ge=1, le=2000)
    schema_discovery_default_sample_limit: int = Field(default=100, ge=1, le=500)
    schema_discovery_max_sample_limit: int = Field(default=500, ge=1, le=1000)
    enable_api_docs: bool = True

    # PostgreSQL persistence configuration (optional; absent = stateless mode)
    database_url: SecretStr | None = None
    database_pool_min_size: int = Field(default=1, ge=1, le=50)
    database_pool_max_size: int = Field(default=10, ge=1, le=100)
    database_pool_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    database_health_check_enabled: bool = True

    # Credential encryption configuration (required when DATABASE_URL is configured)
    credential_encryption_keys: SecretStr | None = None
    credential_active_key_id: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    @property
    def database_enabled(self) -> bool:
        """Return True when PostgreSQL persistence is configured."""
        return self.database_url is not None

    @property
    def encryption_configured(self) -> bool:
        """Return True when credential encryption keys and active key ID are configured."""
        return (
            self.credential_encryption_keys is not None
            and self.credential_active_key_id is not None
        )

    @field_validator("database_url", mode="before")
    @classmethod
    def _validate_database_url(cls, value: object) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        stripped = raw.strip()
        if not stripped:
            return None

        parsed = urlparse(stripped)
        if parsed.scheme not in ("postgresql", "postgres"):
            raise ValueError("DATABASE_URL must use a postgresql:// or postgres:// scheme.")
        if not parsed.hostname or not parsed.netloc:
            raise ValueError("DATABASE_URL must include a valid hostname.")
        if not parsed.path or not parsed.path.strip("/"):
            raise ValueError("DATABASE_URL must include a target database name.")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("DATABASE_URL contains an invalid port.") from exc
        if port is not None and (port < 1 or port > 65535):
            raise ValueError("DATABASE_URL port must be between 1 and 65535.")

        return value if isinstance(value, SecretStr) else SecretStr(stripped)

    @field_validator("credential_encryption_keys", mode="before")
    @classmethod
    def _validate_credential_encryption_keys(cls, value: object) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        stripped = raw.strip()
        if not stripped:
            return None
        parse_credential_keyring_json(stripped)
        return value if isinstance(value, SecretStr) else SecretStr(stripped)

    @field_validator("credential_active_key_id", mode="before")
    @classmethod
    def _validate_credential_active_key_id(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("CREDENTIAL_ACTIVE_KEY_ID must be a string.")
        stripped = value.strip()
        if not stripped:
            return None
        if not _KEY_ID_RE.match(stripped):
            raise ValueError("CREDENTIAL_ACTIVE_KEY_ID has an invalid format.")
        return stripped

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
    def _validate_runtime_invariants(self) -> "Settings":
        if self.database_pool_min_size > self.database_pool_max_size:
            raise ValueError(
                "DATABASE_POOL_MIN_SIZE must be less than or equal to DATABASE_POOL_MAX_SIZE."
            )

        if self.schema_discovery_default_sample_limit > self.schema_discovery_max_sample_limit:
            raise ValueError(
                "SCHEMA_DISCOVERY_DEFAULT_SAMPLE_LIMIT must be <= "
                "SCHEMA_DISCOVERY_MAX_SAMPLE_LIMIT."
            )

        if (self.credential_encryption_keys is None) != (self.credential_active_key_id is None):
            raise ValueError(
                "CREDENTIAL_ENCRYPTION_KEYS and CREDENTIAL_ACTIVE_KEY_ID must both be set together."
            )

        if (
            self.credential_encryption_keys is not None
            and self.credential_active_key_id is not None
        ):
            keyring = parse_credential_keyring_json(
                self.credential_encryption_keys.get_secret_value()
            )
            if self.credential_active_key_id not in keyring:
                raise ValueError(
                    "CREDENTIAL_ACTIVE_KEY_ID must match a key defined in "
                    "CREDENTIAL_ENCRYPTION_KEYS."
                )

        # In production, do not keep localhost defaults unless explicitly configured.
        if self.app_env == "production" and self.cors_allowed_origins == [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]:
            if "CORS_ALLOWED_ORIGINS" not in os.environ:
                self.cors_allowed_origins = []
        return self


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
