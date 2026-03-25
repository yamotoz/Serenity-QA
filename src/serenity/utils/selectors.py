"""CSS / XPath selector generation for Playwright ElementHandles."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import ElementHandle, Page


async def generate_selector(page: Page, element: ElementHandle) -> str:
    """Generate a stable CSS selector for a Playwright ElementHandle.

    Strategy priority:
    1. ``[data-testid="..."]``
    2. ``#id``
    3. Unique ``tag.class`` combination
    4. ``nth-child`` path from the nearest identifiable ancestor

    Args:
        page: The Playwright :class:`Page` instance.
        element: The target :class:`ElementHandle`.

    Returns:
        A CSS selector string that should uniquely identify the element on the
        current page.
    """
    selector = await page.evaluate(
        """(el) => {
            // 1. data-testid
            const testId = el.getAttribute('data-testid');
            if (testId) return `[data-testid="${testId}"]`;

            // 2. id attribute
            const id = el.getAttribute('id');
            if (id && document.querySelectorAll('#' + CSS.escape(id)).length === 1) {
                return '#' + CSS.escape(id);
            }

            // 3. Unique tag + class combination
            const tag = el.tagName.toLowerCase();
            const classes = Array.from(el.classList).filter(c => c.trim());
            if (classes.length > 0) {
                const classSelector = tag + '.' + classes.map(c => CSS.escape(c)).join('.');
                if (document.querySelectorAll(classSelector).length === 1) {
                    return classSelector;
                }
            }

            // 4. Build an nth-child path up to body or an element with id
            function nthChildPath(node) {
                const parts = [];
                let current = node;
                while (current && current !== document.body && current !== document.documentElement) {
                    let seg = current.tagName.toLowerCase();

                    // Check for id on ancestor — if found, anchor there
                    const cid = current.getAttribute('id');
                    if (cid && document.querySelectorAll('#' + CSS.escape(cid)).length === 1) {
                        parts.unshift('#' + CSS.escape(cid));
                        return parts.join(' > ');
                    }

                    // Compute nth-child index
                    const parent = current.parentElement;
                    if (parent) {
                        const siblings = Array.from(parent.children);
                        const index = siblings.indexOf(current) + 1;
                        seg += ':nth-child(' + index + ')';
                    }

                    parts.unshift(seg);
                    current = current.parentElement;
                }
                parts.unshift('body');
                return parts.join(' > ');
            }

            return nthChildPath(el);
        }""",
        element,
    )
    return selector


async def generate_xpath(page: Page, element: ElementHandle) -> str:
    """Generate an XPath expression for a Playwright ElementHandle.

    The resulting XPath uses positional indices relative to parent elements and
    anchors on ``id`` attributes when available.

    Args:
        page: The Playwright :class:`Page` instance.
        element: The target :class:`ElementHandle`.

    Returns:
        An XPath string like ``//*[@id="main"]/div[2]/a[1]``.
    """
    xpath: str = await page.evaluate(
        """(el) => {
            function getXPath(node) {
                // If node has a unique id, use it as anchor
                if (node.id && document.querySelectorAll('#' + CSS.escape(node.id)).length === 1) {
                    return '//*[@id="' + node.id + '"]';
                }
                if (node === document.body) return '/html/body';
                if (node === document.documentElement) return '/html';
                if (!node.parentElement) return '';

                const siblings = Array.from(node.parentElement.children).filter(
                    c => c.tagName === node.tagName
                );
                const index = siblings.indexOf(node) + 1;
                const tag = node.tagName.toLowerCase();
                const suffix = siblings.length > 1 ? '[' + index + ']' : '';
                return getXPath(node.parentElement) + '/' + tag + suffix;
            }
            return getXPath(el);
        }""",
        element,
    )
    return xpath
