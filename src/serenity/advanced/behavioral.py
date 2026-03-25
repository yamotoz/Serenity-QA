"""Behavioral analysis — simulate human-like browsing to detect bot defenses."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

from serenity.constants import Severity
from serenity.scoring.finding import Finding
from serenity.utils.bezier import add_jitter, generate_bezier_path
from serenity.utils.timing import gaussian_jitter, poisson_delay

if TYPE_CHECKING:
    from playwright.async_api import Page

    from serenity.core.state import ScanContext

logger = logging.getLogger("serenity.advanced.behavioral")

# Selectors that indicate bot detection / CAPTCHA challenge pages.
_CAPTCHA_SELECTORS = [
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='captcha']",
    ".g-recaptcha",
    ".h-captcha",
    "#captcha",
    "[data-captcha]",
    "[class*='captcha']",
    "[id*='captcha']",
    "[class*='challenge']",
]

_BLOCK_INDICATORS = [
    "text=Access Denied",
    "text=403 Forbidden",
    "text=Bot detected",
    "text=Please verify you are a human",
    "text=Checking your browser",
    "text=Just a moment",
    "text=Enable JavaScript and cookies",
]

_HONEYPOT_SELECTORS = [
    "input[style*='display:none']",
    "input[style*='display: none']",
    "input[style*='visibility:hidden']",
    "input[style*='visibility: hidden']",
    "input[tabindex='-1'][style*='position:absolute']",
    ".honeypot",
    "#honeypot",
    "input[name='honeypot']",
    "input[name='hp']",
    "input[autocomplete='off'][style*='opacity:0']",
]

_MAX_SAMPLE_PAGES = 3


class BehavioralAnalyzer:
    """Simulate human-like browsing behavior and detect bot defenses."""

    async def run(self, ctx: ScanContext) -> list[Finding]:
        logger.info("Starting behavioral analysis")
        findings: list[Finding] = []

        urls = _sample_urls(ctx)
        if not urls:
            logger.info("No URLs available for behavioral analysis")
            return findings

        page = await ctx.page_pool.acquire()
        try:
            for url in urls:
                page_findings = await self._test_url(page, url)
                findings.extend(page_findings)
        except Exception:
            logger.exception("Behavioral analysis failed")
        finally:
            await ctx.page_pool.release(page)

        logger.info("Behavioral analysis complete: %d findings", len(findings))
        return findings

    async def _test_url(self, page: Page, url: str) -> list[Finding]:
        """Navigate to a URL with human-like behavior and check for defenses."""
        findings: list[Finding] = []

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            if not response:
                return findings

            # Wait a human-like amount before interacting.
            await asyncio.sleep(poisson_delay(800) / 1000.0)

            # --- Check for CAPTCHA / block page immediately after load ---
            findings.extend(await self._check_captcha(page, url, stage="on_load"))
            findings.extend(await self._check_block_page(page, url, stage="on_load"))

            # --- Perform human-like mouse movement ---
            await self._simulate_human_browsing(page)

            # --- After interaction, re-check for triggered defenses ---
            findings.extend(await self._check_captcha(page, url, stage="after_interaction"))
            findings.extend(await self._check_block_page(page, url, stage="after_interaction"))

            # --- Check for honeypot traps ---
            findings.extend(await self._check_honeypots(page, url))

        except Exception:
            logger.debug("Error testing behavioral on %s", url, exc_info=True)

        return findings

    async def _simulate_human_browsing(self, page: Page) -> None:
        """Move the mouse along Bezier curves, scroll, and click like a human."""
        viewport = page.viewport_size or {"width": 1280, "height": 900}
        vw, vh = viewport["width"], viewport["height"]

        # Perform 3-5 random mouse movements across the page.
        num_movements = random.randint(3, 5)
        current_x, current_y = float(vw // 2), float(vh // 2)

        for _ in range(num_movements):
            target_x = random.uniform(50, vw - 50)
            target_y = random.uniform(50, vh - 50)

            path = generate_bezier_path(
                (current_x, current_y), (target_x, target_y), num_points=30
            )
            path = add_jitter(path, jitter_px=1.5)

            for px, py in path:
                await page.mouse.move(px, py)
                await asyncio.sleep(gaussian_jitter(8, 3) / 1000.0)

            current_x, current_y = target_x, target_y
            await asyncio.sleep(poisson_delay(300) / 1000.0)

        # Scroll down gently.
        scroll_steps = random.randint(2, 4)
        for _ in range(scroll_steps):
            delta = random.randint(100, 350)
            await page.mouse.wheel(0, delta)
            await asyncio.sleep(poisson_delay(400) / 1000.0)

        # Attempt to click a safe, visible link.
        try:
            links = await page.query_selector_all("a[href]:not([target='_blank'])")
            visible_links = []
            for link in links[:20]:
                if await link.is_visible():
                    visible_links.append(link)
                if len(visible_links) >= 5:
                    break

            if visible_links:
                target_link = random.choice(visible_links)
                box = await target_link.bounding_box()
                if box:
                    cx = box["x"] + box["width"] / 2
                    cy = box["y"] + box["height"] / 2
                    path = generate_bezier_path(
                        (current_x, current_y), (cx, cy), num_points=20
                    )
                    path = add_jitter(path, jitter_px=1.0)
                    for px, py in path:
                        await page.mouse.move(px, py)
                        await asyncio.sleep(gaussian_jitter(6, 2) / 1000.0)

                    await asyncio.sleep(poisson_delay(200) / 1000.0)
                    await page.mouse.click(cx, cy)
                    await asyncio.sleep(poisson_delay(500) / 1000.0)
        except Exception:
            logger.debug("Could not click a link during behavioral simulation")

    async def _check_captcha(
        self, page: Page, url: str, *, stage: str
    ) -> list[Finding]:
        findings: list[Finding] = []
        for selector in _CAPTCHA_SELECTORS:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.HIGH,
                            title="CAPTCHA / bot challenge detected",
                            description=(
                                f"A CAPTCHA or bot challenge was detected {stage} at "
                                f"{url}. Selector: {selector}. This may indicate the site "
                                "is flagging automated visitors, which could affect real "
                                "users behind corporate proxies or VPNs."
                            ),
                            url=url,
                            element_selector=selector,
                            metadata={"stage": stage, "type": "captcha"},
                        )
                    )
                    break  # One finding per URL per stage is enough.
            except Exception:
                continue
        return findings

    async def _check_block_page(
        self, page: Page, url: str, *, stage: str
    ) -> list[Finding]:
        findings: list[Finding] = []
        page_text = ""
        try:
            page_text = await page.inner_text("body")
        except Exception:
            return findings

        block_phrases = [
            "access denied",
            "403 forbidden",
            "bot detected",
            "please verify you are a human",
            "checking your browser",
            "just a moment",
            "enable javascript and cookies",
        ]
        text_lower = page_text.lower()
        for phrase in block_phrases:
            if phrase in text_lower:
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.MEDIUM,
                        title="Possible bot-blocking page detected",
                        description=(
                            f"The page at {url} contains the phrase '{phrase}' ({stage}). "
                            "This may be a bot-blocking interstitial that impacts automated "
                            "testing and potentially real users with unusual browser "
                            "configurations."
                        ),
                        url=url,
                        metadata={"stage": stage, "phrase": phrase, "type": "block_page"},
                    )
                )
                break
        return findings

    async def _check_honeypots(self, page: Page, url: str) -> list[Finding]:
        """Detect hidden form fields that act as honeypot traps."""
        findings: list[Finding] = []
        for selector in _HONEYPOT_SELECTORS:
            try:
                elements = await page.query_selector_all(selector)
                for elem in elements:
                    # Verify it is a truly hidden input (not just styled).
                    tag = await elem.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "input":
                        findings.append(
                            Finding(
                                domain="advanced",
                                severity=Severity.LOW,
                                title="Honeypot form field detected",
                                description=(
                                    f"A hidden honeypot input was found at {url} "
                                    f"matching selector '{selector}'. Automated form "
                                    "fillers that populate this field will be flagged "
                                    "as bots. Ensure legitimate assistive technologies "
                                    "do not accidentally trigger it."
                                ),
                                url=url,
                                element_selector=selector,
                                metadata={"type": "honeypot"},
                            )
                        )
                        break  # One honeypot finding per URL.
            except Exception:
                continue
            if findings:
                break
        return findings


def _sample_urls(ctx: ScanContext) -> list[str]:
    """Pick a representative sample of discovered URLs."""
    urls = list(ctx.state.discovered_urls)
    if not urls:
        # Fall back to target URL.
        return [ctx.config.target_url]
    random.shuffle(urls)
    return urls[:_MAX_SAMPLE_PAGES]
