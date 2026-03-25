"""Chaos engineering — controlled failure injection to test UI resilience."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import TYPE_CHECKING, Any

from serenity.constants import Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page, Route

    from serenity.core.state import ScanContext

logger = logging.getLogger("serenity.advanced.chaos")

_MAX_SAMPLE_PAGES = 2
_CHAOS_TIMEOUT_MS = 10_000
_SETTLE_DELAY_S = 1.5


class ChaosEngineer:
    """Inject controlled failures and observe UI resilience."""

    async def run(self, ctx: ScanContext) -> list[Finding]:
        logger.info("Starting chaos engineering tests")
        findings: list[Finding] = []

        urls = _sample_urls(ctx)
        if not urls:
            logger.info("No URLs available for chaos testing")
            return findings

        try:
            for url in urls:
                findings.extend(await self._test_500_errors(ctx, url))
                findings.extend(await self._test_malformed_json(ctx, url))
                findings.extend(await self._test_empty_responses(ctx, url))
                findings.extend(await self._test_timeout(ctx, url))
                findings.extend(await self._test_offline_mode(ctx, url))

            # JS-disabled test runs once on the target URL.
            findings.extend(await self._test_js_disabled(ctx, urls[0]))
        except Exception:
            logger.exception("Chaos engineering tests failed")

        logger.info("Chaos engineering complete: %d findings", len(findings))
        return findings

    # ------------------------------------------------------------------
    # Test 1: HTTP 500 Error Injection
    # ------------------------------------------------------------------

    async def _test_500_errors(
        self, ctx: ScanContext, url: str
    ) -> list[Finding]:
        """Intercept API requests and return 500 errors."""
        findings: list[Finding] = []
        chaos_ctx: BrowserContext | None = None
        try:
            chaos_ctx = await ctx.page_pool.new_context(
                viewport={"width": 1280, "height": 900},
                ignore_https_errors=True,
            )
            page = await chaos_ctx.new_page()

            intercepted_count = 0
            # Extract the target domain to distinguish app API calls from third-party
            from urllib.parse import urlparse as _urlparse
            _target_domain = _urlparse(url).netloc

            async def route_handler(route: Route) -> None:
                nonlocal intercepted_count
                request = route.request
                if request.resource_type in ("fetch", "xhr"):
                    req_domain = _urlparse(request.url).netloc
                    # Only intercept same-domain API calls (not analytics, CDN, etc.)
                    is_same_domain = req_domain == _target_domain
                    is_api_call = "/api" in request.url or is_same_domain
                    # Skip known third-party / infrastructure requests
                    is_infra = any(
                        kw in request.url.lower()
                        for kw in [".well-known", "analytics", "gtag", "gtm",
                                   "google", "facebook", "sentry", "hotjar",
                                   "intercom", "crisp", "hubspot"]
                    )
                    if is_same_domain and not is_infra:
                        intercepted_count += 1
                        await route.fulfill(
                            status=500,
                            content_type="application/json",
                            body=json.dumps({"error": "Internal Server Error"}),
                        )
                    else:
                        await route.continue_()
                else:
                    await route.continue_()

            await page.route("**/*", route_handler)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                await asyncio.sleep(_SETTLE_DELAY_S)
            except Exception:
                pass

            if intercepted_count > 0:
                ui_state = await _assess_ui_state(page)

                if ui_state["is_white_screen"]:
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.CRITICAL,
                            title="White screen when API returns 500 errors",
                            description=(
                                f"Injecting HTTP 500 errors on all API requests at "
                                f"{url} caused a completely blank/white screen. "
                                f"{intercepted_count} API request(s) were intercepted. "
                                "The application lacks error boundaries and does not "
                                "degrade gracefully when backend services fail."
                            ),
                            url=url,
                            metadata={
                                "type": "chaos_500_white_screen",
                                "intercepted_requests": intercepted_count,
                                "ui_state": ui_state,
                            },
                            fix_snippet=(
                                "// React error boundary example:\n"
                                "class ErrorBoundary extends React.Component {\n"
                                "  state = { hasError: false };\n"
                                "  static getDerivedStateFromError() { return { hasError: true }; }\n"
                                "  render() {\n"
                                "    if (this.state.hasError) return <ErrorFallback />;\n"
                                "    return this.props.children;\n"
                                "  }\n"
                                "}"
                            ),
                        )
                    )
                elif not ui_state["has_error_message"]:
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.HIGH,
                            title="No error message shown when API returns 500",
                            description=(
                                f"When all API requests at {url} return 500 errors, "
                                "the page does not display any user-friendly error "
                                f"message. {intercepted_count} request(s) failed. "
                                "Users should see clear feedback when services are "
                                "unavailable."
                            ),
                            url=url,
                            metadata={
                                "type": "chaos_500_no_message",
                                "intercepted_requests": intercepted_count,
                                "ui_state": ui_state,
                            },
                        )
                    )

                if ui_state["is_stuck_loading"]:
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.HIGH,
                            title="UI stuck in loading state after API 500 errors",
                            description=(
                                f"After injecting 500 errors at {url}, the UI remains "
                                "stuck in a loading state (spinner, skeleton, or "
                                "progress indicator visible indefinitely). Loading "
                                "states should have timeouts and fallback to error UI."
                            ),
                            url=url,
                            metadata={
                                "type": "chaos_500_stuck_loading",
                                "ui_state": ui_state,
                            },
                        )
                    )

        except Exception:
            logger.debug("500 error injection test failed for %s", url, exc_info=True)
        finally:
            if chaos_ctx:
                try:
                    await chaos_ctx.close()
                except Exception:
                    pass

        return findings

    # ------------------------------------------------------------------
    # Test 2: Malformed JSON Responses
    # ------------------------------------------------------------------

    async def _test_malformed_json(
        self, ctx: ScanContext, url: str
    ) -> list[Finding]:
        """Return malformed JSON for same-domain API requests."""
        findings: list[Finding] = []
        chaos_ctx: BrowserContext | None = None
        try:
            chaos_ctx = await ctx.page_pool.new_context(
                viewport={"width": 1280, "height": 900},
                ignore_https_errors=True,
            )
            page = await chaos_ctx.new_page()

            intercepted_count = 0
            from urllib.parse import urlparse as _urlparse
            _target_domain = _urlparse(url).netloc

            async def route_handler(route: Route) -> None:
                nonlocal intercepted_count
                request = route.request
                if request.resource_type in ("fetch", "xhr"):
                    req_domain = _urlparse(request.url).netloc
                    is_infra = any(kw in request.url.lower() for kw in [".well-known", "analytics", "gtag", "google", "facebook", "sentry"])
                    if req_domain == _target_domain and not is_infra:
                        intercepted_count += 1
                        await route.fulfill(
                            status=200,
                            content_type="application/json",
                            body="{invalid json: [broken,",
                        )
                    else:
                        await route.continue_()
                else:
                    await route.continue_()

            await page.route("**/*", route_handler)

            errors_captured: list[str] = []

            def on_page_error(error: str) -> None:
                errors_captured.append(str(error))

            page.on("pageerror", on_page_error)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                await asyncio.sleep(_SETTLE_DELAY_S)
            except Exception:
                pass

            page.remove_listener("pageerror", on_page_error)

            if intercepted_count > 0 and errors_captured:
                json_errors = [
                    e for e in errors_captured
                    if "json" in e.lower() or "parse" in e.lower()
                    or "unexpected" in e.lower() or "syntax" in e.lower()
                ]
                if json_errors:
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.HIGH,
                            title="Unhandled JSON parse error in application",
                            description=(
                                f"When API responses at {url} return malformed JSON, "
                                f"the application throws unhandled errors: "
                                f"'{json_errors[0][:200]}'. "
                                f"{intercepted_count} request(s) were intercepted. "
                                "JSON.parse() calls should be wrapped in try/catch "
                                "blocks with appropriate error handling."
                            ),
                            url=url,
                            metadata={
                                "type": "chaos_malformed_json",
                                "error_count": len(json_errors),
                                "sample_error": json_errors[0][:500],
                            },
                            fix_snippet=(
                                "// Safe JSON parsing:\n"
                                "async function safeFetch(url) {\n"
                                "  const resp = await fetch(url);\n"
                                "  const text = await resp.text();\n"
                                "  try {\n"
                                "    return JSON.parse(text);\n"
                                "  } catch (e) {\n"
                                "    console.error('Invalid JSON response:', text.slice(0, 100));\n"
                                "    return null;\n"
                                "  }\n"
                                "}"
                            ),
                        )
                    )

        except Exception:
            logger.debug("Malformed JSON test failed for %s", url, exc_info=True)
        finally:
            if chaos_ctx:
                try:
                    await chaos_ctx.close()
                except Exception:
                    pass

        return findings

    # ------------------------------------------------------------------
    # Test 3: Empty Responses Where Array Expected
    # ------------------------------------------------------------------

    async def _test_empty_responses(
        self, ctx: ScanContext, url: str
    ) -> list[Finding]:
        """Return empty object for same-domain API requests."""
        findings: list[Finding] = []
        chaos_ctx: BrowserContext | None = None
        try:
            chaos_ctx = await ctx.page_pool.new_context(
                viewport={"width": 1280, "height": 900},
                ignore_https_errors=True,
            )
            page = await chaos_ctx.new_page()

            intercepted_count = 0
            from urllib.parse import urlparse as _urlparse
            _target_domain = _urlparse(url).netloc

            async def route_handler(route: Route) -> None:
                nonlocal intercepted_count
                request = route.request
                if request.resource_type in ("fetch", "xhr"):
                    req_domain = _urlparse(request.url).netloc
                    is_infra = any(kw in request.url.lower() for kw in [".well-known", "analytics", "gtag", "google", "facebook", "sentry"])
                    if req_domain == _target_domain and not is_infra:
                        intercepted_count += 1
                        await route.fulfill(
                            status=200,
                            content_type="application/json",
                            body="{}",
                        )
                    else:
                        await route.continue_()
                else:
                    await route.continue_()

            await page.route("**/*", route_handler)

            errors_captured: list[str] = []

            def on_page_error(error: str) -> None:
                errors_captured.append(str(error))

            page.on("pageerror", on_page_error)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                await asyncio.sleep(_SETTLE_DELAY_S)
            except Exception:
                pass

            page.remove_listener("pageerror", on_page_error)

            if intercepted_count > 0 and errors_captured:
                type_errors = [
                    e for e in errors_captured
                    if "typeerror" in e.lower()
                    or "is not a function" in e.lower()
                    or "is not iterable" in e.lower()
                    or "cannot read propert" in e.lower()
                    or "map is not" in e.lower()
                    or "foreach" in e.lower()
                ]
                if type_errors:
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.MEDIUM,
                            title="App crashes when API returns empty data structure",
                            description=(
                                f"When API responses at {url} return empty objects "
                                "instead of expected data structures, the application "
                                f"throws TypeErrors: '{type_errors[0][:200]}'. "
                                "Code should validate response shapes before accessing "
                                "properties (e.g., optional chaining, default values)."
                            ),
                            url=url,
                            metadata={
                                "type": "chaos_empty_response",
                                "error_count": len(type_errors),
                                "sample_error": type_errors[0][:500],
                            },
                            fix_snippet=(
                                "// Defensive data access:\n"
                                "const items = response?.data?.items ?? [];\n"
                                "items.forEach(item => { /* safe */ });"
                            ),
                        )
                    )

        except Exception:
            logger.debug("Empty response test failed for %s", url, exc_info=True)
        finally:
            if chaos_ctx:
                try:
                    await chaos_ctx.close()
                except Exception:
                    pass

        return findings

    # ------------------------------------------------------------------
    # Test 4: Simulated Timeout (30s delay)
    # ------------------------------------------------------------------

    async def _test_timeout(
        self, ctx: ScanContext, url: str
    ) -> list[Finding]:
        """Delay API responses to simulate timeouts (same-domain only)."""
        findings: list[Finding] = []
        chaos_ctx: BrowserContext | None = None
        try:
            chaos_ctx = await ctx.page_pool.new_context(
                viewport={"width": 1280, "height": 900},
                ignore_https_errors=True,
            )
            page = await chaos_ctx.new_page()

            intercepted_count = 0
            from urllib.parse import urlparse as _urlparse
            _target_domain = _urlparse(url).netloc

            async def route_handler(route: Route) -> None:
                nonlocal intercepted_count
                request = route.request
                if request.resource_type in ("fetch", "xhr"):
                    req_domain = _urlparse(request.url).netloc
                    is_infra = any(
                        kw in request.url.lower()
                        for kw in [".well-known", "analytics", "gtag", "gtm",
                                   "google", "facebook", "sentry", "hotjar",
                                   "intercom", "crisp", "hubspot"]
                    )
                    if req_domain == _target_domain and not is_infra:
                        intercepted_count += 1
                        await asyncio.sleep(5)
                        await route.fulfill(
                            status=200,
                            content_type="application/json",
                            body="{}",
                        )
                    else:
                        await route.continue_()
                else:
                    await route.continue_()

            await page.route("**/*", route_handler)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            except Exception:
                pass

            # Wait a few seconds for the UI to react.
            await asyncio.sleep(3.0)

            if intercepted_count > 0:
                ui_state = await _assess_ui_state(page)

                if ui_state["is_stuck_loading"]:
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.MEDIUM,
                            title="UI stuck loading during network timeout",
                            description=(
                                f"When API responses at {url} are delayed (simulated "
                                "30s timeout), the UI remains in a loading state with "
                                "no timeout handling. Users should see a timeout "
                                "message or retry option after a reasonable wait "
                                "(e.g., 10s). The app should use AbortController with "
                                "a timeout signal."
                            ),
                            url=url,
                            metadata={
                                "type": "chaos_timeout_stuck",
                                "intercepted_requests": intercepted_count,
                                "ui_state": ui_state,
                            },
                            fix_snippet=(
                                "// Fetch with timeout using AbortController:\n"
                                "const controller = new AbortController();\n"
                                "const timeoutId = setTimeout(() => controller.abort(), 10000);\n"
                                "try {\n"
                                "  const resp = await fetch(url, { signal: controller.signal });\n"
                                "} catch (e) {\n"
                                "  if (e.name === 'AbortError') showTimeoutMessage();\n"
                                "} finally { clearTimeout(timeoutId); }"
                            ),
                        )
                    )

        except Exception:
            logger.debug("Timeout test failed for %s", url, exc_info=True)
        finally:
            if chaos_ctx:
                try:
                    await chaos_ctx.close()
                except Exception:
                    pass

        return findings

    # ------------------------------------------------------------------
    # Test 5: Offline Mode
    # ------------------------------------------------------------------

    async def _test_offline_mode(
        self, ctx: ScanContext, url: str
    ) -> list[Finding]:
        """Set the browser offline and check for offline indicators.

        Only reports problems if the app registers a Service Worker or has
        a PWA manifest — SSR apps without SW are not expected to work offline.
        """
        findings: list[Finding] = []
        chaos_ctx: BrowserContext | None = None
        try:
            chaos_ctx = await ctx.page_pool.new_context(
                viewport={"width": 1280, "height": 900},
                ignore_https_errors=True,
            )
            page = await chaos_ctx.new_page()

            # First, load the page normally so assets are cached.
            try:
                await page.goto(url, wait_until="networkidle", timeout=20_000)
                await asyncio.sleep(1.0)
            except Exception:
                return findings

            # Check if this app is a PWA / has a Service Worker
            has_sw = await page.evaluate("""() => {
                return !!(navigator.serviceWorker && navigator.serviceWorker.controller);
            }""")
            has_manifest = await page.evaluate("""() => {
                return !!document.querySelector('link[rel="manifest"]');
            }""")
            is_pwa = has_sw or has_manifest

            # If no SW/manifest, this is a standard SSR app — offline is expected to fail
            if not is_pwa:
                logger.debug("Skipping offline test for %s — no Service Worker or PWA manifest", url)
                return findings

            # Go offline.
            await chaos_ctx.set_offline(True)
            await asyncio.sleep(0.5)

            # Try navigating or interacting.
            try:
                await page.reload(timeout=10_000)
            except Exception:
                pass

            await asyncio.sleep(2.0)

            ui_state = await _assess_ui_state(page)

            # Check for offline indicator / messaging.
            has_offline_ui = await page.evaluate("""() => {
                const body = document.body ? document.body.innerText.toLowerCase() : '';
                const offlineTerms = ['offline', 'no connection', 'no internet',
                                     'network error', 'connection lost', 'sem conexão'];
                return offlineTerms.some(term => body.includes(term));
            }""")

            if ui_state["is_white_screen"]:
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.MEDIUM,
                        title="PWA shows white screen in offline mode",
                        description=(
                            f"This app registers a Service Worker or PWA manifest, "
                            f"but going offline after loading {url} and refreshing "
                            "results in a blank white screen. The Service Worker should "
                            "serve a cached offline fallback page."
                        ),
                        url=url,
                        metadata={"type": "chaos_offline_white", "ui_state": ui_state, "has_sw": has_sw, "has_manifest": has_manifest},
                    )
                )
            elif not has_offline_ui:
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.LOW,
                        title="PWA lacks offline indicator",
                        description=(
                            f"This app has a Service Worker/manifest but going offline "
                            f"while on {url} does not display any offline indicator. "
                            "Users may not realize their actions are not being saved."
                        ),
                        url=url,
                        metadata={"type": "chaos_no_offline_indicator", "has_sw": has_sw},
                        fix_snippet=(
                            "// Listen for offline/online events:\n"
                            "window.addEventListener('offline', () => {\n"
                            "  showBanner('You are offline. Changes may not be saved.');\n"
                            "});\n"
                            "window.addEventListener('online', () => hideBanner());"
                        ),
                    )
                )

        except Exception:
            logger.debug("Offline test failed for %s", url, exc_info=True)
        finally:
            if chaos_ctx:
                try:
                    await chaos_ctx.close()
                except Exception:
                    pass

        return findings

    # ------------------------------------------------------------------
    # Test 6: JavaScript Disabled
    # ------------------------------------------------------------------

    async def _test_js_disabled(
        self, ctx: ScanContext, url: str
    ) -> list[Finding]:
        """Load the page with JavaScript disabled and check what renders."""
        findings: list[Finding] = []
        no_js_ctx: BrowserContext | None = None
        try:
            no_js_ctx = await ctx.page_pool.new_context(
                java_script_enabled=False,
                viewport={"width": 1280, "height": 900},
                ignore_https_errors=True,
            )
            page = await no_js_ctx.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                await asyncio.sleep(2.0)
            except Exception:
                pass

            # Check what rendered without JS.
            render_info = await page.evaluate("""() => {
                const body = document.body;
                if (!body) return { empty: true, text: '', noscript: false };
                const text = body.innerText || '';
                const noscript = document.querySelector('noscript');
                const hasContent = text.trim().length > 50;
                return {
                    empty: !hasContent,
                    textLength: text.trim().length,
                    hasNoscript: noscript !== null,
                    noscriptText: noscript ? noscript.innerText.slice(0, 200) : '',
                    title: document.title || '',
                };
            }""")

            if render_info.get("empty") and not render_info.get("hasNoscript"):
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.MEDIUM,
                        title="Page renders nothing with JavaScript disabled",
                        description=(
                            f"With JavaScript disabled, {url} renders an empty or "
                            "near-empty page with no <noscript> fallback. This "
                            "affects: SEO crawlers, users with JS disabled, screen "
                            "readers in some configurations, and RSS/feed parsers. "
                            "Consider server-side rendering (SSR) or at minimum a "
                            "<noscript> message."
                        ),
                        url=url,
                        metadata={
                            "type": "chaos_no_js_empty",
                            "render_info": render_info,
                        },
                        fix_snippet=(
                            "<!-- Add a noscript fallback at minimum: -->\n"
                            "<noscript>\n"
                            "  <p>This application requires JavaScript to run. "
                            "Please enable JavaScript in your browser settings.</p>\n"
                            "</noscript>"
                        ),
                    )
                )
            elif render_info.get("empty") and render_info.get("hasNoscript"):
                logger.debug(
                    "JS-disabled page has noscript fallback: %s",
                    render_info.get("noscriptText", "")[:100],
                )

        except Exception:
            logger.debug("JS-disabled test failed for %s", url, exc_info=True)
        finally:
            if no_js_ctx:
                try:
                    await no_js_ctx.close()
                except Exception:
                    pass

        return findings


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

async def _assess_ui_state(page: Page) -> dict[str, Any]:
    """Evaluate the current state of the UI after chaos injection."""
    try:
        result = await page.evaluate("""() => {
            const body = document.body;
            if (!body) return { is_white_screen: true, has_error_message: false,
                               is_stuck_loading: false, text_length: 0 };

            const text = body.innerText || '';
            const textLower = text.toLowerCase();
            const textLength = text.trim().length;

            // White screen detection.
            const isWhiteScreen = textLength < 20;

            // Error message detection.
            const errorTerms = ['error', 'failed', 'something went wrong',
                               'try again', 'oops', 'unable to load',
                               'could not', 'unavailable', 'problem',
                               'erro', 'falhou', 'tente novamente'];
            const hasErrorMessage = errorTerms.some(t => textLower.includes(t));

            // Stuck loading detection.
            const loadingSelectors = [
                '[class*="spinner"]', '[class*="loading"]', '[class*="skeleton"]',
                '[class*="progress"]', '[role="progressbar"]',
                '[class*="loader"]', '.animate-spin', '.animate-pulse'
            ];
            let isStuckLoading = false;
            for (const sel of loadingSelectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const style = getComputedStyle(el);
                    if (style.display !== 'none' && style.visibility !== 'hidden') {
                        isStuckLoading = true;
                        break;
                    }
                }
            }

            return {
                is_white_screen: isWhiteScreen,
                has_error_message: hasErrorMessage,
                is_stuck_loading: isStuckLoading,
                text_length: textLength,
            };
        }""")
        return result
    except Exception:
        return {
            "is_white_screen": True,
            "has_error_message": False,
            "is_stuck_loading": False,
            "text_length": 0,
        }


def _sample_urls(ctx: ScanContext) -> list[str]:
    """Pick a representative sample of discovered URLs."""
    urls = list(ctx.state.discovered_urls)
    if not urls:
        return [ctx.config.target_url]
    random.shuffle(urls)
    return urls[:_MAX_SAMPLE_PAGES]
