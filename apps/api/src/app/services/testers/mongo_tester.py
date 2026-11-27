"""MongoDB read-only connectivity tester using PyMongo AsyncMongoClient."""

import logging
import socket
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import dns.exception
import pymongo
import pymongo.errors

from app.schemas.connection_test import (
    ConnectionTestErrorCode,
    ConnectionTestRequest,
    ConnectionTestResult,
    ConnectorType,
    MongoDbConnectionTestRequest,
)
from app.services.testers.common import (
    build_success_result,
    build_tester_failure,
    elapsed_ms,
    iter_exception_chain,
)

logger = logging.getLogger("databridge.api")

MongoClientFactory = Callable[..., Any]

_MONGO_AUTH_ERROR_CODES = {13, 18, 8000}
_MONGO_TIMEOUT_ERROR_CODES = {50}


def build_mongo_client_kwargs(
    request: MongoDbConnectionTestRequest,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Build AsyncMongoClient kwargs while avoiding duplicate URI/kwarg credentials."""
    timeout_ms = max(100, int(timeout_seconds * 1000))
    transient_uri = (
        request.credentials.connectionUri.get_secret_value().strip()
        if request.credentials and request.credentials.connectionUri is not None
        else None
    )
    target_uri = transient_uri or request.config.uri
    parsed = urlsplit(target_uri)
    has_embedded_userinfo = parsed.username is not None or "@" in parsed.netloc

    kwargs: dict[str, Any] = {
        "host": target_uri,
        "connectTimeoutMS": timeout_ms,
        "serverSelectionTimeoutMS": timeout_ms,
        "socketTimeoutMS": timeout_ms,
    }

    password_secret = (
        request.credentials.password.get_secret_value()
        if request.credentials and request.credentials.password is not None
        else None
    )

    if has_embedded_userinfo:
        # URI already carries userinfo; only supply password kwarg if URI lacks a password.
        if parsed.password is None and password_secret is not None:
            kwargs["password"] = password_secret
    else:
        if request.config.username:
            kwargs["username"] = request.config.username
        if password_secret is not None:
            kwargs["password"] = password_secret

    return kwargs


def _classify_mongo_error(exc: BaseException) -> tuple[ConnectionTestErrorCode, str, bool]:
    for cause in iter_exception_chain(exc):
        if isinstance(cause, (dns.exception.DNSException, socket.gaierror)):
            return (
                "DNS_RESOLUTION_FAILED",
                "Unable to resolve the MongoDB hostname or SRV record.",
                False,
            )
        if isinstance(cause, ConnectionRefusedError):
            return (
                "CONNECTION_REFUSED",
                "Unable to connect to MongoDB. Connection was refused by the target host.",
                False,
            )

    if isinstance(exc, pymongo.errors.OperationFailure):
        errmsg = str((exc.details or {}).get("errmsg", "")).lower()
        if exc.code in _MONGO_AUTH_ERROR_CODES or "authentication" in errmsg:
            return (
                "AUTHENTICATION_FAILED",
                "MongoDB authentication failed. Verify the credentials and auth database.",
                False,
            )
        if exc.code in _MONGO_TIMEOUT_ERROR_CODES:
            return (
                "TIMEOUT",
                "MongoDB ping command timed out before the server responded.",
                True,
            )
        return (
            "SERVER_ERROR",
            "MongoDB ping verification failed.",
            False,
        )

    if isinstance(exc, pymongo.errors.ConfigurationError):
        detail = str(exc).lower()
        if "dns" in detail or "srv" in detail or "resolution" in detail:
            return (
                "DNS_RESOLUTION_FAILED",
                "Unable to resolve the MongoDB hostname or SRV record.",
                False,
            )
        return (
            "INVALID_CONFIGURATION",
            "MongoDB connection URI or configuration is invalid.",
            False,
        )

    if isinstance(
        exc,
        (
            pymongo.errors.ServerSelectionTimeoutError,
            pymongo.errors.NetworkTimeout,
            pymongo.errors.ExecutionTimeout,
            TimeoutError,
        ),
    ):
        detail = str(exc).lower()
        if "connection refused" in detail:
            return (
                "CONNECTION_REFUSED",
                "Unable to connect to MongoDB. Connection was refused by the target host.",
                False,
            )
        if any(
            token in detail
            for token in (
                "nodename nor servname",
                "name or service not known",
                "temporary failure in name resolution",
                "dns",
            )
        ):
            return (
                "DNS_RESOLUTION_FAILED",
                "Unable to resolve the MongoDB hostname or SRV record.",
                False,
            )
        if "authentication failed" in detail:
            return (
                "AUTHENTICATION_FAILED",
                "MongoDB authentication failed. Verify the credentials and auth database.",
                False,
            )
        return (
            "TIMEOUT",
            "MongoDB connection test timed out waiting for server selection.",
            True,
        )

    if isinstance(exc, pymongo.errors.ConnectionFailure):
        return (
            "NETWORK_ERROR",
            "Unable to reach the MongoDB server over the network.",
            False,
        )

    return (
        "SERVER_ERROR",
        "An unexpected error occurred while testing the MongoDB connection.",
        False,
    )


class MongoDbConnectionTester:
    """Performs a read-only admin ping check against MongoDB (standard or SRV URI)."""

    connector_type: ConnectorType = "MongoDB"

    def __init__(self, client_factory: MongoClientFactory | None = None) -> None:
        self._client_factory: MongoClientFactory = client_factory or pymongo.AsyncMongoClient

    async def test(
        self,
        request: ConnectionTestRequest,
        timeout_seconds: float,
    ) -> ConnectionTestResult:
        if not isinstance(request, MongoDbConnectionTestRequest):
            return build_tester_failure(
                message="Invalid request type dispatched to MongoDB tester.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=0,
            )

        started = time.monotonic()
        client_kwargs = build_mongo_client_kwargs(request, timeout_seconds)
        client: Any = None

        try:
            client = self._client_factory(**client_kwargs)
            ping_response = await client.admin.command("ping")
            if not isinstance(ping_response, dict) or not ping_response.get("ok"):
                return build_tester_failure(
                    message="MongoDB ping command did not return an ok response.",
                    error_code="SERVER_ERROR",
                    duration_ms=elapsed_ms(started),
                )
            return build_success_result(
                "MongoDB connection established and verified with admin ping.",
                elapsed_ms(started),
            )
        except Exception as exc:
            error_code, safe_message, is_timeout = _classify_mongo_error(exc)
            return build_tester_failure(
                message=safe_message,
                error_code=error_code,
                duration_ms=elapsed_ms(started),
                is_timeout=is_timeout,
            )
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception as cleanup_exc:
                    logger.debug(
                        "MongoDB client cleanup failed: %s",
                        type(cleanup_exc).__name__,
                    )
