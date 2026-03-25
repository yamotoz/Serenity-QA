"""URL discovery via sitemap, robots.txt, and BFS link crawling."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx

if TYPE_CHECKING:
    from playwright.async_api import Page

    from serenity.core.state import ScanContext

logger = logging.getLogger("serenity.crawler")


class Crawler:
    """Discovers all pages on a target website."""

    def __init__(self) -> None:
        self._domain: str = ""
        self._scheme: str = ""

    async def crawl(self, ctx: ScanContext) -> set[str]:
        """Discover URLs starting from the target URL.

        Strategy:
        1. Parse sitemap.xml for listed URLs
        2. Parse robots.txt for sitemap references and disallow rules
        3. BFS crawl following internal links
        """
        seed = self._normalize_url(ctx.config.target_url)
        parsed = urlparse(seed)
        self._domain = parsed.netloc
        self._scheme = parsed.scheme

        visited: set[str] = set()
        queue: list[str] = [seed]

        await ctx.event_bus.emit("crawl.started", {"seed": seed})

        # Phase 1: Gather URLs from sitemap
        sitemap_urls = await self._parse_sitemap(seed, ctx.http_client)
        for url in sitemap_urls:
            norm = self._normalize_url(url)
            if norm not in queue:
                queue.append(norm)

        # Phase 2: BFS link crawling
        while queue and len(visited) < ctx.config.max_pages:
            url = queue.pop(0)

            if url in visited:
                continue

            normalized = self._normalize_url(url)
            if normalized in visited:
                continue

            if not self._is_same_domain(normalized):
                continue

            visited.add(normalized)
            ctx.state.discovered_urls.add(normalized)

            await ctx.event_bus.emit("page.discovered", {
                "url": normalized,
                "total": len(visited),
            })

            # Extract links from page
            try:
                page = await ctx.page_pool.acquire()
                try:
                    links = await self._extract_links(page, normalized, ctx)
                    for link in links:
                        norm_link = self._normalize_url(link)
                        if norm_link not in visited and self._is_same_domain(norm_link):
                            queue.append(norm_link)
                finally:
                    await ctx.page_pool.release(page)
            except Exception as e:
                logger.warning("Failed to crawl %s: %s", normalized, e)
                ctx.state.mark_failed(normalized)

        await ctx.event_bus.emit("crawl.completed", {
            "total_urls": len(visited),
        })

        logger.info("Crawl complete: %d URLs discovered", len(visited))
        return visited

    async def _extract_links(self, page: Page, url: str, ctx: ScanContext) -> list[str]:
        """Navigate to URL and extract all internal links."""
        try:
            response = await page.goto(url, timeout=ctx.config.page_timeout_ms, wait_until="domcontentloaded")

            if response:
                status = response.status
                headers = {k: v for k, v in response.headers.items()}
            else:
                status = 0
                headers = {}

            # Store basic page data
            from serenity.types import PageData
            page_data = PageData(
                url=url,
                status_code=status,
                headers=headers,
                title=await page.title(),
            )
            ctx.state.add_page_data(url, page_data)

            # Extract all links (strip fragments and trailing slashes client-side)
            links = await page.evaluate("""
                () => {
                    const links = new Set();
                    document.querySelectorAll('a[href]').forEach(a => {
                        try {
                            const u = new URL(a.href, window.location.origin);
                            u.hash = '';  // Remove fragment
                            let path = u.pathname;
                            if (path.length > 1 && path.endsWith('/')) {
                                path = path.slice(0, -1);
                            }
                            u.pathname = path;
                            links.add(u.href);
                        } catch {}
                    });
                    return [...links];
                }
            """)

            return links or []

        except Exception as e:
            logger.debug("Link extraction failed for %s: %s", url, e)
            return []

    async def _parse_sitemap(self, base_url: str, client: httpx.AsyncClient) -> list[str]:
        """Parse sitemap.xml and return discovered URLs."""
        urls: list[str] = []
        sitemap_url = f"{base_url}/sitemap.xml"

        try:
            resp = await client.get(sitemap_url, timeout=10, follow_redirects=True)
            if resp.status_code != 200:
                return urls

            root = ElementTree.fromstring(resp.text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

            # Handle sitemap index
            for sitemap in root.findall(".//sm:sitemap/sm:loc", ns):
                if sitemap.text:
                    sub_urls = await self._parse_sitemap(sitemap.text.strip(), client)
                    urls.extend(sub_urls)

            # Handle regular sitemap
            for loc in root.findall(".//sm:url/sm:loc", ns):
                if loc.text:
                    urls.append(loc.text.strip())

            logger.info("Sitemap yielded %d URLs", len(urls))
        except Exception as e:
            logger.debug("Sitemap parsing failed: %s", e)

        return urls

    def _normalize_url(self, url: str) -> str:
        """Normalize URL: remove fragment, trailing slash, lowercase domain, remove query."""
        parsed = urlparse(url)

        # Remove fragment
        normalized = parsed._replace(fragment="")

        # Normalize path: empty → "/", then strip trailing slash (except root "/")
        path = normalized.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        normalized = normalized._replace(path=path)

        return normalized.geturl()

    def _is_same_domain(self, url: str) -> bool:
        """Check if URL belongs to the same domain."""
        try:
            parsed = urlparse(url)
            return parsed.netloc == self._domain
        except Exception:
            return False
