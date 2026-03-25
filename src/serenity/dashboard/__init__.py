"""Serenity QA Live Dashboard — real-time scan monitoring via WebSocket.

Provides a FastAPI server that serves a single-page dashboard application,
streaming scan progress, findings, scores, and alerts to connected browsers
through WebSocket connections.

Usage:
    The dashboard is activated by the ``--live`` CLI flag and launched by the
    scan engine via :func:`serenity.dashboard.server.start_dashboard`.
"""
