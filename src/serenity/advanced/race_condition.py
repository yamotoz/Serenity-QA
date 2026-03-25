"""Race condition detection — double submits, mid-flight navigation, rapid input."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

from serenity.constants import Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import Page, Request, Response

    from serenity.core.state import ScanContext

logger = logging.getLogger("serenity.advanced.race_condition")

_MAX_SAMPLE_PAGES = 3


class RaceConditionDetector:
    """Detect race conditions via rapid interaction patterns."""

    async def run(self, ctx: ScanContext) -> list[Finding]:
        logger.info("Starting race condition detection")
        findings: list[Finding] = []

        urls = _sample_urls(ctx)
        if not urls:
            logger.info("No URLs available for race condition detection")
            return findings

        page = await ctx.page_pool.acquire()
        try:
            for url in urls:
                findings.extend(await self._test_double_submit(page, url))
                findings.extend(await self._test_navigation_during_fetch(page, url))
                findings.extend(await self._test_rapid_input(page, url))
        except Exception:
            logger.exception("Race condition detection failed")
        finally:
            await ctx.page_pool.release(page)

        logger.info("Race condition detection complete: %d findings", len(findings))
        return findings

    # ------------------------------------------------------------------
    # Test 1: Double Submit
    # ------------------------------------------------------------------

    async def _test_double_submit(self, page: Page, url: str) -> list[Finding]:
        """Click a submit button twice rapidly and detect double submission."""
        findings: list[Finding] = []
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(0.5)

            # Find forms with submit buttons.
            submit_buttons = await page.query_selector_all(
                "button[type='submit'], input[type='submit'], "
                "form button:not([type='button']):not([type='reset'])"
            )

            for button in submit_buttons[:3]:
                if not await button.is_visible():
                    continue

                # Track outgoing requests triggered by clicking.
                requests_captured: list[Request] = []

                def on_request(req: Request) -> None:
                    if req.resource_type in ("fetch", "xhr", "document"):
                        requests_captured.append(req)

                page.on("request", on_request)

                try:
                    # Click twice with only 50ms gap.
                    await button.click(force=True, no_wait_after=True)
                    await asyncio.sleep(0.05)
                    await button.click(force=True, no_wait_after=True)
                    # Wait for any triggered requests to fire.
                    await asyncio.sleep(2.0)
                except Exception:
                    logger.debug("Double-click test failed on %s", url)
                    continue
                finally:
                    page.remove_listener("request", on_request)

                # If two or more POST/PUT requests were sent, it is a double submit.
                mutation_requests = [
                    r for r in requests_captured
                    if r.method in ("POST", "PUT", "PATCH", "DELETE")
                ]
                if len(mutation_requests) >= 2:
                    endpoints = list({r.url for r in mutation_requests})
                    button_text = await _safe_inner_text(button)
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.HIGH,
                            title="Double form submission detected",
                            description=(
                                f"Clicking the submit button '{button_text}' twice "
                                f"rapidly (50ms apart) at {url} resulted in "
                                f"{len(mutation_requests)} mutation requests being sent "
                                f"to: {', '.join(endpoints[:3])}. The form lacks "
                                "protection against accidental double submission "
                                "(e.g., button disable on click, request deduplication)."
                            ),
                            url=url,
                            metadata={
                                "type": "double_submit",
                                "request_count": len(mutation_requests),
                                "endpoints": endpoints[:5],
                            },
                            fix_snippet=(
                                "// Disable submit button immediately on click:\n"
                                "form.addEventListener('submit', (e) => {\n"
                                "  const btn = form.querySelector('[type=submit]');\n"
                                "  btn.disabled = true;\n"
                                "  btn.textContent = 'Submitting...';\n"
                                "});"
                            ),
                        )
                    )
                    break  # One finding per page is sufficient.

            # Navigate back to the page for subsequent tests.
            await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        except Exception:
            logger.debug("Double-submit test error on %s", url, exc_info=True)

        return findings

    # ------------------------------------------------------------------
    # Test 2: Navigation During Pending Fetch
    # ------------------------------------------------------------------

    async def _test_navigation_during_fetch(
        self, page: Page, url: str
    ) -> list[Finding]:
        """Navigate away while fetch requests are still pending."""
        findings: list[Finding] = []
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(1.0)

            # Track pending requests.
            pending_requests: list[Request] = []
            completed_requests: set[str] = set()
            errors_captured: list[str] = []

            def on_request(req: Request) -> None:
                if req.resource_type in ("fetch", "xhr"):
                    pending_requests.append(req)

            def on_response(resp: Response) -> None:
                completed_requests.add(resp.url)

            def on_page_error(error: str) -> None:
                errors_captured.append(str(error))

            page.on("request", on_request)
            page.on("response", on_response)
            page.on("pageerror", on_page_error)

            try:
                # Trigger interactions that might start fetches.
                await _trigger_page_interactions(page)
                await asyncio.sleep(0.3)

                # Navigate away immediately while requests may be in-flight.
                if pending_requests:
                    await page.goto("about:blank", timeout=10_000)
                    await asyncio.sleep(1.0)

                    # Check for unhandled errors from aborted requests.
                    if errors_captured:
                        findings.append(
                            Finding(
                                domain="advanced",
                                severity=Severity.MEDIUM,
                                title="Unhandled errors during mid-navigation fetch abort",
                                description=(
                                    f"Navigating away from {url} while {len(pending_requests)} "
                                    "fetch requests were pending caused "
                                    f"{len(errors_captured)} unhandled error(s): "
                                    f"'{errors_captured[0][:200]}'. The application "
                                    "should handle AbortError gracefully when requests "
                                    "are cancelled due to navigation."
                                ),
                                url=url,
                                metadata={
                                    "type": "navigation_abort",
                                    "pending_count": len(pending_requests),
                                    "error_count": len(errors_captured),
                                    "sample_error": errors_captured[0][:500],
                                },
                                fix_snippet=(
                                    "// Use AbortController and handle cancellation:\n"
                                    "const controller = new AbortController();\n"
                                    "try {\n"
                                    "  const resp = await fetch(url, { signal: controller.signal });\n"
                                    "} catch (e) {\n"
                                    "  if (e.name !== 'AbortError') throw e;\n"
                                    "}"
                                ),
                            )
                        )
            finally:
                page.remove_listener("request", on_request)
                page.remove_listener("response", on_response)
                page.remove_listener("pageerror", on_page_error)

        except Exception:
            logger.debug("Navigation-during-fetch test error on %s", url, exc_info=True)

        return findings

    # ------------------------------------------------------------------
    # Test 3: Rapid Input on Debounced Fields
    # ------------------------------------------------------------------

    async def _test_rapid_input(self, page: Page, url: str) -> list[Finding]:
        """Type rapidly into search/filter inputs and check for race conditions."""
        findings: list[Finding] = []
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(0.5)

            # Find search/filter-type inputs.
            inputs = await page.query_selector_all(
                "input[type='search'], input[type='text'][name*='search'], "
                "input[type='text'][name*='filter'], input[type='text'][name*='query'], "
                "input[placeholder*='Search' i], input[placeholder*='Filter' i], "
                "input[role='searchbox']"
            )

            for input_el in inputs[:2]:
                if not await input_el.is_visible():
                    continue

                requests_captured: list[Request] = []

                def on_request(req: Request) -> None:
                    if req.resource_type in ("fetch", "xhr"):
                        requests_captured.append(req)

                page.on("request", on_request)

                try:
                    await input_el.click()
                    # Type rapidly character by character with minimal delay.
                    test_string = "testing rapid input"
                    for char in test_string:
                        await input_el.type(char, delay=10)

                    await asyncio.sleep(2.0)
                finally:
                    page.remove_listener("request", on_request)

                # If many requests were sent (no debouncing), report it.
                if len(requests_captured) > 5:
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.MEDIUM,
                            title="Missing input debouncing causes excessive requests",
                            description=(
                                f"Typing '{test_string}' rapidly into a search/filter "
                                f"input at {url} triggered {len(requests_captured)} "
                                "network requests. Without debouncing, each keystroke "
                                "fires a request, wasting bandwidth and potentially "
                                "causing race conditions where older responses arrive "
                                "after newer ones."
                            ),
                            url=url,
                            metadata={
                                "type": "missing_debounce",
                                "request_count": len(requests_captured),
                                "endpoints": list({r.url for r in requests_captured})[:5],
                            },
                            fix_snippet=(
                                "// Debounce search input (300ms):\n"
                                "let timer;\n"
                                "input.addEventListener('input', () => {\n"
                                "  clearTimeout(timer);\n"
                                "  timer = setTimeout(() => search(input.value), 300);\n"
                                "});"
                            ),
                        )
                    )
                    break  # One finding per URL.

        except Exception:
            logger.debug("Rapid input test error on %s", url, exc_info=True)

        return findings


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

async def _safe_inner_text(element: object) -> str:
    """Safely get text content from an element."""
    try:
        text = await element.inner_text()  # type: ignore[union-attr]
        return text.strip()[:100] if text else "(no text)"
    except Exception:
        return "(unknown)"


async def _trigger_page_interactions(page: Page) -> None:
    """Click buttons or links that might trigger fetch requests."""
    try:
        clickables = await page.query_selector_all(
            "button:not([type='submit']), a[href^='#'], [role='tab'], "
            "[data-toggle], [onclick]"
        )
        for el in clickables[:3]:
            try:
                if await el.is_visible():
                    await el.click(force=True, no_wait_after=True, timeout=2000)
                    await asyncio.sleep(0.1)
            except Exception:
                continue
    except Exception:
        pass


def _sample_urls(ctx: ScanContext) -> list[str]:
    """Pick URLs that likely have forms or interactive elements."""
    urls = list(ctx.state.discovered_urls)
    if not urls:
        return [ctx.config.target_url]
    random.shuffle(urls)
    return urls[:_MAX_SAMPLE_PAGES]
