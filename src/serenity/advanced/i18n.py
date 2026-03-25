"""Internationalization stress testing — pseudo-localization, RTL, edge-case data."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Any

from serenity.constants import Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

    from serenity.core.state import ScanContext

logger = logging.getLogger("serenity.advanced.i18n")

_MAX_SAMPLE_PAGES = 3

# Edge-case input values for form fields.
_EDGE_CASE_INPUTS: list[dict[str, Any]] = [
    {"value": "Null", "label": "literal 'Null' string"},
    {"value": "null", "label": "lowercase 'null'"},
    {"value": "undefined", "label": "literal 'undefined'"},
    {"value": "None", "label": "Python None string"},
    {"value": "O'Brien", "label": "apostrophe in name (SQL injection vector)"},
    {"value": "O\"Connor", "label": "double quote in name"},
    {"value": "<script>alert(1)</script>", "label": "XSS test string"},
    {"value": "Robert'); DROP TABLE users;--", "label": "SQL injection test"},
    {"value": "\U0001F600\U0001F389\U0001F30D\U0001F680", "label": "emoji sequence"},
    {"value": "\U0001F468\u200D\U0001F469\u200D\U0001F467\u200D\U0001F466", "label": "complex emoji (family)"},
    {"value": "a" * 500, "label": "very long string (500 chars)"},
    {"value": "\u0645\u0631\u062D\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645", "label": "Arabic text"},
    {"value": "\u3053\u3093\u306B\u3061\u306F\u4E16\u754C", "label": "Japanese text"},
    {"value": "\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439 \u043C\u0438\u0440", "label": "Russian text"},
    {"value": "test\u0000value", "label": "null byte in string"},
    {"value": "   ", "label": "whitespace-only input"},
    {"value": "", "label": "empty string"},
]


class I18nTester:
    """Internationalization and edge-case data stress testing."""

    async def run(self, ctx: ScanContext) -> list[Finding]:
        logger.info("Starting i18n / edge-case testing")
        findings: list[Finding] = []

        urls = _sample_urls(ctx)
        if not urls:
            logger.info("No URLs available for i18n testing")
            return findings

        try:
            for url in urls:
                findings.extend(await self._test_text_expansion(ctx, url))
                findings.extend(await self._test_rtl(ctx, url))

            # Form edge-case testing on all URLs.
            page = await ctx.page_pool.acquire()
            try:
                for url in urls:
                    findings.extend(await self._test_edge_case_inputs(page, url))
            finally:
                await ctx.page_pool.release(page)

        except Exception:
            logger.exception("I18n testing failed")

        logger.info("I18n testing complete: %d findings", len(findings))
        return findings

    # ------------------------------------------------------------------
    # Test 1: Pseudo-localization / Text Expansion
    # ------------------------------------------------------------------

    async def _test_text_expansion(
        self, ctx: ScanContext, url: str
    ) -> list[Finding]:
        """Inject CSS to simulate 40% text expansion and detect layout breaks."""
        findings: list[Finding] = []
        test_ctx: BrowserContext | None = None
        try:
            test_ctx = await ctx.page_pool.new_context(
                viewport={"width": 1280, "height": 900},
                ignore_https_errors=True,
            )
            page = await test_ctx.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(1.0)

            # Capture baseline layout metrics.
            baseline = await self._capture_layout_metrics(page)

            # Inject CSS that simulates text expansion (word-spacing + letter-spacing).
            await page.add_style_tag(content="""
                * {
                    word-spacing: 0.15em !important;
                    letter-spacing: 0.05em !important;
                }
                /* Also stretch inline text nodes via font-size-adjust */
                body {
                    font-size-adjust: none;
                }
                p, span, a, li, td, th, label, button, h1, h2, h3, h4, h5, h6,
                input, textarea, select, option {
                    word-spacing: 0.2em !important;
                    letter-spacing: 0.06em !important;
                }
            """)

            await asyncio.sleep(1.0)

            # Capture expanded layout metrics.
            expanded = await self._capture_layout_metrics(page)

            # Compare for overflow issues.
            overflow_issues = self._compare_layouts(baseline, expanded)

            if overflow_issues:
                sample = overflow_issues[:5]
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.MEDIUM,
                        title="Layout breaks with expanded text (i18n simulation)",
                        description=(
                            f"Simulating ~40% text expansion at {url} caused "
                            f"{len(overflow_issues)} element(s) to overflow or break "
                            f"layout: {'; '.join(sample)}. "
                            "When translating to languages like German, French, or "
                            "Finnish, text can expand 30-50%. Layouts must accommodate "
                            "this expansion without breaking."
                        ),
                        url=url,
                        metadata={
                            "type": "i18n_text_expansion",
                            "overflow_count": len(overflow_issues),
                            "issues": overflow_issues[:10],
                        },
                        fix_snippet=(
                            "/* Allow text containers to expand: */\n"
                            ".button, .label, .nav-item {\n"
                            "  white-space: normal; /* not nowrap */\n"
                            "  overflow-wrap: break-word;\n"
                            "  min-width: 0;\n"
                            "}"
                        ),
                    )
                )

        except Exception:
            logger.debug("Text expansion test failed for %s", url, exc_info=True)
        finally:
            if test_ctx:
                try:
                    await test_ctx.close()
                except Exception:
                    pass

        return findings

    async def _capture_layout_metrics(self, page: Page) -> list[dict[str, Any]]:
        """Capture bounding boxes and overflow state of key elements."""
        try:
            return await page.evaluate("""() => {
                const elements = [];
                const selectors = [
                    'button', 'a', 'nav', 'header', '.nav', '.menu', '.sidebar',
                    '.card', '.badge', '.label', '.tag', '.chip',
                    'h1', 'h2', 'h3', 'p', 'li', 'td', 'th'
                ];
                const seen = new Set();

                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of Array.from(els).slice(0, 20)) {
                        if (seen.has(el)) continue;
                        seen.add(el);

                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        const isOverflowing = el.scrollWidth > el.clientWidth
                                           || el.scrollHeight > el.clientHeight;
                        const isClipped = style.overflow === 'hidden'
                                       && (el.scrollWidth > el.clientWidth
                                           || el.scrollHeight > el.clientHeight);

                        elements.push({
                            tag: el.tagName.toLowerCase(),
                            selector: _getSelector(el),
                            text: (el.innerText || '').slice(0, 50),
                            width: rect.width,
                            height: rect.height,
                            left: rect.left,
                            top: rect.top,
                            isOverflowing: isOverflowing,
                            isClipped: isClipped,
                            overflowX: style.overflowX,
                            overflowY: style.overflowY,
                        });
                    }
                }

                function _getSelector(el) {
                    if (el.id) return '#' + el.id;
                    let sel = el.tagName.toLowerCase();
                    if (el.className && typeof el.className === 'string') {
                        sel += '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.');
                    }
                    return sel;
                }

                return elements;
            }""")
        except Exception:
            return []

    def _compare_layouts(
        self,
        baseline: list[dict[str, Any]],
        expanded: list[dict[str, Any]],
    ) -> list[str]:
        """Compare baseline and expanded layouts to find overflow issues."""
        issues: list[str] = []

        # Build maps by selector for comparison.
        baseline_map = {e["selector"]: e for e in baseline}
        expanded_map = {e["selector"]: e for e in expanded}

        for selector, exp in expanded_map.items():
            base = baseline_map.get(selector)

            # New overflow introduced.
            if exp.get("isOverflowing") and (not base or not base.get("isOverflowing")):
                text_preview = exp.get("text", "")[:30]
                issues.append(
                    f"{selector} overflows after expansion "
                    f"(text: '{text_preview}')"
                )
                continue

            # New clipping introduced.
            if exp.get("isClipped") and (not base or not base.get("isClipped")):
                issues.append(f"{selector} text now clipped after expansion")
                continue

            # Element pushed off-screen.
            if base and exp.get("left", 0) + exp.get("width", 0) > 1300:
                if base.get("left", 0) + base.get("width", 0) <= 1300:
                    issues.append(
                        f"{selector} pushed off-screen after expansion"
                    )

        return issues

    # ------------------------------------------------------------------
    # Test 2: RTL Layout
    # ------------------------------------------------------------------

    # Languages that use RTL writing direction
    _RTL_LANGS = {"ar", "he", "fa", "ur", "yi", "ps", "sd", "ckb", "dv"}

    async def _test_rtl(
        self, ctx: ScanContext, url: str
    ) -> list[Finding]:
        """Inject dir='rtl' on the html element and check for layout issues.

        Skips RTL testing if the page language is LTR-only (e.g. pt-BR, en, es).
        RTL testing is only relevant for sites that support Arabic, Hebrew, etc.
        """
        findings: list[Finding] = []
        test_ctx: BrowserContext | None = None
        try:
            test_ctx = await ctx.page_pool.new_context(
                viewport={"width": 1280, "height": 900},
                ignore_https_errors=True,
            )
            page = await test_ctx.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(1.0)

            # Check if the page language is LTR — skip RTL test if so.
            page_lang = await page.evaluate(
                "() => (document.documentElement.lang || '').split('-')[0].toLowerCase()"
            )
            if page_lang and page_lang not in self._RTL_LANGS:
                logger.debug("Skipping RTL test for %s — page lang '%s' is LTR", url, page_lang)
                return findings

            # Inject RTL direction.
            await page.evaluate("""() => {
                document.documentElement.setAttribute('dir', 'rtl');
                document.documentElement.style.direction = 'rtl';
            }""")

            await asyncio.sleep(1.5)

            # Check for RTL layout issues.
            rtl_issues = await page.evaluate("""() => {
                const issues = [];
                const vw = window.innerWidth;

                // Check for horizontal scrollbar (common RTL bug).
                if (document.documentElement.scrollWidth > vw + 10) {
                    issues.push('Horizontal scrollbar appeared in RTL mode '
                        + '(page width: ' + document.documentElement.scrollWidth
                        + 'px vs viewport: ' + vw + 'px)');
                }

                // Check for elements that overflow to the left.
                const allElements = document.querySelectorAll('*');
                let overflowCount = 0;
                for (const el of allElements) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.left < -50 && rect.width < vw) {
                        overflowCount++;
                        if (overflowCount <= 3) {
                            const tag = el.tagName.toLowerCase();
                            const cls = el.className
                                ? ('.' + String(el.className).trim().split(/\\s+/)[0])
                                : '';
                            issues.push(tag + cls + ' overflows left in RTL '
                                + '(left: ' + Math.round(rect.left) + 'px)');
                        }
                    }
                }
                if (overflowCount > 3) {
                    issues.push('... and ' + (overflowCount - 3) + ' more elements overflow');
                }

                // Check for text-align: left that should be start.
                const textElements = document.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, span');
                let hardcodedLeft = 0;
                for (const el of Array.from(textElements).slice(0, 50)) {
                    const style = getComputedStyle(el);
                    if (style.textAlign === 'left') {
                        hardcodedLeft++;
                    }
                }
                if (hardcodedLeft > 5) {
                    issues.push(hardcodedLeft + ' elements have hardcoded text-align:left '
                        + '(use text-align:start for RTL support)');
                }

                return issues;
            }""")

            if rtl_issues:
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.LOW,
                        title="Layout issues in RTL (right-to-left) mode",
                        description=(
                            f"Switching {url} to RTL direction revealed "
                            f"{len(rtl_issues)} issue(s): "
                            + "; ".join(rtl_issues[:5])
                            + ". RTL support is essential for Arabic, Hebrew, Persian, "
                            "and Urdu users. Use CSS logical properties "
                            "(margin-inline-start) instead of physical ones "
                            "(margin-left)."
                        ),
                        url=url,
                        metadata={
                            "type": "i18n_rtl_issues",
                            "issue_count": len(rtl_issues),
                            "issues": rtl_issues[:10],
                        },
                        fix_snippet=(
                            "/* Use CSS logical properties for RTL support: */\n"
                            ".element {\n"
                            "  margin-inline-start: 1rem; /* instead of margin-left */\n"
                            "  padding-inline-end: 1rem;  /* instead of padding-right */\n"
                            "  text-align: start;         /* instead of text-align: left */\n"
                            "}"
                        ),
                    )
                )

        except Exception:
            logger.debug("RTL test failed for %s", url, exc_info=True)
        finally:
            if test_ctx:
                try:
                    await test_ctx.close()
                except Exception:
                    pass

        return findings

    # ------------------------------------------------------------------
    # Test 3: Edge-case Form Inputs
    # ------------------------------------------------------------------

    async def _test_edge_case_inputs(
        self, page: Page, url: str
    ) -> list[Finding]:
        """Fill form fields with edge-case data and detect failures."""
        findings: list[Finding] = []
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(0.5)

            # Find all visible text inputs and textareas.
            inputs = await page.query_selector_all(
                "input[type='text'], input[type='email'], input[type='search'], "
                "input[type='tel'], input[type='url'], input:not([type]), textarea"
            )

            visible_inputs = []
            for inp in inputs[:20]:
                try:
                    if await inp.is_visible():
                        visible_inputs.append(inp)
                except Exception:
                    continue
                if len(visible_inputs) >= 5:
                    break

            if not visible_inputs:
                return findings

            errors_per_input: list[dict[str, Any]] = []

            for edge_case in _EDGE_CASE_INPUTS:
                value = edge_case["value"]
                label = edge_case["label"]

                for inp in visible_inputs:
                    errors_captured: list[str] = []

                    def on_page_error(error: str) -> None:
                        errors_captured.append(str(error))

                    page.on("pageerror", on_page_error)

                    try:
                        # Clear and fill.
                        await inp.click(timeout=3000)
                        await inp.fill("")
                        await inp.fill(value[:200])  # Cap input length for safety.
                        await asyncio.sleep(0.3)

                        # Check for JS errors.
                        if errors_captured:
                            errors_per_input.append({
                                "value": value[:50],
                                "label": label,
                                "errors": errors_captured[:3],
                            })
                    except Exception:
                        pass
                    finally:
                        page.remove_listener("pageerror", on_page_error)

            if errors_per_input:
                summary = "; ".join(
                    f"'{e['label']}' caused: {e['errors'][0][:80]}"
                    for e in errors_per_input[:5]
                )
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.HIGH,
                        title="JavaScript errors triggered by edge-case form input",
                        description=(
                            f"Entering edge-case data into form fields at {url} "
                            f"triggered {len(errors_per_input)} JavaScript error(s): "
                            f"{summary}. Input handling must be robust against unusual "
                            "characters, null bytes, Unicode, and adversarial strings."
                        ),
                        url=url,
                        metadata={
                            "type": "i18n_edge_case_errors",
                            "error_count": len(errors_per_input),
                            "details": errors_per_input[:10],
                        },
                    )
                )

            # Check if the page crashed / went blank after all inputs.
            try:
                body_text = await page.inner_text("body")
                if len(body_text.strip()) < 20:
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.HIGH,
                            title="Page crashed after edge-case form input",
                            description=(
                                f"After entering various edge-case inputs into forms "
                                f"at {url}, the page content was lost (body text < 20 "
                                "chars). The application does not handle unusual input "
                                "data gracefully."
                            ),
                            url=url,
                            metadata={"type": "i18n_crash_after_input"},
                        )
                    )
            except Exception:
                pass

        except Exception:
            logger.debug("Edge-case input test failed for %s", url, exc_info=True)

        return findings


def _sample_urls(ctx: ScanContext) -> list[str]:
    """Pick a representative sample of discovered URLs."""
    urls = list(ctx.state.discovered_urls)
    if not urls:
        return [ctx.config.target_url]
    random.shuffle(urls)
    return urls[:_MAX_SAMPLE_PAGES]
