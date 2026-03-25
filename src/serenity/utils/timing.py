"""Timing and distribution utilities for human-like delays."""

from __future__ import annotations

import random
import time


def poisson_delay(mean_ms: float = 500.0) -> float:
    """Generate a Poisson-distributed delay in milliseconds.

    Uses the exponential distribution as the inter-arrival time of a Poisson
    process, which produces naturalistic, variable wait times.

    Args:
        mean_ms: The average delay in milliseconds.  Must be positive.

    Returns:
        A non-negative delay value in milliseconds.
    """
    if mean_ms <= 0:
        return 0.0
    return random.expovariate(1.0 / mean_ms)


def gaussian_jitter(base_ms: float, std_ms: float = 50.0) -> float:
    """Add Gaussian noise to a base timing value.

    The result is clamped so it never goes below zero.

    Args:
        base_ms: The base timing value in milliseconds.
        std_ms: Standard deviation of the Gaussian noise.

    Returns:
        ``base_ms`` plus a random offset, clamped to ``>= 0``.
    """
    return max(0.0, random.gauss(base_ms, std_ms))


class Timer:
    """Simple context manager that measures wall-clock elapsed time.

    Usage::

        with Timer() as t:
            do_work()
        print(f"Took {t.elapsed_ms:.1f} ms")

    Attributes:
        elapsed: Elapsed time in **seconds** (float).
        elapsed_ms: Elapsed time in **milliseconds** (float).
    """

    def __init__(self) -> None:
        self._start: float = 0.0
        self.elapsed: float = 0.0
        self.elapsed_ms: float = 0.0

    # -- Sync context manager --------------------------------------------------

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.elapsed = time.perf_counter() - self._start
        self.elapsed_ms = self.elapsed * 1000.0

    # -- Async context manager -------------------------------------------------

    async def __aenter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.elapsed = time.perf_counter() - self._start
        self.elapsed_ms = self.elapsed * 1000.0

    def __repr__(self) -> str:
        return f"Timer(elapsed_ms={self.elapsed_ms:.2f})"
