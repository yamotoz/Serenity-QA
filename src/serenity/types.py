"""Serenity QA shared type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PageData:
    """Data collected for a single page during crawl."""

    url: str
    status_code: int = 0
    ttfb_ms: float = 0.0
    load_time_ms: float = 0.0
    page_size_bytes: int = 0
    request_count: int = 0
    title: str = ""
    meta_description: str = ""
    h1_texts: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    screenshots: dict[str, str] = field(default_factory=dict)  # viewport -> path
    html_content: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    cookies: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class InteractionResult:
    """Result of clicking an interactive element."""

    element_selector: str
    element_xpath: str
    element_tag: str
    element_text: str
    action: str  # "click", "hover", "focus"
    url_before: str
    url_after: str
    url_changed: bool
    console_errors: list[str] = field(default_factory=list)
    dom_changes: list[str] = field(default_factory=list)
    new_elements: list[str] = field(default_factory=list)
    screenshot_before: str = ""
    screenshot_after: str = ""
    response_time_ms: float = 0.0
    passed: bool = True
    failure_reason: str = ""


@dataclass
class NavigationNode:
    """Node in the navigation graph."""

    url: str
    title: str = ""
    incoming_edges: int = 0
    outgoing_edges: int = 0
    is_orphan: bool = False
    is_dead_end: bool = False


@dataclass
class NavigationEdge:
    """Edge in the navigation graph."""

    source_url: str
    target_url: str
    trigger_selector: str = ""
    trigger_text: str = ""
