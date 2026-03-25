"""Chrome DevTools Protocol session wrapper."""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import CDPSession, Page

logger = logging.getLogger("serenity.cdp")


class CDPManager:
    """Manages CDP sessions for low-level browser instrumentation.

    Used for: heap profiling, network interception, performance metrics,
    request modification, and other advanced features.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, CDPSession] = {}

    async def create_session(self, page: Page, key: str = "default") -> CDPSession:
        """Create or retrieve a CDP session for a page."""
        existing = self._sessions.get(key)
        if existing:
            return existing

        client = await page.context.new_cdp_session(page)
        self._sessions[key] = client
        logger.debug("CDP session created: %s", key)
        return client

    async def send(self, session_key: str, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a CDP command via a named session."""
        session = self._sessions.get(session_key)
        if not session:
            raise RuntimeError(f"CDP session '{session_key}' not found")
        return await session.send(method, params or {})

    async def enable_performance(self, page: Page) -> CDPSession:
        """Enable Performance domain on a page."""
        session = await self.create_session(page, f"perf_{id(page)}")
        await session.send("Performance.enable")
        return session

    async def enable_network_interception(self, page: Page) -> CDPSession:
        """Enable Fetch domain for network interception."""
        session = await self.create_session(page, f"net_{id(page)}")
        await session.send("Fetch.enable", {
            "patterns": [{"urlPattern": "*", "requestStage": "Response"}]
        })
        return session

    async def get_heap_usage(self, page: Page) -> dict[str, Any]:
        """Get current JS heap usage via Performance metrics."""
        session = await self.create_session(page, f"heap_{id(page)}")
        await session.send("Performance.enable")
        result = await session.send("Performance.getMetrics")
        metrics = {m["name"]: m["value"] for m in result.get("metrics", [])}
        return metrics

    async def collect_garbage(self, page: Page) -> None:
        """Force garbage collection."""
        session = await self.create_session(page, f"heap_{id(page)}")
        await session.send("HeapProfiler.collectGarbage")

    async def close_session(self, key: str) -> None:
        """Close and remove a CDP session."""
        session = self._sessions.pop(key, None)
        if session:
            try:
                await session.detach()
            except Exception:
                pass

    async def close_all(self) -> None:
        """Close all active CDP sessions."""
        for key in list(self._sessions.keys()):
            await self.close_session(key)
        logger.debug("All CDP sessions closed")
