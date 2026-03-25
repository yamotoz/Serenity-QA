"""Technical SEO analyzer — titles, meta tags, headings, structured data."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from serenity.analyzers.base import BaseAnalyzer
from serenity.constants import HTTP_TIMEOUT_S, Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import Page

    from serenity.core.state import ScanContext

logger = logging.getLogger(__name__)

# Ideal character ranges
TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX = 120, 160

# Required Open Graph tags
OG_REQUIRED = ("og:title", "og:description", "og:image")


class SEOAnalyzer(BaseAnalyzer):
    """Checks on-page SEO signals: titles, meta, headings, structured data."""

    domain: str = "seo"
    weight: float = 0.20

    def __init__(self) -> None:
        self._title_map: dict[str, list[str]] = {}
        self._desc_map: dict[str, list[str]] = {}

    async def setup(self, ctx: ScanContext) -> None:
        self._title_map = {}
        self._desc_map = {}

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

        findings.extend(await self._check_title(page, url))
        findings.extend(await self._check_meta_description(page, url))
        findings.extend(await self._check_h1(page, url))
        findings.extend(await self._check_heading_hierarchy(page, url))
        findings.extend(await self._check_open_graph(page, url))
        findings.extend(await self._check_canonical(page, url))
        findings.extend(await self._check_structured_data(page, url))

        # Accumulate for global dedup
        title = await self._get_title(page)
        if title:
            self._title_map.setdefault(title, []).append(url)

        desc = await self._get_meta_description(page)
        if desc:
            self._desc_map.setdefault(desc, []).append(url)

        return findings

    # ------------------------------------------------------------------ #
    # Global / cross-page analysis
    # ------------------------------------------------------------------ #

    async def analyze_global(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []

        findings.extend(await self._check_sitemap(ctx))
        findings.extend(await self._check_robots_txt(ctx))
        findings.extend(self._check_duplicate_titles())
        findings.extend(self._check_duplicate_descriptions())

        return findings

    # ================================================================== #
    # Title
    # ================================================================== #

    async def _get_title(self, page: Page) -> str:
        try:
            return (await page.title()).strip()
        except Exception:
            return ""

    async def _check_title(self, page: Page, url: str) -> list[Finding]:
        findings: list[Finding] = []
        title = await self._get_title(page)

        if not title:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title="Missing page title",
                    description=(
                        "The page has no <title> tag. Page titles are critical for "
                        "search-engine rankings and appear in browser tabs and "
                        "social shares."
                    ),
                    url=url,
                    element_selector="head > title",
                    fix_snippet="<title>Your Page Title — Brand Name</title>",
                    estimated_fix_minutes=5,
                ),
            )
            return findings

        length = len(title)
        if length < TITLE_MIN:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.LOW,
                    title=f"Page title too short ({length} chars)",
                    description=(
                        f"The title \"{title}\" is {length} characters. "
                        f"Aim for {TITLE_MIN}–{TITLE_MAX} characters to maximize "
                        "click-through rate in search results."
                    ),
                    url=url,
                    element_selector="head > title",
                    estimated_fix_minutes=5,
                    metadata={"title": title, "length": length},
                ),
            )
        elif length > TITLE_MAX:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.LOW,
                    title=f"Page title too long ({length} chars)",
                    description=(
                        f"The title \"{title}\" is {length} characters and will be "
                        f"truncated in SERPs. Keep it under {TITLE_MAX} characters."
                    ),
                    url=url,
                    element_selector="head > title",
                    estimated_fix_minutes=5,
                    metadata={"title": title, "length": length},
                ),
            )

        return findings

    # ================================================================== #
    # Meta description
    # ================================================================== #

    async def _get_meta_description(self, page: Page) -> str:
        try:
            el = await page.query_selector('meta[name="description"]')
            if el:
                return (await el.get_attribute("content") or "").strip()
        except Exception:
            pass
        return ""

    async def _check_meta_description(self, page: Page, url: str) -> list[Finding]:
        findings: list[Finding] = []
        desc = await self._get_meta_description(page)

        if not desc:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title="Missing meta description",
                    description=(
                        "No <meta name=\"description\"> tag was found. Meta "
                        "descriptions appear as the snippet in search results and "
                        "significantly affect click-through rates."
                    ),
                    url=url,
                    element_selector='meta[name="description"]',
                    fix_snippet='<meta name="description" content="A concise summary of the page content.">',
                    estimated_fix_minutes=5,
                ),
            )
            return findings

        length = len(desc)
        if length < DESC_MIN:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.LOW,
                    title=f"Meta description too short ({length} chars)",
                    description=(
                        f"The meta description is only {length} characters. "
                        f"Aim for {DESC_MIN}–{DESC_MAX} characters for optimal "
                        "display in search results."
                    ),
                    url=url,
                    element_selector='meta[name="description"]',
                    estimated_fix_minutes=5,
                    metadata={"description": desc, "length": length},
                ),
            )
        elif length > DESC_MAX:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.LOW,
                    title=f"Meta description too long ({length} chars)",
                    description=(
                        f"The meta description is {length} characters and will be "
                        f"truncated. Keep it under {DESC_MAX} characters."
                    ),
                    url=url,
                    element_selector='meta[name="description"]',
                    estimated_fix_minutes=5,
                    metadata={"description": desc, "length": length},
                ),
            )

        return findings

    # ================================================================== #
    # H1
    # ================================================================== #

    async def _check_h1(self, page: Page, url: str) -> list[Finding]:
        findings: list[Finding] = []
        try:
            h1_elements = await page.query_selector_all("h1")
        except Exception:
            h1_elements = []

        if not h1_elements:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title="Missing H1 heading",
                    description=(
                        "The page has no <h1> element. Every page should have "
                        "exactly one H1 that summarizes the page content for "
                        "search engines and screen readers."
                    ),
                    url=url,
                    element_selector="h1",
                    fix_snippet="<h1>Your Primary Page Heading</h1>",
                    estimated_fix_minutes=5,
                ),
            )
        elif len(h1_elements) > 1:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"Multiple H1 headings ({len(h1_elements)} found)",
                    description=(
                        f"The page contains {len(h1_elements)} <h1> elements. "
                        "Best practice is to have exactly one H1 per page to "
                        "clearly signal the primary topic to search engines."
                    ),
                    url=url,
                    element_selector="h1",
                    estimated_fix_minutes=10,
                    metadata={"h1_count": len(h1_elements)},
                ),
            )

        return findings

    # ================================================================== #
    # Heading hierarchy
    # ================================================================== #

    async def _check_heading_hierarchy(self, page: Page, url: str) -> list[Finding]:
        findings: list[Finding] = []
        try:
            headings = await page.evaluate("""
                () => {
                    const result = [];
                    document.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(el => {
                        result.push({
                            tag: el.tagName.toLowerCase(),
                            level: parseInt(el.tagName[1]),
                            text: el.textContent.trim().substring(0, 80)
                        });
                    });
                    return result;
                }
            """)
        except Exception as exc:
            logger.debug("Heading hierarchy check failed for %s: %s", url, exc)
            return findings

        if not headings:
            return findings

        # Check for skipped levels (e.g., H1 -> H3 without H2)
        skipped_levels: list[str] = []
        for i in range(1, len(headings)):
            prev_level = headings[i - 1]["level"]
            curr_level = headings[i]["level"]
            # Only flag when going deeper (child heading) and skipping a level
            if curr_level > prev_level and curr_level - prev_level > 1:
                skipped = f"H{prev_level} -> H{curr_level}"
                if skipped not in skipped_levels:
                    skipped_levels.append(skipped)

        if skipped_levels:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.LOW,
                    title="Heading hierarchy has skipped levels",
                    description=(
                        "The heading structure skips levels: "
                        + ", ".join(skipped_levels) + ". "
                        "A proper hierarchy (H1 > H2 > H3 ...) helps search "
                        "engines understand content structure and improves "
                        "accessibility for screen readers."
                    ),
                    url=url,
                    estimated_fix_minutes=15,
                    metadata={
                        "skipped_levels": skipped_levels,
                        "headings": headings[:20],  # Limit metadata size
                    },
                ),
            )

        # First heading should be H1 — but only flag if the page has NO H1 at all.
        # Many sites have H2 in the header (site name) and H1 in the main content.
        has_any_h1 = any(h["level"] == 1 for h in headings)
        if headings[0]["level"] != 1 and not has_any_h1:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"First heading is H{headings[0]['level']}, not H1",
                    description=(
                        f"The first heading on the page is an <h{headings[0]['level']}> "
                        "and no <h1> exists anywhere on the page. The document "
                        "should have at least one H1 heading."
                    ),
                    url=url,
                    element_selector=headings[0]["tag"],
                    estimated_fix_minutes=5,
                ),
            )

        return findings

    # ================================================================== #
    # Open Graph
    # ================================================================== #

    async def _check_open_graph(self, page: Page, url: str) -> list[Finding]:
        findings: list[Finding] = []
        try:
            og_tags: dict[str, str] = await page.evaluate("""
                () => {
                    const tags = {};
                    document.querySelectorAll('meta[property^="og:"]').forEach(el => {
                        tags[el.getAttribute('property')] = el.getAttribute('content') || '';
                    });
                    return tags;
                }
            """)
        except Exception as exc:
            logger.debug("Open Graph check failed for %s: %s", url, exc)
            return findings

        missing_og: list[str] = []
        for tag in OG_REQUIRED:
            if tag not in og_tags or not og_tags[tag].strip():
                missing_og.append(tag)

        if missing_og:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"Missing Open Graph tag(s): {', '.join(missing_og)}",
                    description=(
                        "Open Graph tags control how the page appears when shared "
                        "on social media platforms like Facebook, LinkedIn, and "
                        "Twitter. Missing tags: " + ", ".join(missing_og) + "."
                    ),
                    url=url,
                    element_selector='meta[property^="og:"]',
                    fix_snippet=(
                        '<meta property="og:title" content="Page Title">\n'
                        '<meta property="og:description" content="Page description.">\n'
                        '<meta property="og:image" content="https://example.com/image.jpg">'
                    ),
                    estimated_fix_minutes=10,
                    metadata={"missing_tags": missing_og, "present_tags": list(og_tags.keys())},
                ),
            )

        return findings

    # ================================================================== #
    # Canonical URL
    # ================================================================== #

    async def _check_canonical(self, page: Page, url: str) -> list[Finding]:
        findings: list[Finding] = []
        try:
            canonical_el = await page.query_selector('link[rel="canonical"]')
        except Exception:
            canonical_el = None

        if not canonical_el:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title="Missing canonical URL",
                    description=(
                        "No <link rel=\"canonical\"> tag was found. Canonical tags "
                        "prevent duplicate-content issues by telling search engines "
                        "which version of a URL is the preferred one."
                    ),
                    url=url,
                    element_selector='link[rel="canonical"]',
                    fix_snippet=f'<link rel="canonical" href="{url}">',
                    estimated_fix_minutes=5,
                ),
            )
        else:
            href = await canonical_el.get_attribute("href")
            if not href or not href.strip():
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title="Canonical URL is empty",
                        description=(
                            "A <link rel=\"canonical\"> tag exists but has an "
                            "empty href attribute."
                        ),
                        url=url,
                        element_selector='link[rel="canonical"]',
                        fix_snippet=f'<link rel="canonical" href="{url}">',
                        estimated_fix_minutes=5,
                    ),
                )
            elif href.strip() != url:
                # Not necessarily a bug, but worth noting if it points elsewhere
                canonical_parsed = urlparse(href.strip())
                url_parsed = urlparse(url)
                # Flag if canonical points to a completely different domain
                if canonical_parsed.netloc and canonical_parsed.netloc != url_parsed.netloc:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.HIGH,
                            title="Canonical URL points to a different domain",
                            description=(
                                f"The canonical tag points to {href.strip()} "
                                f"which is on a different domain than {url}. This "
                                "could cause search engines to de-index this page."
                            ),
                            url=url,
                            element_selector='link[rel="canonical"]',
                            estimated_fix_minutes=5,
                            metadata={"canonical_href": href.strip()},
                        ),
                    )

        return findings

    # ================================================================== #
    # Schema.org Structured Data (JSON-LD)
    # ================================================================== #

    async def _check_structured_data(self, page: Page, url: str) -> list[Finding]:
        findings: list[Finding] = []
        try:
            ld_json_scripts: list[str] = await page.evaluate("""
                () => {
                    const scripts = document.querySelectorAll(
                        'script[type="application/ld+json"]'
                    );
                    return Array.from(scripts).map(s => s.textContent || '');
                }
            """)
        except Exception as exc:
            logger.debug("Structured data check failed for %s: %s", url, exc)
            return findings

        if not ld_json_scripts:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.LOW,
                    title="No Schema.org structured data found",
                    description=(
                        "The page has no JSON-LD structured data. Adding "
                        "Schema.org markup helps search engines understand page "
                        "content and can enable rich snippets in results."
                    ),
                    url=url,
                    element_selector='script[type="application/ld+json"]',
                    fix_snippet=(
                        '<script type="application/ld+json">\n'
                        '{\n'
                        '  "@context": "https://schema.org",\n'
                        '  "@type": "WebPage",\n'
                        '  "name": "Page Title",\n'
                        '  "description": "Page description"\n'
                        '}\n'
                        '</script>'
                    ),
                    estimated_fix_minutes=20,
                ),
            )
            return findings

        # Validate that each script block is valid JSON
        for i, script_content in enumerate(ld_json_scripts):
            script_content = script_content.strip()
            if not script_content:
                continue
            try:
                data = json.loads(script_content)
                # Check that @context and @type are present
                if isinstance(data, dict):
                    if "@context" not in data:
                        findings.append(
                            Finding(
                                domain=self.domain,
                                severity=Severity.LOW,
                                title=f"Structured data block #{i + 1} missing @context",
                                description=(
                                    "A JSON-LD block is missing the required "
                                    "@context field. This should typically be "
                                    '"https://schema.org".'
                                ),
                                url=url,
                                element_selector='script[type="application/ld+json"]',
                                estimated_fix_minutes=5,
                            ),
                        )
                    if "@type" not in data:
                        findings.append(
                            Finding(
                                domain=self.domain,
                                severity=Severity.LOW,
                                title=f"Structured data block #{i + 1} missing @type",
                                description=(
                                    "A JSON-LD block is missing the @type field. "
                                    "Without it, search engines cannot determine "
                                    "the entity type."
                                ),
                                url=url,
                                element_selector='script[type="application/ld+json"]',
                                estimated_fix_minutes=5,
                            ),
                        )
            except json.JSONDecodeError as exc:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title=f"Invalid JSON in structured data block #{i + 1}",
                        description=(
                            f"JSON-LD block #{i + 1} contains invalid JSON: {exc}. "
                            "Search engines will ignore malformed structured data."
                        ),
                        url=url,
                        element_selector='script[type="application/ld+json"]',
                        estimated_fix_minutes=10,
                        metadata={"error": str(exc)},
                    ),
                )

        return findings

    # ================================================================== #
    # Global: sitemap.xml
    # ================================================================== #

    async def _check_sitemap(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        base_url = ctx.config.target_url
        sitemap_url = base_url.rstrip("/") + "/sitemap.xml"

        # Use a search-engine User-Agent to avoid bot protection (Vercel, Cloudflare)
        _CRAWLER_HEADERS = {
            "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "Accept": "application/xml, text/xml, text/plain, */*",
        }

        try:
            resp = await ctx.http_client.get(
                sitemap_url,
                follow_redirects=True,
                timeout=HTTP_TIMEOUT_S,
                headers=_CRAWLER_HEADERS,
            )
            # Retry with standard browser UA if bot protection blocks Googlebot
            if resp.status_code == 403:
                resp = await ctx.http_client.get(
                    sitemap_url,
                    follow_redirects=True,
                    timeout=HTTP_TIMEOUT_S,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "application/xml, text/xml, */*",
                    },
                )
            if resp.status_code != 200:
                # 403 from bot protection is likely not a real missing sitemap
                severity = Severity.LOW if resp.status_code == 403 else Severity.MEDIUM
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=severity,
                        title="sitemap.xml not accessible",
                        description=(
                            f"Requesting {sitemap_url} returned HTTP "
                            f"{resp.status_code}. "
                            + ("This may be caused by bot protection (Vercel/Cloudflare). " if resp.status_code == 403 else "")
                            + "A sitemap helps search engines "
                            "discover and index all pages on the site."
                        ),
                        url=sitemap_url,
                        fix_snippet=(
                            "# Generate a sitemap and place it at /sitemap.xml\n"
                            "# For frameworks, use a sitemap plugin:\n"
                            "#   Next.js: next-sitemap\n"
                            "#   Django: django.contrib.sitemaps"
                        ),
                        estimated_fix_minutes=30,
                        metadata={"status_code": resp.status_code},
                    ),
                )
            else:
                # Basic validation: should contain XML/sitemap namespace
                content = resp.text[:2000]
                if "<urlset" not in content and "<sitemapindex" not in content:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.MEDIUM,
                            title="sitemap.xml does not appear to be a valid sitemap",
                            description=(
                                "The file at /sitemap.xml returned HTTP 200 but "
                                "does not contain a valid <urlset> or "
                                "<sitemapindex> element."
                            ),
                            url=sitemap_url,
                            estimated_fix_minutes=20,
                        ),
                    )
        except Exception as exc:
            logger.debug("Sitemap check failed: %s", exc)
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title="Unable to fetch sitemap.xml",
                    description=(
                        f"Could not retrieve {sitemap_url}: {exc}. "
                        "Ensure a sitemap is available for search-engine crawlers."
                    ),
                    url=sitemap_url,
                    estimated_fix_minutes=30,
                    metadata={"error": str(exc)},
                ),
            )

        return findings

    # ================================================================== #
    # Global: robots.txt
    # ================================================================== #

    async def _check_robots_txt(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        base_url = ctx.config.target_url
        robots_url = base_url.rstrip("/") + "/robots.txt"

        _CRAWLER_HEADERS = {
            "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "Accept": "text/plain, */*",
        }

        try:
            resp = await ctx.http_client.get(
                robots_url,
                follow_redirects=True,
                timeout=HTTP_TIMEOUT_S,
                headers=_CRAWLER_HEADERS,
            )
            if resp.status_code == 403:
                resp = await ctx.http_client.get(
                    robots_url,
                    follow_redirects=True,
                    timeout=HTTP_TIMEOUT_S,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "text/plain, */*",
                    },
                )
            if resp.status_code != 200:
                severity = Severity.LOW if resp.status_code == 403 else Severity.MEDIUM
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=severity,
                        title="robots.txt not accessible",
                        description=(
                            f"Requesting {robots_url} returned HTTP "
                            f"{resp.status_code}. "
                            + ("This may be caused by bot protection. " if resp.status_code == 403 else "")
                            + "A robots.txt file guides "
                            "search-engine crawlers on which pages to index."
                        ),
                        url=robots_url,
                        fix_snippet=(
                            "# Basic robots.txt\n"
                            "User-agent: *\n"
                            "Allow: /\n"
                            "Sitemap: " + base_url + "/sitemap.xml"
                        ),
                        estimated_fix_minutes=10,
                        metadata={"status_code": resp.status_code},
                    ),
                )
            else:
                content = resp.text.lower()
                # Warn if all crawling is disallowed
                if "disallow: /" in content and "allow:" not in content:
                    # Check it's a blanket disallow (not just a specific path)
                    lines = [
                        line.strip()
                        for line in resp.text.splitlines()
                        if line.strip().lower().startswith("disallow")
                    ]
                    blanket_block = any(
                        re.match(r"^disallow:\s*/\s*$", line, re.IGNORECASE)
                        for line in lines
                    )
                    if blanket_block:
                        findings.append(
                            Finding(
                                domain=self.domain,
                                severity=Severity.HIGH,
                                title="robots.txt blocks all crawlers",
                                description=(
                                    "The robots.txt file contains 'Disallow: /' "
                                    "which blocks all search-engine crawlers from "
                                    "indexing the site. If this is unintentional, "
                                    "the site will not appear in search results."
                                ),
                                url=robots_url,
                                estimated_fix_minutes=5,
                            ),
                        )

                # Check for Sitemap directive
                if "sitemap:" not in content:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.LOW,
                            title="robots.txt missing Sitemap directive",
                            description=(
                                "The robots.txt file does not include a Sitemap "
                                "directive. Adding one helps search engines find "
                                "the sitemap faster."
                            ),
                            url=robots_url,
                            fix_snippet=f"Sitemap: {base_url}/sitemap.xml",
                            estimated_fix_minutes=5,
                        ),
                    )
        except Exception as exc:
            logger.debug("robots.txt check failed: %s", exc)

        return findings

    # ================================================================== #
    # Global: duplicate titles
    # ================================================================== #

    def _check_duplicate_titles(self) -> list[Finding]:
        findings: list[Finding] = []
        for title, urls in self._title_map.items():
            if len(urls) > 1:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title=f"Duplicate page title across {len(urls)} pages",
                        description=(
                            f"The title \"{title}\" is used on {len(urls)} pages: "
                            + ", ".join(urls[:5])
                            + (f" (and {len(urls) - 5} more)" if len(urls) > 5 else "")
                            + ". Each page should have a unique title to help "
                            "search engines distinguish between pages."
                        ),
                        url=urls[0],
                        estimated_fix_minutes=10,
                        metadata={"title": title, "urls": urls},
                    ),
                )
        return findings

    # ================================================================== #
    # Global: duplicate descriptions
    # ================================================================== #

    def _check_duplicate_descriptions(self) -> list[Finding]:
        findings: list[Finding] = []
        for desc, urls in self._desc_map.items():
            if len(urls) > 1:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.LOW,
                        title=f"Duplicate meta description across {len(urls)} pages",
                        description=(
                            f"The same meta description is used on {len(urls)} "
                            "pages: "
                            + ", ".join(urls[:5])
                            + (f" (and {len(urls) - 5} more)" if len(urls) > 5 else "")
                            + ". Unique descriptions improve search-result snippets."
                        ),
                        url=urls[0],
                        estimated_fix_minutes=10,
                        metadata={"description": desc[:200], "urls": urls},
                    ),
                )
        return findings
