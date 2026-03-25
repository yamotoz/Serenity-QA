"""Screenshot capture utilities for Playwright pages."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from serenity.utils.sanitize import safe_filename

if TYPE_CHECKING:
    from playwright.async_api import Page


async def capture_screenshot(
    page: Page,
    viewport: dict[str, int],
    output_path: str,
) -> str:
    """Set the viewport size, take a screenshot, and return the file path.

    Args:
        page: A Playwright :class:`Page` instance.
        viewport: A dict with ``width`` and ``height`` keys (pixel values).
        output_path: Absolute path where the PNG should be saved.

    Returns:
        The *output_path* on success.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    await page.set_viewport_size(viewport)
    # Allow a brief settle for any responsive layout recalculations.
    await page.wait_for_timeout(300)
    await page.screenshot(path=output_path, type="png")
    return output_path


async def capture_full_page_screenshot(page: Page, output_path: str) -> str:
    """Capture a full-page (scrolling) screenshot.

    Args:
        page: A Playwright :class:`Page` instance.
        output_path: Absolute path where the PNG should be saved.

    Returns:
        The *output_path* on success.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    await page.screenshot(path=output_path, full_page=True, type="png")
    return output_path


def safe_screenshot_path(output_dir: str, url: str, viewport: str) -> str:
    """Build a collision-resistant screenshot path from a URL and viewport label.

    Args:
        output_dir: Directory where screenshot files are stored.
        url: The page URL being captured.
        viewport: A human-readable viewport label, e.g. ``"desktop"``,
            ``"mobile"``, ``"1280x900"``.

    Returns:
        An absolute file path ending in ``.png``.
    """
    name = safe_filename(url)
    filename = f"{name}_{viewport}.png"
    return os.path.join(output_dir, filename)
