"""Browser and page lifecycle management with Playwright."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from serenity.exceptions import BrowserError

if TYPE_CHECKING:
    from serenity.config import ScanConfig

logger = logging.getLogger("serenity.browser")


class BrowserPool:
    """Manages a pool of browser pages for concurrent analysis."""

    def __init__(self, config: ScanConfig) -> None:
        self._config = config
        self._playwright: object | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._available_pages: asyncio.Queue[Page] = asyncio.Queue()
        self._pool_size = 3
        self._initialized = False

    async def start(self) -> Browser:
        """Launch the browser and create the page pool."""
        pw = await async_playwright().start()
        self._playwright = pw

        self._browser = await pw.chromium.launch(
            headless=self._config.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
        )

        for _ in range(self._pool_size):
            page = await self._context.new_page()
            await self._available_pages.put(page)

        self._initialized = True
        logger.info("Browser pool started with %d pages", self._pool_size)
        return self._browser

    async def acquire(self) -> Page:
        """Get an available page from the pool."""
        if not self._initialized:
            raise BrowserError("Browser pool not initialized. Call start() first.")
        try:
            page = await asyncio.wait_for(self._available_pages.get(), timeout=30)
            return page
        except asyncio.TimeoutError:
            # All pages busy — create a new one
            if self._context:
                page = await self._context.new_page()
                self._pool_size += 1
                logger.debug("Pool expanded to %d pages", self._pool_size)
                return page
            raise BrowserError("No browser context available")

    async def release(self, page: Page) -> None:
        """Return a page to the pool after use."""
        try:
            # Clear page state for reuse
            await page.goto("about:blank", timeout=5000)
            await self._available_pages.put(page)
        except Exception:
            # Page is broken, create a replacement
            logger.warning("Page broken, creating replacement")
            if self._context:
                try:
                    new_page = await self._context.new_page()
                    await self._available_pages.put(new_page)
                except Exception:
                    logger.error("Failed to create replacement page")
                    self._pool_size -= 1

    async def new_context(self, **kwargs: object) -> BrowserContext:
        """Create a new browser context with custom settings."""
        if not self._browser:
            raise BrowserError("Browser not started")
        return await self._browser.new_context(**kwargs)  # type: ignore[arg-type]

    async def shutdown(self) -> None:
        """Close all pages, contexts, and the browser."""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass

        if self._playwright:
            try:
                await self._playwright.stop()  # type: ignore[union-attr]
            except Exception:
                pass

        self._initialized = False
        logger.info("Browser pool shut down")

    @property
    def browser(self) -> Browser:
        if not self._browser:
            raise BrowserError("Browser not started")
        return self._browser

    @property
    def context(self) -> BrowserContext:
        if not self._context:
            raise BrowserError("Browser context not available")
        return self._context
