"""Serenity QA analyzers — one module per analysis domain."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from serenity.analyzers.base import BaseAnalyzer


def get_analyzers(domains: list[str] | None = None) -> list[BaseAnalyzer]:
    """Import and instantiate all analyzers, optionally filtered by domain."""
    from serenity.analyzers.accessibility import AccessibilityAnalyzer
    from serenity.analyzers.click_agent import ClickAgentAnalyzer
    from serenity.analyzers.content import ContentAnalyzer
    from serenity.analyzers.forms import FormAnalyzer
    from serenity.analyzers.functionality import FunctionalityAnalyzer
    from serenity.analyzers.infrastructure import InfrastructureAnalyzer
    from serenity.analyzers.performance import PerformanceAnalyzer
    from serenity.analyzers.responsiveness import ResponsivenessAnalyzer
    from serenity.analyzers.seo import SEOAnalyzer

    all_analyzers: list[BaseAnalyzer] = [
        InfrastructureAnalyzer(),
        PerformanceAnalyzer(),
        SEOAnalyzer(),
        ClickAgentAnalyzer(),
        FormAnalyzer(),
        FunctionalityAnalyzer(),
        ResponsivenessAnalyzer(),
        AccessibilityAnalyzer(),
        ContentAnalyzer(),
    ]

    if domains:
        return [a for a in all_analyzers if a.domain in domains]

    return all_analyzers
