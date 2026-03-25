"""Screenshot annotation utilities for reports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from serenity.scoring.finding import Finding


def annotate_screenshot(image_path: str, findings: list[Finding]) -> str:
    """Annotate a screenshot image with finding markers.

    This is a placeholder implementation. In a future release it will use
    Pillow to draw bounding boxes and severity labels on the screenshot
    based on the element selectors in each finding.

    Args:
        image_path: Path to the original screenshot image.
        findings: Findings that relate to elements visible in the screenshot.

    Returns:
        Path to the (potentially annotated) screenshot.  Currently returns
        the original path unchanged.
    """
    # TODO: implement actual annotation with Pillow
    return image_path
