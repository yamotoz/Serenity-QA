"""Pydantic models for WebSocket message serialization."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class WSMessage(BaseModel):
    """A single WebSocket message sent from server to dashboard clients.

    Attributes:
        type: Event type identifier (e.g. ``scan.progress``, ``finding.new``).
        timestamp: ISO-8601 timestamp of when the event was created.
        payload: Arbitrary event-specific data dictionary.
    """

    type: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    payload: dict[str, Any] = Field(default_factory=dict)


def create_message(event_type: str, data: Any = None) -> WSMessage:
    """Create a :class:`WSMessage` from raw event data.

    Parameters:
        event_type: The event bus event name (e.g. ``scan.started``).
        data: Arbitrary event data.  Pydantic models are serialized via
              ``model_dump``; other values are wrapped in ``{"data": ...}``.

    Returns:
        A :class:`WSMessage` ready for JSON serialization and broadcast.
    """
    if data is None:
        payload: dict[str, Any] = {}
    elif isinstance(data, dict):
        payload = data
    elif hasattr(data, "model_dump"):
        payload = data.model_dump(mode="json")
    elif hasattr(data, "__dict__"):
        payload = _safe_dict(data.__dict__)
    else:
        payload = {"data": data}

    return WSMessage(type=event_type, payload=payload)


def _safe_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Convert a dict to JSON-safe types, stringifying anything non-trivial."""
    safe: dict[str, Any] = {}
    for key, value in d.items():
        if key.startswith("_"):
            continue
        if isinstance(value, (str, int, float, bool, type(None))):
            safe[key] = value
        elif isinstance(value, (list, tuple)):
            safe[key] = [
                v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
                for v in value
            ]
        elif isinstance(value, dict):
            safe[key] = _safe_dict(value)
        elif isinstance(value, set):
            safe[key] = list(value)
        elif isinstance(value, datetime):
            safe[key] = value.isoformat()
        else:
            safe[key] = str(value)
    return safe
