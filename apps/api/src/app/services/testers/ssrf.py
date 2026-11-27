"""SSRF protection, hostname/IP validation, and DNS-rebinding-safe HTTP transport."""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

AsyncHostResolver = Callable[[str, int], Awaitable[list[str]]]

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "169.254.169.254",
        "100.100.100.200",
        "fd00:ec2::254",
    }
)

_BLOCKED_HOSTNAME_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".localdomain",
    ".intranet",
    ".corp",
    ".home",
    ".lan",
)

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_NUMERIC_OR_HEX_LABEL_RE = re.compile(r"^(?:0x[0-9a-f]+|0[0-7]*|[1-9]\d*)$", re.IGNORECASE)


class SsrfBlockedError(ValueError):
    """Raised when a URL, hostname, or resolved IP violates SSRF policy."""


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def validate_ip_address(raw_ip: str) -> str:
    """Validate that an IP address is strictly global/public and safe to connect to."""
    if "%" in raw_ip:
        raise SsrfBlockedError("Link-local IPv6 zone identifiers are not permitted.")

    try:
        ip = ipaddress.ip_address(raw_ip)
    except ValueError as exc:
        raise SsrfBlockedError("Resolved address is not a valid IP address.") from exc

    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        validate_ip_address(str(ip.ipv4_mapped))

    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NETWORK)
        or not ip.is_global
    ):
        raise SsrfBlockedError("Target resolves to a prohibited non-public IP address.")

    return str(ip)


def validate_hostname_policy(hostname: str) -> str:
    """Validate target hostname against SSRF blocklists and structural rules."""
    normalized = hostname.strip().rstrip(".").lower()
    if (
        not normalized
        or any(ch.isspace() for ch in normalized)
        or "@" in normalized
        or "%" in normalized
    ):
        raise SsrfBlockedError("Target hostname is invalid.")

    if normalized in _BLOCKED_HOSTNAMES or any(
        normalized.endswith(suffix) for suffix in _BLOCKED_HOSTNAME_SUFFIXES
    ):
        raise SsrfBlockedError("Target hostname is not permitted.")

    if _is_ip_literal(normalized):
        validate_ip_address(normalized)
        return normalized

    # Non-IP-literal hostnames must not contain colons (e.g., malformed/scoped IPv6).
    if ":" in normalized:
        raise SsrfBlockedError("Target hostname is invalid.")

    labels = normalized.split(".")
    # Reject single-label hostnames and obfuscated numeric/octal/hex IPv4 forms (e.g., 127.1).
    if (
        len(labels) < 2
        or any(not label for label in labels)
        or all(_NUMERIC_OR_HEX_LABEL_RE.match(label) for label in labels)
    ):
        raise SsrfBlockedError("Target hostname must be a public FQDN or public IP address.")

    return normalized


def validate_http_target_url(raw_url: str) -> tuple[str, int]:
    """Validate HTTP(S) target URL structure and hostname policy, returning `(hostname, port)`."""
    parsed = urlsplit(raw_url.strip())
    if parsed.scheme not in ("http", "https"):
        raise SsrfBlockedError("Only http:// and https:// URLs are permitted.")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise SsrfBlockedError("Target URL must not contain embedded credentials.")
    if not parsed.hostname:
        raise SsrfBlockedError("Target URL must include a valid hostname.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise SsrfBlockedError("Target URL contains an invalid port.") from exc

    resolved_port = port if port is not None else (443 if parsed.scheme == "https" else 80)
    if resolved_port < 1 or resolved_port > 65535:
        raise SsrfBlockedError("Target URL port is out of valid range.")

    safe_host = validate_hostname_policy(parsed.hostname)
    return safe_host, resolved_port


async def default_async_host_resolver(hostname: str, port: int) -> list[str]:
    """Resolve `hostname` via asyncio loop getaddrinfo without blocking the event loop."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for _family, _type, _proto, _canonname, sockaddr in infos:
        if sockaddr and isinstance(sockaddr[0], str):
            addresses.append(sockaddr[0])
    return addresses


async def resolve_and_validate_host(
    hostname: str,
    port: int,
    *,
    resolver: AsyncHostResolver | None = None,
    timeout: float | None = None,
) -> str:
    """Resolve hostname once, validate every returned IP, and return a pinned public IP."""
    normalized_host = validate_hostname_policy(hostname)
    if _is_ip_literal(normalized_host):
        return validate_ip_address(normalized_host)

    resolve_fn = resolver or default_async_host_resolver
    coro = resolve_fn(normalized_host, port)
    raw_ips = await (asyncio.wait_for(coro, timeout=timeout) if timeout is not None else coro)
    if not raw_ips:
        raise socket.gaierror(socket.EAI_NONAME, "Hostname did not resolve to any address")

    validated_ips = [validate_ip_address(ip_str) for ip_str in raw_ips]
    return validated_ips[0]


class SsrfSafeNetworkBackend(httpcore.AsyncNetworkBackend):
    """Network backend that pins TCP connections to validated public IPs to prevent rebinding."""

    def __init__(
        self,
        *,
        inner: httpcore.AsyncNetworkBackend,
        resolver: AsyncHostResolver | None = None,
    ) -> None:
        self._inner = inner
        self._resolver = resolver

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        pinned_ip = await resolve_and_validate_host(
            host,
            port,
            resolver=self._resolver,
            timeout=timeout,
        )
        return await self._inner.connect_tcp(
            pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        raise SsrfBlockedError("Unix domain sockets are disabled.")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class SsrfSafeHttpTransport(httpx.AsyncHTTPTransport):
    """HTTP transport with proxy env disabled, no connection keepalive reuse, and SSRF pinning."""

    def __init__(
        self,
        *,
        resolver: AsyncHostResolver | None = None,
        inner_backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        super().__init__(
            verify=True,
            trust_env=False,
            http1=True,
            http2=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            retries=0,
        )
        ssl_context = httpx.create_ssl_context(verify=True, trust_env=False)
        resolved_inner = inner_backend or self._pool._network_backend
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=1,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            retries=0,
            network_backend=SsrfSafeNetworkBackend(
                inner=resolved_inner,
                resolver=resolver,
            ),
        )
