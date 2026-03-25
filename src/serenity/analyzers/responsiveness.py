"""Domain 7 — Responsiveness & Layout analyzer.

Takes viewport screenshots, detects horizontal overflow, zoom-blocking
meta tags, missing image dimensions, undersized touch targets, and
small text on mobile.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from serenity.analyzers.base import BaseAnalyzer
from serenity.constants import VIEWPORTS, Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import Page

    from serenity.core.state import ScanContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JavaScript snippets executed in the browser context
# ---------------------------------------------------------------------------

_JS_HORIZONTAL_OVERFLOW = """
() => {
    return {
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
    };
}
"""

_JS_OVERFLOW_ELEMENTS = """
() => {
    const results = [];
    const elements = document.querySelectorAll('*');

    // Check if this element or any ancestor clips overflow
    function isOverflowClipped(el) {
        let current = el;
        while (current && current !== document.documentElement) {
            const st = window.getComputedStyle(current);
            const ox = st.overflowX;
            // overflow-x: hidden clips content — no horizontal scroll possible
            // overflow-x: auto/scroll means horizontal scroll is INTENTIONAL
            if (ox === 'hidden' || ox === 'auto' || ox === 'scroll') return true;
            current = current.parentElement;
        }
        return false;
    }

    for (const el of elements) {
        if (el.scrollWidth > el.clientWidth + 2) {
            // Skip sr-only / visually-hidden elements
            const elCls = el.className && typeof el.className === 'string' ? el.className : '';
            if (elCls.includes('sr-only') || elCls.includes('visually-hidden')) continue;
            const style = window.getComputedStyle(el);
            if (style.position === 'absolute' && style.width === '1px') continue;
            // Skip pointer-events:none decorative elements
            if (style.pointerEvents === 'none' && style.position === 'absolute') continue;

            // Skip elements where overflow is clipped or intentionally scrollable
            // This handles: overflow-x:hidden (content cut off, no scroll bar)
            // and overflow-x:auto/scroll (intentional horizontal scroll, e.g. tabs)
            if (isOverflowClipped(el)) continue;

            const tag = el.tagName.toLowerCase();
            const id = el.id ? '#' + el.id : '';
            const cls = elCls
                ? '.' + elCls.trim().split(/\\s+/).join('.')
                : '';
            const selector = tag + id + cls;
            results.push({
                selector: selector,
                scrollWidth: el.scrollWidth,
                clientWidth: el.clientWidth,
            });
        }
        if (results.length >= 20) break;
    }
    return results;
}
"""

_JS_IMAGES_WITHOUT_DIMENSIONS = """
() => {
    const imgs = document.querySelectorAll('img');
    const results = [];
    for (const img of imgs) {
        const hasWidth = img.hasAttribute('width') || img.style.width;
        const hasHeight = img.hasAttribute('height') || img.style.height;
        if (!hasWidth || !hasHeight) {
            results.push({
                src: img.src ? img.src.substring(0, 120) : '(no src)',
                selector: img.getAttribute('data-testid')
                    || img.id
                    || img.className
                    || img.tagName.toLowerCase(),
            });
        }
        if (results.length >= 30) break;
    }
    return results;
}
"""

_JS_TOUCH_TARGETS = """
() => {
    const interactive = document.querySelectorAll(
        'a, button, input, select, textarea, [role="button"], [tabindex]'
    );
    const small = [];
    for (const el of interactive) {
        // Skip sr-only / visually-hidden elements (e.g., skip-to-content links)
        const style = window.getComputedStyle(el);
        const cls = el.className && typeof el.className === 'string' ? el.className : '';
        const isSrOnly = cls.includes('sr-only') || cls.includes('visually-hidden')
            || style.clip === 'rect(0px, 0px, 0px, 0px)'
            || (style.position === 'absolute' && style.width === '1px' && style.height === '1px');
        if (isSrOnly) continue;

        // Skip hidden elements
        if (style.display === 'none' || style.visibility === 'hidden') continue;

        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;

        // Use the EFFECTIVE touch area: the element's bounding rect,
        // OR the parent's bounding rect if the parent is the actual
        // clickable container (e.g., <li> with padding around <a>)
        let effectiveW = rect.width;
        let effectiveH = rect.height;

        // Check if parent/grandparent provides a larger clickable area
        // (common pattern: <li class="py-3"><a>Link text</a></li>)
        const parent = el.parentElement;
        if (parent) {
            const pRect = parent.getBoundingClientRect();
            const pStyle = window.getComputedStyle(parent);
            // If parent is block/flex and wraps only this element, its area IS the touch target
            const pDisplay = pStyle.display;
            if ((pDisplay === 'block' || pDisplay === 'flex' || pDisplay === 'list-item' || pDisplay.includes('grid'))
                && pRect.width >= effectiveW && pRect.height > effectiveH) {
                effectiveW = Math.max(effectiveW, pRect.width);
                effectiveH = Math.max(effectiveH, pRect.height);
            }
        }

        // Also account for the element's own padding via computed style
        // getBoundingClientRect SHOULD include padding, but for inline elements
        // it may only reflect the text line-height. Check computed box.
        const paddingTop = parseFloat(style.paddingTop) || 0;
        const paddingBottom = parseFloat(style.paddingBottom) || 0;
        const paddingLeft = parseFloat(style.paddingLeft) || 0;
        const paddingRight = parseFloat(style.paddingRight) || 0;
        const computedH = rect.height + (style.display === 'inline' ? paddingTop + paddingBottom : 0);
        const computedW = rect.width + (style.display === 'inline' ? paddingLeft + paddingRight : 0);
        effectiveW = Math.max(effectiveW, computedW);
        effectiveH = Math.max(effectiveH, computedH);

        // Flag if BOTH dimensions are under 44px, or if either is critically small (<24px)
        const tooSmall = (effectiveW < 44 && effectiveH < 44)
            || effectiveW < 24 || effectiveH < 24;
        if (tooSmall) {
            const tag = el.tagName.toLowerCase();
            const id = el.id ? '#' + el.id : '';
            small.push({
                selector: tag + id,
                width: Math.round(effectiveW),
                height: Math.round(effectiveH),
                text: (el.textContent || '').trim().substring(0, 60),
            });
        }
        if (small.length >= 20) break;
    }
    return small;
}
"""

_JS_SMALL_TEXT = """
() => {
    const walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_TEXT,
        {
            acceptNode: (node) =>
                node.textContent.trim().length > 0
                    ? NodeFilter.FILTER_ACCEPT
                    : NodeFilter.FILTER_REJECT,
        }
    );
    const found = [];
    const seen = new Set();
    while (walker.nextNode()) {
        const parent = walker.currentNode.parentElement;
        if (!parent || seen.has(parent)) continue;
        seen.add(parent);
        const style = window.getComputedStyle(parent);
        const size = parseFloat(style.fontSize);
        if (size < 12) {
            const tag = parent.tagName.toLowerCase();
            const id = parent.id ? '#' + parent.id : '';
            found.push({
                selector: tag + id,
                fontSize: size,
                text: parent.textContent.trim().substring(0, 80),
            });
        }
        if (found.length >= 15) break;
    }
    return found;
}
"""


def _safe_filename(url: str) -> str:
    """Convert a URL into a filesystem-safe name."""
    name = re.sub(r"https?://", "", url)
    name = re.sub(r"[^\w\-.]", "_", name)
    return name[:120]


class ResponsivenessAnalyzer(BaseAnalyzer):
    """Analyzes page responsiveness across mobile, tablet, and desktop viewports."""

    domain: str = "responsiveness"
    weight: float = 0.15

    async def analyze_page(
        self, ctx: ScanContext, url: str, page: Page
    ) -> list[Finding]:
        findings: list[Finding] = []
        safe_name = _safe_filename(url)

        # ---- 1. Viewport screenshots ------------------------------------------
        screenshot_dir = ctx.config.get_output_path() / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        url_screenshots: dict[str, str] = {}
        for vp_name, vp_size in VIEWPORTS.items():
            try:
                await page.set_viewport_size(vp_size)
                # Allow a brief reflow before capturing
                await page.wait_for_timeout(300)

                path = screenshot_dir / f"{safe_name}_{vp_name}.png"
                await page.screenshot(path=str(path), full_page=True)
                url_screenshots[vp_name] = str(path)
            except Exception:
                logger.warning(
                    "Failed to capture %s screenshot for %s", vp_name, url, exc_info=True
                )

        ctx.state.screenshots[url] = url_screenshots

        # ---- 2. Horizontal overflow per viewport -----------------------------
        for vp_name, vp_size in VIEWPORTS.items():
            try:
                await page.set_viewport_size(vp_size)
                await page.wait_for_timeout(200)
                overflow = await page.evaluate(_JS_HORIZONTAL_OVERFLOW)
                if overflow["scrollWidth"] > vp_size["width"] + 1:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.HIGH,
                            title=f"Horizontal overflow at {vp_name} ({vp_size['width']}px)",
                            description=(
                                f"Page scroll width ({overflow['scrollWidth']}px) exceeds "
                                f"viewport width ({vp_size['width']}px). Content is clipped "
                                f"or requires horizontal scrolling."
                            ),
                            url=url,
                            estimated_fix_minutes=15,
                            metadata={
                                "viewport": vp_name,
                                "scroll_width": overflow["scrollWidth"],
                                "viewport_width": vp_size["width"],
                            },
                        )
                    )
            except Exception:
                logger.debug(
                    "Overflow check failed for %s at %s", url, vp_name, exc_info=True
                )

        # ---- 3. Elements with overflow ---------------------------------------
        try:
            # Check on mobile viewport where overflow is most problematic
            await page.set_viewport_size(VIEWPORTS["mobile"])
            await page.wait_for_timeout(200)
            overflow_els = await page.evaluate(_JS_OVERFLOW_ELEMENTS)
            for el in overflow_els[:5]:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title="Element overflows its container on mobile",
                        description=(
                            f"Element `{el['selector']}` has scrollWidth "
                            f"{el['scrollWidth']}px > clientWidth {el['clientWidth']}px."
                        ),
                        url=url,
                        element_selector=el["selector"],
                        fix_snippet="overflow-x: auto; /* or */ max-width: 100%;",
                        estimated_fix_minutes=10,
                        metadata=el,
                    )
                )
        except Exception:
            logger.debug("Element overflow check failed for %s", url, exc_info=True)

        # ---- 4. Zoom-blocking viewport meta ----------------------------------
        try:
            meta_content = await page.evaluate("""
                () => {
                    const meta = document.querySelector('meta[name="viewport"]');
                    return meta ? meta.getAttribute('content') : null;
                }
            """)
            if meta_content:
                lower = meta_content.lower().replace(" ", "")
                if "user-scalable=no" in lower:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.HIGH,
                            title="Zoom is disabled via user-scalable=no",
                            description=(
                                "The viewport meta tag contains `user-scalable=no`, "
                                "which prevents users from zooming. This is an "
                                "accessibility issue and a mobile usability failure."
                            ),
                            url=url,
                            element_selector='meta[name="viewport"]',
                            fix_snippet='<meta name="viewport" content="width=device-width, initial-scale=1">',
                            estimated_fix_minutes=5,
                        )
                    )
                max_scale_match = re.search(r"maximum-scale\s*=\s*([\d.]+)", lower)
                if max_scale_match:
                    scale_val = float(max_scale_match.group(1))
                    if scale_val <= 1.0:
                        findings.append(
                            Finding(
                                domain=self.domain,
                                severity=Severity.HIGH,
                                title="Zoom is effectively blocked via maximum-scale=1",
                                description=(
                                    f"The viewport meta tag sets `maximum-scale={scale_val}`, "
                                    "preventing users from zooming in. This harms "
                                    "accessibility for visually impaired users."
                                ),
                                url=url,
                                element_selector='meta[name="viewport"]',
                                fix_snippet='<meta name="viewport" content="width=device-width, initial-scale=1">',
                                estimated_fix_minutes=5,
                            )
                        )
        except Exception:
            logger.debug("Viewport meta check failed for %s", url, exc_info=True)

        # ---- 5. Images without width/height (CLS risk) -----------------------
        try:
            images = await page.evaluate(_JS_IMAGES_WITHOUT_DIMENSIONS)
            if images:
                details = [f"`{img['src'][:80]}`" for img in images[:10]]
                count = len(images)
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title=f"{count} image(s) missing explicit width/height attributes",
                        description=(
                            f"Found {count} image(s) without both width and height "
                            "attributes, causing cumulative layout shift (CLS):\n"
                            + "\n".join(f"  - {d}" for d in details)
                            + ("\n  ... and more" if count > 10 else "")
                        ),
                        url=url,
                        element_selector=images[0].get("selector") if images else None,
                        fix_snippet='<img src="..." width="800" height="600" alt="...">',
                        estimated_fix_minutes=min(5 * count, 30),
                        metadata={"count": count, "images": [i["src"][:120] for i in images[:10]]},
                    )
                )
        except Exception:
            logger.debug("Image dimension check failed for %s", url, exc_info=True)

        # ---- 6. Touch targets < 44x44 on mobile ------------------------------
        try:
            await page.set_viewport_size(VIEWPORTS["mobile"])
            await page.wait_for_timeout(200)
            small_targets = await page.evaluate(_JS_TOUCH_TARGETS)
            if small_targets:
                # Group into a single finding per page
                details = [
                    f"`{t['selector']}` ({t['width']}x{t['height']}px) \"{t['text']}\""
                    for t in small_targets[:10]
                ]
                count = len(small_targets)
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title=f"{count} touch target(s) too small on mobile",
                        description=(
                            f"Found {count} interactive element(s) below the minimum "
                            "recommended 44x44px touch target size:\n"
                            + "\n".join(f"  - {d}" for d in details)
                            + ("\n  ... and more" if count > 10 else "")
                        ),
                        url=url,
                        element_selector=small_targets[0]["selector"] if small_targets else None,
                        fix_snippet="min-width: 44px; min-height: 44px;",
                        estimated_fix_minutes=min(5 * count, 30),
                        metadata={
                            "count": count,
                            "targets": small_targets[:10],
                        },
                    )
                )
        except Exception:
            logger.debug("Touch target check failed for %s", url, exc_info=True)

        # ---- 7. Text smaller than 12px on mobile -----------------------------
        try:
            await page.set_viewport_size(VIEWPORTS["mobile"])
            await page.wait_for_timeout(200)
            small_text = await page.evaluate(_JS_SMALL_TEXT)
            if small_text:
                details = [
                    f"`{item['selector']}` ({item['fontSize']}px) \"{item['text'][:40]}\""
                    for item in small_text[:10]
                ]
                count = len(small_text)
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.LOW,
                        title=f"{count} text element(s) too small on mobile",
                        description=(
                            f"Found {count} text element(s) with font-size below 12px:\n"
                            + "\n".join(f"  - {d}" for d in details)
                            + ("\n  ... and more" if count > 10 else "")
                        ),
                        url=url,
                        element_selector=small_text[0]["selector"] if small_text else None,
                        fix_snippet="font-size: 14px; /* or use rem units */",
                        estimated_fix_minutes=min(5 * count, 25),
                        metadata={
                            "count": count,
                            "elements": small_text[:10],
                        },
                    )
                )
        except Exception:
            logger.debug("Small text check failed for %s", url, exc_info=True)

        # Restore desktop viewport for subsequent analyzers
        try:
            await page.set_viewport_size(VIEWPORTS["desktop"])
        except Exception:
            pass

        return findings
