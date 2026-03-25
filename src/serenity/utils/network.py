"""Network and SSL inspection utilities."""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from serenity.constants import SECURITY_HEADERS


# ---------------------------------------------------------------------------
# SSL certificate inspection
# ---------------------------------------------------------------------------

async def check_ssl_certificate(hostname: str, port: int = 443) -> dict[str, Any]:
    """Connect to *hostname* over TLS and inspect the certificate.

    The blocking ``ssl.SSLSocket`` handshake is offloaded to a thread via
    :func:`asyncio.to_thread` so the event loop stays responsive.

    Args:
        hostname: Domain name to connect to (e.g. ``"example.com"``).
        port: TCP port (default ``443``).

    Returns:
        A dict with keys: *valid*, *issuer*, *subject*, *not_before*,
        *not_after*, *days_remaining*, *cipher_strength*.
    """

    def _inspect() -> dict[str, Any]:
        ctx = ssl.create_default_context()
        result: dict[str, Any] = {
            "valid": False,
            "issuer": "",
            "subject": "",
            "not_before": "",
            "not_after": "",
            "days_remaining": 0,
            "cipher_strength": 0,
        }
        try:
            with socket.create_connection((hostname, port), timeout=10) as sock:
                with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert = ssock.getpeercert()
                    if not cert:
                        return result

                    # Parse issuer / subject into readable strings.
                    issuer_parts = []
                    for rdn in cert.get("issuer", ()):
                        for attr_name, attr_value in rdn:
                            issuer_parts.append(f"{attr_name}={attr_value}")
                    result["issuer"] = ", ".join(issuer_parts)

                    subject_parts = []
                    for rdn in cert.get("subject", ()):
                        for attr_name, attr_value in rdn:
                            subject_parts.append(f"{attr_name}={attr_value}")
                    result["subject"] = ", ".join(subject_parts)

                    not_before_str = cert.get("notBefore", "")
                    not_after_str = cert.get("notAfter", "")
                    result["not_before"] = not_before_str
                    result["not_after"] = not_after_str

                    # Calculate days remaining.
                    if not_after_str:
                        # Python's ssl module returns dates like
                        # "Sep 15 12:00:00 2025 GMT"
                        not_after_dt = datetime.strptime(
                            not_after_str, "%b %d %H:%M:%S %Y %Z"
                        ).replace(tzinfo=timezone.utc)
                        delta = not_after_dt - datetime.now(timezone.utc)
                        result["days_remaining"] = max(delta.days, 0)

                    # Cipher strength (key bit length).
                    cipher_info = ssock.cipher()
                    if cipher_info:
                        # cipher() returns (name, protocol, bits)
                        result["cipher_strength"] = cipher_info[2]

                    result["valid"] = True
        except ssl.SSLCertVerificationError:
            result["valid"] = False
        except (OSError, socket.timeout):
            result["valid"] = False

        return result

    return await asyncio.to_thread(_inspect)


# ---------------------------------------------------------------------------
# HTTPS redirect check
# ---------------------------------------------------------------------------

async def check_https_redirect(
    url: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Check whether an HTTP URL redirects to its HTTPS equivalent.

    Args:
        url: An ``http://`` URL to probe.
        client: A shared :class:`httpx.AsyncClient` (caller manages lifetime).

    Returns:
        A dict with keys: *redirects* (bool), *redirect_url*, *status_code*.
    """
    result: dict[str, Any] = {
        "redirects": False,
        "redirect_url": "",
        "status_code": 0,
    }

    # Ensure we are testing the HTTP version.
    if url.startswith("https://"):
        url = url.replace("https://", "http://", 1)

    try:
        resp = await client.get(url, follow_redirects=False, timeout=10)
        result["status_code"] = resp.status_code

        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            result["redirect_url"] = location
            result["redirects"] = location.lower().startswith("https://")
    except httpx.HTTPError:
        pass

    return result


# ---------------------------------------------------------------------------
# URL status / performance probe
# ---------------------------------------------------------------------------

async def check_url_status(
    url: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Perform a GET request and return basic response metadata.

    Args:
        url: Absolute URL to request.
        client: A shared :class:`httpx.AsyncClient`.

    Returns:
        A dict with keys: *status_code*, *ttfb_ms*, *content_type*,
        *content_length*.
    """
    result: dict[str, Any] = {
        "status_code": 0,
        "ttfb_ms": 0.0,
        "content_type": "",
        "content_length": 0,
    }
    try:
        start = time.monotonic()
        resp = await client.get(url, follow_redirects=True, timeout=15)
        ttfb = (time.monotonic() - start) * 1000

        result["status_code"] = resp.status_code
        result["ttfb_ms"] = round(ttfb, 2)
        result["content_type"] = resp.headers.get("content-type", "")
        result["content_length"] = int(resp.headers.get("content-length", 0))
    except httpx.HTTPError:
        pass

    return result


# ---------------------------------------------------------------------------
# Security header analysis
# ---------------------------------------------------------------------------

def parse_security_headers(headers: dict[str, str]) -> dict[str, Any]:
    """Evaluate the presence and basic validity of common security headers.

    Args:
        headers: A case-insensitive mapping of HTTP response headers.  When
            passing :class:`httpx.Headers` this works directly; for plain dicts
            the lookup is normalized to lower-case.

    Returns:
        A dict keyed by header name, each value being a dict with *present*
        (bool), *value* (str), and *valid* (bool — simple heuristic check).
    """
    # Normalize to lower-case keys for plain dicts.
    lower_headers = {k.lower(): v for k, v in headers.items()}

    results: dict[str, Any] = {}
    for header in SECURITY_HEADERS:
        value = lower_headers.get(header, "")
        present = bool(value)

        # Simple validity heuristics per header.
        valid = False
        if present:
            if header == "strict-transport-security":
                valid = "max-age=" in value.lower()
            elif header == "x-content-type-options":
                valid = value.lower().strip() == "nosniff"
            elif header == "x-frame-options":
                valid = value.upper().strip() in ("DENY", "SAMEORIGIN")
            elif header == "content-security-policy":
                # Must contain at least a default-src or script-src directive.
                valid = "default-src" in value or "script-src" in value
            elif header == "referrer-policy":
                allowed = {
                    "no-referrer",
                    "no-referrer-when-downgrade",
                    "origin",
                    "origin-when-cross-origin",
                    "same-origin",
                    "strict-origin",
                    "strict-origin-when-cross-origin",
                    "unsafe-url",
                }
                valid = value.lower().strip() in allowed
            elif header == "permissions-policy":
                # Just check that it isn't empty.
                valid = len(value.strip()) > 0
            else:
                valid = present

        results[header] = {
            "present": present,
            "value": value,
            "valid": valid,
        }

    return results
