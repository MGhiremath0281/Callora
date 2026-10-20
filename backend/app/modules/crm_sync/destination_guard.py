"""SSRF destination checks for outbound CRM requests.

The request schema performs static checks (scheme, credentials, literal IPs,
blocked hostnames). This module adds the checks that need DNS resolution and
runs them again immediately before every outgoing request, so a destination
cannot become internal between configuration and use. The HTTP client never
follows redirects.

The only exception to the non-public destination rule is an explicit,
development-only allowlist of ``host:port`` entries
(``CALLORA_CRM_DEV_ALLOWED_DESTINATIONS``). It is empty by default, so
production rejects private, loopback, and link-local destinations — including
``host.docker.internal`` — unless an operator opts in for a specific local
fake CRM.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import TYPE_CHECKING, List, Optional, Set, Tuple
from urllib.parse import urlparse

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.config import Settings

# Hostnames that always denote the local machine / deployment internals.
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "0.0.0.0",
        "::1",
        "ip6-localhost",
        "host.docker.internal",
        "metadata.google.internal",
    }
)

_DEFAULT_PORTS = {"http": 80, "https": 443}


class DestinationNotAllowedError(Exception):
    """Raised when an outgoing CRM destination fails the SSRF guard."""


def parse_allowed_destinations(raw: str) -> Set[Tuple[str, int]]:
    """Parse ``host:port,host:port`` into an explicit allowlist.

    Malformed entries are ignored (fail closed): a typo can never widen
    access to a private destination.
    """
    allowed: Set[Tuple[str, int]] = set()
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        host, separator, port_text = entry.rpartition(":")
        if not separator or not host or not port_text.isdigit():
            continue
        port = int(port_text)
        if not 0 < port < 65536:
            continue
        allowed.add((host.strip("[]").lower(), port))
    return allowed


def host_port_from_url(url: str) -> Optional[Tuple[str, int]]:
    """Return the (host, port) pair of an http(s) URL, or None if invalid."""
    parsed = urlparse(url)
    if parsed.scheme not in _DEFAULT_PORTS:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = _DEFAULT_PORTS[parsed.scheme]
    return host, port


def is_dev_allowed_destination(host: str, port: int, settings: "Settings") -> bool:
    """True only for an explicitly configured development destination."""
    allowed = parse_allowed_destinations(settings.crm_dev_allowed_destinations)
    return (host.lower(), port) in allowed


def is_forbidden_ip(ip: ipaddress._BaseAddress) -> bool:
    """Reject loopback, private, link-local, metadata, and similar ranges."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and is_forbidden_ip(mapped):
        return True
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_private
        or ip.is_reserved
    )


async def resolve_host_ips(host: str, port: int) -> List[ipaddress._BaseAddress]:
    """Resolve a hostname to unique IP addresses (async, non-blocking)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: List[ipaddress._BaseAddress] = []
    seen = set()
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:  # pragma: no cover - defensive
            continue
        if ip not in seen:
            seen.add(ip)
            addresses.append(ip)
    return addresses


async def ensure_destination_allowed(base_url: str, settings: "Settings") -> None:
    """Validate the destination address immediately before a request.

    Raises :class:`DestinationNotAllowedError` for forbidden destinations.
    DNS failures propagate as :class:`OSError` and are treated by the caller
    as an unreachable CRM, not as an allowed destination.
    """
    parsed = urlparse(base_url)
    if parsed.scheme not in _DEFAULT_PORTS or not parsed.netloc:
        raise DestinationNotAllowedError(
            "CRM base URL must be an absolute http(s) URL."
        )
    if parsed.username or parsed.password:
        raise DestinationNotAllowedError(
            "CRM base URL must not contain embedded credentials."
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise DestinationNotAllowedError("CRM base URL must include a hostname.")
    pair = host_port_from_url(base_url)
    if pair is None:
        raise DestinationNotAllowedError("CRM base URL has an invalid port.")
    _, port = pair

    # Pre-existing, explicitly documented development escape hatch: permits
    # any destination (including private ones). Unset in production.
    if settings.crm_allow_private_urls:
        return

    # Narrow explicit allowlist for a local fake CRM, e.g.
    # host.docker.internal:9000. Exact host AND port must match.
    if is_dev_allowed_destination(host, port, settings):
        return

    if host in BLOCKED_HOSTNAMES or host.endswith(".internal"):
        raise DestinationNotAllowedError(
            f"CRM destination host '{host}' is not an allowed external "
            "destination."
        )

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if is_forbidden_ip(literal):
            raise DestinationNotAllowedError(
                f"CRM destination IP '{host}' is not an allowed external "
                "destination."
            )
        return

    # DNS name: enforce the same policy on every address it resolves to,
    # which also defeats a hostname that points at an internal address.
    addresses = await resolve_host_ips(host, port)
    if not addresses:
        raise DestinationNotAllowedError(
            f"CRM destination host '{host}' did not resolve to any address."
        )
    for ip in addresses:
        if is_forbidden_ip(ip):
            raise DestinationNotAllowedError(
                f"CRM destination host '{host}' resolves to non-public "
                f"address {ip}; only explicitly allowlisted development "
                "destinations may use private, loopback, or link-local "
                "addresses."
            )
