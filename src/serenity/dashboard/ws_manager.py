"""Thread-safe WebSocket connection manager."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket

from serenity.dashboard.messages import WSMessage

logger = logging.getLogger("serenity.dashboard.ws")


class ConnectionManager:
    """Manages active WebSocket connections with thread-safe operations.

    All public methods are safe to call from any coroutine — internal state
    is protected by an :class:`asyncio.Lock`.
    """

    def __init__(self) -> None:
        self._active: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        """Number of currently connected clients."""
        return len(self._active)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self._active.add(websocket)
        logger.info(
            "Dashboard client connected (%d active)", self.client_count,
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active set."""
        async with self._lock:
            self._active.discard(websocket)
        logger.info(
            "Dashboard client disconnected (%d active)", self.client_count,
        )

    async def broadcast(self, message: WSMessage) -> None:
        """Send *message* to every connected client.

        Connections that fail during send are silently removed so a single
        bad client never blocks the rest.
        """
        json_str = message.model_dump_json()

        async with self._lock:
            targets = list(self._active)

        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(json_str)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._active.discard(ws)
            logger.debug("Removed %d dead connections", len(dead))

    async def broadcast_json(self, data: dict) -> None:
        """Convenience: broadcast a raw dict as JSON text."""
        import json as _json

        text = _json.dumps(data)

        async with self._lock:
            targets = list(self._active)

        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._active.discard(ws)
