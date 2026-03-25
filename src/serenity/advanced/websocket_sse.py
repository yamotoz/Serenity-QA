"""WebSocket and Server-Sent Events analysis via CDP network domain."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING, Any

from serenity.constants import Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import CDPSession, Page

    from serenity.core.state import ScanContext

logger = logging.getLogger("serenity.advanced.websocket_sse")

_MAX_SAMPLE_PAGES = 3
_MONITOR_DURATION_S = 10
_EXCESSIVE_RECONNECT_THRESHOLD = 3
_HIGH_FREQUENCY_MSG_PER_SEC = 50


class WebSocketAnalyzer:
    """Detect and analyze WebSocket connections and SSE streams."""

    async def run(self, ctx: ScanContext) -> list[Finding]:
        logger.info("Starting WebSocket/SSE analysis")
        findings: list[Finding] = []

        urls = _sample_urls(ctx)
        if not urls:
            logger.info("No URLs available for WebSocket analysis")
            return findings

        page = await ctx.page_pool.acquire()
        try:
            for url in urls:
                url_findings = await self._analyze_url(ctx, page, url)
                findings.extend(url_findings)
        except Exception:
            logger.exception("WebSocket/SSE analysis failed")
        finally:
            await ctx.page_pool.release(page)

        logger.info("WebSocket/SSE analysis complete: %d findings", len(findings))
        return findings

    async def _analyze_url(
        self, ctx: ScanContext, page: Page, url: str
    ) -> list[Finding]:
        """Navigate to a URL and monitor WebSocket/SSE activity."""
        findings: list[Finding] = []
        ws_connections: dict[str, _WSConnection] = {}

        cdp_session: CDPSession | None = None
        try:
            cdp_session = await ctx.cdp.create_session(page, f"ws_{id(page)}_{id(url)}")
            await cdp_session.send("Network.enable")

            # Set up CDP event handlers for WebSocket lifecycle.
            def on_ws_created(params: dict[str, Any]) -> None:
                request_id = params.get("requestId", "")
                ws_url = params.get("url", "")
                ws_connections[request_id] = _WSConnection(
                    request_id=request_id,
                    url=ws_url,
                    created_at=time.time(),
                )
                logger.debug("WebSocket created: %s -> %s", request_id, ws_url)

            def on_ws_closed(params: dict[str, Any]) -> None:
                request_id = params.get("requestId", "")
                conn = ws_connections.get(request_id)
                if conn:
                    conn.closed_at = time.time()
                    conn.close_count += 1

            def on_ws_frame_received(params: dict[str, Any]) -> None:
                request_id = params.get("requestId", "")
                conn = ws_connections.get(request_id)
                if conn:
                    payload = params.get("response", {})
                    data = payload.get("payloadData", "")
                    conn.messages_received += 1
                    conn.bytes_received += len(data)

            def on_ws_frame_sent(params: dict[str, Any]) -> None:
                request_id = params.get("requestId", "")
                conn = ws_connections.get(request_id)
                if conn:
                    payload = params.get("response", {})
                    data = payload.get("payloadData", "")
                    conn.messages_sent += 1
                    conn.bytes_sent += len(data)

            def on_ws_frame_error(params: dict[str, Any]) -> None:
                request_id = params.get("requestId", "")
                conn = ws_connections.get(request_id)
                if conn:
                    conn.errors += 1
                    conn.error_messages.append(
                        params.get("errorMessage", "unknown error")
                    )

            cdp_session.on("Network.webSocketCreated", on_ws_created)
            cdp_session.on("Network.webSocketClosed", on_ws_closed)
            cdp_session.on("Network.webSocketFrameReceived", on_ws_frame_received)
            cdp_session.on("Network.webSocketFrameSent", on_ws_frame_sent)
            cdp_session.on("Network.webSocketFrameError", on_ws_frame_error)

            # Navigate and monitor.
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(_MONITOR_DURATION_S)

            # Also detect SSE connections via JavaScript evaluation.
            sse_info = await self._detect_sse(page)

            # Analyze what we found.
            findings.extend(self._analyze_ws_connections(ws_connections, url))
            findings.extend(self._analyze_sse(sse_info, url))

        except Exception:
            logger.debug("WebSocket analysis failed for %s", url, exc_info=True)
        finally:
            if cdp_session:
                session_key = f"ws_{id(page)}_{id(url)}"
                await ctx.cdp.close_session(session_key)

        return findings

    def _analyze_ws_connections(
        self, connections: dict[str, _WSConnection], url: str
    ) -> list[Finding]:
        """Analyze captured WebSocket connections for issues."""
        findings: list[Finding] = []

        if not connections:
            return findings

        for conn in connections.values():
            elapsed = (conn.closed_at or time.time()) - conn.created_at
            if elapsed <= 0:
                elapsed = 1.0

            # --- High message frequency ---
            msg_rate = conn.messages_received / elapsed
            if msg_rate > _HIGH_FREQUENCY_MSG_PER_SEC:
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.MEDIUM,
                        title="High-frequency WebSocket messages",
                        description=(
                            f"WebSocket connection to {conn.url} received "
                            f"{conn.messages_received} messages in {elapsed:.1f}s "
                            f"({msg_rate:.1f} msg/s). High-frequency real-time updates "
                            "can overwhelm the browser's main thread and cause UI jank. "
                            "Consider batching updates or throttling on the server."
                        ),
                        url=url,
                        metadata={
                            "type": "ws_high_frequency",
                            "ws_url": conn.url,
                            "messages_received": conn.messages_received,
                            "rate_per_sec": round(msg_rate, 1),
                            "bytes_received": conn.bytes_received,
                        },
                    )
                )

            # --- Large data volume ---
            total_bytes = conn.bytes_received + conn.bytes_sent
            if total_bytes > 1024 * 1024:  # > 1 MB in monitoring window.
                mb = total_bytes / (1024 * 1024)
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.MEDIUM,
                        title="High-volume WebSocket data transfer",
                        description=(
                            f"WebSocket connection to {conn.url} transferred "
                            f"{mb:.2f} MB in {elapsed:.1f}s. Large WebSocket payloads "
                            "increase memory pressure and bandwidth usage, especially "
                            "on mobile connections."
                        ),
                        url=url,
                        metadata={
                            "type": "ws_high_volume",
                            "ws_url": conn.url,
                            "total_bytes": total_bytes,
                            "duration_s": round(elapsed, 1),
                        },
                    )
                )

            # --- Excessive errors ---
            if conn.errors > 0:
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.HIGH,
                        title="WebSocket connection errors detected",
                        description=(
                            f"WebSocket connection to {conn.url} experienced "
                            f"{conn.errors} error(s) during monitoring: "
                            f"{'; '.join(conn.error_messages[:3])}. Connection errors "
                            "may indicate server instability, authentication issues, "
                            "or protocol mismatches."
                        ),
                        url=url,
                        metadata={
                            "type": "ws_errors",
                            "ws_url": conn.url,
                            "error_count": conn.errors,
                            "errors": conn.error_messages[:5],
                        },
                    )
                )

            # --- Check for missing authentication ---
            ws_url_lower = conn.url.lower()
            has_auth = any(
                indicator in ws_url_lower
                for indicator in ("token=", "auth=", "key=", "ticket=", "jwt=")
            )
            # WebSocket over WSS is a baseline requirement.
            if not conn.url.startswith("wss://"):
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.HIGH,
                        title="Insecure WebSocket connection (ws:// instead of wss://)",
                        description=(
                            f"WebSocket connection to {conn.url} uses unencrypted "
                            "ws:// protocol. All WebSocket connections should use "
                            "wss:// (WebSocket Secure) to prevent eavesdropping "
                            "and man-in-the-middle attacks."
                        ),
                        url=url,
                        metadata={
                            "type": "ws_insecure",
                            "ws_url": conn.url,
                        },
                    )
                )

        # --- Check for excessive reconnections (multiple connections to same host) ---
        host_counts: dict[str, int] = {}
        for conn in connections.values():
            from urllib.parse import urlparse
            host = urlparse(conn.url).netloc
            host_counts[host] = host_counts.get(host, 0) + 1

        for host, count in host_counts.items():
            if count >= _EXCESSIVE_RECONNECT_THRESHOLD:
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.MEDIUM,
                        title="Excessive WebSocket reconnections",
                        description=(
                            f"Detected {count} WebSocket connections to {host} within "
                            f"the monitoring window on {url}. Frequent reconnections "
                            "suggest connection instability or missing keep-alive / "
                            "heartbeat mechanisms."
                        ),
                        url=url,
                        metadata={
                            "type": "ws_reconnections",
                            "host": host,
                            "connection_count": count,
                        },
                    )
                )

        return findings

    async def _detect_sse(self, page: Page) -> dict[str, Any]:
        """Detect active Server-Sent Events connections via JavaScript."""
        try:
            result = await page.evaluate("""() => {
                const info = { detected: false, sources: [] };
                // Check for EventSource in performance entries.
                if (window.performance) {
                    const entries = performance.getEntriesByType('resource');
                    for (const entry of entries) {
                        if (entry.initiatorType === 'other' &&
                            entry.name.includes('event') ||
                            entry.name.includes('stream') ||
                            entry.name.includes('sse')) {
                            info.detected = true;
                            info.sources.push({
                                url: entry.name,
                                duration: entry.duration,
                                size: entry.transferSize || 0
                            });
                        }
                    }
                }
                // Check if EventSource constructor has been used.
                if (typeof EventSource !== 'undefined') {
                    info.eventSourceAvailable = true;
                }
                return info;
            }""")
            return result
        except Exception:
            return {"detected": False, "sources": []}

    def _analyze_sse(
        self, sse_info: dict[str, Any], url: str
    ) -> list[Finding]:
        """Analyze detected SSE connections."""
        findings: list[Finding] = []

        if not sse_info.get("detected"):
            return findings

        sources = sse_info.get("sources", [])
        for source in sources:
            sse_url = source.get("url", "unknown")
            size = source.get("size", 0)

            if size > 512 * 1024:  # > 512KB accumulated.
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.LOW,
                        title="Large SSE data accumulation",
                        description=(
                            f"Server-Sent Events stream at {sse_url} has accumulated "
                            f"{size / 1024:.1f} KB of data on page {url}. SSE "
                            "connections that accumulate large amounts of data may "
                            "cause memory pressure in the browser."
                        ),
                        url=url,
                        metadata={
                            "type": "sse_large_data",
                            "sse_url": sse_url,
                            "bytes": size,
                        },
                    )
                )

        return findings


class _WSConnection:
    """Track state of a single WebSocket connection."""

    __slots__ = (
        "request_id",
        "url",
        "created_at",
        "closed_at",
        "messages_received",
        "messages_sent",
        "bytes_received",
        "bytes_sent",
        "errors",
        "error_messages",
        "close_count",
    )

    def __init__(self, request_id: str, url: str, created_at: float) -> None:
        self.request_id = request_id
        self.url = url
        self.created_at = created_at
        self.closed_at: float | None = None
        self.messages_received: int = 0
        self.messages_sent: int = 0
        self.bytes_received: int = 0
        self.bytes_sent: int = 0
        self.errors: int = 0
        self.error_messages: list[str] = []
        self.close_count: int = 0


def _sample_urls(ctx: ScanContext) -> list[str]:
    """Pick a representative sample of discovered URLs."""
    urls = list(ctx.state.discovered_urls)
    if not urls:
        return [ctx.config.target_url]
    random.shuffle(urls)
    return urls[:_MAX_SAMPLE_PAGES]
