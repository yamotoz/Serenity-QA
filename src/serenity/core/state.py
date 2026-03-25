"""Central scan state and context — shared across all modules."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from serenity.types import NavigationEdge, NavigationNode, PageData

if TYPE_CHECKING:
    import httpx
    from playwright.async_api import Browser

    from serenity.config import ScanConfig
    from serenity.core.browser_pool import BrowserPool
    from serenity.core.cdp_manager import CDPManager
    from serenity.core.event_bus import EventBus
    from serenity.scoring.finding import Finding


class ScanState:
    """Mutable state accumulated during a scan."""

    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.discovered_urls: set[str] = set()
        self.analyzed_urls: set[str] = set()
        self.failed_urls: set[str] = set()
        self.page_data: dict[str, PageData] = {}
        self.domain_scores: dict[str, float] = {}
        self.overall_score: float = 0.0
        self.nav_nodes: dict[str, NavigationNode] = {}
        self.nav_edges: list[NavigationEdge] = []
        self.screenshots: dict[str, dict[str, str]] = {}  # url -> {viewport -> path}
        self.start_time: float = time.time()
        self.end_time: float | None = None
        self.api_endpoints: list[dict[str, Any]] = []
        self.interaction_results: list[Any] = []

    def add_finding(self, finding: Finding) -> None:
        self.findings.append(finding)

    def add_page_data(self, url: str, data: PageData) -> None:
        self.page_data[url] = data
        self.analyzed_urls.add(url)

    def mark_failed(self, url: str) -> None:
        self.failed_urls.add(url)

    @property
    def elapsed_seconds(self) -> float:
        end = self.end_time or time.time()
        return end - self.start_time

    @property
    def pages_analyzed(self) -> int:
        return len(self.analyzed_urls)

    @property
    def total_findings(self) -> int:
        return len(self.findings)


@dataclass
class ScanContext:
    """Immutable context passed to every analyzer."""

    config: ScanConfig
    browser: Browser
    cdp: CDPManager
    state: ScanState
    event_bus: EventBus
    http_client: httpx.AsyncClient
    page_pool: BrowserPool
