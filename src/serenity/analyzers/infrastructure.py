"""Infrastructure & Availability analyzer — SSL, redirects, headers, exposed files."""

from __future__ import annotations

import logging
import ssl
import socket
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from serenity.analyzers.base import BaseAnalyzer
from serenity.constants import (
    HTTP_TIMEOUT_S,
    SECURITY_HEADERS,
    SENSITIVE_PATHS,
    Severity,
)
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import Page

    from serenity.core.state import ScanContext

logger = logging.getLogger(__name__)


class InfrastructureAnalyzer(BaseAnalyzer):
    """Checks SSL certificates, redirects, security headers, and exposed files."""

    domain: str = "infrastructure"
    weight: float = 0.10

    def __init__(self) -> None:
        # Track which security headers are missing across ALL pages
        # to report them once globally instead of per-page
        self._missing_headers_tracker: dict[str, list[str]] = {}  # header -> [urls]
        self._headers_checked = False

    async def setup(self, ctx: ScanContext) -> None:
        self._missing_headers_tracker.clear()
        self._headers_checked = False

    # ------------------------------------------------------------------ #
    # Per-page analysis
    # ------------------------------------------------------------------ #

    async def analyze_page(
        self,
        ctx: ScanContext,
        url: str,
        page: Page,
    ) -> list[Finding]:
        findings: list[Finding] = []

        parsed = urlparse(url)
        is_root = parsed.path in ("", "/")

        # --- SSL certificate ------------------------------------------------
        if parsed.scheme == "https" and is_root:
            findings.extend(await self._check_ssl(parsed.hostname or "", url))

        # --- HTTP → HTTPS redirect ------------------------------------------
        if parsed.scheme == "https" and is_root:
            findings.extend(await self._check_http_redirect(ctx, parsed.hostname or "", url))

        # --- Status code check -----------------------------------------------
        page_data = ctx.state.page_data.get(url)
        if page_data and page_data.status_code >= 400:
            sev = Severity.CRITICAL if page_data.status_code >= 500 else Severity.HIGH
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=sev,
                    title=f"HTTP {page_data.status_code} error on page",
                    description=(
                        f"The page returned status code {page_data.status_code}. "
                        "This indicates a server or client error that prevents "
                        "users from accessing content."
                    ),
                    url=url,
                    estimated_fix_minutes=15,
                    metadata={"status_code": page_data.status_code},
                ),
            )

        # --- TTFB -----------------------------------------------------------
        findings.extend(await self._check_ttfb(ctx, url))

        # --- Security headers (collect per-page, report globally later) ------
        self._collect_security_headers(ctx, url)

        # --- Sensitive paths (only on root page to avoid duplicate work) -----
        if is_root:
            findings.extend(await self._probe_sensitive_paths(ctx, url))

        return findings

    # ------------------------------------------------------------------ #
    # Global analysis — report accumulated security header issues once
    # ------------------------------------------------------------------ #

    async def analyze_global(self, ctx: ScanContext) -> list[Finding]:
        """Report security header issues once globally, not per-page."""
        findings: list[Finding] = []

        # Count how many of the non-CSP headers are present (all pages have them)
        total_non_csp_headers = len(SECURITY_HEADERS) - 1  # exclude CSP
        missing_non_csp = sum(
            1 for h in self._missing_headers_tracker
            if h != "content-security-policy" and self._missing_headers_tracker[h]
        )
        other_headers_mostly_present = missing_non_csp <= 1

        for header_name, affected_urls in self._missing_headers_tracker.items():
            if not affected_urls:
                continue

            # CSP is hard to implement with frameworks that use inline scripts
            # (Astro, Next.js, etc.).  If all other security headers are present,
            # CSP alone shouldn't penalize heavily — it's a nice-to-have.
            if header_name == "content-security-policy" and other_headers_mostly_present:
                severity = Severity.LOW
            elif header_name == "strict-transport-security":
                severity = Severity.HIGH
            elif header_name == "content-security-policy":
                severity = Severity.HIGH
            else:
                severity = Severity.MEDIUM
            count = len(affected_urls)
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=severity,
                    title=f"Missing security header: {header_name}",
                    description=(
                        self._header_descriptions.get(
                            header_name,
                            f"The security header '{header_name}' is not set.",
                        )
                        + f"\n\nAffects {count} page(s)."
                    ),
                    url=affected_urls[0],  # Primary URL
                    fix_snippet=self._header_fixes.get(header_name),
                    estimated_fix_minutes=10,
                    metadata={
                        "header": header_name,
                        "affected_pages": count,
                        "sample_urls": affected_urls[:5],
                    },
                ),
            )

        return findings

    # ------------------------------------------------------------------ #
    # SSL certificate validation
    # ------------------------------------------------------------------ #

    async def _check_ssl(self, hostname: str, url: str) -> list[Finding]:
        findings: list[Finding] = []
        try:
            ctx_ssl = ssl.create_default_context()
            with socket.create_connection((hostname, 443), timeout=10) as sock:
                with ctx_ssl.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert = ssock.getpeercert()

            if not cert:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.CRITICAL,
                        title="SSL certificate could not be retrieved",
                        description=(
                            f"No certificate was returned for {hostname}. "
                            "This may indicate a misconfigured TLS setup."
                        ),
                        url=url,
                        estimated_fix_minutes=30,
                    ),
                )
                return findings

            # Parse expiry date
            not_after_str = cert.get("notAfter", "")
            not_before_str = cert.get("notBefore", "")

            if not_after_str:
                not_after = datetime.strptime(
                    not_after_str, "%b %d %H:%M:%S %Y %Z",
                ).replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                days_remaining = (not_after - now).days

                if days_remaining < 0:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.CRITICAL,
                            title="SSL certificate has expired",
                            description=(
                                f"The SSL certificate for {hostname} expired on "
                                f"{not_after.isoformat()}. Visitors will see a "
                                "security warning in their browser."
                            ),
                            url=url,
                            estimated_fix_minutes=30,
                            metadata={
                                "not_after": not_after.isoformat(),
                                "days_remaining": days_remaining,
                            },
                        ),
                    )
                elif days_remaining < 14:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.HIGH,
                            title="SSL certificate expires within 14 days",
                            description=(
                                f"The SSL certificate for {hostname} expires on "
                                f"{not_after.isoformat()} ({days_remaining} days "
                                "remaining). Renew it immediately to avoid downtime."
                            ),
                            url=url,
                            estimated_fix_minutes=20,
                            metadata={
                                "not_after": not_after.isoformat(),
                                "days_remaining": days_remaining,
                            },
                        ),
                    )
                elif days_remaining < 30:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.MEDIUM,
                            title="SSL certificate expires within 30 days",
                            description=(
                                f"The SSL certificate for {hostname} expires on "
                                f"{not_after.isoformat()} ({days_remaining} days "
                                "remaining). Plan renewal soon."
                            ),
                            url=url,
                            estimated_fix_minutes=15,
                            metadata={
                                "not_after": not_after.isoformat(),
                                "days_remaining": days_remaining,
                            },
                        ),
                    )

            if not_before_str:
                not_before = datetime.strptime(
                    not_before_str, "%b %d %H:%M:%S %Y %Z",
                ).replace(tzinfo=timezone.utc)
                if not_before > datetime.now(timezone.utc):
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.CRITICAL,
                            title="SSL certificate is not yet valid",
                            description=(
                                f"The certificate for {hostname} is not valid until "
                                f"{not_before.isoformat()}."
                            ),
                            url=url,
                            estimated_fix_minutes=30,
                            metadata={"not_before": not_before.isoformat()},
                        ),
                    )

        except ssl.SSLCertVerificationError as exc:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.CRITICAL,
                    title="SSL certificate verification failed",
                    description=(
                        f"Certificate verification failed for {hostname}: {exc}. "
                        "Browsers will display a security warning."
                    ),
                    url=url,
                    estimated_fix_minutes=30,
                    metadata={"error": str(exc)},
                ),
            )
        except (socket.timeout, socket.gaierror, OSError) as exc:
            logger.warning("SSL check failed for %s: %s", hostname, exc)
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title="Unable to connect for SSL validation",
                    description=(
                        f"Could not establish a TLS connection to {hostname} "
                        f"on port 443: {exc}"
                    ),
                    url=url,
                    estimated_fix_minutes=20,
                    metadata={"error": str(exc)},
                ),
            )

        return findings

    # ------------------------------------------------------------------ #
    # HTTP → HTTPS redirect
    # ------------------------------------------------------------------ #

    async def _check_http_redirect(
        self,
        ctx: ScanContext,
        hostname: str,
        url: str,
    ) -> list[Finding]:
        findings: list[Finding] = []
        http_url = f"http://{hostname}/"
        try:
            resp = await ctx.http_client.get(
                http_url,
                follow_redirects=True,
                timeout=HTTP_TIMEOUT_S,
            )
            final_url = str(resp.url)
            if not final_url.startswith("https://"):
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.HIGH,
                        title="HTTP to HTTPS redirect not configured",
                        description=(
                            f"Requesting {http_url} does not redirect to HTTPS. "
                            "The final URL was: " + final_url + ". All traffic "
                            "should be forced to HTTPS for security."
                        ),
                        url=url,
                        fix_snippet=(
                            "# Nginx example:\n"
                            "server {\n"
                            "    listen 80;\n"
                            "    return 301 https://$host$request_uri;\n"
                            "}"
                        ),
                        estimated_fix_minutes=10,
                        metadata={"http_url": http_url, "final_url": final_url},
                    ),
                )
        except Exception as exc:
            logger.debug("HTTP redirect check failed for %s: %s", hostname, exc)

        return findings

    # ------------------------------------------------------------------ #
    # TTFB (Time to First Byte)
    # ------------------------------------------------------------------ #

    async def _check_ttfb(self, ctx: ScanContext, url: str) -> list[Finding]:
        findings: list[Finding] = []
        page_data = ctx.state.page_data.get(url)
        if not page_data or page_data.ttfb_ms <= 0:
            return findings

        ttfb = page_data.ttfb_ms
        if ttfb > 2000:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title=f"Very slow TTFB ({ttfb:.0f} ms)",
                    description=(
                        f"Time to First Byte is {ttfb:.0f} ms, well above the "
                        "recommended 800 ms threshold. This indicates slow server "
                        "response, possibly due to unoptimized backend code, "
                        "missing caching, or an overloaded server."
                    ),
                    url=url,
                    estimated_fix_minutes=60,
                    metadata={"ttfb_ms": round(ttfb, 2)},
                ),
            )
        elif ttfb > 800:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"Slow TTFB ({ttfb:.0f} ms)",
                    description=(
                        f"Time to First Byte is {ttfb:.0f} ms. Google recommends "
                        "a TTFB under 800 ms. Consider adding server-side caching "
                        "or a CDN."
                    ),
                    url=url,
                    estimated_fix_minutes=30,
                    metadata={"ttfb_ms": round(ttfb, 2)},
                ),
            )

        return findings

    # ------------------------------------------------------------------ #
    # Security headers — collected per-page, reported once globally
    # ------------------------------------------------------------------ #

    _header_descriptions: dict[str, str] = {
        "content-security-policy": (
            "CSP helps prevent Cross-Site Scripting (XSS) and data injection "
            "attacks by specifying allowed content sources."
        ),
        "strict-transport-security": (
            "HSTS tells browsers to always use HTTPS, protecting against "
            "protocol downgrade attacks and cookie hijacking."
        ),
        "x-frame-options": (
            "X-Frame-Options prevents the page from being loaded in an "
            "iframe, protecting against clickjacking attacks."
        ),
        "x-content-type-options": (
            "X-Content-Type-Options prevents browsers from MIME-sniffing "
            "responses away from the declared content type."
        ),
        "permissions-policy": (
            "Permissions-Policy controls which browser features (camera, "
            "microphone, geolocation, etc.) the page can use."
        ),
        "referrer-policy": (
            "Referrer-Policy controls how much referrer information is "
            "sent with requests, protecting user privacy."
        ),
    }

    _header_fixes: dict[str, str] = {
        "content-security-policy": "Content-Security-Policy: default-src 'self'; script-src 'self'",
        "strict-transport-security": "Strict-Transport-Security: max-age=31536000; includeSubDomains; preload",
        "x-frame-options": "X-Frame-Options: DENY",
        "x-content-type-options": "X-Content-Type-Options: nosniff",
        "permissions-policy": "Permissions-Policy: camera=(), microphone=(), geolocation=()",
        "referrer-policy": "Referrer-Policy: strict-origin-when-cross-origin",
    }

    def _collect_security_headers(self, ctx: ScanContext, url: str) -> None:
        """Collect missing headers per page (reported globally in analyze_global)."""
        page_data = ctx.state.page_data.get(url)
        if not page_data or not page_data.headers:
            return

        response_headers = {k.lower(): v for k, v in page_data.headers.items()}

        for header_name in SECURITY_HEADERS:
            if header_name not in response_headers:
                if header_name not in self._missing_headers_tracker:
                    self._missing_headers_tracker[header_name] = []
                self._missing_headers_tracker[header_name].append(url)

    # ------------------------------------------------------------------ #
    # Sensitive paths probing
    # ------------------------------------------------------------------ #

    async def _probe_sensitive_paths(
        self,
        ctx: ScanContext,
        url: str,
    ) -> list[Finding]:
        findings: list[Finding] = []
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # Phrases in the response body that indicate access is properly restricted
        _ACCESS_DENIED_PHRASES = [
            "access denied", "unauthorized", "forbidden", "restricted",
            "login required", "acesso restrito", "acesso negado",
            "authentication required", "not authorized", "sign in",
            "iniciar sessão", "fazer login", "entrar",
        ]

        # Paths that typically have client-side auth gates (SPA renders
        # "Acesso Restrito" via JS, but httpx only sees the HTML shell)
        _CLIENT_AUTH_PATHS = ("/admin", "/dashboard", "/internal", "/painel")

        for path in SENSITIVE_PATHS:
            probe_url = base + path
            try:
                resp = await ctx.http_client.get(
                    probe_url,
                    follow_redirects=True,
                    timeout=HTTP_TIMEOUT_S,
                )
                # A 200 response to a sensitive path is concerning
                if resp.status_code == 200:
                    # security.txt at well-known is actually good practice
                    if path == "/.well-known/security.txt":
                        continue

                    body_lower = resp.text[:5000].lower()

                    # Check body content — if the page shows access denied / login,
                    # it's properly protected (not a real exposure)
                    is_protected = any(
                        phrase in body_lower for phrase in _ACCESS_DENIED_PHRASES
                    )

                    # Known SPA auth paths are always considered protected.
                    # These paths render client-side auth gates (login forms,
                    # "Acesso Restrito" etc.) that httpx can't see because
                    # the content is rendered by JavaScript after page load.
                    if not is_protected and path in _CLIENT_AUTH_PATHS:
                        is_protected = True

                    if is_protected:
                        logger.info(
                            "Path %s returns 200 but shows access restriction", path
                        )
                        continue

                    severity = (
                        Severity.CRITICAL
                        if path in ("/.env", "/.git/config")
                        else Severity.HIGH
                    )
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=severity,
                            title=f"Sensitive path exposed: {path}",
                            description=(
                                f"The path {probe_url} returned HTTP 200 with "
                                "accessible content. This file or directory should "
                                "not be publicly accessible and may leak sensitive "
                                "information such as credentials, configuration "
                                "data, or internal structure."
                            ),
                            url=probe_url,
                            fix_snippet=(
                                f"# Nginx: block access to {path}\n"
                                f"location {path} {{\n"
                                f"    return 404;\n"
                                f"}}"
                            ),
                            estimated_fix_minutes=10,
                            metadata={"path": path, "status_code": resp.status_code},
                        ),
                    )
            except Exception as exc:
                logger.debug("Probe failed for %s: %s", probe_url, exc)

        return findings
