"""REST API HTTP connectivity tester with strict SSRF and resource protections."""

import asyncio
import socket
import ssl
import time
from urllib.parse import urljoin

import httpx

from app.schemas.connection_test import (
    ConnectionTestErrorCode,
    ConnectionTestRequest,
    ConnectionTestResult,
    ConnectorType,
    RestApiConnectionTestRequest,
)
from app.services.testers.common import (
    build_success_result,
    build_tester_failure,
    elapsed_ms,
    iter_exception_chain,
)
from app.services.testers.ssrf import (
    AsyncHostResolver,
    SsrfBlockedError,
    SsrfSafeHttpTransport,
    resolve_and_validate_host,
    validate_http_target_url,
)

DEFAULT_MAX_RESPONSE_BYTES = 65_536
_STREAM_CHUNK_SIZE = 4096


def _build_auth_headers(request: RestApiConnectionTestRequest) -> dict[str, str]:
    headers: dict[str, str] = {
        "Accept": "application/json, */*;q=0.8",
        "User-Agent": "DataBridge-ConnectionTest/1.0",
    }

    auth_type = request.config.authType
    if auth_type == "none":
        return headers

    if auth_type == "bearer":
        token = (
            request.credentials.bearerToken.get_secret_value().strip()
            if request.credentials and request.credentials.bearerToken is not None
            else ""
        )
        if not token:
            raise ValueError("Bearer token is required for Bearer Token authentication.")
        headers["Authorization"] = f"Bearer {token}"
        return headers

    if auth_type == "api_key":
        api_key = (
            request.credentials.apiKey.get_secret_value().strip()
            if request.credentials and request.credentials.apiKey is not None
            else ""
        )
        if not api_key:
            raise ValueError("API key is required for API Key authentication.")
        headers["X-API-Key"] = api_key
        return headers

    raise ValueError("Unsupported REST API authentication type.")


def _is_redirect_target_safe(
    location: str | None,
    *,
    base_url: str,
) -> bool:
    """Validate redirect Location syntactically without triggering a DNS lookup or HTTP request."""
    if not location or not location.strip():
        return True
    resolved_url = urljoin(base_url, location.strip())
    try:
        validate_http_target_url(resolved_url)
        return True
    except (SsrfBlockedError, ValueError):
        return False


def _classify_http_exception(exc: BaseException) -> tuple[ConnectionTestErrorCode, str, bool]:
    for cause in iter_exception_chain(exc):
        if isinstance(cause, SsrfBlockedError):
            return (
                "INVALID_CONFIGURATION",
                "Target URL or resolved network address is not permitted.",
                False,
            )
        if isinstance(cause, (httpx.TimeoutException, TimeoutError)):
            return (
                "TIMEOUT",
                "REST API connection test timed out before the server responded.",
                True,
            )
        if isinstance(cause, ssl.SSLError):
            return (
                "NETWORK_ERROR",
                "TLS/SSL verification failed while connecting to the REST API endpoint.",
                False,
            )
        if isinstance(cause, ConnectionRefusedError):
            return (
                "CONNECTION_REFUSED",
                "Unable to connect to the REST API endpoint. Connection was refused.",
                False,
            )
        if isinstance(cause, socket.gaierror):
            return (
                "DNS_RESOLUTION_FAILED",
                "Unable to resolve the REST API hostname.",
                False,
            )

    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        detail = str(exc).lower()
        if any(token in detail for token in ("ssl", "tls", "certificate")):
            return (
                "NETWORK_ERROR",
                "TLS/SSL verification failed while connecting to the REST API endpoint.",
                False,
            )
        if "connection refused" in detail:
            return (
                "CONNECTION_REFUSED",
                "Unable to connect to the REST API endpoint. Connection was refused.",
                False,
            )
        if any(
            token in detail
            for token in (
                "nodename nor servname",
                "name or service not known",
                "temporary failure in name resolution",
            )
        ):
            return (
                "DNS_RESOLUTION_FAILED",
                "Unable to resolve the REST API hostname.",
                False,
            )
        return (
            "NETWORK_ERROR",
            "Unable to reach the REST API endpoint over the network.",
            False,
        )

    if isinstance(exc, (httpx.ProtocolError, httpx.DecodingError, httpx.HTTPError)):
        return (
            "SERVER_ERROR",
            "REST API endpoint returned a malformed or invalid HTTP response.",
            False,
        )

    return (
        "SERVER_ERROR",
        "An unexpected error occurred while testing the REST API connection.",
        False,
    )


class RestApiConnectionTester:
    """Performs a bounded, SSRF-protected GET check against a REST API baseUrl."""

    connector_type: ConnectorType = "REST API"

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: AsyncHostResolver | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._transport = transport
        self._resolver = resolver
        self._max_response_bytes = max_response_bytes

    async def test(
        self,
        request: ConnectionTestRequest,
        timeout_seconds: float,
    ) -> ConnectionTestResult:
        started = time.monotonic()
        if not isinstance(request, RestApiConnectionTestRequest):
            return build_tester_failure(
                message="Invalid request type dispatched to REST API tester.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )

        try:
            headers = _build_auth_headers(request)
        except ValueError as exc:
            return build_tester_failure(
                message=str(exc),
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )

        effective_timeout = max(0.1, float(timeout_seconds))

        try:
            hostname, port = validate_http_target_url(request.config.baseUrl)
            if self._transport is not None:
                # When an external mock transport is injected, still enforce DNS validation once.
                await resolve_and_validate_host(
                    hostname,
                    port,
                    resolver=self._resolver,
                    timeout=effective_timeout,
                )
        except SsrfBlockedError:
            return build_tester_failure(
                message="Target URL or resolved network address is not permitted.",
                error_code="INVALID_CONFIGURATION",
                duration_ms=elapsed_ms(started),
            )
        except TimeoutError:
            return build_tester_failure(
                message="REST API connection test timed out while resolving hostname.",
                error_code="TIMEOUT",
                duration_ms=elapsed_ms(started),
                is_timeout=True,
            )
        except socket.gaierror:
            return build_tester_failure(
                message="Unable to resolve the REST API hostname.",
                error_code="DNS_RESOLUTION_FAILED",
                duration_ms=elapsed_ms(started),
            )

        transport = self._transport or SsrfSafeHttpTransport(resolver=self._resolver)
        timeout_config = httpx.Timeout(effective_timeout)
        chunk_size = min(_STREAM_CHUNK_SIZE, max(1, self._max_response_bytes))

        try:
            async with asyncio.timeout(effective_timeout):
                async with httpx.AsyncClient(
                    transport=transport,
                    timeout=timeout_config,
                    follow_redirects=False,
                    trust_env=False,
                    max_redirects=0,
                ) as client:
                    async with client.stream(
                        "GET",
                        request.config.baseUrl,
                        headers=headers,
                    ) as response:
                        status_code = response.status_code

                        if 200 <= status_code < 300:
                            # Consume only a bounded prefix to confirm readability.
                            bytes_read = 0
                            async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                                bytes_read += len(chunk)
                                if bytes_read >= self._max_response_bytes:
                                    break
                            return build_success_result(
                                f"REST API endpoint responded with HTTP {status_code}.",
                                elapsed_ms(started),
                            )

                        if 300 <= status_code < 400:
                            redirect_safe = _is_redirect_target_safe(
                                response.headers.get("location"),
                                base_url=request.config.baseUrl,
                            )
                            if not redirect_safe:
                                return build_tester_failure(
                                    message=(
                                        "REST API endpoint attempted to redirect to a prohibited "
                                        "destination."
                                    ),
                                    error_code="INVALID_CONFIGURATION",
                                    duration_ms=elapsed_ms(started),
                                )
                            return build_tester_failure(
                                message=(
                                    f"REST API endpoint returned HTTP {status_code} redirect; "
                                    "automatic redirects are disabled."
                                ),
                                error_code="INVALID_CONFIGURATION",
                                duration_ms=elapsed_ms(started),
                            )

                        if status_code == 401:
                            return build_tester_failure(
                                message="REST API authentication failed (HTTP 401).",
                                error_code="AUTHENTICATION_FAILED",
                                duration_ms=elapsed_ms(started),
                            )

                        if status_code == 403:
                            return build_tester_failure(
                                message="REST API authorization was rejected (HTTP 403).",
                                error_code="AUTHENTICATION_FAILED",
                                duration_ms=elapsed_ms(started),
                            )

                        if 400 <= status_code < 500:
                            return build_tester_failure(
                                message=(
                                    "REST API endpoint was reached but returned "
                                    f"HTTP {status_code}."
                                ),
                                error_code="INVALID_CONFIGURATION",
                                duration_ms=elapsed_ms(started),
                            )

                        if 500 <= status_code < 600:
                            return build_tester_failure(
                                message=(
                                    "REST API endpoint was reached but returned server error "
                                    f"HTTP {status_code}."
                                ),
                                error_code="SERVER_ERROR",
                                duration_ms=elapsed_ms(started),
                            )

                        return build_tester_failure(
                            message=(
                                f"REST API endpoint returned unexpected HTTP status {status_code}."
                            ),
                            error_code="SERVER_ERROR",
                            duration_ms=elapsed_ms(started),
                        )
        except Exception as exc:
            error_code, safe_message, is_timeout = _classify_http_exception(exc)
            return build_tester_failure(
                message=safe_message,
                error_code=error_code,
                duration_ms=elapsed_ms(started),
                is_timeout=is_timeout,
            )
