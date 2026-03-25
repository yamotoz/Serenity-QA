"""Domain 8 — Accessibility (WCAG 2.1) analyzer.

Checks colour contrast, missing alt text, unlabelled inputs, lang attribute,
skip-navigation links, focus order, ARIA landmarks, and interactive elements
without accessible names.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from serenity.analyzers.base import BaseAnalyzer
from serenity.constants import Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import Page

    from serenity.core.state import ScanContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WCAG 2.1 contrast helpers
# ---------------------------------------------------------------------------


def _relative_luminance(r: float, g: float, b: float) -> float:
    """Calculate relative luminance per WCAG 2.1."""

    def linearize(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)


def _contrast_ratio(l1: float, l2: float) -> float:
    """Return the contrast ratio between two luminance values."""
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _parse_rgb(rgb_string: str) -> tuple[float, float, float] | None:
    """Parse an rgb(r, g, b) or rgba(r, g, b, a) string into (r, g, b)."""
    match = re.match(
        r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", rgb_string
    )
    if match:
        return float(match.group(1)), float(match.group(2)), float(match.group(3))
    return None


# ---------------------------------------------------------------------------
# JavaScript snippets
# ---------------------------------------------------------------------------

_JS_TEXT_CONTRAST = """
() => {
    // Parse rgba string into [r, g, b, a]
    function parseRgba(str) {
        const m = str.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?/);
        if (!m) return null;
        return [parseInt(m[1]), parseInt(m[2]), parseInt(m[3]), m[4] !== undefined ? parseFloat(m[4]) : 1];
    }

    // Composite a semi-transparent color over an opaque background
    function composite(fg, bg) {
        const a = fg[3];
        return [
            Math.round(fg[0] * a + bg[0] * (1 - a)),
            Math.round(fg[1] * a + bg[1] * (1 - a)),
            Math.round(fg[2] * a + bg[2] * (1 - a)),
            1
        ];
    }

    // Walk up the DOM compositing backgrounds to get the effective rendered color
    function getEffectiveBg(el) {
        const layers = [];
        let current = el;
        while (current && current !== document.documentElement) {
            const style = window.getComputedStyle(current);
            const bg = style.backgroundColor;
            if (bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)') {
                const parsed = parseRgba(bg);
                if (parsed) {
                    layers.push(parsed);
                    // If this layer is fully opaque, stop
                    if (parsed[3] >= 0.99) break;
                }
            }
            current = current.parentElement;
        }
        if (layers.length === 0) return 'rgb(255, 255, 255)';
        // Composite from bottom to top (last layer is the base)
        let result = layers[layers.length - 1];
        if (result[3] < 0.99) {
            // Base isn't fully opaque, assume white behind it
            result = composite(result, [255, 255, 255, 1]);
        }
        for (let i = layers.length - 2; i >= 0; i--) {
            result = composite(layers[i], result);
        }
        return 'rgb(' + result[0] + ', ' + result[1] + ', ' + result[2] + ')';
    }
    const results = [];
    const walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_TEXT,
        {
            acceptNode: (node) =>
                node.textContent.trim().length > 1
                    ? NodeFilter.FILTER_ACCEPT
                    : NodeFilter.FILTER_REJECT,
        }
    );
    const seen = new Set();
    while (walker.nextNode()) {
        const el = walker.currentNode.parentElement;
        if (!el || seen.has(el)) continue;
        seen.add(el);
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        if (parseFloat(style.opacity) < 0.1) continue;
        const color = style.color;
        const bgColor = getEffectiveBg(el);
        const fontSize = parseFloat(style.fontSize);
        const fontWeight = parseInt(style.fontWeight, 10) || 400;
        const tag = el.tagName.toLowerCase();
        const id = el.id ? '#' + el.id : '';

        // Detect video/canvas background — contrast calculation is unreliable
        // when text overlays a <video> or <canvas> element.
        // Check: does any ancestor contain a <video> or <canvas>?
        // This catches both child videos and sibling videos in the same container.
        let hasVideoBg = false;
        let bgCheck = el.parentElement;
        while (bgCheck && bgCheck !== document.documentElement) {
            // Check for video/canvas anywhere within this ancestor
            if (bgCheck.querySelector('video, canvas')) {
                hasVideoBg = true;
                break;
            }
            // Also check for CSS background-image with video-like patterns
            const bgImg = window.getComputedStyle(bgCheck).backgroundImage;
            if (bgImg && bgImg !== 'none' && (bgImg.includes('gradient') || bgImg.includes('url'))) {
                // Has a background image/gradient — still compute contrast from CSS
                break;
            }
            bgCheck = bgCheck.parentElement;
        }

        // Detect decorative/incidental context (navigation indicators, badges, steppers)
        const elCls = el.className && typeof el.className === 'string' ? el.className : '';
        const parentCls = el.parentElement ? (el.parentElement.className || '') : '';
        const grandCls = el.parentElement && el.parentElement.parentElement
            ? (el.parentElement.parentElement.className || '') : '';
        const allCtx = (elCls + ' ' + parentCls + ' ' + grandCls).toLowerCase();
        const isDecorative = fontSize <= 11 && (
            allCtx.includes('stepper') || allCtx.includes('wizard') ||
            allCtx.includes('indicator') || allCtx.includes('badge') ||
            allCtx.includes('step') || allCtx.includes('tab') ||
            allCtx.includes('progress') || allCtx.includes('material-symbols') ||
            allCtx.includes('material-icons') || allCtx.includes('icon') ||
            el.closest('[role="tablist"]') !== null ||
            el.closest('[role="navigation"]') !== null
        );

        results.push({
            selector: tag + id,
            color: color,
            bgColor: bgColor,
            fontSize: fontSize,
            fontWeight: fontWeight,
            text: el.textContent.trim().substring(0, 60),
            isDecorative: isDecorative,
            hasVideoBg: hasVideoBg,
        });
        if (results.length >= 50) break;
    }
    return results;
}
"""

_JS_UNLABELLED_INPUTS = """
() => {
    const inputs = document.querySelectorAll(
        'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]):not([type="image"]), select, textarea'
    );
    const results = [];
    for (const inp of inputs) {
        const id = inp.id;
        let hasLabel = false;
        // Check for associated <label for="id">
        if (id) {
            hasLabel = !!document.querySelector('label[for="' + CSS.escape(id) + '"]');
        }
        // Check for wrapping <label>
        if (!hasLabel) {
            hasLabel = !!inp.closest('label');
        }
        // Check for aria-label or aria-labelledby
        if (!hasLabel) {
            hasLabel = !!(inp.getAttribute('aria-label') || inp.getAttribute('aria-labelledby'));
        }
        // Check for title attribute as last resort
        if (!hasLabel) {
            hasLabel = !!inp.getAttribute('title');
        }
        if (!hasLabel) {
            const tag = inp.tagName.toLowerCase();
            const type = inp.type || '';
            const name = inp.name || '';
            const elId = inp.id ? '#' + inp.id : '';
            results.push({
                selector: tag + elId,
                type: type,
                name: name,
            });
        }
        if (results.length >= 20) break;
    }
    return results;
}
"""

_JS_INTERACTIVE_WITHOUT_NAME = """
() => {
    const elements = document.querySelectorAll(
        'a, button, [role="button"], [role="link"], [role="tab"], [tabindex]:not([tabindex="-1"])'
    );
    const results = [];
    for (const el of elements) {
        const text = (el.textContent || '').trim();
        const ariaLabel = el.getAttribute('aria-label') || '';
        const ariaLabelledby = el.getAttribute('aria-labelledby') || '';
        const title = el.getAttribute('title') || '';
        const imgAlt = el.querySelector('img[alt]');
        const hasName = text || ariaLabel || ariaLabelledby || title || imgAlt;
        if (!hasName) {
            const tag = el.tagName.toLowerCase();
            const id = el.id ? '#' + el.id : '';
            const cls = el.className && typeof el.className === 'string'
                ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.')
                : '';
            results.push({
                selector: tag + id + cls,
                outerHTML: el.outerHTML.substring(0, 150),
            });
        }
        if (results.length >= 15) break;
    }
    return results;
}
"""

_JS_ARIA_LANDMARKS = """
() => {
    const landmarks = {
        banner: !!document.querySelector('header, [role="banner"]'),
        navigation: !!document.querySelector('nav, [role="navigation"]'),
        main: !!document.querySelector('main, [role="main"]'),
        contentinfo: !!document.querySelector('footer, [role="contentinfo"]'),
    };
    return landmarks;
}
"""

_JS_SKIP_NAV = """
() => {
    // Check if the first focusable element is a skip-to-content link
    const focusable = document.querySelectorAll(
        'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (focusable.length === 0) return { found: false, reason: 'no focusable elements' };
    const first = focusable[0];
    const href = first.getAttribute('href') || '';
    const text = (first.textContent || '').trim().toLowerCase();
    const isSkip = (
        href.startsWith('#') &&
        (text.includes('skip') || text.includes('pular') ||
         text.includes('main') || text.includes('content') ||
         text.includes('conteudo') || text.includes('conteúdo'))
    );
    return {
        found: isSkip,
        firstTag: first.tagName.toLowerCase(),
        firstText: (first.textContent || '').trim().substring(0, 60),
        firstHref: href,
    };
}
"""

_JS_FOCUS_ORDER = """
async () => {
    const focusable = Array.from(document.querySelectorAll(
        'a[href], button, input:not([type="hidden"]), select, textarea, [tabindex]:not([tabindex="-1"])'
    )).filter(el => {
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden';
    });
    const positions = [];
    for (const el of focusable.slice(0, 50)) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        positions.push({
            tag: el.tagName.toLowerCase(),
            top: Math.round(rect.top),
            left: Math.round(rect.left),
        });
    }
    // Check if DOM order roughly follows visual order (top-to-bottom, left-to-right)
    let outOfOrder = 0;
    for (let i = 1; i < positions.length; i++) {
        const prev = positions[i - 1];
        const curr = positions[i];
        // Allow 50px tolerance for elements on the same visual line
        if (curr.top < prev.top - 50) {
            outOfOrder++;
        }
    }
    return { total: positions.length, outOfOrder: outOfOrder };
}
"""


class AccessibilityAnalyzer(BaseAnalyzer):
    """Checks WCAG 2.1 accessibility criteria."""

    domain: str = "accessibility"
    weight: float = 0.10

    async def analyze_page(
        self, ctx: ScanContext, url: str, page: Page
    ) -> list[Finding]:
        findings: list[Finding] = []

        # ---- 1. Contrast ratio -----------------------------------------------
        try:
            text_samples = await page.evaluate(_JS_TEXT_CONTRAST)
            for sample in text_samples:
                fg = _parse_rgb(sample["color"])
                bg = _parse_rgb(sample["bgColor"])
                if not fg or not bg:
                    continue
                # Skip transparent / identical background (often inherited)
                if bg == (0.0, 0.0, 0.0) and fg == (0.0, 0.0, 0.0):
                    continue

                # Skip text over video/canvas backgrounds — CSS-based contrast
                # calculation is unreliable (the actual bg is a video frame)
                if sample.get("hasVideoBg", False):
                    continue

                fg_lum = _relative_luminance(*fg)
                bg_lum = _relative_luminance(*bg)
                ratio = _contrast_ratio(fg_lum, bg_lum)

                font_size = sample.get("fontSize", 16)
                font_weight = sample.get("fontWeight", 400)

                # WCAG: large text is >=18pt (24px) or >=14pt (18.66px) bold
                is_large = font_size >= 24 or (font_size >= 18.66 and font_weight >= 700)
                min_ratio = 3.0 if is_large else 4.5

                if ratio < min_ratio:
                    # Decorative/incidental text (tiny labels in steppers, badges, icons)
                    # gets lower severity — WCAG allows exceptions for incidental text
                    is_decorative = sample.get("isDecorative", False)
                    if is_decorative:
                        severity = Severity.LOW
                    elif ratio < 2.0:
                        # Effectively illegible — critical contrast failure
                        severity = Severity.HIGH
                    elif ratio < 3.0:
                        # Very difficult to read
                        severity = Severity.HIGH if not is_large else Severity.MEDIUM
                    elif ratio < 4.5:
                        # Readable but fails WCAG — borderline cases
                        severity = Severity.MEDIUM if ratio < 3.5 else Severity.LOW
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=severity,
                            title="Insufficient colour contrast",
                            description=(
                                f"Element `{sample['selector']}` has contrast ratio "
                                f"{ratio:.2f}:1 (minimum {min_ratio}:1 for "
                                f"{'large' if is_large else 'normal'} text). "
                                f"Text: \"{sample['text']}\""
                            ),
                            url=url,
                            element_selector=sample["selector"],
                            estimated_fix_minutes=10,
                            metadata={
                                "contrast_ratio": round(ratio, 2),
                                "required_ratio": min_ratio,
                                "fg_color": sample["color"],
                                "bg_color": sample["bgColor"],
                                "font_size_px": font_size,
                            },
                        )
                    )
        except Exception:
            logger.debug("Contrast check failed for %s", url, exc_info=True)

        # ---- 2. Images without alt text --------------------------------------
        try:
            no_alt_images = await page.query_selector_all("img:not([alt])")
            if no_alt_images:
                img_sources = []
                for img in no_alt_images[:15]:
                    src = await img.get_attribute("src") or "(no src)"
                    img_sources.append(src[:120])

                count = len(no_alt_images)
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.HIGH,
                        title=f"{count} image(s) missing alt text",
                        description=(
                            f"Found {count} <img> element(s) without `alt` attribute. "
                            "Screen readers cannot describe these images.\n"
                            + "\n".join(f"  - {s}" for s in img_sources[:5])
                            + ("\n  ... and more" if count > 5 else "")
                        ),
                        url=url,
                        element_selector="img",
                        fix_snippet='<img src="..." alt="Descriptive text here">',
                        estimated_fix_minutes=min(5 * count, 30),
                        metadata={"count": count, "sources": img_sources[:10]},
                    )
                )
        except Exception:
            logger.debug("Alt-text check failed for %s", url, exc_info=True)

        # ---- 3. Form inputs without labels -----------------------------------
        try:
            unlabelled = await page.evaluate(_JS_UNLABELLED_INPUTS)
            if unlabelled:
                # Group into a single finding per page instead of one per input
                input_details = [
                    f"`{inp['selector']}` (type={inp['type']}, name={inp['name']})"
                    for inp in unlabelled
                ]
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.HIGH,
                        title=f"{len(unlabelled)} form input(s) without associated label",
                        description=(
                            f"Found {len(unlabelled)} input(s) without an associated "
                            "<label>, aria-label, or aria-labelledby:\n"
                            + "\n".join(f"  - {d}" for d in input_details[:10])
                            + ("\n  ... and more" if len(input_details) > 10 else "")
                        ),
                        url=url,
                        element_selector=unlabelled[0]["selector"] if unlabelled else None,
                        fix_snippet=(
                            '<label for="inputId">Label text</label>\n'
                            '<input id="inputId" type="text">'
                        ),
                        estimated_fix_minutes=min(5 * len(unlabelled), 30),
                        metadata={"count": len(unlabelled), "inputs": unlabelled[:10]},
                    )
                )
        except Exception:
            logger.debug("Unlabelled input check failed for %s", url, exc_info=True)

        # ---- 4. HTML lang attribute ------------------------------------------
        try:
            import re as _re

            lang = None

            # Method 1: DOM property (fastest, works when JS preserves lang)
            try:
                lang = await page.evaluate(
                    "() => document.documentElement.lang || document.documentElement.getAttribute('lang') || ''"
                )
            except Exception:
                pass

            # Method 2: querySelector on the live DOM
            if not lang or not lang.strip():
                try:
                    lang = await page.evaluate(
                        "() => { const h = document.querySelector('html[lang]'); return h ? h.getAttribute('lang') : ''; }"
                    )
                except Exception:
                    pass

            # Method 3: Check the raw server-rendered HTML via HTTP.
            # page.content() returns the CURRENT DOM (after JS mutations),
            # which may have lost the lang attribute.  A direct HTTP request
            # gets the original server-rendered HTML which is what search
            # engines and initial screen reader parsing will see.
            if not lang or not lang.strip():
                try:
                    import httpx
                    async with httpx.AsyncClient(
                        follow_redirects=True, verify=False, timeout=10
                    ) as client:
                        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 Serenity-QA"})
                        match = _re.search(
                            r'<html[^>]*\slang=["\']([^"\']+)["\']',
                            resp.text[:3000],
                            _re.IGNORECASE,
                        )
                        if match:
                            lang = match.group(1)
                            logger.debug("Lang '%s' found via HTTP fallback for %s", lang, url)
                except Exception:
                    logger.debug("HTTP lang fallback failed for %s", url, exc_info=True)

            if not lang or not lang.strip():
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.HIGH,
                        title="Missing lang attribute on <html>",
                        description=(
                            "The <html> element does not have a `lang` attribute. "
                            "Screen readers use this to determine the correct "
                            "pronunciation rules."
                        ),
                        url=url,
                        element_selector="html",
                        fix_snippet='<html lang="en">',
                        estimated_fix_minutes=5,
                    )
                )
        except Exception:
            logger.debug("Lang attribute check failed for %s", url, exc_info=True)

        # ---- 5. Skip navigation link -----------------------------------------
        try:
            skip_info = await page.evaluate(_JS_SKIP_NAV)
            if not skip_info.get("found"):
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title="No skip-to-content navigation link",
                        description=(
                            "The first focusable element is not a skip-to-content "
                            "link. Keyboard users must tab through the entire "
                            "navigation on every page load. "
                            f"First focusable: <{skip_info.get('firstTag', '?')}> "
                            f"\"{skip_info.get('firstText', '')}\""
                        ),
                        url=url,
                        fix_snippet=(
                            '<a href="#main-content" class="skip-link">Skip to content</a>\n'
                            '<!-- ... navigation ... -->\n'
                            '<main id="main-content">...</main>'
                        ),
                        estimated_fix_minutes=15,
                    )
                )
        except Exception:
            logger.debug("Skip nav check failed for %s", url, exc_info=True)

        # ---- 6. Focus order --------------------------------------------------
        try:
            focus_info = await page.evaluate(_JS_FOCUS_ORDER)
            total = focus_info.get("total", 0)
            out_of_order = focus_info.get("outOfOrder", 0)
            if total > 0 and out_of_order > total * 0.3:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title="Focus order does not follow visual layout",
                        description=(
                            f"Of {total} focusable elements, {out_of_order} appear "
                            "out of visual order when tabbing. This may confuse "
                            "keyboard-only users."
                        ),
                        url=url,
                        estimated_fix_minutes=30,
                        metadata={
                            "total_focusable": total,
                            "out_of_order": out_of_order,
                        },
                    )
                )
        except Exception:
            logger.debug("Focus order check failed for %s", url, exc_info=True)

        # ---- 7. Interactive elements without accessible name ------------------
        try:
            nameless = await page.evaluate(_JS_INTERACTIVE_WITHOUT_NAME)
            if nameless:
                el_details = [
                    f"`{el['selector']}`: {el['outerHTML'][:80]}"
                    for el in nameless
                ]
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.HIGH,
                        title=f"{len(nameless)} interactive element(s) without accessible name",
                        description=(
                            f"Found {len(nameless)} interactive element(s) with no "
                            "visible text, aria-label, aria-labelledby, or title:\n"
                            + "\n".join(f"  - {d}" for d in el_details[:5])
                            + ("\n  ... and more" if len(el_details) > 5 else "")
                        ),
                        url=url,
                        element_selector=nameless[0]["selector"] if nameless else None,
                        fix_snippet='<button aria-label="Descriptive action">...</button>',
                        estimated_fix_minutes=min(5 * len(nameless), 25),
                        metadata={"count": len(nameless), "elements": nameless[:10]},
                    )
                )
        except Exception:
            logger.debug(
                "Interactive name check failed for %s", url, exc_info=True
            )

        # ---- 8. ARIA landmarks -----------------------------------------------
        try:
            landmarks = await page.evaluate(_JS_ARIA_LANDMARKS)
            missing = [name for name, present in landmarks.items() if not present]
            if missing:
                # Login/auth pages are standalone by design — they typically
                # don't have full header/nav/footer.  WCAG only strictly
                # requires <main>.  For login pages, only require main.
                is_login_page = await page.evaluate("""() => {
                    const form = document.querySelector('form');
                    if (!form) return false;
                    const hasPwd = !!form.querySelector('input[type="password"]');
                    const hasEmail = !!form.querySelector('input[type="email"], input[name="email"]');
                    const isSmallPage = document.querySelectorAll('a[href]').length < 10;
                    return hasPwd && hasEmail && isSmallPage;
                }""")
                if is_login_page:
                    # For login pages, only flag if <main> is missing
                    missing = [m for m in missing if m == "main"]
                    if not missing:
                        pass  # Login page with <main> is fine

                severity = Severity.HIGH if "main" in missing else Severity.MEDIUM
                if is_login_page and missing:
                    severity = Severity.MEDIUM  # Login pages get lower severity

                if missing:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=severity,
                            title="Missing ARIA landmark regions",
                            description=(
                                f"The page is missing the following landmark regions: "
                                f"{', '.join(missing)}. Landmarks help assistive "
                                "technology users navigate the page structure."
                            ),
                            url=url,
                            fix_snippet=(
                                "<header><!-- banner --></header>\n"
                                "<nav><!-- navigation --></nav>\n"
                                "<main><!-- main content --></main>\n"
                                "<footer><!-- contentinfo --></footer>"
                            ),
                            estimated_fix_minutes=15,
                            metadata={"missing_landmarks": missing},
                        )
                    )
        except Exception:
            logger.debug("Landmarks check failed for %s", url, exc_info=True)

        return findings
