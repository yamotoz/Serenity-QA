"""Domain 9 — Content Analysis analyzer.

Detects placeholder text, placeholder images, fake contact information,
outdated copyright years, broken social media links, and hardcoded
secrets in JavaScript.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from serenity.analyzers.base import BaseAnalyzer
from serenity.constants import (
    PLACEHOLDER_PATTERNS,
    PLACEHOLDER_PATTERNS_CASE_SENSITIVE,
    PLACEHOLDER_PATTERNS_NON_LATIN,
    Severity,
)
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import Page

    from serenity.core.state import ScanContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Current year for copyright checks
# ---------------------------------------------------------------------------

CURRENT_YEAR = datetime.now(timezone.utc).year

# ---------------------------------------------------------------------------
# Fake contact info patterns (beyond PLACEHOLDER_PATTERNS)
# ---------------------------------------------------------------------------

_FAKE_CONTACT_PATTERNS: list[tuple[str, str]] = [
    (r"99999[\-.]?9999", "fake phone number 99999-9999"),
    (r"\(00\)\s*0000[\-.]?0000", "fake phone number (00) 0000-0000"),
    (r"test@test\.com", "placeholder email test@test.com"),
    (r"email@example\.com", "placeholder email email@example.com"),
    (r"nome@dominio\.com", "placeholder email nome@dominio.com"),
    (r"user@mail\.com", "placeholder email user@mail.com"),
    (r"foo@bar\.\w+", "placeholder email foo@bar.*"),
    (r"xxx@\w+\.\w+", "placeholder email xxx@*"),
    (r"123\.456\.789[\-.]00", "fake CPF 123.456.789-00"),
    (r"000\.000\.000[\-.]00", "fake CPF 000.000.000-00"),
    (r"12\.345\.678/0001[\-.]00", "fake CNPJ"),
    (r"1234\s*Main\s*St", "fake address 1234 Main St"),
    (r"Rua\s+Exemplo", "fake address Rua Exemplo"),
]

# ---------------------------------------------------------------------------
# Placeholder image indicators
# ---------------------------------------------------------------------------

_PLACEHOLDER_IMAGE_URLS: list[str] = [
    "placeholder.com",
    "via.placeholder.com",
    "placehold.co",
    "placehold.it",
    "placekitten.com",
    "picsum.photos",
    "dummyimage.com",
    "fakeimg.pl",
    "lorempixel.com",
    "placeholderimg",
    "placeholder.png",
    "placeholder.jpg",
    "placeholder.svg",
    "placeholder.webp",
    "default-image",
    "no-image",
    "noimage",
    "image-placeholder",
]

# ---------------------------------------------------------------------------
# Social media domains to check
# ---------------------------------------------------------------------------

_SOCIAL_MEDIA_DOMAINS: list[tuple[str, str]] = [
    (r"facebook\.com", "Facebook"),
    (r"twitter\.com|x\.com", "Twitter/X"),
    (r"instagram\.com", "Instagram"),
    (r"linkedin\.com", "LinkedIn"),
    (r"youtube\.com|youtu\.be", "YouTube"),
    (r"tiktok\.com", "TikTok"),
    (r"pinterest\.com", "Pinterest"),
]

# ---------------------------------------------------------------------------
# Secret / credential patterns in JavaScript
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"""(?:api[_-]?key|apikey)\s*[:=]\s*['"][A-Za-z0-9\-_]{16,}['"]""", "API key"),
    (r"""(?:secret[_-]?key|secretkey)\s*[:=]\s*['"][A-Za-z0-9\-_]{16,}['"]""", "Secret key"),
    (r"""(?:access[_-]?token|accesstoken)\s*[:=]\s*['"][A-Za-z0-9\-_.]{16,}['"]""", "Access token"),
    (r"""(?:auth[_-]?token|authtoken)\s*[:=]\s*['"][A-Za-z0-9\-_.]{16,}['"]""", "Auth token"),
    (r"""(?:password|passwd|pwd)\s*[:=]\s*['"][^'"]{6,}['"]""", "Hardcoded password"),
    (r"""(?:private[_-]?key)\s*[:=]\s*['"][A-Za-z0-9\-_/+=]{16,}['"]""", "Private key"),
    (r"""(?:aws[_-]?access[_-]?key[_-]?id)\s*[:=]\s*['"]AKIA[A-Z0-9]{16}['"]""", "AWS Access Key"),
    (r"""(?:aws[_-]?secret)\s*[:=]\s*['"][A-Za-z0-9/+=]{40}['"]""", "AWS Secret"),
    (r"""sk[_-]live[_-][A-Za-z0-9]{24,}""", "Stripe live secret key"),
    (r"""sk[_-]test[_-][A-Za-z0-9]{24,}""", "Stripe test secret key"),
    (r"""ghp_[A-Za-z0-9]{36,}""", "GitHub personal access token"),
    (r"""AIzaSy[A-Za-z0-9\-_]{33}""", "Google API key"),
]

# ---------------------------------------------------------------------------
# JavaScript helpers for in-browser evaluation
# ---------------------------------------------------------------------------

_JS_GET_PAGE_TEXT = """
() => {
    return document.body ? document.body.innerText : '';
}
"""

_JS_GET_ALL_IMAGES = """
() => {
    const imgs = document.querySelectorAll('img, [style*="background-image"], source[srcset]');
    const results = [];
    for (const el of imgs) {
        let src = '';
        let hasLazyLoading = false;
        if (el.tagName === 'IMG') {
            src = el.src || el.getAttribute('data-src') || '';
            // Detect progressive loading patterns
            hasLazyLoading = !!(
                el.getAttribute('data-src') ||
                el.getAttribute('data-srcset') ||
                el.getAttribute('data-lazy') ||
                el.loading === 'lazy' ||
                el.closest('[data-lazy]') ||
                el.classList.contains('lazyload') ||
                el.classList.contains('lazy')
            );
        } else if (el.tagName === 'SOURCE') {
            src = el.srcset || '';
        } else {
            const bg = window.getComputedStyle(el).backgroundImage;
            const match = bg.match(/url\\(['"]?(.+?)['"]?\\)/);
            src = match ? match[1] : '';
        }
        if (src) {
            const rect = el.getBoundingClientRect();
            results.push({
                src: src.substring(0, 250),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                naturalWidth: el.naturalWidth || 0,
                naturalHeight: el.naturalHeight || 0,
                tag: el.tagName.toLowerCase(),
                hasLazyLoading: hasLazyLoading,
            });
        }
        if (results.length >= 50) break;
    }
    return results;
}
"""

_JS_GET_FOOTER_TEXT = """
() => {
    const footer = document.querySelector('footer')
        || document.querySelector('[role="contentinfo"]')
        || document.querySelector('.footer')
        || document.querySelector('#footer');
    if (footer) return footer.innerText || '';
    // Fallback: last 500 chars of the body text
    const body = document.body ? document.body.innerText : '';
    return body.slice(-500);
}
"""

_JS_GET_SOCIAL_LINKS = """
() => {
    const anchors = document.querySelectorAll('a[href]');
    const links = [];
    for (const a of anchors) {
        const href = a.href || '';
        if (
            href.match(/facebook\\.com|twitter\\.com|x\\.com|instagram\\.com|linkedin\\.com|youtube\\.com|youtu\\.be|tiktok\\.com|pinterest\\.com/i)
        ) {
            links.push({
                href: href,
                text: (a.textContent || '').trim().substring(0, 60),
                selector: a.tagName.toLowerCase() + (a.id ? '#' + a.id : ''),
            });
        }
        if (links.length >= 20) break;
    }
    return links;
}
"""

_JS_GET_INLINE_SCRIPTS = """
() => {
    const scripts = document.querySelectorAll('script:not([src])');
    const contents = [];
    for (const s of scripts) {
        const text = s.textContent || '';
        if (text.trim().length > 0) {
            contents.push(text.substring(0, 5000));
        }
        if (contents.length >= 20) break;
    }
    return contents;
}
"""

_JS_GET_EXTERNAL_SCRIPT_URLS = """
() => {
    const scripts = document.querySelectorAll('script[src]');
    const urls = [];
    for (const s of scripts) {
        const src = s.src || s.getAttribute('src') || '';
        if (src && !src.includes('googleapis.com') && !src.includes('cdnjs.')
            && !src.includes('unpkg.com') && !src.includes('cdn.jsdelivr.net')
            && !src.includes('googletagmanager') && !src.includes('google-analytics')
            && !src.includes('gtag')) {
            urls.push(src);
        }
        if (urls.length >= 10) break;
    }
    return urls;
}
"""


class ContentAnalyzer(BaseAnalyzer):
    """Analyzes page content quality: placeholders, fake data, secrets, etc."""

    domain: str = "content"
    weight: float = 0.20  # Aliased to SEO domain weight in scoring

    async def analyze_page(
        self, ctx: ScanContext, url: str, page: Page
    ) -> list[Finding]:
        findings: list[Finding] = []

        # ---- 1. Placeholder text from PLACEHOLDER_PATTERNS -------------------
        try:
            page_text = await page.evaluate(_JS_GET_PAGE_TEXT)

            # Detect page language to avoid false positives in PT-BR/ES
            page_lang = ""
            try:
                page_lang = (await page.evaluate(
                    "() => document.documentElement.getAttribute('lang') || ''"
                ) or "").lower()
            except Exception:
                pass

            is_latin_lang = page_lang.startswith(("pt", "es", "it", "fr"))

            # Build pattern list: always use base patterns, add non-latin only if not PT/ES
            active_patterns: list[tuple[str, int]] = []  # (pattern, flags)

            # Case-insensitive patterns (safe in all languages)
            for p in PLACEHOLDER_PATTERNS:
                active_patterns.append((p, re.IGNORECASE))

            # Case-sensitive patterns (TODO, FIXME — uppercase only)
            for p in PLACEHOLDER_PATTERNS_CASE_SENSITIVE:
                active_patterns.append((p, 0))  # No IGNORECASE

            # Language-dependent patterns
            if not is_latin_lang:
                for p in PLACEHOLDER_PATTERNS_NON_LATIN:
                    active_patterns.append((p, re.IGNORECASE))

            for pattern, flags in active_patterns:
                matches = re.findall(pattern, page_text, flags)
                if matches:
                    # Deduplicate
                    unique_matches = list(set(m if isinstance(m, str) else m[0] for m in matches))
                    sample = ", ".join(unique_matches[:3])
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.HIGH,
                            title="Placeholder text detected",
                            description=(
                                f"Found {len(matches)} occurrence(s) of placeholder "
                                f"pattern `{pattern}` in page text. "
                                f"Matches: \"{sample}\""
                            ),
                            url=url,
                            estimated_fix_minutes=10,
                            metadata={
                                "pattern": pattern,
                                "match_count": len(matches),
                                "samples": unique_matches[:5],
                            },
                        )
                    )
        except Exception:
            logger.debug("Placeholder text check failed for %s", url, exc_info=True)

        # ---- 2. Placeholder images -------------------------------------------
        try:
            images = await page.evaluate(_JS_GET_ALL_IMAGES)
            for img in images:
                src_lower = img["src"].lower()
                is_placeholder = False
                reason = ""

                # Skip images with lazy loading (data URI is intentional LQIP/blurHash)
                if img.get("hasLazyLoading", False) and src_lower.startswith("data:image"):
                    continue

                # Check against known placeholder URLs
                for indicator in _PLACEHOLDER_IMAGE_URLS:
                    if indicator in src_lower:
                        is_placeholder = True
                        reason = f"URL contains known placeholder pattern: {indicator}"
                        break

                # Check for very small natural dimensions suggesting a 1x1 pixel
                if not is_placeholder and img["tag"] == "img":
                    nw = img.get("naturalWidth", 0)
                    nh = img.get("naturalHeight", 0)
                    if 0 < nw <= 2 and 0 < nh <= 2:
                        is_placeholder = True
                        reason = f"Tracking pixel or placeholder ({nw}x{nh}px natural size)"

                # Check for data URI — but NOT blurHash/LQIP progressive loading
                if not is_placeholder and src_lower.startswith("data:image"):
                    src_full = img["src"]
                    # Detect blurHash / LQIP patterns (intentional progressive loading)
                    is_blur_placeholder = (
                        "fegaussianblur" in src_full.lower()
                        or "filter" in src_full.lower()
                        or img.get("naturalWidth", 0) >= 4  # blurHash thumbnails are 4-32px
                    )
                    # Check if img has data-src or lazy loading (progressive pattern)
                    has_lazy_loading = img.get("tag") == "img"  # Will be checked via JS below
                    if not is_blur_placeholder and len(src_full) < 300:
                        is_placeholder = True
                        reason = "Tiny inline data URI image (likely placeholder)"

                if is_placeholder:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.MEDIUM,
                            title="Placeholder image detected",
                            description=(
                                f"Image appears to be a placeholder. {reason}. "
                                f"Source: {img['src'][:120]}"
                            ),
                            url=url,
                            estimated_fix_minutes=15,
                            metadata={
                                "src": img["src"][:250],
                                "reason": reason,
                                "display_size": f"{img['width']}x{img['height']}",
                            },
                        )
                    )
        except Exception:
            logger.debug("Placeholder image check failed for %s", url, exc_info=True)

        # ---- 3. Fake contact information -------------------------------------
        try:
            page_text = page_text if "page_text" in dir() else await page.evaluate(_JS_GET_PAGE_TEXT)
            for pattern, label in _FAKE_CONTACT_PATTERNS:
                matches = re.findall(pattern, page_text, re.IGNORECASE)
                if matches:
                    sample = ", ".join(list(set(matches))[:3])
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.HIGH,
                            title="Fake contact information detected",
                            description=(
                                f"Found {label} in page content: \"{sample}\". "
                                "This suggests placeholder or test data was not "
                                "replaced before deployment."
                            ),
                            url=url,
                            estimated_fix_minutes=10,
                            metadata={
                                "pattern_label": label,
                                "match_count": len(matches),
                                "samples": list(set(matches))[:5],
                            },
                        )
                    )
        except Exception:
            logger.debug("Fake contact check failed for %s", url, exc_info=True)

        # ---- 4. Outdated copyright year --------------------------------------
        try:
            footer_text = await page.evaluate(_JS_GET_FOOTER_TEXT)
            # Match patterns like "© 2023", "Copyright 2022", "(c) 2021"
            year_matches = re.findall(
                r"(?:©|\(c\)|copyright)\s*(\d{4})",
                footer_text,
                re.IGNORECASE,
            )
            for year_str in year_matches:
                year = int(year_str)
                # Flag years more than 1 year old
                if year < CURRENT_YEAR - 1:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.LOW,
                            title="Outdated copyright year",
                            description=(
                                f"Copyright notice shows year {year}, but the "
                                f"current year is {CURRENT_YEAR}. This makes the "
                                "site appear unmaintained."
                            ),
                            url=url,
                            fix_snippet=f"&copy; {CURRENT_YEAR} Company Name",
                            estimated_fix_minutes=5,
                            metadata={
                                "found_year": year,
                                "current_year": CURRENT_YEAR,
                            },
                        )
                    )
        except Exception:
            logger.debug("Copyright year check failed for %s", url, exc_info=True)

        # ---- 5. Social media links (check for 404) ---------------------------
        try:
            social_links = await page.evaluate(_JS_GET_SOCIAL_LINKS)
            checked_hrefs: set[str] = set()
            for link in social_links:
                href = link["href"]
                if href in checked_hrefs:
                    continue
                checked_hrefs.add(href)

                # Determine which platform this is
                platform = "Social media"
                for pattern, name in _SOCIAL_MEDIA_DOMAINS:
                    if re.search(pattern, href, re.IGNORECASE):
                        platform = name
                        break

                # Check if the link is just a root domain (no real profile)
                parsed_path = re.sub(r"https?://[^/]+/?", "", href).strip("/")
                if not parsed_path or parsed_path in ("#", "/"):
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.MEDIUM,
                            title=f"{platform} link points to root domain",
                            description=(
                                f"Social media link `{href}` points to the platform "
                                "root instead of a specific profile page."
                            ),
                            url=url,
                            element_selector=link.get("selector"),
                            estimated_fix_minutes=5,
                            metadata={"href": href, "platform": platform},
                        )
                    )
                    continue

                # HTTP check for broken links
                try:
                    response = await ctx.http_client.head(
                        href,
                        follow_redirects=True,
                        timeout=10.0,
                    )
                    if response.status_code == 404:
                        findings.append(
                            Finding(
                                domain=self.domain,
                                severity=Severity.HIGH,
                                title=f"Broken {platform} link (404)",
                                description=(
                                    f"Social media link `{href}` returned HTTP 404 "
                                    "Not Found. The profile may have been deleted or "
                                    "the URL is incorrect."
                                ),
                                url=url,
                                element_selector=link.get("selector"),
                                estimated_fix_minutes=10,
                                metadata={
                                    "href": href,
                                    "platform": platform,
                                    "status_code": response.status_code,
                                },
                            )
                        )
                    elif response.status_code >= 400:
                        findings.append(
                            Finding(
                                domain=self.domain,
                                severity=Severity.MEDIUM,
                                title=f"{platform} link returned HTTP {response.status_code}",
                                description=(
                                    f"Social media link `{href}` returned HTTP "
                                    f"{response.status_code}. The link may be "
                                    "broken or inaccessible."
                                ),
                                url=url,
                                element_selector=link.get("selector"),
                                estimated_fix_minutes=10,
                                metadata={
                                    "href": href,
                                    "platform": platform,
                                    "status_code": response.status_code,
                                },
                            )
                        )
                except Exception:
                    logger.debug(
                        "HTTP check for social link %s failed", href, exc_info=True
                    )
        except Exception:
            logger.debug("Social media link check failed for %s", url, exc_info=True)

        # ---- 6. Hardcoded secrets in inline JS --------------------------------
        try:
            inline_scripts = await page.evaluate(_JS_GET_INLINE_SCRIPTS)
            await self._check_scripts_for_secrets(
                findings, inline_scripts, url, source="inline script"
            )
        except Exception:
            logger.debug("Inline script secret check failed for %s", url, exc_info=True)

        # ---- 7. Hardcoded secrets in external JS files -------------------------
        try:
            script_urls = await page.evaluate(_JS_GET_EXTERNAL_SCRIPT_URLS)
            for script_url in script_urls:
                try:
                    resp = await ctx.http_client.get(
                        script_url,
                        follow_redirects=True,
                        timeout=10.0,
                    )
                    if resp.status_code == 200:
                        # Only scan first 50KB to avoid very large bundles
                        content = resp.text[:50_000]
                        await self._check_scripts_for_secrets(
                            findings,
                            [content],
                            url,
                            source=f"external script {script_url[:100]}",
                        )
                except Exception:
                    logger.debug(
                        "Failed to fetch external script %s",
                        script_url,
                        exc_info=True,
                    )
        except Exception:
            logger.debug(
                "External script secret check failed for %s", url, exc_info=True
            )

        return findings

    async def _check_scripts_for_secrets(
        self,
        findings: list[Finding],
        scripts: list[str],
        url: str,
        source: str,
    ) -> None:
        """Scan JavaScript content for hardcoded secrets and credentials."""
        for script_content in scripts:
            for pattern, label in _SECRET_PATTERNS:
                matches = re.findall(pattern, script_content, re.IGNORECASE)
                if matches:
                    # Redact the actual value for safety
                    redacted = [
                        m[:8] + "..." + m[-4:] if len(m) > 16 else m[:4] + "..."
                        for m in matches
                    ]
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.CRITICAL,
                            title=f"Possible {label} exposed in {source}",
                            description=(
                                f"Detected a potential {label} in client-side "
                                f"JavaScript ({source}). Exposed credentials can "
                                "be harvested by attackers. "
                                f"Redacted matches: {', '.join(redacted[:3])}"
                            ),
                            url=url,
                            estimated_fix_minutes=30,
                            metadata={
                                "secret_type": label,
                                "source": source,
                                "match_count": len(matches),
                            },
                        )
                    )
