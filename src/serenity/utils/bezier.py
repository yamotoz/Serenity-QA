"""Cubic Bezier curve mouse-trajectory generation for human-like cursor
movement."""

from __future__ import annotations

import math
import random


def generate_bezier_path(
    start: tuple[float, float],
    end: tuple[float, float],
    num_points: int = 50,
) -> list[tuple[float, float]]:
    """Generate a smooth cubic Bezier path between two screen coordinates.

    Two control points are placed at random offsets perpendicular to the line
    connecting *start* and *end*, producing a natural-looking arc rather than a
    straight line.

    Args:
        start: ``(x, y)`` origin.
        end: ``(x, y)`` destination.
        num_points: Number of points to sample along the curve (including the
            start and end points).

    Returns:
        A list of ``(x, y)`` float tuples tracing the curve.
    """
    if num_points < 2:
        num_points = 2

    sx, sy = start
    ex, ey = end

    dx = ex - sx
    dy = ey - sy
    distance = math.hypot(dx, dy)

    # Perpendicular unit vector for control-point offsets.
    if distance == 0:
        return [start] * num_points
    perp_x = -dy / distance
    perp_y = dx / distance

    # Random lateral offset scaled to ~20-50% of the travel distance, with
    # opposite signs so the curve has an S-like or arc shape.
    offset1 = random.uniform(0.2, 0.5) * distance * random.choice((-1, 1))
    offset2 = random.uniform(0.1, 0.4) * distance * random.choice((-1, 1))

    # Control point 1 at roughly 1/3 along the straight line.
    cp1x = sx + dx * random.uniform(0.2, 0.4) + perp_x * offset1
    cp1y = sy + dy * random.uniform(0.2, 0.4) + perp_y * offset1

    # Control point 2 at roughly 2/3 along the straight line.
    cp2x = sx + dx * random.uniform(0.6, 0.8) + perp_x * offset2
    cp2y = sy + dy * random.uniform(0.6, 0.8) + perp_y * offset2

    points: list[tuple[float, float]] = []
    for i in range(num_points):
        t = i / (num_points - 1)
        u = 1 - t
        # Cubic Bezier formula: B(t) = (1-t)^3*P0 + 3*(1-t)^2*t*P1
        #                              + 3*(1-t)*t^2*P2 + t^3*P3
        x = (
            u * u * u * sx
            + 3 * u * u * t * cp1x
            + 3 * u * t * t * cp2x
            + t * t * t * ex
        )
        y = (
            u * u * u * sy
            + 3 * u * u * t * cp1y
            + 3 * u * t * t * cp2y
            + t * t * t * ey
        )
        points.append((x, y))

    return points


def add_jitter(
    points: list[tuple[float, float]],
    jitter_px: float = 2.0,
) -> list[tuple[float, float]]:
    """Add small random offsets to each point for realism.

    The first and last points are left untouched so the cursor still starts
    and ends at the exact intended coordinates.

    Args:
        points: Ordered ``(x, y)`` coordinates (e.g. from
            :func:`generate_bezier_path`).
        jitter_px: Maximum pixel offset applied uniformly in both axes.

    Returns:
        A new list of ``(x, y)`` tuples with jitter applied to interior points.
    """
    if len(points) <= 2:
        return list(points)

    result: list[tuple[float, float]] = [points[0]]
    for x, y in points[1:-1]:
        jx = x + random.uniform(-jitter_px, jitter_px)
        jy = y + random.uniform(-jitter_px, jitter_px)
        result.append((jx, jy))
    result.append(points[-1])
    return result
