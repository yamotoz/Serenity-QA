"""Memory leak detection via CDP heap profiling across navigation cycles."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Any

from serenity.constants import Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import Page

    from serenity.core.state import ScanContext

logger = logging.getLogger("serenity.advanced.memory_leak")

_MAX_SAMPLE_URLS = 3
_NUM_CYCLES = 3
_GROWTH_THRESHOLD_BYTES = 2 * 1024 * 1024  # 2 MB consistent growth signals a leak.
_GROWTH_RATIO_THRESHOLD = 1.15  # 15% growth between cycles.


class MemoryLeakDetector:
    """Detect JavaScript memory leaks by cycling through pages and measuring heap."""

    async def run(self, ctx: ScanContext) -> list[Finding]:
        logger.info("Starting memory leak detection")
        findings: list[Finding] = []

        urls = _sample_urls(ctx)
        if not urls:
            logger.info("No URLs available for memory leak detection")
            return findings

        page = await ctx.page_pool.acquire()
        try:
            findings = await self._detect_leaks(ctx, page, urls)
        except Exception:
            logger.exception("Memory leak detection failed")
        finally:
            await ctx.page_pool.release(page)

        logger.info("Memory leak detection complete: %d findings", len(findings))
        return findings

    async def _detect_leaks(
        self, ctx: ScanContext, page: Page, urls: list[str]
    ) -> list[Finding]:
        findings: list[Finding] = []
        heap_snapshots: list[dict[str, Any]] = []

        for cycle in range(_NUM_CYCLES):
            logger.debug("Memory cycle %d/%d", cycle + 1, _NUM_CYCLES)

            for url in urls:
                try:
                    await page.goto(url, wait_until="networkidle", timeout=20_000)
                    # Let the page settle and run its JS.
                    await asyncio.sleep(1.0)
                except Exception:
                    logger.debug("Navigation failed for %s in cycle %d", url, cycle + 1)
                    continue

            # Force garbage collection and measure heap after each full cycle.
            try:
                await ctx.cdp.collect_garbage(page)
                await asyncio.sleep(0.5)
                metrics = await ctx.cdp.get_heap_usage(page)

                heap_used = metrics.get("JSHeapUsedSize", 0)
                heap_total = metrics.get("JSHeapTotalSize", 0)
                dom_nodes = metrics.get("Nodes", 0)
                event_listeners = metrics.get("JSEventListeners", 0)

                heap_snapshots.append(
                    {
                        "cycle": cycle + 1,
                        "heap_used": heap_used,
                        "heap_total": heap_total,
                        "dom_nodes": dom_nodes,
                        "event_listeners": event_listeners,
                    }
                )
                logger.debug(
                    "Cycle %d heap: used=%.2f MB, total=%.2f MB, nodes=%d, listeners=%d",
                    cycle + 1,
                    heap_used / (1024 * 1024),
                    heap_total / (1024 * 1024),
                    dom_nodes,
                    event_listeners,
                )
            except Exception:
                logger.debug("Failed to collect heap metrics after cycle %d", cycle + 1)

        # Analyze growth patterns across cycles.
        if len(heap_snapshots) >= 2:
            findings.extend(
                self._analyze_heap_growth(heap_snapshots, urls)
            )

        return findings

    def _analyze_heap_growth(
        self, snapshots: list[dict[str, Any]], urls: list[str]
    ) -> list[Finding]:
        findings: list[Finding] = []
        url_context = ", ".join(urls[:3])
        if len(urls) > 3:
            url_context += f" (+{len(urls) - 3} more)"

        # Check for consistent heap growth.
        heap_values = [s["heap_used"] for s in snapshots]
        monotonic_growth = all(
            heap_values[i] > heap_values[i - 1] for i in range(1, len(heap_values))
        )

        if monotonic_growth and len(heap_values) >= 2:
            total_growth = heap_values[-1] - heap_values[0]
            growth_ratio = heap_values[-1] / heap_values[0] if heap_values[0] > 0 else 0

            if total_growth > _GROWTH_THRESHOLD_BYTES or growth_ratio > _GROWTH_RATIO_THRESHOLD:
                growth_mb = total_growth / (1024 * 1024)
                growth_pct = (growth_ratio - 1) * 100 if growth_ratio > 0 else 0

                severity = Severity.CRITICAL if growth_mb > 10 else (
                    Severity.HIGH if growth_mb > 5 else Severity.MEDIUM
                )

                cycle_details = "; ".join(
                    f"cycle {s['cycle']}: {s['heap_used'] / (1024 * 1024):.2f} MB"
                    for s in snapshots
                )

                findings.append(
                    Finding(
                        domain="advanced",
                        severity=severity,
                        title="Potential JavaScript memory leak detected",
                        description=(
                            f"JS heap usage grew monotonically across {len(snapshots)} "
                            f"navigation cycles: {cycle_details}. "
                            f"Total growth: {growth_mb:.2f} MB ({growth_pct:.1f}%). "
                            f"Pages tested: {url_context}. "
                            "Consistent heap growth after forced GC strongly suggests "
                            "a memory leak — detached DOM nodes, uncleaned event "
                            "listeners, or growing closures."
                        ),
                        url=urls[0] if urls else None,
                        metadata={
                            "type": "memory_leak",
                            "snapshots": snapshots,
                            "total_growth_bytes": total_growth,
                            "growth_ratio": round(growth_ratio, 3),
                        },
                        fix_snippet=(
                            "// Common leak patterns to check:\n"
                            "// 1. addEventListener without removeEventListener\n"
                            "// 2. setInterval without clearInterval on unmount\n"
                            "// 3. Closures holding references to detached DOM nodes\n"
                            "// 4. Growing arrays/maps that are never cleared\n"
                            "// Use Chrome DevTools > Memory > Heap snapshot to identify"
                        ),
                    )
                )

        # Check for DOM node accumulation.
        dom_values = [s.get("dom_nodes", 0) for s in snapshots]
        if all(v > 0 for v in dom_values) and len(dom_values) >= 2:
            dom_growth = all(
                dom_values[i] > dom_values[i - 1] for i in range(1, len(dom_values))
            )
            if dom_growth:
                total_node_growth = dom_values[-1] - dom_values[0]
                if total_node_growth > 500:
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.MEDIUM,
                            title="DOM node accumulation detected",
                            description=(
                                f"DOM node count grew consistently across navigation "
                                f"cycles: {' -> '.join(str(int(v)) for v in dom_values)}. "
                                f"Net increase: {int(total_node_growth)} nodes. "
                                "This suggests detached DOM nodes are not being garbage "
                                "collected, possibly due to lingering JS references."
                            ),
                            url=urls[0] if urls else None,
                            metadata={
                                "type": "dom_leak",
                                "dom_counts": dom_values,
                                "growth": total_node_growth,
                            },
                        )
                    )

        # Check for event listener accumulation.
        listener_values = [s.get("event_listeners", 0) for s in snapshots]
        if all(v > 0 for v in listener_values) and len(listener_values) >= 2:
            listener_growth = all(
                listener_values[i] > listener_values[i - 1]
                for i in range(1, len(listener_values))
            )
            if listener_growth:
                total_listener_growth = listener_values[-1] - listener_values[0]
                if total_listener_growth > 50:
                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.MEDIUM,
                            title="Event listener accumulation detected",
                            description=(
                                f"Event listener count grew consistently: "
                                f"{' -> '.join(str(int(v)) for v in listener_values)}. "
                                f"Net increase: {int(total_listener_growth)} listeners. "
                                "Listeners that are not removed on navigation or component "
                                "unmount will accumulate and cause memory leaks."
                            ),
                            url=urls[0] if urls else None,
                            metadata={
                                "type": "listener_leak",
                                "listener_counts": listener_values,
                                "growth": total_listener_growth,
                            },
                        )
                    )

        return findings


def _sample_urls(ctx: ScanContext) -> list[str]:
    """Pick a representative sample of discovered URLs."""
    urls = list(ctx.state.discovered_urls)
    if not urls:
        return [ctx.config.target_url]
    random.shuffle(urls)
    return urls[:_MAX_SAMPLE_URLS]
