"""Unit and transport-level security tests for the SSRF-protected REST API tester."""

import asyncio
import logging
import socket
import ssl
from collections.abc import AsyncIterator
from typing import Any

import httpcore
import httpx
import pytest

from app.schemas.connection_test import (
    CsvConnectionTestConfig,
    CsvConnectionTestRequest,
    RestApiConnectionTestConfig,
    RestApiConnectionTestCredentials,
    RestApiConnectionTestRequest,
)
from app.services.testers.rest_tester import RestApiConnectionTester
from app.services.testers.ssrf import (
    SsrfBlockedError,
    SsrfSafeHttpTransport,
    SsrfSafeNetworkBackend,
)


def _rest_request(
    *,
    base_url: str = "https://api.public-service.example/v2/health",
    auth_type: str = "none",
    api_key: str | None = None,
    bearer_token: str | None = None,
) -> RestApiConnectionTestRequest:
    creds = None
    if api_key is not None or bearer_token is not None:
        creds = RestApiConnectionTestCredentials(
            apiKey=api_key,
            bearerToken=bearer_token,
        )
    return RestApiConnectionTestRequest(
        connectionId="conn-rest-1",
        connectorType="REST API",
        config=RestApiConnectionTestConfig(
            baseUrl=base_url,
            authType=auth_type,  # type: ignore[arg-type]
        ),
        credentials=creds,
    )


async def _public_resolver(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


class _FakeWireStream(httpcore.AsyncNetworkStream):
    """Minimal AsyncNetworkStream simulating an HTTP/1.1 + TLS wire socket."""

    def __init__(self, response_bytes: bytes) -> None:
        self._response_bytes = response_bytes
        self._offset = 0
        self.written_bytes = bytearray()
        self.closed = False
        self.tls_calls: list[dict[str, Any]] = []

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del timeout
        if self._offset >= len(self._response_bytes):
            return b""
        chunk = self._response_bytes[self._offset : self._offset + max_bytes]
        self._offset += len(chunk)
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del timeout
        self.written_bytes.extend(buffer)

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.tls_calls.append(
            {
                "ssl_context": ssl_context,
                "server_hostname": server_hostname,
                "timeout": timeout,
            }
        )
        return self

    def get_extra_info(self, info: str) -> Any:
        del info
        return None


class _RecordingInnerBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, response_bytes: bytes) -> None:
        self._response_bytes = response_bytes
        self.tcp_calls: list[tuple[str, int, float | None]] = []
        self.streams: list[_FakeWireStream] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        del local_address, socket_options
        self.tcp_calls.append((host, port, timeout))
        stream = _FakeWireStream(self._response_bytes)
        self.streams.append(stream)
        return stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise AssertionError("Unix domain sockets must never be invoked")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@pytest.mark.anyio
async def test_rest_success_with_none_bearer_and_api_key_auth() -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    tester = RestApiConnectionTester(transport=transport, resolver=_public_resolver)

    res_none = await tester.test(_rest_request(auth_type="none"), timeout_seconds=5.0)
    res_bearer = await tester.test(
        _rest_request(auth_type="bearer", bearer_token="tok_secret_123"),
        timeout_seconds=5.0,
    )
    res_key = await tester.test(
        _rest_request(auth_type="api_key", api_key="key_secret_456"),
        timeout_seconds=5.0,
    )

    assert res_none.success is True and res_none.status == "connected"
    assert res_bearer.success is True and res_bearer.status == "connected"
    assert res_key.success is True and res_key.status == "connected"

    assert captured_requests[0].method == "GET"
    assert "authorization" not in captured_requests[0].headers
    assert "x-api-key" not in captured_requests[0].headers
    assert captured_requests[1].headers["authorization"] == "Bearer tok_secret_123"
    assert captured_requests[2].headers["x-api-key"] == "key_secret_456"


@pytest.mark.anyio
async def test_rest_missing_required_transient_credential_fails_before_request() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    tester = RestApiConnectionTester(
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    )

    res = await tester.test(_rest_request(auth_type="bearer"), timeout_seconds=5.0)
    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
    assert called is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("http_status", "expected_code"),
    [
        (401, "AUTHENTICATION_FAILED"),
        (403, "AUTHENTICATION_FAILED"),
        (404, "INVALID_CONFIGURATION"),
        (429, "INVALID_CONFIGURATION"),
        (500, "SERVER_ERROR"),
        (503, "SERVER_ERROR"),
    ],
)
async def test_rest_http_status_classification(http_status: int, expected_code: str) -> None:
    tester = RestApiConnectionTester(
        transport=httpx.MockTransport(lambda _req: httpx.Response(http_status, text="secret_body")),
        resolver=_public_resolver,
    )

    res = await tester.test(_rest_request(), timeout_seconds=5.0)
    assert res.success is False
    assert res.status == "failed"
    assert res.errorCode == expected_code
    assert "secret_body" not in res.message


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("redirect_location", "expect_prohibited"),
    [
        ("https://other.public.example/v2", False),
        ("http://127.0.0.1/admin", True),
        ("http://localhost:8080/internal", True),
        ("http://10.0.0.5/secret", True),
        ("http://169.254.169.254/latest/meta-data", True),
        ("http://metadata.google.internal/computeMetadata/v1/", True),
        ("file:///etc/passwd", True),
    ],
)
async def test_rest_redirects_never_trigger_secondary_request_or_dns_lookup(
    redirect_location: str,
    expect_prohibited: bool,
) -> None:
    http_calls = 0
    dns_calls = 0

    async def counting_resolver(_host: str, _port: int) -> list[str]:
        nonlocal dns_calls
        dns_calls += 1
        return ["93.184.216.34"]

    def redirect_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(302, headers={"Location": redirect_location})

    tester = RestApiConnectionTester(
        transport=httpx.MockTransport(redirect_handler),
        resolver=counting_resolver,
    )
    res = await tester.test(_rest_request(), timeout_seconds=5.0)

    assert http_calls == 1
    assert dns_calls == 1
    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
    assert redirect_location not in res.message
    if expect_prohibited:
        assert "prohibited" in res.message.lower()
    else:
        assert "redirect" in res.message.lower()


@pytest.mark.anyio
async def test_rest_network_dns_timeout_tls_and_malformed_errors() -> None:
    async def failing_dns(_host: str, _port: int) -> list[str]:
        raise socket.gaierror(8, "nodename nor servname provided")

    dns_tester = RestApiConnectionTester(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
        resolver=failing_dns,
    )
    dns_res = await dns_tester.test(_rest_request(), timeout_seconds=5.0)
    assert dns_res.errorCode == "DNS_RESOLUTION_FAILED"

    def raise_refused(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused") from ConnectionRefusedError(
            61, "Connection refused"
        )

    refused_tester = RestApiConnectionTester(
        transport=httpx.MockTransport(raise_refused),
        resolver=_public_resolver,
    )
    assert (await refused_tester.test(_rest_request(), timeout_seconds=5.0)).errorCode == (
        "CONNECTION_REFUSED"
    )

    def raise_timeout(_req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    timeout_tester = RestApiConnectionTester(
        transport=httpx.MockTransport(raise_timeout),
        resolver=_public_resolver,
    )
    timeout_res = await timeout_tester.test(_rest_request(), timeout_seconds=5.0)
    assert timeout_res.status == "timeout"
    assert timeout_res.errorCode == "TIMEOUT"

    def raise_tls(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("TLS failure") from ssl.SSLCertVerificationError("cert bad")

    tls_tester = RestApiConnectionTester(
        transport=httpx.MockTransport(raise_tls),
        resolver=_public_resolver,
    )
    tls_res = await tls_tester.test(_rest_request(), timeout_seconds=5.0)
    assert tls_res.errorCode == "NETWORK_ERROR"
    assert "tls" in tls_res.message.lower()

    def raise_protocol(_req: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("bad HTTP")

    proto_tester = RestApiConnectionTester(
        transport=httpx.MockTransport(raise_protocol),
        resolver=_public_resolver,
    )
    assert (await proto_tester.test(_rest_request(), timeout_seconds=5.0)).errorCode == (
        "SERVER_ERROR"
    )


@pytest.mark.anyio
async def test_rest_dns_timeout_and_slow_response_timeout() -> None:
    async def hanging_dns(_host: str, _port: int) -> list[str]:
        await asyncio.sleep(1.0)
        return ["93.184.216.34"]

    dns_timeout_tester = RestApiConnectionTester(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
        resolver=hanging_dns,
    )
    dns_timeout_res = await dns_timeout_tester.test(_rest_request(), timeout_seconds=0.1)
    assert dns_timeout_res.success is False
    assert dns_timeout_res.status == "timeout"
    assert dns_timeout_res.errorCode == "TIMEOUT"

    class _TrickleStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            for _ in range(20):
                await asyncio.sleep(0.05)
                yield b"chunk"

    slow_tester = RestApiConnectionTester(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, stream=_TrickleStream())),
        resolver=_public_resolver,
    )
    slow_res = await slow_tester.test(_rest_request(), timeout_seconds=0.1)
    assert slow_res.success is False
    assert slow_res.status == "timeout"
    assert slow_res.errorCode == "TIMEOUT"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "unsafe_url",
    [
        "http://localhost/health",
        "https://sub.localhost/health",
        "http://127.0.0.1:8080/health",
        "http://0.0.0.0:8080/health",
        "http://[::]/health",
        "http://[::1]:8080/health",
        "http://10.0.0.12/internal",
        "http://172.16.5.4/internal",
        "http://192.168.1.1/admin",
        "http://[fc00::1]/internal",
        "http://169.254.169.254/latest/meta-data/",
        "http://[fe80::1]/meta",
        "http://[fe80::1%eth0]/meta",
        "http://[fe80::1%25eth0]/meta",
        "http://[::ffff:127.0.0.1%25eth0]/meta",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://100.100.100.200/latest/meta-data/",
        "http://100.64.0.1/internal",
        "http://224.0.0.1/multicast",
        "http://240.0.0.1/reserved",
        "http://[::ffff:127.0.0.1]/admin",
        "http://[::ffff:169.254.169.254]/latest/meta-data/",
        "http://internal-redis/ping",
        "http://2130706433/admin",
        "http://0x7f000001/admin",
        "http://127.1/admin",
        "http://0177.0.0.1/admin",
        "http://0x7f.0.0.1/admin",
    ],
)
async def test_rest_blocks_ssrf_urls(unsafe_url: str) -> None:
    called = False

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    tester = RestApiConnectionTester(
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    )
    res = await tester.test(_rest_request(base_url=unsafe_url), timeout_seconds=5.0)

    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
    assert called is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "resolved_ip",
    [
        "127.0.0.1",
        "::1",
        "0.0.0.0",  # noqa: S104
        "10.1.2.3",
        "172.20.0.1",
        "192.168.0.5",
        "100.64.1.1",
        "169.254.169.254",
        "fe80::1",
        "fe80::1%eth0",
        "::ffff:127.0.0.1",
        "224.0.0.1",
    ],
)
async def test_rest_blocks_mixed_public_and_prohibited_dns_ips(resolved_ip: str) -> None:
    called = False

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async def malicious_dns(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34", resolved_ip]

    tester = RestApiConnectionTester(
        transport=httpx.MockTransport(handler),
        resolver=malicious_dns,
    )
    res = await tester.test(_rest_request(), timeout_seconds=5.0)

    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
    assert called is False


@pytest.mark.anyio
async def test_ssrf_transport_pins_ip_preserves_host_sni_tls_and_ignores_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9999")

    dns_calls = 0
    dns_answers = iter([["93.184.216.34"], ["127.0.0.1"]])

    async def rebinding_resolver(_host: str, _port: int) -> list[str]:
        nonlocal dns_calls
        dns_calls += 1
        return next(dns_answers)

    raw_http_response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 11\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b'{"ok":true}'
    )
    inner_backend = _RecordingInnerBackend(raw_http_response)
    transport = SsrfSafeHttpTransport(resolver=rebinding_resolver, inner_backend=inner_backend)

    # Confirm SSL verification and hostname checking remain strictly enabled.
    ssl_ctx = transport._pool._ssl_context
    assert ssl_ctx is not None
    assert ssl_ctx.check_hostname is True
    assert ssl_ctx.verify_mode == ssl.CERT_REQUIRED

    # Confirm RestApiConnectionTester executes through SsrfSafeHttpTransport with proxy env ignored.
    async with httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        resp = await client.get("https://api.public-service.example/v2/health")

    assert resp.status_code == 200
    assert dns_calls == 1
    assert inner_backend.tcp_calls == [("93.184.216.34", 443, 5.0)]
    stream = inner_backend.streams[0]
    assert stream.closed is True
    assert len(stream.tls_calls) == 1
    assert stream.tls_calls[0]["server_hostname"] == "api.public-service.example"
    assert b"Host: api.public-service.example\r\n" in stream.written_bytes

    # Second connection attempt hitting the rebound 127.0.0.1 DNS answer is blocked.
    with pytest.raises(SsrfBlockedError):
        await transport._pool._network_backend.connect_tcp("api.public-service.example", 443)
    assert len(inner_backend.tcp_calls) == 1

    # Prove RestApiConnectionTester ignores HTTP_PROXY/HTTPS_PROXY when building its transport.
    res_with_proxy_env = await RestApiConnectionTester(
        resolver=lambda _h, _p: _public_resolver(_h, _p),
    ).test(_rest_request(base_url="https://127.0.0.1/admin"), timeout_seconds=5.0)
    assert res_with_proxy_env.success is False
    assert res_with_proxy_env.errorCode == "INVALID_CONFIGURATION"


@pytest.mark.anyio
async def test_ssrf_network_backend_blocks_unix_sockets_and_rebinding() -> None:
    connected_targets: list[tuple[str, int]] = []

    class _FakeInnerBackend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: Any = None,
        ) -> Any:
            del timeout, local_address, socket_options
            connected_targets.append((host, port))
            return object()

        async def connect_unix_socket(
            self,
            path: str,
            timeout: float | None = None,
            socket_options: Any = None,
        ) -> Any:
            del path, timeout, socket_options
            raise AssertionError("Should not reach inner unix socket")

        async def sleep(self, seconds: float) -> None:
            del seconds
            return None

    responses = iter([["93.184.216.34"], ["127.0.0.1"]])

    async def rebinding_resolver(_host: str, _port: int) -> list[str]:
        return next(responses)

    backend = SsrfSafeNetworkBackend(inner=_FakeInnerBackend(), resolver=rebinding_resolver)
    await backend.connect_tcp("rebind.example.com", 443)
    assert connected_targets == [("93.184.216.34", 443)]

    with pytest.raises(SsrfBlockedError):
        await backend.connect_tcp("rebind.example.com", 443)

    with pytest.raises(SsrfBlockedError):
        await backend.connect_unix_socket("/var/run/docker.sock")


@pytest.mark.anyio
async def test_rest_bounded_response_consumption_closes_stream_and_never_logs_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    chunks_yielded = 0
    stream_closed = False

    class _LargeByteStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            nonlocal chunks_yielded
            for _ in range(20):
                chunks_yielded += 1
                yield b"secret_payload_chunk_" * 200  # ~4200 bytes per chunk

        async def aclose(self) -> None:
            nonlocal stream_closed
            stream_closed = True

    def large_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_LargeByteStream())

    tester = RestApiConnectionTester(
        transport=httpx.MockTransport(large_handler),
        resolver=_public_resolver,
        max_response_bytes=8192,
    )

    with caplog.at_level(logging.DEBUG):
        res = await tester.test(
            _rest_request(auth_type="bearer", bearer_token="super_secret_rest_token"),
            timeout_seconds=5.0,
        )
        key_res = await tester.test(
            _rest_request(
                base_url="https://api.public-service.example/fail",
                auth_type="api_key",
                api_key="super_secret_api_key_999",
            ),
            timeout_seconds=5.0,
        )

    assert res.success is True
    assert key_res.success is True
    assert chunks_yielded < 20
    assert stream_closed is True
    assert "super_secret_rest_token" not in caplog.text
    assert "super_secret_api_key_999" not in caplog.text
    assert "secret_payload_chunk" not in caplog.text


@pytest.mark.anyio
async def test_rest_single_dns_lookup_on_production_path(monkeypatch: pytest.MonkeyPatch) -> None:
    dns_calls = 0

    async def counting_resolver(_host: str, _port: int) -> list[str]:
        nonlocal dns_calls
        dns_calls += 1
        return ["127.0.0.1"]

    # Ensure RestApiConnectionTester (transport=None) resolves only once via SsrfSafeHttpTransport.
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    tester = RestApiConnectionTester(resolver=counting_resolver)
    res = await tester.test(_rest_request(), timeout_seconds=5.0)

    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
    assert dns_calls == 1


@pytest.mark.anyio
async def test_rest_rejects_mismatched_request_type() -> None:
    tester = RestApiConnectionTester()
    csv_req = CsvConnectionTestRequest(
        connectorType="CSV",
        config=CsvConnectionTestConfig(fileName="orders.csv"),
        fileContent="id\n1\n",
    )
    res = await tester.test(csv_req, timeout_seconds=5.0)
    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
