"""URL normalization and sanitization utilities."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse


def normalize_url(url: str, base_url: str | None = None) -> str:
    """Normalize a URL by removing fragments, trailing slashes, and lower-casing
    the scheme and domain.

    If *url* is relative and *base_url* is provided, the URL is resolved against
    the base first.

    Args:
        url: The URL to normalize (absolute or relative).
        base_url: Optional base URL for resolving relative references.

    Returns:
        The normalized, absolute URL string.
    """
    url = url.strip()

    # Resolve relative URLs when a base is available.
    if base_url and not urlparse(url).scheme:
        url = urljoin(base_url, url)

    # Strip the fragment component.
    url, _ = urldefrag(url)

    parsed = urlparse(url)

    # Lower-case scheme and network location (domain + optional port).
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Remove default ports.
    netloc = re.sub(r":80$", "", netloc) if scheme == "http" else netloc
    netloc = re.sub(r":443$", "", netloc) if scheme == "https" else netloc

    # Strip a single trailing slash from the path, but keep "/" for root.
    path = parsed.path.rstrip("/") or "/"

    normalized = urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))
    return normalized


def is_same_domain(url: str, base_url: str) -> bool:
    """Check whether *url* belongs to the same domain as *base_url*.

    Comparison is done on the registered domain (hostname without port), so
    ``https://example.com:8080/page`` is considered same-domain as
    ``https://example.com/other``.

    Args:
        url: The URL to test.
        base_url: The reference URL whose domain is authoritative.

    Returns:
        ``True`` if both URLs share the same hostname.
    """
    try:
        url_host = urlparse(url).hostname or ""
        base_host = urlparse(base_url).hostname or ""
        return url_host.lower() == base_host.lower()
    except Exception:
        return False


def extract_domain(url: str) -> str:
    """Extract the domain (hostname) from a URL.

    Args:
        url: Any absolute URL.

    Returns:
        The hostname string, or an empty string on failure.
    """
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def safe_filename(url: str, max_length: int = 120) -> str:
    """Convert a URL into a filesystem-safe filename.

    The result contains only alphanumeric characters, hyphens, and underscores.
    A short hash suffix is appended to avoid collisions when long URLs are
    truncated.

    Args:
        url: The URL to convert.
        max_length: Maximum length of the returned filename (excluding the
            hash suffix).

    Returns:
        A safe filename string (without extension).
    """
    # Remove scheme.
    name = re.sub(r"^https?://", "", url)

    # Replace non-alphanumeric characters with underscores.
    name = re.sub(r"[^a-zA-Z0-9]", "_", name)

    # Collapse consecutive underscores.
    name = re.sub(r"_+", "_", name).strip("_")

    # Truncate and append a short hash for uniqueness.
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:8]

    if len(name) > max_length:
        name = name[:max_length]

    return f"{name}_{url_hash}" if name else url_hash
