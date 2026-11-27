"""Unit tests for AES-256-GCM credential encryption, key rotation, and config safety."""

import base64
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.schemas.connection_test import (
    MongoDbConnectionTestCredentials,
    RestApiConnectionTestCredentials,
    SqlConnectionTestCredentials,
)
from app.services.credential_encryption import (
    CredentialConfigurationError,
    CredentialDecryptionError,
    CredentialEncryptionService,
    CredentialPayloadError,
    EncryptedCredential,
    MongoDbStoredCredentials,
    SqlStoredCredentials,
)

# Deterministic 32-byte test keys (never used in production).
_TEST_KEY_V1_BYTES = bytes(range(1, 33))
_TEST_KEY_V2_BYTES = bytes(range(101, 133))
_TEST_KEY_V1_B64 = base64.b64encode(_TEST_KEY_V1_BYTES).decode("ascii")
_TEST_KEY_V2_B64 = base64.b64encode(_TEST_KEY_V2_BYTES).decode("ascii")


def _make_service(
    *,
    keys: dict[str, bytes] | None = None,
    active_key_id: str = "k1",
) -> CredentialEncryptionService:
    keyring = keys if keys is not None else {"k1": _TEST_KEY_V1_BYTES, "k2": _TEST_KEY_V2_BYTES}
    return CredentialEncryptionService(keyring=keyring, active_key_id=active_key_id)


def test_round_trip_encryption_and_decryption_for_supported_connectors() -> None:
    svc = _make_service(active_key_id="k1")

    pg_enc = svc.encrypt(
        connection_id="conn-pg-1",
        connector_type="PostgreSQL",
        credentials=SqlConnectionTestCredentials(password="pg_super_secret"),
    )
    assert isinstance(pg_enc, EncryptedCredential)
    assert pg_enc.key_id == "k1"
    assert pg_enc.algorithm == "AES-256-GCM"
    assert pg_enc.format_version == 1
    assert len(pg_enc.nonce) == 12
    assert b"pg_super_secret" not in pg_enc.ciphertext
    assert "pg_super_secret" not in repr(pg_enc)

    pg_dec = svc.decrypt(
        encrypted=pg_enc,
        connection_id="conn-pg-1",
        connector_type="PostgreSQL",
    )
    assert isinstance(pg_dec, SqlConnectionTestCredentials)
    assert pg_dec.password is not None
    assert pg_dec.password.get_secret_value() == "pg_super_secret"

    mongo_enc = svc.encrypt(
        connection_id="conn-mongo-1",
        connector_type="MongoDB",
        credentials=MongoDbConnectionTestCredentials(
            password="mongo_pw",
            connectionUri="mongodb+srv://reader:mongo_pw@cluster01.mongodb.net",
        ),
    )
    mongo_dec = svc.decrypt(
        encrypted=mongo_enc,
        connection_id="conn-mongo-1",
        connector_type="MongoDB",
    )
    assert isinstance(mongo_dec, MongoDbConnectionTestCredentials)
    assert mongo_dec.connectionUri is not None
    assert (
        mongo_dec.connectionUri.get_secret_value()
        == "mongodb+srv://reader:mongo_pw@cluster01.mongodb.net"
    )

    rest_enc = svc.encrypt(
        connection_id="conn-rest-1",
        connector_type="REST API",
        credentials=RestApiConnectionTestCredentials(apiKey="sk_live_test_123"),
    )
    rest_dec = svc.decrypt(
        encrypted=rest_enc,
        connection_id="conn-rest-1",
        connector_type="REST API",
    )
    assert isinstance(rest_dec, RestApiConnectionTestCredentials)
    assert rest_dec.apiKey is not None
    assert rest_dec.apiKey.get_secret_value() == "sk_live_test_123"


def test_repeated_encryption_uses_fresh_random_nonces() -> None:
    svc = _make_service()
    creds = SqlConnectionTestCredentials(password="identical_secret")

    enc_a = svc.encrypt(connection_id="conn-1", connector_type="MySQL", credentials=creds)
    enc_b = svc.encrypt(connection_id="conn-1", connector_type="MySQL", credentials=creds)

    assert enc_a.nonce != enc_b.nonce
    assert enc_a.ciphertext != enc_b.ciphertext


def test_key_rotation_selects_active_key_and_decrypts_historical_keys() -> None:
    svc_v1 = _make_service(keys={"k1": _TEST_KEY_V1_BYTES}, active_key_id="k1")
    legacy_record = svc_v1.encrypt(
        connection_id="conn-rot-1",
        connector_type="PostgreSQL",
        credentials=SqlConnectionTestCredentials(password="legacy_pw"),
    )
    assert legacy_record.key_id == "k1"

    svc_v2 = _make_service(
        keys={"k1": _TEST_KEY_V1_BYTES, "k2": _TEST_KEY_V2_BYTES},
        active_key_id="k2",
    )
    new_record = svc_v2.encrypt(
        connection_id="conn-rot-2",
        connector_type="PostgreSQL",
        credentials=SqlConnectionTestCredentials(password="new_pw"),
    )
    assert new_record.key_id == "k2"

    # Both k1 (historical) and k2 (active) decrypt cleanly using record.key_id.
    dec_legacy = svc_v2.decrypt(
        encrypted=legacy_record,
        connection_id="conn-rot-1",
        connector_type="PostgreSQL",
    )
    dec_new = svc_v2.decrypt(
        encrypted=new_record,
        connection_id="conn-rot-2",
        connector_type="PostgreSQL",
    )
    assert dec_legacy.password is not None and dec_legacy.password.get_secret_value() == "legacy_pw"
    assert dec_new.password is not None and dec_new.password.get_secret_value() == "new_pw"


def test_unknown_key_id_fails_safely() -> None:
    svc_v1 = _make_service(keys={"k1": _TEST_KEY_V1_BYTES}, active_key_id="k1")
    enc = svc_v1.encrypt(
        connection_id="conn-1",
        connector_type="PostgreSQL",
        credentials=SqlConnectionTestCredentials(password="secret_pw"),
    )

    svc_v2_only = _make_service(keys={"k2": _TEST_KEY_V2_BYTES}, active_key_id="k2")
    with pytest.raises(CredentialDecryptionError) as exc_info:
        svc_v2_only.decrypt(encrypted=enc, connection_id="conn-1", connector_type="PostgreSQL")

    assert "secret_pw" not in str(exc_info.value)
    assert _TEST_KEY_V1_B64 not in str(exc_info.value)


def test_invalid_base64_and_key_lengths_rejected_without_leaking_key_material() -> None:
    bad_b64 = "not!valid!base64==="
    with pytest.raises(ValueError) as bad_b64_err:
        Settings(
            credential_encryption_keys=json.dumps({"k1": bad_b64}),  # type: ignore[arg-type]
            credential_active_key_id="k1",
        )
    assert bad_b64 not in str(bad_b64_err.value)

    short_key = base64.b64encode(b"too_short_key").decode("ascii")
    with pytest.raises(ValueError) as short_err:
        Settings(
            credential_encryption_keys=json.dumps({"k1": short_key}),  # type: ignore[arg-type]
            credential_active_key_id="k1",
        )
    assert short_key not in str(short_err.value)

    with pytest.raises(ValueError):
        Settings(
            credential_encryption_keys=json.dumps({"k1": _TEST_KEY_V1_B64}),  # type: ignore[arg-type]
            credential_active_key_id="missing_key",
        )

    with pytest.raises(CredentialConfigurationError):
        CredentialEncryptionService(keyring={"k1": b"short"}, active_key_id="k1")


def test_invalid_nonce_tampered_ciphertext_aad_mismatch_and_version_errors() -> None:
    svc = _make_service()
    enc = svc.encrypt(
        connection_id="conn-pg-1",
        connector_type="PostgreSQL",
        credentials=SqlConnectionTestCredentials(password="sensitive_secret_123"),
    )

    # Invalid nonce length
    bad_nonce = EncryptedCredential(
        ciphertext=enc.ciphertext,
        nonce=b"short",
        key_id=enc.key_id,
    )
    with pytest.raises(CredentialDecryptionError):
        svc.decrypt(encrypted=bad_nonce, connection_id="conn-pg-1", connector_type="PostgreSQL")

    # Tampered ciphertext / GCM auth tag failure
    tampered_bytes = bytearray(enc.ciphertext)
    tampered_bytes[0] ^= 0xFF
    tampered = EncryptedCredential(
        ciphertext=bytes(tampered_bytes),
        nonce=enc.nonce,
        key_id=enc.key_id,
    )
    with pytest.raises(CredentialDecryptionError) as tamper_err:
        svc.decrypt(encrypted=tampered, connection_id="conn-pg-1", connector_type="PostgreSQL")
    assert "sensitive_secret_123" not in str(tamper_err.value)

    # AAD mismatch across connection IDs
    with pytest.raises(CredentialDecryptionError):
        svc.decrypt(encrypted=enc, connection_id="conn-pg-other", connector_type="PostgreSQL")

    # AAD mismatch across connector types
    with pytest.raises(CredentialDecryptionError):
        svc.decrypt(encrypted=enc, connection_id="conn-pg-1", connector_type="MySQL")

    # Unsupported format version
    bad_version = EncryptedCredential(
        ciphertext=enc.ciphertext,
        nonce=enc.nonce,
        key_id=enc.key_id,
        format_version=99,
    )
    with pytest.raises(CredentialDecryptionError):
        svc.decrypt(encrypted=bad_version, connection_id="conn-pg-1", connector_type="PostgreSQL")


def test_disallowed_file_connector_and_empty_credentials_rejected() -> None:
    svc = _make_service()

    with pytest.raises(CredentialPayloadError):
        svc.encrypt(
            connection_id="conn-csv-1",
            connector_type="CSV",
            credentials={"fileContent": "id,name\n1,Alice\n"},
        )

    with pytest.raises(CredentialPayloadError):
        svc.encrypt(
            connection_id="conn-json-1",
            connector_type="JSON",
            credentials={"password": "x"},
        )

    with pytest.raises(CredentialPayloadError):
        svc.encrypt(
            connection_id="conn-pg-1",
            connector_type="PostgreSQL",
            credentials=SqlConnectionTestCredentials(password=None),
        )


def test_no_plaintext_or_keys_in_logs_or_validation_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc = _make_service()
    secret = "ultra_sensitive_secret_9988"

    with caplog.at_level(logging.DEBUG):
        enc = svc.encrypt(
            connection_id="conn-1",
            connector_type="REST API",
            credentials=RestApiConnectionTestCredentials(bearerToken=secret),
        )
        tampered = EncryptedCredential(
            ciphertext=b"\x00" * 24,
            nonce=enc.nonce,
            key_id=enc.key_id,
        )
        with pytest.raises(CredentialDecryptionError) as err:
            svc.decrypt(encrypted=tampered, connection_id="conn-1", connector_type="REST API")

    assert secret not in str(err.value)
    assert secret not in caplog.text
    assert _TEST_KEY_V1_B64 not in caplog.text
    assert _TEST_KEY_V2_B64 not in caplog.text


def test_persistent_mode_requires_encryption_config_while_stateless_mode_remains_operational() -> (
    None
):
    # Persistence enabled without encryption keys prevents startup.
    persistent_without_keys = Settings(
        database_url="postgresql://databridge:pw@127.0.0.1:5432/databridge",  # type: ignore[arg-type]
    )
    with pytest.raises(CredentialConfigurationError):
        create_app(settings=persistent_without_keys)

    # Stateless mode (no DATABASE_URL) starts cleanly without encryption keys for Task 03.
    stateless_app = create_app(settings=Settings(database_url=None))
    with TestClient(stateless_app) as client:
        assert client.get("/health").status_code == 200
        resp = client.post(
            "/api/v1/connections/test",
            json={
                "connectorType": "JSON",
                "config": {"fileName": "check.json"},
                "fileContent": '{"ok":true}',
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


def test_stored_credentials_support_encrypted_usernames_and_legacy_records() -> None:
    svc = _make_service(active_key_id="k1")

    sql_enc = svc.encrypt(
        connection_id="conn-sql-u",
        connector_type="PostgreSQL",
        credentials=SqlStoredCredentials(username="sql_secret_user", password="sql_secret_pw"),
    )
    assert b"sql_secret_user" not in sql_enc.ciphertext
    assert b"sql_secret_pw" not in sql_enc.ciphertext

    sql_dec = svc.decrypt(
        encrypted=sql_enc,
        connection_id="conn-sql-u",
        connector_type="PostgreSQL",
    )
    assert isinstance(sql_dec, SqlStoredCredentials)
    assert sql_dec.username is not None
    assert sql_dec.username.get_secret_value() == "sql_secret_user"
    assert sql_dec.password is not None
    assert sql_dec.password.get_secret_value() == "sql_secret_pw"

    mongo_enc = svc.encrypt(
        connection_id="conn-mongo-u",
        connector_type="MongoDB",
        credentials=MongoDbStoredCredentials(
            username="mongo_secret_user",
            password="mongo_secret_pw",
        ),
    )
    assert b"mongo_secret_user" not in mongo_enc.ciphertext
    mongo_dec = svc.decrypt(
        encrypted=mongo_enc,
        connection_id="conn-mongo-u",
        connector_type="MongoDB",
    )
    assert isinstance(mongo_dec, MongoDbStoredCredentials)
    assert mongo_dec.username is not None
    assert mongo_dec.username.get_secret_value() == "mongo_secret_user"
