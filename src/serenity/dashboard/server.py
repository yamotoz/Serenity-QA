"""Dashboard FastAPI server — serves the SPA and WebSocket endpoint."""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from serenity.constants import DASHBOARD_HOST, DASHBOARD_PORT
from serenity.dashboard.messages import WSMessage, create_message
from serenity.dashboard.ws_manager import ConnectionManager

logger = logging.getLogger("serenity.dashboard.server")

# Module-level references (set by ``start_dashboard``)
_manager = ConnectionManager()
_ctx: Any = None  # ScanContext, typed loosely to avoid circular imports

# ---------------------------------------------------------------------------
# Dashboard HTML — served from the static file bundled with the package
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _load_html() -> str:
    """Read the self-contained dashboard HTML file."""
    html_path = _STATIC_DIR / "index.html"
    if not html_path.exists():
        return "<h1>Dashboard HTML not found</h1>"
    return html_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------


def _create_app() -> FastAPI:
    """Build the FastAPI application with all routes."""

    app = FastAPI(
        title="Serenity QA Live Dashboard",
        docs_url=None,
        redoc_url=None,
    )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard_page() -> HTMLResponse:
        """Serve the self-contained dashboard SPA."""
        return HTMLResponse(content=_load_html())

    @app.get("/api/state", response_class=JSONResponse)
    async def api_state() -> JSONResponse:
        """Return a snapshot of the current scan state."""
        if _ctx is None:
            return JSONResponse(content={"error": "No active scan"}, status_code=503)

        state = _ctx.state
        config = _ctx.config

        findings_summary: list[dict[str, Any]] = []
        for f in state.findings:
            findings_summary.append({
                "id": f.id,
                "domain": f.domain,
                "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
                "title": f.title,
                "url": f.url,
                "timestamp": f.timestamp.isoformat() if hasattr(f.timestamp, "isoformat") else str(f.timestamp),
            })

        return JSONResponse(content={
            "target_url": config.target_url,
            "max_pages": config.max_pages,
            "elapsed_seconds": state.elapsed_seconds,
            "pages_analyzed": state.pages_analyzed,
            "total_findings": state.total_findings,
            "discovered_urls": list(state.discovered_urls),
            "analyzed_urls": list(state.analyzed_urls),
            "failed_urls": list(state.failed_urls),
            "domain_scores": state.domain_scores,
            "overall_score": state.overall_score,
            "findings": findings_summary,
        })

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """Handle a WebSocket connection for live dashboard updates."""
        await _manager.connect(websocket)
        try:
            # Send current state snapshot to newly connected client
            if _ctx is not None:
                snapshot = _build_snapshot()
                await websocket.send_text(snapshot.model_dump_json())

            # Keep connection alive; listen for client pings
            while True:
                data = await websocket.receive_text()
                # Client can send "ping" to keep alive
                if data == "ping":
                    pong = WSMessage(type="pong", payload={})
                    await websocket.send_text(pong.model_dump_json())
        except WebSocketDisconnect:
            await _manager.disconnect(websocket)
        except asyncio.CancelledError:
            await _manager.disconnect(websocket)
        except Exception:
            await _manager.disconnect(websocket)

    return app


def _build_snapshot() -> WSMessage:
    """Build a full state snapshot message for newly connected clients."""
    state = _ctx.state
    config = _ctx.config

    findings_list: list[dict[str, Any]] = []
    for f in state.findings:
        findings_list.append({
            "id": f.id,
            "domain": f.domain,
            "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
            "title": f.title,
            "url": f.url,
            "timestamp": f.timestamp.isoformat() if hasattr(f.timestamp, "isoformat") else str(f.timestamp),
        })

    page_statuses: dict[str, str] = {}
    for url in state.discovered_urls:
        if url in state.analyzed_urls:
            score = state.page_data.get(url)
            page_statuses[url] = "passed"  # default
        elif url in state.failed_urls:
            page_statuses[url] = "failed"
        else:
            page_statuses[url] = "pending"

    return WSMessage(
        type="state.snapshot",
        payload={
            "target_url": config.target_url,
            "max_pages": config.max_pages,
            "elapsed_seconds": state.elapsed_seconds,
            "pages_analyzed": state.pages_analyzed,
            "discovered_count": len(state.discovered_urls),
            "total_findings": state.total_findings,
            "domain_scores": state.domain_scores,
            "overall_score": state.overall_score,
            "findings": findings_list,
            "page_statuses": page_statuses,
        },
    )


# ---------------------------------------------------------------------------
# Event bus subscriber callbacks
# ---------------------------------------------------------------------------

async def _on_scan_started(data: Any) -> None:
    msg = create_message("scan.started", data)
    await _manager.broadcast(msg)


async def _on_scan_progress(data: Any) -> None:
    msg = create_message("scan.progress", data)
    await _manager.broadcast(msg)


async def _on_finding_new(data: Any) -> None:
    msg = create_message("finding.new", data)
    await _manager.broadcast(msg)


async def _on_score_update(data: Any) -> None:
    msg = create_message("score.update", data)
    await _manager.broadcast(msg)


async def _on_page_heatmap(data: Any) -> None:
    msg = create_message("page.heatmap", data)
    await _manager.broadcast(msg)


async def _on_alert_critical(data: Any) -> None:
    msg = create_message("alert.critical", data)
    await _manager.broadcast(msg)


async def _on_scan_completed(data: Any) -> None:
    msg = create_message("scan.completed", data)
    await _manager.broadcast(msg)


async def _on_page_analyzing(data: Any) -> None:
    msg = create_message("page.analyzing", data)
    await _manager.broadcast(msg)


async def _on_page_done(data: Any) -> None:
    msg = create_message("page.done", data)
    await _manager.broadcast(msg)


_EVENT_HANDLERS: dict[str, Any] = {
    "scan.started": _on_scan_started,
    "scan.progress": _on_scan_progress,
    "finding.new": _on_finding_new,
    "score.update": _on_score_update,
    "page.heatmap": _on_page_heatmap,
    "alert.critical": _on_alert_critical,
    "scan.completed": _on_scan_completed,
    "page.analyzing": _on_page_analyzing,
    "page.done": _on_page_done,
}


def _subscribe_events(event_bus: Any) -> None:
    """Register all event-bus listeners that forward events to WebSocket."""
    for event_name, handler in _EVENT_HANDLERS.items():
        event_bus.on(event_name, handler)


def _unsubscribe_events(event_bus: Any) -> None:
    """Remove all event-bus listeners for clean shutdown."""
    for event_name, handler in _EVENT_HANDLERS.items():
        event_bus.off(event_name, handler)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def start_dashboard(ctx: Any) -> None:
    """Launch the live dashboard server.

    Called by the scan engine when ``--live`` is set.  Runs uvicorn in the
    current asyncio event loop, subscribes to the event bus, and opens the
    dashboard in the default browser.

    Parameters:
        ctx: :class:`~serenity.core.state.ScanContext` instance for the
             active scan.
    """
    global _ctx  # noqa: PLW0603
    _ctx = ctx

    host = getattr(ctx.config, "dashboard_host", DASHBOARD_HOST)
    port = getattr(ctx.config, "dashboard_port", DASHBOARD_PORT)

    # Subscribe to event bus
    _subscribe_events(ctx.event_bus)

    app = _create_app()

    # Suppress uvicorn error logs during shutdown
    logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="error",
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)

    # Open browser after a short delay so the server has time to bind
    async def _open_browser() -> None:
        await asyncio.sleep(1.0)
        url = f"http://{host}:{port}"
        logger.info("Opening dashboard at %s", url)
        try:
            webbrowser.open(url)
        except Exception:
            logger.warning("Could not open browser — navigate to http://%s:%d", host, port)

    asyncio.create_task(_open_browser())

    logger.info("Starting dashboard server on %s:%d", host, port)
    try:
        await server.serve()
    except (asyncio.CancelledError, Exception):
        server.should_exit = True
