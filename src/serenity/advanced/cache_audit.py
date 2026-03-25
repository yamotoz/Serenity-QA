"""Cache audit — compare cold vs warm loads, check caching headers, detect PWA."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from serenity.constants import Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page, Request, Response

    from serenity.core.state import ScanContext

logger = logging.getLogger("serenity.advanced.cache_audit")

_MAX_SAMPLE_PAGES = 3
_MIN_CACHE_MAX_AGE = 3600  # 1 hour — minimum for static assets.

# Regex to detect content-hash in filenames (e.g. admin.DEd9KrAh.css, index.CfVawArw.js)
# These files use content-addressable URLs — when content changes, the hash changes,
# so max-age=0 with ETag/must-revalidate is a valid caching strategy.
_CONTENT_HASH_PATTERN = re.compile(r'\.[A-Za-z0-9_-]{6,12}\.(js|css|mjs)$')

# File extensions considered "static" and expected to have caching headers.
_STATIC_EXTENSIONS = frozenset({
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".ico", ".map",
})


class CacheAuditor:
    """Audit browser caching effectiveness by comparing cold and warm loads."""

    async def run(self, ctx: ScanContext) -> list[Finding]:
        logger.info("Starting cache audit")
        findings: list[Finding] = []

        urls = _sample_urls(ctx)
        if not urls:
            logger.info("No URLs available for cache audit")
            return findings

        try:
            for url in urls:
                url_findings = await self._audit_url(ctx, url)
                findings.extend(url_findings)
        except Exception:
            logger.exception("Cache audit failed")

        logger.info("Cache audit complete: %d findings", len(findings))
        return findings

    async def _audit_url(self, ctx: ScanContext, url: str) -> list[Finding]:
        """Run cold and warm load comparisons for a single URL."""
        findings: list[Finding] = []

        # We need a fresh context for cold load (no pre-existing cache).
        cold_ctx: BrowserContext | None = None
        warm_ctx: BrowserContext | None = None
        try:
            cold_ctx = await ctx.page_pool.new_context(
                viewport={"width": 1280, "height": 900},
                ignore_https_errors=True,
            )
            cold_page = await cold_ctx.new_page()

            # --- Cold Load ---
            cold_data = await self._timed_load(cold_page, url)
            if not cold_data:
                return findings

            # --- Warm Load (same context = has cache) ---
            warm_data = await self._timed_load(cold_page, url)
            if not warm_data:
                return findings

            # --- Compare cold vs warm ---
            findings.extend(self._compare_loads(cold_data, warm_data, url))

            # --- Check individual resource caching headers ---
            findings.extend(self._check_cache_headers(cold_data, url))

            # --- Check for service worker (PWA) ---
            findings.extend(await self._check_service_worker(cold_page, url))

        except Exception:
            logger.debug("Cache audit error for %s", url, exc_info=True)
        finally:
            if cold_ctx:
                try:
                    await cold_ctx.close()
                except Exception:
                    pass

        return findings

    async def _timed_load(
        self, page: Page, url: str
    ) -> dict[str, Any] | None:
        """Load a page and capture timing + network metrics."""
        requests_log: list[dict[str, Any]] = []

        async def on_response(response: Response) -> None:
            try:
                request = response.request
                size = 0
                try:
                    sizes = await response.request.sizes()
                    size = sizes.get("responseBodySize", 0)
                except Exception:
                    pass

                parsed = urlparse(request.url)
                ext = _get_extension(parsed.path)

                requests_log.append({
                    "url": request.url,
                    "status": response.status,
                    "resource_type": request.resource_type,
                    "extension": ext,
                    "size": size,
                    "cache_control": response.headers.get("cache-control", ""),
                    "etag": response.headers.get("etag", ""),
                    "last_modified": response.headers.get("last-modified", ""),
                    "expires": response.headers.get("expires", ""),
                    "from_cache": response.request.resource_type == "document"
                                  and response.status == 304,
                    "is_static": ext in _STATIC_EXTENSIONS,
                })
            except Exception:
                pass

        page.on("response", on_response)

        start = time.perf_counter()
        try:
            await page.goto(url, wait_until="networkidle", timeout=25_000)
            await asyncio.sleep(0.5)
        except Exception:
            logger.debug("Timed load failed for %s", url)
            page.remove_listener("response", on_response)
            return None
        finally:
            page.remove_listener("response", on_response)

        elapsed_ms = (time.perf_counter() - start) * 1000
        total_size = sum(r.get("size", 0) for r in requests_log)

        return {
            "url": url,
            "request_count": len(requests_log),
            "total_size": total_size,
            "load_time_ms": elapsed_ms,
            "requests": requests_log,
        }

    def _compare_loads(
        self,
        cold: dict[str, Any],
        warm: dict[str, Any],
        url: str,
    ) -> list[Finding]:
        """Compare cold and warm load metrics."""
        findings: list[Finding] = []

        cold_count = cold["request_count"]
        warm_count = warm["request_count"]
        cold_size = cold["total_size"]
        warm_size = warm["total_size"]
        cold_time = cold["load_time_ms"]
        warm_time = warm["load_time_ms"]

        # Calculate savings.
        request_reduction = cold_count - warm_count
        size_reduction = cold_size - warm_size
        time_reduction = cold_time - warm_time

        request_reduction_pct = (
            (request_reduction / cold_count * 100) if cold_count > 0 else 0
        )
        size_reduction_pct = (
            (size_reduction / cold_size * 100) if cold_size > 0 else 0
        )

        # Use BYTES transferred as the primary cache effectiveness metric.
        # Browsers reuse connections and send conditional requests (304 Not Modified)
        # so request COUNT may stay the same while bytes drop dramatically.
        # A warm load that transfers <30% of cold bytes = effective caching.
        if cold_size > 0 and size_reduction_pct < 30 and cold_count > 5:
            findings.append(
                Finding(
                    domain="advanced",
                    severity=Severity.HIGH,
                    title="Ineffective browser caching",
                    description=(
                        f"Warm (cached) load of {url} transferred "
                        f"{warm_size / 1024:.0f} KB vs {cold_size / 1024:.0f} KB on "
                        f"cold load — only {size_reduction_pct:.0f}% byte reduction. "
                        f"Effective caching should reduce transferred bytes by 70%+. "
                        f"Cold: {cold_count} reqs / {cold_time:.0f} ms. "
                        f"Warm: {warm_count} reqs / {warm_time:.0f} ms."
                    ),
                    url=url,
                    metadata={
                        "type": "ineffective_cache",
                        "cold_requests": cold_count,
                        "warm_requests": warm_count,
                        "cold_size": cold_size,
                        "warm_size": warm_size,
                        "cold_time_ms": round(cold_time),
                        "warm_time_ms": round(warm_time),
                        "size_reduction_pct": round(size_reduction_pct, 1),
                    },
                )
            )

        return findings

    def _check_cache_headers(
        self, load_data: dict[str, Any], url: str
    ) -> list[Finding]:
        """Check that static resources have appropriate caching headers."""
        findings: list[Finding] = []
        uncached_static: list[str] = []
        short_cached: list[tuple[str, int]] = []

        for req in load_data.get("requests", []):
            if not req.get("is_static"):
                continue
            if req.get("status", 0) != 200:
                continue

            cc = req.get("cache_control", "")
            etag = req.get("etag", "")
            last_mod = req.get("last_modified", "")
            resource_url = req.get("url", "")

            # No caching mechanism at all.
            if not cc and not etag and not last_mod:
                uncached_static.append(resource_url)
                continue

            # Content-hash filenames (e.g. admin.DEd9KrAh.css) use content-
            # addressable URLs.  max-age=0 + must-revalidate + ETag is a valid
            # caching strategy for these — the URL itself changes on redeploy.
            from urllib.parse import urlparse as _urlparse
            url_path = _urlparse(resource_url).path
            if _CONTENT_HASH_PATTERN.search(url_path):
                continue  # Content-addressable — skip cache duration check

            # Has Cache-Control but too short.
            if cc:
                max_age_match = re.search(r"max-age\s*=\s*(\d+)", cc)
                if max_age_match:
                    max_age = int(max_age_match.group(1))
                    if max_age < _MIN_CACHE_MAX_AGE:
                        short_cached.append((resource_url, max_age))
                elif "no-cache" in cc or "no-store" in cc:
                    uncached_static.append(resource_url)

        if uncached_static:
            sample = uncached_static[:5]
            remaining = len(uncached_static) - len(sample)
            findings.append(
                Finding(
                    domain="advanced",
                    severity=Severity.MEDIUM,
                    title="Static assets missing cache headers",
                    description=(
                        f"{len(uncached_static)} static resource(s) at {url} have no "
                        f"Cache-Control, ETag, or Last-Modified headers: "
                        f"{', '.join(_short_url(u) for u in sample)}"
                        f"{f' (+{remaining} more)' if remaining > 0 else ''}. "
                        "Without caching headers, browsers must re-download these "
                        "resources on every visit."
                    ),
                    url=url,
                    metadata={
                        "type": "missing_cache_headers",
                        "count": len(uncached_static),
                        "sample_urls": uncached_static[:10],
                    },
                    fix_snippet=(
                        "# Nginx example for static asset caching:\n"
                        "location ~* \\.(js|css|png|jpg|gif|svg|woff2?)$ {\n"
                        "    expires 1y;\n"
                        "    add_header Cache-Control \"public, immutable\";\n"
                        "}"
                    ),
                )
            )

        if short_cached:
            sample = short_cached[:5]
            findings.append(
                Finding(
                    domain="advanced",
                    severity=Severity.LOW,
                    title="Static assets with short cache duration",
                    description=(
                        f"{len(short_cached)} static resource(s) at {url} have "
                        f"max-age < {_MIN_CACHE_MAX_AGE}s (1 hour): "
                        + ", ".join(
                            f"{_short_url(u)} ({age}s)" for u, age in sample
                        )
                        + ". Static assets with content-hash filenames should use "
                        "max-age=31536000 (1 year) with immutable flag."
                    ),
                    url=url,
                    metadata={
                        "type": "short_cache",
                        "count": len(short_cached),
                        "sample": [
                            {"url": u, "max_age": a} for u, a in short_cached[:10]
                        ],
                    },
                )
            )

        return findings

    async def _check_service_worker(
        self, page: Page, url: str
    ) -> list[Finding]:
        """Check if the site registers a service worker (PWA indicator)."""
        findings: list[Finding] = []
        try:
            has_sw = await page.evaluate("""() => {
                return 'serviceWorker' in navigator
                    ? navigator.serviceWorker.controller !== null
                    : false;
            }""")

            # Also check for manifest link.
            has_manifest = await page.evaluate("""() => {
                const link = document.querySelector('link[rel="manifest"]');
                return link !== null;
            }""")

            if not has_sw and not has_manifest:
                # Not necessarily a problem — just informational.
                pass
            elif has_sw:
                logger.debug("Service worker detected at %s", url)
            elif has_manifest and not has_sw:
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.LOW,
                        title="Web app manifest present but no active service worker",
                        description=(
                            f"The page at {url} includes a web app manifest "
                            "(<link rel='manifest'>) but has no active service worker. "
                            "A service worker is needed for offline support, push "
                            "notifications, and full PWA installability."
                        ),
                        url=url,
                        metadata={"type": "pwa_incomplete"},
                    )
                )
        except Exception:
            logger.debug("Service worker check failed for %s", url)

        return findings


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _get_extension(path: str) -> str:
    """Extract lowercase file extension from a URL path."""
    dot_pos = path.rfind(".")
    if dot_pos == -1:
        return ""
    ext = path[dot_pos:].lower()
    # Trim query fragments that may have leaked in.
    for sep in ("?", "#", ";"):
        sep_pos = ext.find(sep)
        if sep_pos != -1:
            ext = ext[:sep_pos]
    return ext


def _short_url(url: str) -> str:
    """Shorten a URL for display — keep just path + file name."""
    parsed = urlparse(url)
    path = parsed.path
    if len(path) > 60:
        path = "..." + path[-57:]
    return path


def _sample_urls(ctx: ScanContext) -> list[str]:
    """Pick a representative sample of discovered URLs."""
    urls = list(ctx.state.discovered_urls)
    if not urls:
        return [ctx.config.target_url]
    random.shuffle(urls)
    return urls[:_MAX_SAMPLE_PAGES]
