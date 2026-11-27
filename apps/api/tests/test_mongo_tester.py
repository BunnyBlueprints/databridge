"""Unit tests for the MongoDB connection tester."""

from typing import Any

import dns.exception
import pymongo.errors
import pytest

from app.schemas.connection_test import (
    MongoDbConnectionTestConfig,
    MongoDbConnectionTestCredentials,
    MongoDbConnectionTestRequest,
    PostgresConnectionTestRequest,
    SqlConnectionTestConfig,
)
from app.services.testers.mongo_tester import (
    MongoDbConnectionTester,
    build_mongo_client_kwargs,
)


def _mongo_request(
    *,
    uri: str = "mongodb+srv://cluster01.mongodb.net",
    username: str | None = "reader",
    password: str | None = "mongo_secret_pw",
    connection_uri: str | None = None,
) -> MongoDbConnectionTestRequest:
    creds = None
    if password is not None or connection_uri is not None:
        creds = MongoDbConnectionTestCredentials(
            password=password,
            connectionUri=connection_uri,
        )
    return MongoDbConnectionTestRequest(
        connectionId="conn-mongo-1",
        connectorType="MongoDB",
        config=MongoDbConnectionTestConfig(
            uri=uri,
            database="telemetry",
            username=username,
        ),
        credentials=creds,
    )


class _FakeAdminDb:
    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        command_error: BaseException | None = None,
    ) -> None:
        self._response = response if response is not None else {"ok": 1.0}
        self._command_error = command_error
        self.commands: list[str] = []

    async def command(self, name: str) -> dict[str, Any]:
        self.commands.append(name)
        if self._command_error is not None:
            raise self._command_error
        return self._response


class _FakeMongoClient:
    def __init__(
        self,
        admin: _FakeAdminDb,
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self.admin = admin
        self._close_error = close_error
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


@pytest.mark.anyio
async def test_mongo_successful_connection_and_admin_ping() -> None:
    captured_kwargs: dict[str, Any] = {}
    fake_admin = _FakeAdminDb(response={"ok": 1.0})
    fake_client = _FakeMongoClient(fake_admin)

    def fake_factory(**kwargs: Any) -> _FakeMongoClient:
        captured_kwargs.update(kwargs)
        return fake_client

    tester = MongoDbConnectionTester(client_factory=fake_factory)
    result = await tester.test(_mongo_request(), timeout_seconds=8.0)

    assert result.success is True
    assert result.status == "connected"
    assert result.errorCode is None
    assert fake_admin.commands == ["ping"]
    assert fake_client.closed is True
    assert captured_kwargs["host"] == "mongodb+srv://cluster01.mongodb.net"
    assert captured_kwargs["username"] == "reader"
    assert captured_kwargs["password"] == "mongo_secret_pw"
    assert captured_kwargs["connectTimeoutMS"] == 8000
    assert captured_kwargs["serverSelectionTimeoutMS"] == 8000
    assert captured_kwargs["socketTimeoutMS"] == 8000


@pytest.mark.anyio
async def test_mongo_authentication_failure() -> None:
    fake_admin = _FakeAdminDb(
        command_error=pymongo.errors.OperationFailure(
            "Authentication failed.",
            code=18,
            details={"ok": 0, "errmsg": "Authentication failed.", "code": 18},
        )
    )
    fake_client = _FakeMongoClient(fake_admin)

    tester = MongoDbConnectionTester(client_factory=lambda **_kw: fake_client)
    result = await tester.test(_mongo_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "failed"
    assert result.errorCode == "AUTHENTICATION_FAILED"
    assert fake_client.closed is True
    assert "mongo_secret_pw" not in result.message


@pytest.mark.anyio
async def test_mongo_server_selection_timeout() -> None:
    fake_admin = _FakeAdminDb(
        command_error=pymongo.errors.ServerSelectionTimeoutError(
            "cluster01.mongodb.net:27017: timed out"
        )
    )
    fake_client = _FakeMongoClient(fake_admin)

    tester = MongoDbConnectionTester(client_factory=lambda **_kw: fake_client)
    result = await tester.test(_mongo_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "timeout"
    assert result.errorCode == "TIMEOUT"
    assert fake_client.closed is True


@pytest.mark.anyio
async def test_mongo_srv_dns_resolution_failure() -> None:
    def dns_fail_factory(**_kwargs: Any) -> Any:
        try:
            raise dns.exception.DNSException("The DNS query name does not exist")
        except dns.exception.DNSException as err:
            raise pymongo.errors.ConfigurationError(
                "The DNS query name does not exist: _mongodb._tcp.bad.invalid"
            ) from err

    tester = MongoDbConnectionTester(client_factory=dns_fail_factory)
    result = await tester.test(_mongo_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.status == "failed"
    assert result.errorCode == "DNS_RESOLUTION_FAILED"


@pytest.mark.anyio
async def test_mongo_client_cleanup_even_when_close_fails() -> None:
    fake_admin = _FakeAdminDb(command_error=pymongo.errors.ConnectionFailure("network unreachable"))
    fake_client = _FakeMongoClient(fake_admin, close_error=RuntimeError("close boom"))

    tester = MongoDbConnectionTester(client_factory=lambda **_kw: fake_client)
    result = await tester.test(_mongo_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.errorCode == "NETWORK_ERROR"
    assert fake_client.closed is True


@pytest.mark.anyio
async def test_mongo_ping_not_ok_fails() -> None:
    fake_client = _FakeMongoClient(_FakeAdminDb(response={"ok": 0}))

    tester = MongoDbConnectionTester(client_factory=lambda **_kw: fake_client)
    result = await tester.test(_mongo_request(), timeout_seconds=5.0)

    assert result.success is False
    assert result.errorCode == "SERVER_ERROR"
    assert fake_client.closed is True


def test_mongo_uri_credential_handling_avoids_duplication() -> None:
    # Case 1: Transient connectionUri already carries username + password.
    req_with_full_uri = _mongo_request(
        username="config_user",
        password="config_password",
        connection_uri="mongodb+srv://uri_user:uri_password@cluster01.mongodb.net/telemetry",
    )
    kwargs_full = build_mongo_client_kwargs(req_with_full_uri, timeout_seconds=5.0)
    assert (
        kwargs_full["host"] == "mongodb+srv://uri_user:uri_password@cluster01.mongodb.net/telemetry"
    )
    assert "username" not in kwargs_full
    assert "password" not in kwargs_full

    # Case 2: Transient connectionUri carries username only, password supplied separately.
    req_user_only_uri = _mongo_request(
        username="config_user",
        password="separate_password",
        connection_uri="mongodb://uri_user@localhost:27017/telemetry",
    )
    kwargs_user_only = build_mongo_client_kwargs(req_user_only_uri, timeout_seconds=5.0)
    assert kwargs_user_only["host"] == "mongodb://uri_user@localhost:27017/telemetry"
    assert "username" not in kwargs_user_only
    assert kwargs_user_only["password"] == "separate_password"

    # Case 3: Non-secret config.uri with username and password.
    req_split = _mongo_request(
        uri="mongodb://localhost:27017",
        username="reader",
        password="split_password",
        connection_uri=None,
    )
    kwargs_split = build_mongo_client_kwargs(req_split, timeout_seconds=5.0)
    assert kwargs_split["host"] == "mongodb://localhost:27017"
    assert kwargs_split["username"] == "reader"
    assert kwargs_split["password"] == "split_password"


@pytest.mark.anyio
async def test_mongo_rejects_mismatched_request_type() -> None:
    tester = MongoDbConnectionTester()
    pg_req = PostgresConnectionTestRequest(
        connectorType="PostgreSQL",
        config=SqlConnectionTestConfig(
            host="pg.example.com",
            port=5432,
            database="orders",
            username="ro",
        ),
    )
    result = await tester.test(pg_req, timeout_seconds=5.0)
    assert result.success is False
    assert result.errorCode == "INVALID_CONFIGURATION"
