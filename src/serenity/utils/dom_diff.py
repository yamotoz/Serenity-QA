"""DOM snapshot capture and comparison utilities."""

from __future__ import annotations

import difflib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


async def take_snapshot(page: Page) -> str:
    """Capture the current DOM as a serialized HTML string.

    Args:
        page: A Playwright :class:`Page` instance.

    Returns:
        The full outer HTML of the document (``page.content()``).
    """
    return await page.content()


def diff_snapshots(before: str, after: str) -> list[str]:
    """Compare two DOM snapshots and return human-readable change descriptions.

    The comparison uses :mod:`difflib` on a line-by-line basis.  Rather than
    producing a full unified diff, the function summarises what changed in
    terms of added, removed, and modified lines as well as an approximate
    node-level count.

    Args:
        before: The earlier HTML snapshot.
        after: The later HTML snapshot.

    Returns:
        A list of short description strings, e.g.
        ``["DOM changed: 3 lines added, 1 line removed, ~4 element(s) affected"]``.
        An empty list means no changes were detected.
    """
    if before == after:
        return []

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    differ = difflib.unified_diff(before_lines, after_lines, lineterm="")

    added = 0
    removed = 0
    for line in differ:
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1

    if added == 0 and removed == 0:
        return []

    # Estimate element-level changes by counting opening tags in diff lines.
    tag_pattern = re.compile(r"<[a-zA-Z][^/>]*>")

    # Re-run differ to count element tags (generators are single-use).
    differ2 = difflib.unified_diff(before_lines, after_lines, lineterm="")
    elements_affected = 0
    for line in differ2:
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            elements_affected += len(tag_pattern.findall(line))

    descriptions: list[str] = []

    parts: list[str] = []
    if added:
        parts.append(f"{added} line(s) added")
    if removed:
        parts.append(f"{removed} line(s) removed")

    summary = "DOM changed: " + ", ".join(parts)
    if elements_affected:
        summary += f", ~{elements_affected} element(s) affected"
    descriptions.append(summary)

    return descriptions
