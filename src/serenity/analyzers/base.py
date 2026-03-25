"""Base analyzer — abstract contract that all 9 analyzers implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

    from serenity.core.state import ScanContext
    from serenity.scoring.finding import Finding


class BaseAnalyzer(ABC):
    """Abstract base class for all Serenity QA analyzers.

    Lifecycle:
        1. ``setup()`` — called once before analysis begins
        2. ``analyze_page()`` — called for each discovered URL
        3. ``analyze_global()`` — called once after all pages analyzed
        4. ``teardown()`` — called once after analysis completes
    """

    domain: str = ""
    weight: float = 0.0

    async def setup(self, ctx: ScanContext) -> None:
        """Initialize analyzer state. Override if needed."""

    @abstractmethod
    async def analyze_page(self, ctx: ScanContext, url: str, page: Page) -> list[Finding]:
        """Analyze a single page. Must be implemented by all analyzers."""
        ...

    async def analyze_global(self, ctx: ScanContext) -> list[Finding]:
        """Run cross-page analysis after all pages are analyzed. Override if needed."""
        return []

    async def teardown(self, ctx: ScanContext) -> None:
        """Clean up analyzer state. Override if needed."""
