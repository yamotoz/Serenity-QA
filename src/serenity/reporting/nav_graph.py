"""Navigation graph data generation for D3.js force-directed visualization."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from serenity.core.state import ScanContext


def generate_nav_graph_data(ctx: ScanContext) -> dict[str, Any]:
    """Convert navigation nodes and edges to a D3.js-compatible format.

    Returns a dict with:
        nodes: list of {id, label, status} dicts
        links: list of {source, target, label} dicts
    """
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []

    for url, node in ctx.state.nav_nodes.items():
        status = "normal"
        if node.is_orphan:
            status = "orphan"
        elif node.is_dead_end:
            status = "dead_end"

        nodes.append({
            "id": url,
            "label": node.title or _truncate_url(url),
            "status": status,
        })

    for edge in ctx.state.nav_edges:
        links.append({
            "source": edge.source_url,
            "target": edge.target_url,
            "label": edge.trigger_text or edge.trigger_selector or "",
        })

    return {"nodes": nodes, "links": links}


def _truncate_url(url: str, max_len: int = 60) -> str:
    """Shorten a URL for display as a label."""
    if len(url) <= max_len:
        return url
    return url[:max_len - 3] + "..."
