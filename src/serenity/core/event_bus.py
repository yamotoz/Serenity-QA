"""Async pub/sub event bus — the nervous system of Serenity QA."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger("serenity.event_bus")

Listener = Callable[..., Coroutine[Any, Any, None]]


class EventBus:
    """Async event bus for decoupled inter-module communication.

    Analyzers emit events (e.g. ``finding.new``). The dashboard, CLI renderer,
    and state manager subscribe without the emitter knowing about them.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def on(self, event: str, callback: Listener) -> None:
        """Subscribe *callback* to *event*."""
        self._listeners[event].append(callback)

    def off(self, event: str, callback: Listener) -> None:
        """Unsubscribe *callback* from *event*."""
        try:
            self._listeners[event].remove(callback)
        except ValueError:
            pass

    async def emit(self, event: str, data: Any = None) -> None:
        """Emit *event* with *data* to all subscribers concurrently."""
        listeners = self._listeners.get(event, [])
        if not listeners:
            return

        tasks = []
        for listener in listeners:
            try:
                tasks.append(asyncio.create_task(listener(data)))
            except Exception:
                logger.exception("Failed to create task for listener on %s", event)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        "Listener %s on event '%s' raised: %s",
                        listeners[i].__qualname__,
                        event,
                        result,
                    )

    def clear(self) -> None:
        """Remove all listeners."""
        self._listeners.clear()
