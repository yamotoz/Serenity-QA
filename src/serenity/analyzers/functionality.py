"""General Functionality analyzer — links, JS errors, cookies, localStorage."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from serenity.analyzers.base import BaseAnalyzer
from serenity.constants import HTTP_TIMEOUT_S, Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import Page, Request, ConsoleMessage

    from serenity.core.state import ScanContext

logger = logging.getLogger(__name__)

# Cap external link checks to avoid hammering third-party servers
_MAX_EXTERNAL_LINKS = 50

# Known infrastructure/CDN cookies that developers cannot control
_INFRA_COOKIES: set[str] = {
    "_vcrcs", "_vercel_jwt",                     # Vercel
    "__cf_bm", "cf_clearance", "__cfduid",       # Cloudflare
    "_ga", "_gid", "_gat", "_gcl_au",           # Google Analytics
    "AWSALB", "AWSALBCORS",                      # AWS ALB
    "__stripe_mid", "__stripe_sid",               # Stripe
    "hubspotutk", "__hstc", "__hssc",            # HubSpot
    "_fbp", "_fbc",                              # Facebook
    "intercom-session", "intercom-id",           # Intercom
}

# Paths that are expected to return 403/404 and should not be flagged
_IGNORED_RESOURCE_PATHS: list[str] = [
    "/.well-known/",
    "/favicon.ico",
    "/apple-touch-icon",
]

# Patterns that suggest sensitive data in localStorage
_SENSITIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("JWT token", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("email address", re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
    ("credit card number", re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b")),
    ("SSN / CPF", re.compile(r"\b\d{3}[\-.]?\d{2,3}[\-.]?\d{3,4}[\-.]?\d{0,2}\b")),
    ("password field", re.compile(r"(?i)(\"password\"|'password'|password\s*[:=])")),
    ("API key", re.compile(r"(?i)(api[_-]?key|apikey|secret[_-]?key)\s*[:=]\s*[\"']?\w{16,}")),
]


class FunctionalityAnalyzer(BaseAnalyzer):
    """Checks broken links, JS errors, resource failures, cookies, and storage."""

    domain: str = "functionality"
    weight: float = 0.10

    def __init__(self) -> None:
        self._checked_internal_urls: set[str] = set()
        self._checked_external_urls: set[str] = set()

    async def setup(self, ctx: ScanContext) -> None:
        self._checked_internal_urls = set()
        self._checked_external_urls = set()

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

        # Attach event listeners before checks that need them
        console_errors: list[str] = []
        failed_resources: list[dict[str, str]] = []

        def on_console(msg: ConsoleMessage) -> None:
            if msg.type == "error":
                console_errors.append(msg.text)

        def on_request_failed(request: Request) -> None:
            failed_resources.append({
                "url": request.url,
                "resource_type": request.resource_type,
                "failure": request.failure or "unknown",
            })

        page.on("console", on_console)
        page.on("requestfailed", on_request_failed)

        # Wait briefly to capture late-firing errors after page load
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            # Page may not reach networkidle; continue with what we have
            pass

        # --- Link checks -----------------------------------------------------
        findings.extend(await self._check_internal_links(ctx, url, page))
        findings.extend(await self._check_external_links(ctx, url, page))

        # --- Console errors --------------------------------------------------
        findings.extend(self._report_console_errors(console_errors, url))

        # --- Failed resources ------------------------------------------------
        findings.extend(self._report_failed_resources(failed_resources, url))

        # --- Cookies ---------------------------------------------------------
        findings.extend(await self._check_cookies(ctx, url, page))

        # --- localStorage sensitive data -------------------------------------
        findings.extend(await self._check_local_storage(page, url))

        # --- Redirect loops --------------------------------------------------
        findings.extend(await self._check_redirect_loop(ctx, url))

        # Clean up listeners
        page.remove_listener("console", on_console)
        page.remove_listener("requestfailed", on_request_failed)

        return findings

    # ================================================================== #
    # Internal links
    # ================================================================== #

    async def _check_internal_links(
        self,
        ctx: ScanContext,
        url: str,
        page: Page,
    ) -> list[Finding]:
        findings: list[Finding] = []
        parsed_page = urlparse(url)
        page_domain = parsed_page.netloc

        try:
            links: list[dict[str, str]] = await page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        href: a.href,
                        rawHref: a.getAttribute('href') || '',
                        text: a.textContent.trim().substring(0, 80),
                        selector: a.id ? '#' + a.id
                                 : a.className ? 'a.' + a.className.split(' ')[0]
                                 : 'a'
                    }));
                }
            """)
        except Exception as exc:
            logger.debug("Link extraction failed for %s: %s", url, exc)
            return findings

        # Separate fragment-only links (e.g., #section, /#section) — check via DOM, not HTTP
        fragment_links = []
        http_links = []
        # URLs already successfully loaded by the crawler — no need to HTTP-check them
        known_ok_urls = ctx.state.discovered_urls | ctx.state.analyzed_urls
        for link in links:
            raw = link.get("rawHref", "")
            parsed_href = urlparse(link["href"])

            # Fragment links → DOM check, not HTTP
            if raw.startswith("#") or (parsed_href.fragment and parsed_href.path in ("", "/")):
                fragment_links.append(link)
                continue

            # Skip non-HTTP schemes
            if raw.startswith(("javascript:", "mailto:", "tel:")):
                continue

            # Skip links to other domains
            if parsed_href.netloc != page_domain:
                continue

            # Already checked
            if link["href"] in self._checked_internal_urls:
                continue

            # Normalize the target URL and check if it's already known to the crawler
            # (if the crawler already loaded it successfully, it's not broken)
            # Strip fragment before comparing (server never sees fragments)
            href_no_frag = urlparse(link["href"])._replace(fragment="").geturl()
            norm_href = href_no_frag.rstrip("/") if href_no_frag.count("/") > 3 else href_no_frag
            if norm_href in known_ok_urls or norm_href + "/" in known_ok_urls:
                continue

            http_links.append(link)

        # Check fragment links exist in the DOM — but ONLY if the link
        # targets the SAME page. Links like "/#section" from /analytics target
        # the HOME page, not /analytics. Don't check those here.
        current_path = urlparse(url).path.rstrip("/") or "/"
        for link in fragment_links:
            parsed_link = urlparse(link["href"])
            fragment = parsed_link.fragment
            if not fragment:
                continue

            # Determine the target path of this link
            link_path = parsed_link.path.rstrip("/") or "/"

            # Only check fragment in current page's DOM if the link targets THIS page
            # Links to other pages (e.g., /#section from /analytics) are cross-page
            if link_path != current_path:
                continue  # Cross-page fragment — can't verify in current DOM

            try:
                exists = await page.evaluate(
                    "(id) => !!document.getElementById(id) || !!document.querySelector('[name=\"' + CSS.escape(id) + '\"]')",
                    fragment,
                )
                if not exists:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.LOW,
                            title=f"Anchor target #{fragment} not found in DOM",
                            description=(
                                f'Link "{link["text"][:60] or link["href"]}" points to '
                                f'#{fragment} but no element with id="{fragment}" exists on the page.'
                            ),
                            url=url,
                            element_selector=link.get("selector"),
                            estimated_fix_minutes=5,
                            metadata={"target_fragment": fragment, "link_href": link["href"]},
                        ),
                    )
            except Exception:
                pass

        internal_links = http_links

        # Check each internal link's status
        broken: list[dict[str, Any]] = []
        semaphore = asyncio.Semaphore(10)

        async def _check(link: dict[str, str]) -> None:
            href = link["href"]
            self._checked_internal_urls.add(href)
            try:
                async with semaphore:
                    resp = await ctx.http_client.head(
                        href,
                        follow_redirects=True,
                        timeout=HTTP_TIMEOUT_S,
                    )
                if resp.status_code >= 400:
                    broken.append({**link, "status_code": resp.status_code})
            except Exception as exc:
                broken.append({**link, "status_code": 0, "error": str(exc)})

        tasks = [_check(link) for link in internal_links[:100]]
        await asyncio.gather(*tasks)

        for item in broken:
            status = item.get("status_code", 0)
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title=f"Broken internal link ({status or 'connection error'})",
                    description=(
                        f'The link "{item["text"][:60] or item["href"]}" '
                        f'points to {item["href"]} which returned '
                        f'{f"HTTP {status}" if status else "a connection error"}. '
                        "Broken internal links hurt user experience and SEO."
                    ),
                    url=url,
                    element_selector=item.get("selector"),
                    estimated_fix_minutes=10,
                    metadata={
                        "target_url": item["href"],
                        "status_code": status,
                        "link_text": item["text"],
                    },
                ),
            )

        return findings

    # ================================================================== #
    # External links
    # ================================================================== #

    async def _check_external_links(
        self,
        ctx: ScanContext,
        url: str,
        page: Page,
    ) -> list[Finding]:
        findings: list[Finding] = []
        parsed_page = urlparse(url)
        page_domain = parsed_page.netloc

        try:
            links: list[dict[str, str]] = await page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        href: a.href,
                        text: a.textContent.trim().substring(0, 80),
                        selector: a.id ? '#' + a.id
                                 : a.className ? 'a.' + a.className.split(' ')[0]
                                 : 'a'
                    }));
                }
            """)
        except Exception as exc:
            logger.debug("External link extraction failed for %s: %s", url, exc)
            return findings

        external_links = [
            link for link in links
            if urlparse(link["href"]).netloc != page_domain
            and link["href"].startswith(("http://", "https://"))
            and link["href"] not in self._checked_external_urls
        ]

        # Cap to avoid spamming external servers
        to_check = external_links[:_MAX_EXTERNAL_LINKS]

        broken: list[dict[str, Any]] = []
        semaphore = asyncio.Semaphore(5)

        async def _check(link: dict[str, str]) -> None:
            href = link["href"]
            self._checked_external_urls.add(href)
            try:
                async with semaphore:
                    resp = await ctx.http_client.head(
                        href,
                        follow_redirects=True,
                        timeout=HTTP_TIMEOUT_S,
                    )
                # Some servers block HEAD; retry with GET for 405
                if resp.status_code == 405:
                    async with semaphore:
                        resp = await ctx.http_client.get(
                            href,
                            follow_redirects=True,
                            timeout=HTTP_TIMEOUT_S,
                        )
                if resp.status_code >= 400:
                    broken.append({**link, "status_code": resp.status_code})
            except Exception:
                # External link failures are less critical; skip connection errors
                pass

        tasks = [_check(link) for link in to_check]
        await asyncio.gather(*tasks)

        for item in broken:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"Broken external link (HTTP {item['status_code']})",
                    description=(
                        f'The link "{item["text"][:60] or item["href"]}" '
                        f'points to {item["href"]} which returned HTTP '
                        f'{item["status_code"]}. Broken external links degrade '
                        "user trust and can be penalized by search engines."
                    ),
                    url=url,
                    element_selector=item.get("selector"),
                    estimated_fix_minutes=5,
                    metadata={
                        "target_url": item["href"],
                        "status_code": item["status_code"],
                        "link_text": item["text"],
                    },
                ),
            )

        return findings

    # ================================================================== #
    # Console errors
    # ================================================================== #

    def _report_console_errors(
        self,
        console_errors: list[str],
        url: str,
    ) -> list[Finding]:
        findings: list[Finding] = []
        if not console_errors:
            return findings

        # Deduplicate similar messages
        unique_errors: list[str] = list(dict.fromkeys(console_errors))

        if len(unique_errors) <= 3:
            for error in unique_errors:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title="JavaScript console error",
                        description=(
                            f"A JavaScript error was logged to the console: "
                            f"\"{error[:300]}\". Console errors may indicate "
                            "broken functionality, failed API calls, or "
                            "missing resources."
                        ),
                        url=url,
                        estimated_fix_minutes=15,
                        metadata={"error_message": error},
                    ),
                )
        else:
            # Batch report to avoid flooding
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title=f"Multiple JavaScript console errors ({len(unique_errors)})",
                    description=(
                        f"The page produced {len(unique_errors)} unique JavaScript "
                        "errors. First 5:\n"
                        + "\n".join(f"  - {e[:200]}" for e in unique_errors[:5])
                    ),
                    url=url,
                    estimated_fix_minutes=30,
                    metadata={"errors": unique_errors[:20], "total_count": len(unique_errors)},
                ),
            )

        return findings

    # ================================================================== #
    # Failed resources (404 images, scripts, stylesheets)
    # ================================================================== #

    def _report_failed_resources(
        self,
        failed_resources: list[dict[str, str]],
        url: str,
    ) -> list[Finding]:
        findings: list[Finding] = []
        if not failed_resources:
            return findings

        # Filter out expected failures (e.g., .well-known, favicon)
        filtered = [
            res for res in failed_resources
            if not any(ignored in res.get("url", "") for ignored in _IGNORED_RESOURCE_PATHS)
        ]
        if not filtered:
            return findings

        failed_resources = filtered

        # Group by resource type
        by_type: dict[str, list[dict[str, str]]] = {}
        for res in failed_resources:
            rtype = res.get("resource_type", "other")
            by_type.setdefault(rtype, []).append(res)

        for rtype, resources in by_type.items():
            if len(resources) == 1:
                res = resources[0]
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title=f"Failed {rtype} resource",
                        description=(
                            f"The {rtype} resource at {res['url']} failed to load "
                            f"({res.get('failure', 'unknown error')}). Missing "
                            "resources can break page layout and functionality."
                        ),
                        url=url,
                        estimated_fix_minutes=10,
                        metadata={"resource_url": res["url"], "resource_type": rtype},
                    ),
                )
            else:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.HIGH,
                        title=f"{len(resources)} failed {rtype} resource(s)",
                        description=(
                            f"{len(resources)} {rtype} resources failed to load:\n"
                            + "\n".join(
                                f"  - {r['url']} ({r.get('failure', 'unknown')})"
                                for r in resources[:10]
                            )
                        ),
                        url=url,
                        estimated_fix_minutes=20,
                        metadata={
                            "resource_type": rtype,
                            "resources": [r["url"] for r in resources[:20]],
                            "total_count": len(resources),
                        },
                    ),
                )

        return findings

    # ================================================================== #
    # Cookies — HttpOnly, Secure, SameSite
    # ================================================================== #

    async def _check_cookies(
        self,
        ctx: ScanContext,
        url: str,
        page: Page,
    ) -> list[Finding]:
        findings: list[Finding] = []
        try:
            cookies = await page.context.cookies(url)
        except Exception as exc:
            logger.debug("Cookie check failed for %s: %s", url, exc)
            return findings

        if not cookies:
            return findings

        parsed = urlparse(url)
        is_https = parsed.scheme == "https"

        insecure_cookies: list[str] = []
        no_httponly_cookies: list[str] = []
        no_samesite_cookies: list[str] = []

        for cookie in cookies:
            name = cookie.get("name", "unknown")

            # Skip known infrastructure/CDN cookies the developer can't control
            if name in _INFRA_COOKIES:
                continue

            if is_https and not cookie.get("secure", False):
                insecure_cookies.append(name)

            if not cookie.get("httpOnly", False):
                no_httponly_cookies.append(name)

            samesite = cookie.get("sameSite", "None")
            if samesite == "None" or not samesite:
                no_samesite_cookies.append(name)

        if insecure_cookies:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title=f"{len(insecure_cookies)} cookie(s) missing Secure flag",
                    description=(
                        "The following cookies are not marked as Secure on an "
                        "HTTPS site: " + ", ".join(insecure_cookies[:10]) + ". "
                        "Without the Secure flag, cookies can be transmitted "
                        "over unencrypted connections."
                    ),
                    url=url,
                    fix_snippet="Set-Cookie: name=value; Secure; HttpOnly; SameSite=Lax",
                    estimated_fix_minutes=10,
                    metadata={"cookies": insecure_cookies},
                ),
            )

        if no_httponly_cookies:
            # Only flag as high if session-like cookie names are present
            session_patterns = ("session", "sid", "token", "auth", "csrf")
            has_sensitive = any(
                any(p in name.lower() for p in session_patterns)
                for name in no_httponly_cookies
            )
            severity = Severity.HIGH if has_sensitive else Severity.MEDIUM
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=severity,
                    title=f"{len(no_httponly_cookies)} cookie(s) missing HttpOnly flag",
                    description=(
                        "The following cookies are not marked as HttpOnly: "
                        + ", ".join(no_httponly_cookies[:10]) + ". "
                        "Without HttpOnly, cookies are accessible to JavaScript "
                        "and vulnerable to XSS-based theft."
                    ),
                    url=url,
                    fix_snippet="Set-Cookie: name=value; HttpOnly; Secure; SameSite=Lax",
                    estimated_fix_minutes=10,
                    metadata={"cookies": no_httponly_cookies},
                ),
            )

        if no_samesite_cookies:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"{len(no_samesite_cookies)} cookie(s) missing SameSite attribute",
                    description=(
                        "The following cookies have no SameSite attribute (or it "
                        "is set to None): " + ", ".join(no_samesite_cookies[:10])
                        + ". The SameSite attribute helps prevent CSRF attacks."
                    ),
                    url=url,
                    fix_snippet="Set-Cookie: name=value; SameSite=Lax; Secure; HttpOnly",
                    estimated_fix_minutes=10,
                    metadata={"cookies": no_samesite_cookies},
                ),
            )

        return findings

    # ================================================================== #
    # localStorage — sensitive data detection
    # ================================================================== #

    async def _check_local_storage(self, page: Page, url: str) -> list[Finding]:
        findings: list[Finding] = []
        try:
            storage_items: dict[str, str] = await page.evaluate("""
                () => {
                    const items = {};
                    try {
                        for (let i = 0; i < localStorage.length; i++) {
                            const key = localStorage.key(i);
                            if (key) {
                                items[key] = localStorage.getItem(key) || '';
                            }
                        }
                    } catch (e) {
                        // localStorage may be disabled
                    }
                    return items;
                }
            """)
        except Exception as exc:
            logger.debug("localStorage check failed for %s: %s", url, exc)
            return findings

        if not storage_items:
            return findings

        detected: list[dict[str, str]] = []
        for key, value in storage_items.items():
            combined = f"{key}={value}"
            for pattern_name, pattern_re in _SENSITIVE_PATTERNS:
                if pattern_re.search(combined):
                    detected.append({
                        "key": key,
                        "pattern": pattern_name,
                        "value_preview": value[:50] + ("..." if len(value) > 50 else ""),
                    })
                    break  # One match per key is enough

        if detected:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title=f"Potentially sensitive data in localStorage ({len(detected)} item(s))",
                    description=(
                        "The following localStorage keys appear to contain "
                        "sensitive data:\n"
                        + "\n".join(
                            f"  - \"{d['key']}\": detected {d['pattern']}"
                            for d in detected[:10]
                        )
                        + "\n\nStoring sensitive information in localStorage "
                        "makes it accessible to any JavaScript on the page, "
                        "including XSS payloads. Consider using HttpOnly cookies "
                        "or server-side sessions instead."
                    ),
                    url=url,
                    estimated_fix_minutes=30,
                    metadata={"detected_items": detected[:20]},
                ),
            )

        return findings

    # ================================================================== #
    # Redirect loops
    # ================================================================== #

    async def _check_redirect_loop(
        self,
        ctx: ScanContext,
        url: str,
    ) -> list[Finding]:
        findings: list[Finding] = []
        try:
            # Use a fresh request without auto-follow to trace redirects manually
            visited: list[str] = [url]
            current_url = url
            max_hops = 10

            for _ in range(max_hops):
                resp = await ctx.http_client.get(
                    current_url,
                    follow_redirects=False,
                    timeout=HTTP_TIMEOUT_S,
                )
                if resp.status_code not in (301, 302, 303, 307, 308):
                    break

                location = resp.headers.get("location", "")
                if not location:
                    break

                # Resolve relative redirects
                next_url = urljoin(current_url, location)

                if next_url in visited:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.CRITICAL,
                            title="Redirect loop detected",
                            description=(
                                f"A redirect loop was detected starting at {url}. "
                                "The redirect chain visits the same URL twice:\n"
                                + " -> ".join(visited + [next_url])
                                + "\n\nThis makes the page completely inaccessible."
                            ),
                            url=url,
                            estimated_fix_minutes=20,
                            metadata={
                                "redirect_chain": visited + [next_url],
                                "loop_target": next_url,
                            },
                        ),
                    )
                    break

                visited.append(next_url)
                current_url = next_url

            # Also flag excessively long redirect chains (not loops)
            if len(visited) > 3 and not findings:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title=f"Excessive redirect chain ({len(visited) - 1} hops)",
                        description=(
                            f"The URL {url} goes through {len(visited) - 1} "
                            "redirects before reaching the final destination. "
                            "Long redirect chains add latency:\n"
                            + " -> ".join(visited)
                        ),
                        url=url,
                        estimated_fix_minutes=10,
                        metadata={"redirect_chain": visited},
                    ),
                )

        except Exception as exc:
            logger.debug("Redirect loop check failed for %s: %s", url, exc)

        return findings
