"""Domain 2 — Performance & Core Web Vitals analyzer.

Measures LCP, CLS, TTFB, total page weight, request count, and detects
render-blocking resources, missing lazy-loading, and font-display issues.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from serenity.analyzers.base import BaseAnalyzer
from serenity.constants import Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import Page, Response

    from serenity.core.state import ScanContext

logger = logging.getLogger(__name__)

# ── Thresholds (based on Google Core Web Vitals guidance) ────────────────────

LCP_GOOD_MS = 2500
LCP_POOR_MS = 4000

CLS_GOOD = 0.1
CLS_POOR = 0.25

TTFB_GOOD_MS = 800
TTFB_POOR_MS = 1800

PAGE_SIZE_WARN_BYTES = 3_000_000  # 3 MB
PAGE_SIZE_CRITICAL_BYTES = 8_000_000  # 8 MB

REQUEST_COUNT_WARN = 80
REQUEST_COUNT_CRITICAL = 150

LOAD_TIME_WARN_MS = 5_000
LOAD_TIME_CRITICAL_MS = 10_000


# ── Analyzer ─────────────────────────────────────────────────────────────────


class PerformanceAnalyzer(BaseAnalyzer):
    """Measure Core Web Vitals and detect performance anti-patterns."""

    domain: str = "performance"
    weight: float = 0.25

    async def _safe_evaluate(self, page: Page, script: str, default: Any = 0) -> Any:
        """Wrap page.evaluate with error handling for destroyed execution contexts."""
        try:
            return await page.evaluate(script)
        except Exception:
            logger.debug(
                "performance: page.evaluate failed (possibly navigated away)",
                exc_info=True,
            )
            return default

    async def analyze_page(
        self, ctx: ScanContext, url: str, page: Page
    ) -> list[Finding]:
        findings: list[Finding] = []

        # ── Collect response sizes in parallel with page load ─────────
        total_bytes = 0
        request_count = 0
        response_data: list[dict[str, Any]] = []

        async def _on_response(response: Response) -> None:
            nonlocal total_bytes, request_count
            request_count += 1
            try:
                content_length = response.headers.get("content-length")
                if content_length:
                    total_bytes += int(content_length)
                else:
                    body = await response.body()
                    total_bytes += len(body)
            except Exception:
                pass
            try:
                response_data.append({
                    "url": response.url,
                    "status": response.status,
                    "content_type": response.headers.get("content-type", ""),
                    "size": int(response.headers.get("content-length", 0)),
                })
            except Exception:
                pass

        page.on("response", _on_response)

        try:
            # Wait for the page to be ready before evaluating scripts
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                logger.debug("performance: wait_for_load_state timed out for %s", url)

            # Navigate (re-load) to capture all network traffic
            load_start_ms = await self._safe_evaluate(
                page, "() => performance.now()", default=0
            )
            # We don't re-navigate; instead measure from existing page state.
            # The page was already loaded by the crawler.  Capture timing data.

            # ── TTFB ─────────────────────────────────────────────────
            ttfb_ms = await self._measure_ttfb(page)
            findings.extend(self._evaluate_ttfb(ttfb_ms, url))

            # ── LCP ──────────────────────────────────────────────────
            lcp_ms = await self._measure_lcp(page)
            findings.extend(self._evaluate_lcp(lcp_ms, url))

            # ── CLS ──────────────────────────────────────────────────
            cls_score = await self._measure_cls(page)
            findings.extend(self._evaluate_cls(cls_score, url))

            # ── Total load time ──────────────────────────────────────
            load_time_ms = await self._measure_load_time(page)
            findings.extend(self._evaluate_load_time(load_time_ms, url))

            # Small pause to collect any late responses
            await asyncio.sleep(0.5)

            # ── Page size ────────────────────────────────────────────
            findings.extend(self._evaluate_page_size(total_bytes, url))

            # ── Request count ────────────────────────────────────────
            findings.extend(self._evaluate_request_count(request_count, url))

            # ── Render-blocking resources ────────────────────────────
            blocking_findings = await self._detect_render_blocking(page, url)
            findings.extend(blocking_findings)

            # ── Images without lazy loading ──────────────────────────
            lazy_findings = await self._detect_missing_lazy_loading(page, url)
            findings.extend(lazy_findings)

            # ── Blocking fonts ───────────────────────────────────────
            font_findings = await self._detect_blocking_fonts(page, url)
            findings.extend(font_findings)

            # ── Store metrics in page data ───────────────────────────
            pdata = ctx.state.page_data.get(url)
            if pdata:
                pdata.ttfb_ms = ttfb_ms
                pdata.load_time_ms = load_time_ms
                pdata.page_size_bytes = total_bytes
                pdata.request_count = request_count

            logger.info(
                "performance: %s — LCP=%.0fms  CLS=%.3f  TTFB=%.0fms  "
                "size=%dKB  requests=%d  load=%.0fms",
                url, lcp_ms, cls_score, ttfb_ms,
                total_bytes // 1024, request_count, load_time_ms,
            )

        except Exception:
            logger.warning("performance: analysis failed for %s", url, exc_info=True)
        finally:
            page.remove_listener("response", _on_response)

        return findings

    # ================================================================== #
    # Metric measurement                                                   #
    # ================================================================== #

    async def _measure_lcp(self, page: Page) -> float:
        """Measure Largest Contentful Paint via PerformanceObserver."""
        lcp: float = await self._safe_evaluate(
            page,
            """() => new Promise(resolve => {
                let lcpValue = 0;
                try {
                    const observer = new PerformanceObserver(list => {
                        const entries = list.getEntries();
                        if (entries.length) {
                            lcpValue = entries[entries.length - 1].startTime;
                        }
                    });
                    observer.observe({type: 'largest-contentful-paint', buffered: true});
                    setTimeout(() => { observer.disconnect(); resolve(lcpValue); }, 3000);
                } catch(e) {
                    // Fallback: use load event timing
                    const nav = performance.getEntriesByType('navigation')[0];
                    resolve(nav ? nav.loadEventEnd : 0);
                }
            })""",
            default=0.0,
        )
        return lcp

    async def _measure_cls(self, page: Page) -> float:
        """Measure Cumulative Layout Shift via PerformanceObserver."""
        cls: float = await self._safe_evaluate(
            page,
            """() => new Promise(resolve => {
                let clsValue = 0;
                try {
                    const observer = new PerformanceObserver(list => {
                        for (const entry of list.getEntries()) {
                            if (!entry.hadRecentInput) {
                                clsValue += entry.value;
                            }
                        }
                    });
                    observer.observe({type: 'layout-shift', buffered: true});
                    setTimeout(() => { observer.disconnect(); resolve(clsValue); }, 3000);
                } catch(e) {
                    resolve(0);
                }
            })""",
            default=0.0,
        )
        return cls

    async def _measure_ttfb(self, page: Page) -> float:
        """Measure Time To First Byte from Navigation Timing API."""
        ttfb: float = await self._safe_evaluate(
            page,
            """() => {
                const nav = performance.getEntriesByType('navigation')[0];
                if (nav) return nav.responseStart - nav.requestStart;
                // Fallback to legacy API
                const t = performance.timing;
                if (t) return t.responseStart - t.requestStart;
                return 0;
            }""",
            default=0.0,
        )
        return max(ttfb, 0)

    async def _measure_load_time(self, page: Page) -> float:
        """Measure total page load time from navigation start to load event."""
        load_ms: float = await self._safe_evaluate(
            page,
            """() => {
                const nav = performance.getEntriesByType('navigation')[0];
                if (nav && nav.loadEventEnd > 0) return nav.loadEventEnd;
                const t = performance.timing;
                if (t && t.loadEventEnd > 0) return t.loadEventEnd - t.navigationStart;
                return 0;
            }""",
            default=0.0,
        )
        return max(load_ms, 0)

    # ================================================================== #
    # Threshold evaluation                                                 #
    # ================================================================== #

    def _evaluate_lcp(self, lcp_ms: float, url: str) -> list[Finding]:
        if lcp_ms <= 0:
            return []
        findings: list[Finding] = []
        if lcp_ms > LCP_POOR_MS:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title=f"Poor LCP: {lcp_ms:.0f}ms (threshold: {LCP_POOR_MS}ms)",
                    description=(
                        f"Largest Contentful Paint is {lcp_ms:.0f}ms, well above the "
                        f"{LCP_POOR_MS}ms 'poor' threshold. Users perceive the page as "
                        "slow to render. Optimise the largest visible element (hero image, "
                        "heading block) by preloading critical resources and reducing "
                        "server response time."
                    ),
                    url=url,
                    fix_snippet='<link rel="preload" as="image" href="/hero.webp">',
                    estimated_fix_minutes=30,
                    metadata={"metric": "lcp", "value_ms": lcp_ms},
                )
            )
        elif lcp_ms > LCP_GOOD_MS:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"LCP needs improvement: {lcp_ms:.0f}ms (goal: <{LCP_GOOD_MS}ms)",
                    description=(
                        f"Largest Contentful Paint is {lcp_ms:.0f}ms. While not critically "
                        f"slow, it exceeds the {LCP_GOOD_MS}ms 'good' target. Consider "
                        "optimising images, preloading key resources, and reducing "
                        "render-blocking scripts."
                    ),
                    url=url,
                    estimated_fix_minutes=20,
                    metadata={"metric": "lcp", "value_ms": lcp_ms},
                )
            )
        return findings

    def _evaluate_cls(self, cls_score: float, url: str) -> list[Finding]:
        findings: list[Finding] = []
        if cls_score > CLS_POOR:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title=f"Poor CLS: {cls_score:.3f} (threshold: {CLS_POOR})",
                    description=(
                        f"Cumulative Layout Shift is {cls_score:.3f}, above the "
                        f"{CLS_POOR} 'poor' threshold. Elements are visibly shifting "
                        "after render, causing accidental clicks and a jarring experience. "
                        "Set explicit width/height on images and embeds, and avoid "
                        "injecting content above the fold."
                    ),
                    url=url,
                    fix_snippet='<img src="photo.jpg" width="800" height="600" alt="...">',
                    estimated_fix_minutes=20,
                    metadata={"metric": "cls", "value": cls_score},
                )
            )
        elif cls_score > CLS_GOOD:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"CLS needs improvement: {cls_score:.3f} (goal: <{CLS_GOOD})",
                    description=(
                        f"Cumulative Layout Shift is {cls_score:.3f}. Some layout "
                        "instability detected. Reserve space for dynamic content and "
                        "set dimensions on media elements."
                    ),
                    url=url,
                    estimated_fix_minutes=15,
                    metadata={"metric": "cls", "value": cls_score},
                )
            )
        return findings

    def _evaluate_ttfb(self, ttfb_ms: float, url: str) -> list[Finding]:
        if ttfb_ms <= 0:
            return []
        findings: list[Finding] = []
        if ttfb_ms > TTFB_POOR_MS:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title=f"Poor TTFB: {ttfb_ms:.0f}ms (threshold: {TTFB_POOR_MS}ms)",
                    description=(
                        f"Time To First Byte is {ttfb_ms:.0f}ms. The server is taking "
                        "too long to respond. Investigate server-side caching, database "
                        "query performance, and CDN configuration."
                    ),
                    url=url,
                    estimated_fix_minutes=60,
                    metadata={"metric": "ttfb", "value_ms": ttfb_ms},
                )
            )
        elif ttfb_ms > TTFB_GOOD_MS:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"TTFB needs improvement: {ttfb_ms:.0f}ms (goal: <{TTFB_GOOD_MS}ms)",
                    description=(
                        f"Time To First Byte is {ttfb_ms:.0f}ms. Consider enabling "
                        "server-side caching or using a CDN."
                    ),
                    url=url,
                    estimated_fix_minutes=30,
                    metadata={"metric": "ttfb", "value_ms": ttfb_ms},
                )
            )
        return findings

    def _evaluate_load_time(self, load_ms: float, url: str) -> list[Finding]:
        if load_ms <= 0:
            return []
        findings: list[Finding] = []
        if load_ms > LOAD_TIME_CRITICAL_MS:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title=f"Very slow page load: {load_ms:.0f}ms",
                    description=(
                        f"Total page load time is {load_ms:.0f}ms (>{LOAD_TIME_CRITICAL_MS}ms). "
                        "This severely impacts user experience and SEO rankings."
                    ),
                    url=url,
                    estimated_fix_minutes=45,
                    metadata={"metric": "load_time", "value_ms": load_ms},
                )
            )
        elif load_ms > LOAD_TIME_WARN_MS:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"Slow page load: {load_ms:.0f}ms",
                    description=(
                        f"Total page load time is {load_ms:.0f}ms (>{LOAD_TIME_WARN_MS}ms). "
                        "Consider reducing resource count and size."
                    ),
                    url=url,
                    estimated_fix_minutes=30,
                    metadata={"metric": "load_time", "value_ms": load_ms},
                )
            )
        return findings

    def _evaluate_page_size(self, total_bytes: int, url: str) -> list[Finding]:
        findings: list[Finding] = []
        if total_bytes > PAGE_SIZE_CRITICAL_BYTES:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title=f"Page weight is {total_bytes / 1_000_000:.1f} MB",
                    description=(
                        f"Total transferred size is {total_bytes / 1_000_000:.1f} MB, "
                        f"exceeding the {PAGE_SIZE_CRITICAL_BYTES / 1_000_000:.0f} MB critical "
                        "threshold. This wastes bandwidth, especially on mobile. Compress "
                        "images (use WebP/AVIF), enable gzip/brotli, and remove unused assets."
                    ),
                    url=url,
                    estimated_fix_minutes=30,
                    metadata={"metric": "page_size", "value_bytes": total_bytes},
                )
            )
        elif total_bytes > PAGE_SIZE_WARN_BYTES:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"Page weight is {total_bytes / 1_000_000:.1f} MB",
                    description=(
                        f"Total transferred size is {total_bytes / 1_000_000:.1f} MB. "
                        "Consider optimising images and enabling compression."
                    ),
                    url=url,
                    estimated_fix_minutes=20,
                    metadata={"metric": "page_size", "value_bytes": total_bytes},
                )
            )
        return findings

    def _evaluate_request_count(self, count: int, url: str) -> list[Finding]:
        findings: list[Finding] = []
        if count > REQUEST_COUNT_CRITICAL:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.HIGH,
                    title=f"Excessive HTTP requests: {count}",
                    description=(
                        f"The page made {count} HTTP requests, far above the "
                        f"{REQUEST_COUNT_CRITICAL} critical threshold. Each request adds "
                        "latency. Bundle scripts, use CSS sprites, and remove unnecessary "
                        "third-party resources."
                    ),
                    url=url,
                    estimated_fix_minutes=45,
                    metadata={"metric": "request_count", "value": count},
                )
            )
        elif count > REQUEST_COUNT_WARN:
            findings.append(
                Finding(
                    domain=self.domain,
                    severity=Severity.MEDIUM,
                    title=f"High HTTP request count: {count}",
                    description=(
                        f"The page made {count} HTTP requests. Consider bundling "
                        "resources and deferring non-critical requests."
                    ),
                    url=url,
                    estimated_fix_minutes=30,
                    metadata={"metric": "request_count", "value": count},
                )
            )
        return findings

    # ================================================================== #
    # Anti-pattern detection                                               #
    # ================================================================== #

    async def _detect_render_blocking(self, page: Page, url: str) -> list[Finding]:
        """Find render-blocking stylesheets and scripts in <head>."""
        findings: list[Finding] = []
        try:
            blocking: list[dict[str, Any]] = await self._safe_evaluate(
                page,
                """() => {
                    const results = [];
                    const head = document.head;
                    if (!head) return results;

                    // Stylesheets without media query (or media="all") are blocking
                    head.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
                        const media = link.getAttribute('media');
                        if (!media || media === 'all') {
                            results.push({
                                type: 'stylesheet',
                                href: link.href || link.getAttribute('href') || '',
                                selector: 'link[href="' + (link.getAttribute('href') || '') + '"]',
                            });
                        }
                    });

                    // Scripts without async or defer
                    head.querySelectorAll('script[src]').forEach(script => {
                        const hasAsync = script.hasAttribute('async');
                        const hasDefer = script.hasAttribute('defer');
                        const hasModule = script.getAttribute('type') === 'module';
                        if (!hasAsync && !hasDefer && !hasModule) {
                            results.push({
                                type: 'script',
                                href: script.src || script.getAttribute('src') || '',
                                selector: 'script[src="' + (script.getAttribute('src') || '') + '"]',
                            });
                        }
                    });

                    return results;
                }""",
                default=[],
            )

            blocking_stylesheets = [b for b in blocking if b["type"] == "stylesheet"]
            blocking_scripts = [b for b in blocking if b["type"] == "script"]

            if blocking_stylesheets:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title=f"{len(blocking_stylesheets)} render-blocking stylesheet(s) in <head>",
                        description=(
                            "Stylesheets without a media attribute block rendering until "
                            "they are fully downloaded. Use media queries for non-critical "
                            "CSS or load them asynchronously.\n"
                            "Blocking resources: "
                            + ", ".join(b["href"][:80] for b in blocking_stylesheets[:5])
                        ),
                        url=url,
                        fix_snippet=(
                            '<!-- Load non-critical CSS asynchronously -->\n'
                            '<link rel="preload" href="non-critical.css" as="style" '
                            'onload="this.onload=null;this.rel=\'stylesheet\'">\n'
                            '<noscript><link rel="stylesheet" href="non-critical.css"></noscript>'
                        ),
                        estimated_fix_minutes=20,
                        metadata={
                            "issue_type": "render_blocking_css",
                            "count": len(blocking_stylesheets),
                            "resources": [b["href"] for b in blocking_stylesheets[:10]],
                        },
                    )
                )

            if blocking_scripts:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title=f"{len(blocking_scripts)} render-blocking script(s) in <head>",
                        description=(
                            "Scripts in <head> without async or defer block HTML parsing. "
                            "Add the 'defer' attribute or move scripts to the end of <body>.\n"
                            "Blocking scripts: "
                            + ", ".join(b["href"][:80] for b in blocking_scripts[:5])
                        ),
                        url=url,
                        fix_snippet='<script src="app.js" defer></script>',
                        estimated_fix_minutes=15,
                        metadata={
                            "issue_type": "render_blocking_js",
                            "count": len(blocking_scripts),
                            "resources": [b["href"] for b in blocking_scripts[:10]],
                        },
                    )
                )

        except Exception:
            logger.debug("performance: render-blocking detection failed", exc_info=True)

        return findings

    async def _detect_missing_lazy_loading(self, page: Page, url: str) -> list[Finding]:
        """Find below-the-fold images without loading='lazy'."""
        findings: list[Finding] = []
        try:
            non_lazy: list[dict[str, str]] = await self._safe_evaluate(
                page,
                """() => {
                    const viewportHeight = window.innerHeight;
                    const results = [];
                    document.querySelectorAll('img').forEach(img => {
                        const rect = img.getBoundingClientRect();
                        // Image is below the fold
                        if (rect.top > viewportHeight * 1.5) {
                            const loading = img.getAttribute('loading');
                            if (loading !== 'lazy') {
                                results.push({
                                    src: (img.src || img.getAttribute('src') || '').slice(0, 120),
                                    selector: img.id
                                        ? 'img#' + img.id
                                        : 'img[src="' + (img.getAttribute('src') || '') + '"]',
                                    top: Math.round(rect.top),
                                });
                            }
                        }
                    });
                    return results;
                }""",
                default=[],
            )

            if non_lazy:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title=f"{len(non_lazy)} below-the-fold image(s) without lazy loading",
                        description=(
                            f"{len(non_lazy)} images are positioned below the viewport fold "
                            "but do not use loading=\"lazy\". This forces the browser to "
                            "download them immediately, delaying the initial render.\n"
                            "Examples: " + ", ".join(
                                img["src"][:60] for img in non_lazy[:3]
                            )
                        ),
                        url=url,
                        fix_snippet='<img src="photo.jpg" loading="lazy" alt="...">',
                        estimated_fix_minutes=10,
                        metadata={
                            "issue_type": "missing_lazy_loading",
                            "count": len(non_lazy),
                            "images": non_lazy[:10],
                        },
                    )
                )

        except Exception:
            logger.debug("performance: lazy-loading detection failed", exc_info=True)

        return findings

    async def _detect_blocking_fonts(self, page: Page, url: str) -> list[Finding]:
        """Find @font-face rules that lack font-display: swap."""
        findings: list[Finding] = []
        try:
            blocking_fonts: list[dict[str, str]] = await self._safe_evaluate(
                page,
                """() => {
                    const results = [];
                    for (const sheet of document.styleSheets) {
                        try {
                            const rules = sheet.cssRules || sheet.rules;
                            if (!rules) continue;
                            for (const rule of rules) {
                                if (rule instanceof CSSFontFaceRule) {
                                    const display = rule.style.getPropertyValue('font-display');
                                    if (!display || display === 'auto' || display === 'block') {
                                        const family = rule.style.getPropertyValue('font-family')
                                            || 'unknown';
                                        results.push({
                                            family: family.replace(/['"]/g, '').slice(0, 60),
                                            display: display || '(not set)',
                                        });
                                    }
                                }
                            }
                        } catch(e) {
                            // Cross-origin stylesheet — skip
                        }
                    }
                    return results;
                }""",
                default=[],
            )

            if blocking_fonts:
                families = list({f["family"] for f in blocking_fonts})
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title=f"{len(blocking_fonts)} @font-face rule(s) without font-display: swap",
                        description=(
                            "Custom fonts without font-display: swap cause invisible text "
                            "(FOIT) while the font downloads. Users see a blank area instead "
                            "of fallback text.\n"
                            "Affected font families: " + ", ".join(families[:5])
                        ),
                        url=url,
                        fix_snippet=(
                            "@font-face {\n"
                            '  font-family: "MyFont";\n'
                            '  src: url("myfont.woff2") format("woff2");\n'
                            "  font-display: swap;\n"
                            "}"
                        ),
                        estimated_fix_minutes=10,
                        metadata={
                            "issue_type": "blocking_fonts",
                            "count": len(blocking_fonts),
                            "families": families,
                        },
                    )
                )

        except Exception:
            logger.debug("performance: font detection failed", exc_info=True)

        return findings
