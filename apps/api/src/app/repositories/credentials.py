"""Asynchronous PostgreSQL repository for encrypted credential vault records."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from app.db.errors import DatabaseError
from app.repositories.base import BaseRepository, translate_db_error
from app.services.credential_encryption import EncryptedCredential


@dataclass(frozen=True)
class StoredCredentialRecord:
    """Internal database representation of an encrypted credential row (never contains secrets)."""

    connection_id: str
    encrypted: EncryptedCredential
    created_at: str
    updated_at: str


def _to_iso_string(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    return str(value)


def _row_to_stored_credential(row: tuple[Any, ...]) -> StoredCredentialRecord:
    encrypted = EncryptedCredential(
        ciphertext=bytes(row[1]),
        nonce=bytes(row[2]),
        key_id=str(row[3]),
        algorithm=cast(Literal["AES-256-GCM"], str(row[4])),
        format_version=int(row[5]),
    )
    return StoredCredentialRecord(
        connection_id=str(row[0]),
        encrypted=encrypted,
        created_at=_to_iso_string(row[6]),
        updated_at=_to_iso_string(row[7]),
    )


class CredentialRepository(BaseRepository):
    """SQL repository managing `connection_credentials` (performs no encryption/decryption)."""

    async def upsert(
        self,
        *,
        connection_id: str,
        encrypted: EncryptedCredential,
        conn: Any | None = None,
    ) -> StoredCredentialRecord:
        """Insert or replace the encrypted credential record for `connection_id`."""
        query = """
            INSERT INTO connection_credentials (
                connection_id,
                ciphertext,
                nonce,
                key_id,
                algorithm,
                format_version
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (connection_id) DO UPDATE SET
                ciphertext = EXCLUDED.ciphertext,
                nonce = EXCLUDED.nonce,
                key_id = EXCLUDED.key_id,
                algorithm = EXCLUDED.algorithm,
                format_version = EXCLUDED.format_version,
                updated_at = NOW()
            RETURNING
                connection_id,
                ciphertext,
                nonce,
                key_id,
                algorithm,
                format_version,
                created_at,
                updated_at
        """
        params = (
            connection_id,
            encrypted.ciphertext,
            encrypted.nonce,
            encrypted.key_id,
            encrypted.algorithm,
            encrypted.format_version,
        )

        async def _run(active_conn: Any) -> StoredCredentialRecord:
            try:
                cur = await active_conn.execute(query, params)
                row = await cur.fetchone()
                if row is None:
                    raise DatabaseError("Expected persisted credential row was not returned.")
                return _row_to_stored_credential(row)
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.connection() as pooled_conn:
            return await _run(pooled_conn)

    async def get_by_connection_id(
        self,
        connection_id: str,
        *,
        conn: Any | None = None,
    ) -> StoredCredentialRecord | None:
        """Fetch encrypted credential record for `connection_id`, or None if absent."""
        query = """
            SELECT
                connection_id,
                ciphertext,
                nonce,
                key_id,
                algorithm,
                format_version,
                created_at,
                updated_at
            FROM connection_credentials
            WHERE connection_id = %s
        """

        async def _run(active_conn: Any) -> StoredCredentialRecord | None:
            try:
                cur = await active_conn.execute(query, (connection_id,))
                row = await cur.fetchone()
                return _row_to_stored_credential(row) if row is not None else None
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.connection() as pooled_conn:
            return await _run(pooled_conn)

    async def delete_by_connection_id(
        self,
        connection_id: str,
        *,
        conn: Any | None = None,
    ) -> bool:
        """Delete stored credential record for `connection_id`. Return True if deleted."""
        query = """
            DELETE FROM connection_credentials
            WHERE connection_id = %s
            RETURNING connection_id
        """

        async def _run(active_conn: Any) -> bool:
            try:
                cur = await active_conn.execute(query, (connection_id,))
                row = await cur.fetchone()
                return row is not None
            except DatabaseError:
                raise
            except Exception as exc:
                raise translate_db_error(exc) from exc

        if conn is not None:
            return await _run(conn)
        async with self.connection() as pooled_conn:
            return await _run(pooled_conn)
