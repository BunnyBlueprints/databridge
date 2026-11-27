"""Backend-only AES-256-GCM credential encryption and decryption service."""

import json
import os
from collections.abc import Mapping
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from app.core.config import Settings, parse_credential_keyring_json
from app.schemas.connection_test import (
    ConnectorType,
    MongoDbConnectionTestCredentials,
    RestApiConnectionTestCredentials,
    SqlConnectionTestCredentials,
    _require_non_empty_text,
)

SUPPORTED_ENCRYPTION_ALGORITHM = "AES-256-GCM"
SUPPORTED_ENCRYPTION_FORMAT_VERSION = 1
AES_256_KEY_BYTES = 32
AES_GCM_NONCE_BYTES = 12
AES_GCM_TAG_BYTES = 16


class SqlStoredCredentials(SqlConnectionTestCredentials):
    """Stored SQL credentials including encrypted username alongside optional password."""

    username: SecretStr | None = None

    @field_validator("username")
    @classmethod
    def _check_optional_username(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        if not raw:
            return None
        cleaned = _require_non_empty_text(raw, "Username", max_length=128)
        return SecretStr(cleaned)


class MongoDbStoredCredentials(MongoDbConnectionTestCredentials):
    """Stored MongoDB credentials including encrypted username alongside optional password."""

    username: SecretStr | None = None

    @field_validator("username")
    @classmethod
    def _check_optional_username(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        if not raw:
            return None
        cleaned = _require_non_empty_text(raw, "Username", max_length=128)
        return SecretStr(cleaned)


StoredCredentialPayload = (
    SqlStoredCredentials
    | SqlConnectionTestCredentials
    | MongoDbStoredCredentials
    | MongoDbConnectionTestCredentials
    | RestApiConnectionTestCredentials
)


class CredentialEncryptionError(Exception):
    """Base application error for credential encryption/decryption failures."""


class CredentialConfigurationError(CredentialEncryptionError):
    """Raised when encryption keys or active key configuration is missing or invalid."""


class CredentialDecryptionError(CredentialEncryptionError):
    """Raised when stored credentials cannot be verified or decrypted safely."""


class CredentialPayloadError(CredentialEncryptionError):
    """Raised when credential payload structure is invalid for the connector type."""


class EncryptedCredential(BaseModel):
    """Typed storage representation for encrypted connector credentials.

    Never contains plaintext secrets. Raw ciphertext and nonce bytes are excluded
    from object `repr` to prevent accidental exposure in tracebacks or debug output.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ciphertext: bytes = Field(repr=False)
    nonce: bytes = Field(repr=False)
    key_id: str
    algorithm: Literal["AES-256-GCM"] = SUPPORTED_ENCRYPTION_ALGORITHM
    format_version: int = SUPPORTED_ENCRYPTION_FORMAT_VERSION


def build_credential_aad(
    *,
    connection_id: str,
    connector_type: ConnectorType,
    key_id: str,
) -> bytes:
    """Build deterministic Associated Authenticated Data binding ciphertext to its record."""
    clean_conn_id = connection_id.strip()
    clean_key_id = key_id.strip()
    if not clean_conn_id or not clean_key_id:
        raise CredentialPayloadError(
            "Connection ID and key ID are required for credential binding."
        )
    return f"{clean_conn_id}:{connector_type}:{clean_key_id}".encode()


def _validate_credentials_for_connector(
    connector_type: ConnectorType,
    credentials: StoredCredentialPayload | Mapping[str, object],
) -> tuple[StoredCredentialPayload, dict[str, str]]:
    """Validate connector-specific credentials and extract non-empty secret values."""
    if connector_type in ("CSV", "JSON"):
        raise CredentialPayloadError(
            f"Connector type '{connector_type}' does not support stored credentials."
        )

    model_cls: type[StoredCredentialPayload]
    if connector_type in ("PostgreSQL", "MySQL"):
        model_cls = SqlStoredCredentials
    elif connector_type == "MongoDB":
        model_cls = MongoDbStoredCredentials
    elif connector_type == "REST API":
        model_cls = RestApiConnectionTestCredentials
    else:
        raise CredentialPayloadError(f"Unsupported connector type '{connector_type}'.")

    try:
        if type(credentials) is model_cls:
            validated = credentials
        elif isinstance(credentials, BaseModel):
            validated = model_cls.model_validate(
                {
                    name: getattr(credentials, name).get_secret_value()
                    if isinstance(getattr(credentials, name), SecretStr)
                    else getattr(credentials, name)
                    for name in type(credentials).model_fields
                    if getattr(credentials, name) is not None
                }
            )
        else:
            validated = model_cls.model_validate(credentials)
    except ValidationError as exc:
        raise CredentialPayloadError(
            f"Invalid credential payload structure for '{connector_type}'."
        ) from exc

    secret_map: dict[str, str] = {}
    for field_name in type(validated).model_fields:
        field_value = getattr(validated, field_name)
        if field_value is not None:
            raw_secret = field_value.get_secret_value()
            if raw_secret:
                secret_map[field_name] = raw_secret

    if not secret_map:
        raise CredentialPayloadError("At least one non-empty credential field is required.")

    return validated, secret_map


class CredentialEncryptionService:
    """Encrypts and decrypts connector credentials using versioned AES-256-GCM keys."""

    def __init__(
        self,
        *,
        keyring: Mapping[str, bytes],
        active_key_id: str,
    ) -> None:
        if not keyring:
            raise CredentialConfigurationError("Credential encryption keyring must not be empty.")
        if active_key_id not in keyring:
            raise CredentialConfigurationError(
                "Active credential encryption key ID is not present in keyring."
            )

        validated_ring: dict[str, bytes] = {}
        for key_id, key_bytes in keyring.items():
            if not key_id.strip():
                raise CredentialConfigurationError("Credential key ID must not be empty.")
            if not isinstance(key_bytes, bytes) or len(key_bytes) != AES_256_KEY_BYTES:
                raise CredentialConfigurationError(
                    "Credential encryption keys must be exactly 32 bytes."
                )
            validated_ring[key_id] = key_bytes

        self._keyring = validated_ring
        self._active_key_id = active_key_id

    @classmethod
    def from_settings(cls, settings: Settings) -> "CredentialEncryptionService":
        """Construct CredentialEncryptionService from validated environment Settings."""
        if settings.credential_encryption_keys is None or not settings.credential_active_key_id:
            raise CredentialConfigurationError(
                "Credential encryption keys and active key ID must be configured."
            )

        try:
            keyring = parse_credential_keyring_json(
                settings.credential_encryption_keys.get_secret_value()
            )
        except ValueError as exc:
            raise CredentialConfigurationError(
                "Credential encryption configuration is invalid."
            ) from exc

        return cls(keyring=keyring, active_key_id=settings.credential_active_key_id)

    @property
    def active_key_id(self) -> str:
        """Return the key ID currently used for all new encryptions."""
        return self._active_key_id

    def has_key(self, key_id: str) -> bool:
        """Return True if `key_id` is available in the configured keyring."""
        return key_id in self._keyring

    def resolve_key(self, key_id: str) -> bytes:
        """Resolve the 32-byte key for `key_id` or raise CredentialDecryptionError."""
        key = self._keyring.get(key_id)
        if key is None:
            raise CredentialDecryptionError(
                "Unable to decrypt stored credentials: referenced encryption key is unavailable."
            )
        return key

    def validate_encrypted_metadata(self, encrypted: EncryptedCredential) -> None:
        """Validate envelope version, algorithm, nonce length, and ciphertext structure."""
        if encrypted.format_version != SUPPORTED_ENCRYPTION_FORMAT_VERSION:
            raise CredentialDecryptionError("Unsupported encrypted credential format version.")
        if encrypted.algorithm != SUPPORTED_ENCRYPTION_ALGORITHM:
            raise CredentialDecryptionError("Unsupported credential encryption algorithm.")
        if not encrypted.key_id or not encrypted.key_id.strip():
            raise CredentialDecryptionError("Encrypted credential metadata is missing key ID.")
        if len(encrypted.nonce) != AES_GCM_NONCE_BYTES:
            raise CredentialDecryptionError("Encrypted credential nonce has invalid length.")
        if len(encrypted.ciphertext) < AES_GCM_TAG_BYTES:
            raise CredentialDecryptionError("Encrypted credential ciphertext is malformed.")

    def encrypt(
        self,
        *,
        connection_id: str,
        connector_type: ConnectorType,
        credentials: StoredCredentialPayload | Mapping[str, object],
    ) -> EncryptedCredential:
        """Validate and encrypt connector credentials with the active AES-256-GCM key."""
        _, secret_map = _validate_credentials_for_connector(connector_type, credentials)

        key_id = self._active_key_id
        key = self.resolve_key(key_id)
        nonce = os.urandom(AES_GCM_NONCE_BYTES)
        aad = build_credential_aad(
            connection_id=connection_id,
            connector_type=connector_type,
            key_id=key_id,
        )

        envelope = {
            "v": SUPPORTED_ENCRYPTION_FORMAT_VERSION,
            "connectorType": connector_type,
            "secrets": secret_map,
        }
        plaintext_bytes = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        ciphertext = AESGCM(key).encrypt(nonce, plaintext_bytes, aad)

        return EncryptedCredential(
            ciphertext=ciphertext,
            nonce=nonce,
            key_id=key_id,
            algorithm=SUPPORTED_ENCRYPTION_ALGORITHM,
            format_version=SUPPORTED_ENCRYPTION_FORMAT_VERSION,
        )

    def decrypt(
        self,
        *,
        encrypted: EncryptedCredential,
        connection_id: str,
        connector_type: ConnectorType,
    ) -> StoredCredentialPayload:
        """Verify metadata, AAD, and GCM tag, and return validated transient credentials."""
        self.validate_encrypted_metadata(encrypted)
        key = self.resolve_key(encrypted.key_id)
        aad = build_credential_aad(
            connection_id=connection_id,
            connector_type=connector_type,
            key_id=encrypted.key_id,
        )

        try:
            plaintext_bytes = AESGCM(key).decrypt(encrypted.nonce, encrypted.ciphertext, aad)
        except InvalidTag as exc:
            raise CredentialDecryptionError(
                "Stored credentials failed integrity verification or could not be decrypted."
            ) from exc
        except Exception as exc:
            raise CredentialDecryptionError("Stored credentials could not be decrypted.") from exc

        try:
            envelope = json.loads(plaintext_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CredentialDecryptionError("Decrypted credential payload is malformed.") from exc

        if (
            not isinstance(envelope, dict)
            or envelope.get("v") != SUPPORTED_ENCRYPTION_FORMAT_VERSION
            or envelope.get("connectorType") != connector_type
            or not isinstance(envelope.get("secrets"), dict)
        ):
            raise CredentialDecryptionError("Decrypted credential envelope is invalid.")

        try:
            validated, _ = _validate_credentials_for_connector(
                connector_type,
                envelope["secrets"],
            )
            return validated
        except CredentialPayloadError as exc:
            raise CredentialDecryptionError(
                "Decrypted credential payload failed connector validation."
            ) from exc
