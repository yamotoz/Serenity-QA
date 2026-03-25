"""Domain 4 — Click Agent & Interaction Mapping analyzer.

Discovers every interactive element on a page, clicks each one, records
the outcome, flags fake clickables, and builds a site-wide navigation
graph in the global pass.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from serenity.analyzers.base import BaseAnalyzer
from serenity.constants import MAX_INTERACTIVE_ELEMENTS, Severity
from serenity.scoring.finding import Finding
from serenity.types import InteractionResult, NavigationEdge, NavigationNode

if TYPE_CHECKING:
    from playwright.async_api import ElementHandle, Page

    from serenity.core.state import ScanContext

logger = logging.getLogger(__name__)

# ── Selectors ────────────────────────────────────────────────────────────────

INTERACTIVE_SELECTOR = ", ".join(
    [
        "a",
        "button",
        "[role='button']",
        "select",
        "input[type='checkbox']",
        "input[type='radio']",
        "[role='tab']",
        "[role='menuitem']",
        "details > summary",
        "[onclick]",
        "[data-toggle]",
        "[aria-expanded]",
        "[aria-haspopup]",
        "[role='switch']",
    ]
)

MODAL_SELECTORS = ", ".join(
    [
        "[role='dialog']",
        "[role='alertdialog']",
        ".modal",
        ".dialog",
        "[aria-modal='true']",
        ".dropdown-menu",
        ".popup",
        "[class*='overlay']",
    ]
)

# ── Helpers ──────────────────────────────────────────────────────────────────


async def _generate_css_selector(element: ElementHandle) -> str:
    """Build a reasonably stable CSS selector for *element*."""
    try:
        selector: str = await element.evaluate(
            """el => {
                if (el.id) return '#' + CSS.escape(el.id);

                const parts = [];
                let cur = el;
                while (cur && cur !== document.body) {
                    let seg = cur.tagName.toLowerCase();
                    if (cur.id) {
                        parts.unshift('#' + CSS.escape(cur.id));
                        break;
                    }
                    if (cur.className && typeof cur.className === 'string') {
                        const cls = cur.className.trim().split(/\\s+/)
                            .filter(c => c && !c.startsWith('ng-') && !c.startsWith('_'))
                            .slice(0, 2)
                            .map(c => '.' + CSS.escape(c))
                            .join('');
                        if (cls) seg += cls;
                    }
                    const parent = cur.parentElement;
                    if (parent) {
                        const siblings = Array.from(parent.children).filter(
                            c => c.tagName === cur.tagName
                        );
                        if (siblings.length > 1) {
                            const idx = siblings.indexOf(cur) + 1;
                            seg += ':nth-of-type(' + idx + ')';
                        }
                    }
                    parts.unshift(seg);
                    cur = cur.parentElement;
                }
                return parts.join(' > ');
            }"""
        )
        return selector or "unknown"
    except Exception:
        return "unknown"


async def _element_text(element: ElementHandle) -> str:
    """Return trimmed visible text content (max 120 chars)."""
    try:
        text: str = await element.evaluate(
            "el => (el.innerText || el.textContent || '').trim().slice(0, 120)"
        )
        return text
    except Exception:
        return ""


async def _element_tag(element: ElementHandle) -> str:
    try:
        return (await element.evaluate("el => el.tagName.toLowerCase()")) or ""
    except Exception:
        return ""


def _is_internal(url: str, base_host: str) -> bool:
    """Return True when *url* belongs to the same hostname as *base_host*."""
    try:
        parsed = urlparse(url)
        return parsed.hostname == base_host or not parsed.hostname
    except Exception:
        return False


# ── Analyzer ─────────────────────────────────────────────────────────────────


class ClickAgentAnalyzer(BaseAnalyzer):
    """Click every interactive element and map the navigation structure."""

    domain: str = "click_agent"
    weight: float = 0.10

    # ------------------------------------------------------------------ #
    # Per-page analysis                                                    #
    # ------------------------------------------------------------------ #

    async def analyze_page(
        self, ctx: ScanContext, url: str, page: Page
    ) -> list[Finding]:
        findings: list[Finding] = []
        base_host = urlparse(ctx.config.target_url).hostname or ""

        # Phase 1 — Element inventory
        elements = await self._collect_elements(page)
        fake_candidates = await self._detect_fake_clickable_candidates(page, elements)
        logger.info(
            "click_agent: %s — %d interactive elements, %d fake-clickable candidates",
            url,
            len(elements),
            len(fake_candidates),
        )

        # Phase 2 — Interaction execution
        interaction_findings, results = await self._execute_interactions(
            ctx, page, elements, url, base_host
        )
        findings.extend(interaction_findings)

        # Phase 3 — Fake clickable detection
        fake_findings = await self._check_fake_clickables(page, fake_candidates, url)
        findings.extend(fake_findings)

        # Persist interaction results so analyze_global can use them
        ctx.state.interaction_results.extend(results)

        return findings

    # ------------------------------------------------------------------ #
    # Global analysis — navigation graph                                   #
    # ------------------------------------------------------------------ #

    async def analyze_global(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        base_host = urlparse(ctx.config.target_url).hostname or ""

        # Phase 4 — Build navigation graph from page_data
        nodes: dict[str, NavigationNode] = {}
        edges: list[NavigationEdge] = []

        # Initialise a node for every analysed URL
        for page_url, pdata in ctx.state.page_data.items():
            if page_url not in nodes:
                nodes[page_url] = NavigationNode(
                    url=page_url, title=pdata.title
                )

        # Build edges from interaction results (link clicks that changed URL)
        for result in ctx.state.interaction_results:
            if not isinstance(result, InteractionResult):
                continue
            if result.url_changed and _is_internal(result.url_after, base_host):
                edge = NavigationEdge(
                    source_url=result.url_before,
                    target_url=result.url_after,
                    trigger_selector=result.element_selector,
                    trigger_text=result.element_text,
                )
                edges.append(edge)

                # Ensure both endpoints are in the node set
                for u in (result.url_before, result.url_after):
                    if u not in nodes:
                        nodes[u] = NavigationNode(url=u)

        # Also build edges from <a href> collected in page HTML
        for page_url, pdata in ctx.state.page_data.items():
            if not pdata.html_content:
                continue
            # Quick & lightweight link extraction (avoids full HTML parse)
            await self._extract_links_from_html(
                pdata.html_content, page_url, base_host, nodes, edges
            )

        # Additionally, add /cyberdyne, /termos, /privacidade to intentionally
        # unlinkable if they are product pages or legal pages that may only be
        # linked from specific contexts (footer, mobile menu)
        # This is handled by the _INTENTIONALLY_UNLINKABLE list above.

        # Count in/out edges
        for edge in edges:
            src = nodes.get(edge.source_url)
            tgt = nodes.get(edge.target_url)
            if src:
                src.outgoing_edges += 1
            if tgt:
                tgt.incoming_edges += 1

        # Paths that are intentionally not linked in main navigation:
        # - Auth pages (behind login)
        # - Legal/policy pages (linked from footer, not main nav)
        # - Product sub-pages (may be in mobile menu or dynamic nav)
        _INTENTIONALLY_UNLINKABLE = (
            # Auth-required pages
            "/admin", "/dashboard", "/internal", "/settings",
            "/painel", "/gerenciamento", "/backoffice", "/cms",
            "/login", "/signin", "/register", "/signup",
            "/app", "/perfil", "/profile", "/account", "/conta",
            "/analytics", "/biblioteca", "/library",
            "/marketing", "/precos", "/pricing", "/checkout",
            # API endpoints
            "/api", "/graphql", "/webhook",
            # Legal / policy pages (typically footer-only links)
            "/termos", "/terms", "/privacidade", "/privacy",
            "/politica", "/policy", "/legal", "/cookies",
            # Product sub-pages (may be linked from dynamic nav or mobile menu)
            "/cyberdyne", "/serenity", "/about", "/sobre",
        )

        # Detect orphans and dead ends (skip the root page)
        root_url = ctx.config.target_url.rstrip("/")
        for node in nodes.values():
            normalised = node.url.rstrip("/")
            if normalised == root_url:
                continue

            # Skip intentionally unlinkable pages
            parsed_node = urlparse(normalised)
            path_lower = parsed_node.path.lower()
            is_intentional = any(
                path_lower == prefix or path_lower.startswith(prefix + "/")
                for prefix in _INTENTIONALLY_UNLINKABLE
            )

            if node.incoming_edges == 0:
                node.is_orphan = True
                if is_intentional:
                    # These pages are intentionally not linked — not a problem
                    continue
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title="Orphan page detected",
                        description=(
                            f"The page '{node.url}' has no incoming internal links. "
                            "Users cannot discover this page through normal navigation."
                        ),
                        url=node.url,
                        estimated_fix_minutes=10,
                        metadata={"issue_type": "orphan_page"},
                    )
                )
            if node.outgoing_edges == 0:
                node.is_dead_end = True
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.LOW,
                        title="Dead-end page detected",
                        description=(
                            f"The page '{node.url}' has no outgoing internal links. "
                            "Users reaching this page have no way to continue navigating."
                        ),
                        url=node.url,
                        estimated_fix_minutes=10,
                        metadata={"issue_type": "dead_end_page"},
                    )
                )

        # Persist graph on state
        ctx.state.nav_nodes = nodes
        ctx.state.nav_edges = edges

        logger.info(
            "click_agent global: %d nodes, %d edges, %d orphans, %d dead-ends",
            len(nodes),
            len(edges),
            sum(1 for n in nodes.values() if n.is_orphan),
            sum(1 for n in nodes.values() if n.is_dead_end),
        )

        return findings

    # ================================================================== #
    # Private helpers                                                      #
    # ================================================================== #

    async def _collect_elements(self, page: Page) -> list[ElementHandle]:
        """Phase 1 — gather interactive elements up to the limit.

        Filters out sr-only / visually-hidden elements that are only visible
        on focus (e.g., skip-to-content links). These are intentionally hidden
        and should not be click-tested in their hidden state.
        """
        try:
            elements = await page.query_selector_all(INTERACTIVE_SELECTOR)
        except Exception:
            logger.warning("click_agent: failed to query interactive elements")
            return []

        # Filter out sr-only elements (skip-to-content, screen-reader-only)
        filtered: list[ElementHandle] = []
        for el in elements:
            try:
                is_sr_only = await el.evaluate("""el => {
                    const cls = el.className && typeof el.className === 'string' ? el.className : '';
                    if (cls.includes('sr-only') || cls.includes('visually-hidden')) return true;
                    const style = window.getComputedStyle(el);
                    if (style.position === 'absolute' && style.width === '1px' && style.height === '1px') return true;
                    if (style.clip === 'rect(0px, 0px, 0px, 0px)') return true;
                    return false;
                }""")
                if not is_sr_only:
                    filtered.append(el)
            except Exception:
                filtered.append(el)  # If we can't check, include it

        if len(filtered) > MAX_INTERACTIVE_ELEMENTS:
            logger.info(
                "click_agent: capping elements from %d to %d",
                len(filtered),
                MAX_INTERACTIVE_ELEMENTS,
            )
            filtered = filtered[:MAX_INTERACTIVE_ELEMENTS]

        return filtered

    async def _detect_fake_clickable_candidates(
        self, page: Page, known_elements: list[ElementHandle]
    ) -> list[ElementHandle]:
        """Find elements styled with cursor:pointer but lacking real interactivity."""
        try:
            all_pointer = await page.evaluate(
                """() => {
                    const results = [];
                    const all = document.querySelectorAll('*');
                    for (const el of all) {
                        const style = getComputedStyle(el);
                        if (style.cursor === 'pointer') {
                            results.push(el);
                        }
                        if (results.length > 500) break;
                    }
                    return results.length;
                }"""
            )
            # If there are pointer elements, query them properly
            candidates: list[ElementHandle] = await page.query_selector_all("*")
            # Filter in JS to avoid transferring too many handles
            fake_handles: list[ElementHandle] = []
            candidate_elements = await page.evaluate_handle(
                """() => {
                    const fakes = [];
                    const interactive = new Set();
                    document.querySelectorAll(
                        "a, button, [role='button'], select, input, textarea, " +
                        "[onclick], [data-toggle], [aria-expanded], [aria-haspopup], " +
                        "[role='tab'], [role='menuitem'], [role='switch'], " +
                        "details > summary, label"
                    ).forEach(el => interactive.add(el));

                    const all = document.querySelectorAll('*');
                    for (const el of all) {
                        if (interactive.has(el)) continue;
                        const style = getComputedStyle(el);
                        if (style.cursor === 'pointer') {
                            fakes.push(el);
                        }
                        if (fakes.length >= 50) break;
                    }
                    return fakes;
                }"""
            )
            # Convert JSHandle of array to ElementHandles
            length = await candidate_elements.evaluate("arr => arr.length")
            for i in range(min(int(length), 50)):
                handle = await candidate_elements.evaluate_handle(
                    f"arr => arr[{i}]"
                )
                fake_handles.append(handle.as_element())  # type: ignore[arg-type]

            return [h for h in fake_handles if h is not None]

        except Exception:
            logger.debug("click_agent: fake-clickable detection failed", exc_info=True)
            return []

    async def _execute_interactions(
        self,
        ctx: ScanContext,
        page: Page,
        elements: list[ElementHandle],
        url: str,
        base_host: str,
    ) -> tuple[list[Finding], list[InteractionResult]]:
        """Phase 2 — click every element and record results."""
        findings: list[Finding] = []
        results: list[InteractionResult] = []
        console_errors: list[str] = []

        # Set up console error listener
        def _on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        page.on("console", _on_console)

        try:
            for element in elements:
                result = await self._click_single_element(
                    page, element, url, base_host, console_errors
                )
                if result is None:
                    continue

                results.append(result)

                if not result.passed:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.HIGH,
                            title="Interactive element failed on click",
                            description=(
                                f"Clicking '{result.element_text or result.element_selector}' "
                                f"caused an error: {result.failure_reason}"
                            ),
                            url=url,
                            element_selector=result.element_selector,
                            estimated_fix_minutes=15,
                            metadata={
                                "issue_type": "click_failure",
                                "element_tag": result.element_tag,
                                "console_errors": result.console_errors,
                            },
                        )
                    )
        finally:
            page.remove_listener("console", _on_console)

        return findings, results

    async def _click_single_element(
        self,
        page: Page,
        element: ElementHandle,
        url: str,
        base_host: str,
        console_errors: list[str],
    ) -> InteractionResult | None:
        """Click a single element and return the interaction result."""
        try:
            # Check if still attached to DOM
            is_visible = await element.is_visible()
            if not is_visible:
                return None
        except Exception:
            return None

        selector = await _generate_css_selector(element)
        text = await _element_text(element)
        tag = await _element_tag(element)

        errors_before = len(console_errors)
        url_before = page.url

        # Count visible modals before click
        modals_before = await self._count_visible_modals(page)

        result = InteractionResult(
            element_selector=selector,
            element_xpath="",
            element_tag=tag,
            element_text=text,
            action="click",
            url_before=url_before,
            url_after=url_before,
            url_changed=False,
        )

        start = time.monotonic()
        try:
            await element.scroll_into_view_if_needed(timeout=3000)
            await element.click(timeout=5000)
        except Exception as exc:
            result.passed = False
            result.failure_reason = str(exc)[:300]
            result.response_time_ms = (time.monotonic() - start) * 1000
            return result

        # Wait for potential reactions
        await asyncio.sleep(0.5)
        result.response_time_ms = (time.monotonic() - start) * 1000

        # Capture console errors that appeared during click
        # Filter out resource loading errors (403/404) which are network issues, not JS bugs
        new_errors = [
            e for e in console_errors[errors_before:]
            if not (
                "failed to load resource" in e.lower()
                or "the server responded with a status of 4" in e.lower()
                or "net::err_" in e.lower()
            )
        ]
        if new_errors:
            result.console_errors = list(new_errors)
            result.passed = False
            result.failure_reason = f"Console errors after click: {new_errors[0][:200]}"

        # Check URL change
        url_after = page.url
        result.url_after = url_after
        result.url_changed = url_after != url_before

        # Check if modal/dropdown appeared
        modals_after = await self._count_visible_modals(page)
        if modals_after > modals_before:
            result.new_elements.append("modal/dropdown appeared")

        # Navigate back if URL changed to an internal page
        if result.url_changed and _is_internal(url_after, base_host):
            try:
                await page.go_back(wait_until="domcontentloaded", timeout=10000)
                # Small settle time after going back
                await asyncio.sleep(0.3)
            except Exception:
                logger.debug("click_agent: go_back failed after navigating to %s", url_after)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    logger.warning("click_agent: could not return to %s", url)

        return result

    async def _count_visible_modals(self, page: Page) -> int:
        """Return the number of currently visible modal-like elements."""
        try:
            return await page.evaluate(
                """(selector) => {
                    const els = document.querySelectorAll(selector);
                    let count = 0;
                    for (const el of els) {
                        const style = getComputedStyle(el);
                        if (style.display !== 'none' && style.visibility !== 'hidden'
                            && el.offsetWidth > 0 && el.offsetHeight > 0) {
                            count++;
                        }
                    }
                    return count;
                }""",
                MODAL_SELECTORS,
            )
        except Exception:
            return 0

    async def _check_fake_clickables(
        self,
        page: Page,
        candidates: list[ElementHandle],
        url: str,
    ) -> list[Finding]:
        """Phase 3 — test each fake-clickable candidate."""
        findings: list[Finding] = []

        for element in candidates:
            try:
                is_visible = await element.is_visible()
                if not is_visible:
                    continue
            except Exception:
                continue

            selector = await _generate_css_selector(element)
            text = await _element_text(element)
            url_before = page.url

            # Snapshot body childElementCount to detect DOM changes
            try:
                dom_snapshot = await page.evaluate(
                    "() => document.body.innerHTML.length"
                )
            except Exception:
                dom_snapshot = 0

            console_errors: list[str] = []

            def _on_error(msg):
                if msg.type == "error":
                    console_errors.append(msg.text)

            page.on("console", _on_error)

            try:
                await element.scroll_into_view_if_needed(timeout=3000)
                await element.click(timeout=3000)
                await asyncio.sleep(0.5)
            except Exception:
                page.remove_listener("console", _on_error)
                continue

            page.remove_listener("console", _on_error)

            url_after = page.url
            try:
                dom_after = await page.evaluate(
                    "() => document.body.innerHTML.length"
                )
            except Exception:
                dom_after = dom_snapshot

            url_changed = url_after != url_before
            dom_changed = abs(dom_after - dom_snapshot) > 20
            had_errors = len(console_errors) > 0

            something_happened = url_changed or dom_changed or had_errors

            if not something_happened:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.LOW,
                        title="Fake clickable element (cursor:pointer with no effect)",
                        description=(
                            f"The element '{text or selector}' has cursor:pointer styling "
                            "but clicking it produces no visible effect. This confuses users "
                            "who expect interactive behavior."
                        ),
                        url=url,
                        element_selector=selector,
                        fix_snippet=(
                            "/* Remove misleading pointer cursor */\n"
                            f"{selector} {{ cursor: default; }}"
                        ),
                        estimated_fix_minutes=5,
                        metadata={"issue_type": "fake_clickable", "element_text": text},
                    )
                )

            # Restore page if URL changed
            if url_changed:
                try:
                    await page.go_back(wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    pass

        return findings

    async def _extract_links_from_html(
        self,
        html: str,
        source_url: str,
        base_host: str,
        nodes: dict[str, NavigationNode],
        edges: list[NavigationEdge],
    ) -> None:
        """Lightweight link extraction from raw HTML for the nav graph.

        Uses simple string scanning to avoid pulling in an HTML parser
        dependency — accuracy is acceptable for graph construction.
        """
        import re

        href_pattern = re.compile(r'<a\s[^>]*href=["\']([^"\'#][^"\']*)["\']', re.I)
        for match in href_pattern.finditer(html):
            href = match.group(1).strip()
            if href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
                continue

            # Resolve relative URLs
            if href.startswith("/"):
                parsed_source = urlparse(source_url)
                href = f"{parsed_source.scheme}://{parsed_source.netloc}{href}"
            elif not href.startswith("http"):
                continue

            target = href.split("?")[0].split("#")[0].rstrip("/")
            if not _is_internal(target, base_host):
                continue

            if target not in nodes:
                nodes[target] = NavigationNode(url=target)

            edge = NavigationEdge(source_url=source_url, target_url=target)
            edges.append(edge)
