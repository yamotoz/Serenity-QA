"""Serenity QA exception hierarchy."""


class SerenityError(Exception):
    """Base exception for all Serenity errors."""


class ConfigError(SerenityError):
    """Invalid configuration."""


class CrawlError(SerenityError):
    """URL unreachable or crawl failure."""


class AnalyzerError(SerenityError):
    """Analyzer execution failure."""


class BrowserError(SerenityError):
    """Playwright browser crash or disconnect."""


class ReportError(SerenityError):
    """Report generation failure."""


class DashboardError(SerenityError):
    """WebSocket or dashboard server failure."""


class AIError(SerenityError):
    """Gemini API or AI module failure."""
