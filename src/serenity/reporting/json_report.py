"""JSON report generation — machine-readable scan results."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from serenity import __version__
from serenity.constants import DOMAIN_WEIGHTS, Verdict
from serenity.core.state import ScanContext
from serenity.reporting.nav_graph import generate_nav_graph_data
from serenity.scoring.engine import ScoringEngine
from serenity.scoring.finding import Finding


async def generate_json_report(
    ctx: ScanContext,
    scores: dict[str, Any],
    verdict: Verdict,
    output_dir: Path,
) -> Path:
    """Generate a comprehensive JSON report and write it to disk.

    Args:
        ctx: The scan context containing all collected state.
        scores: Domain scores dict as returned by ``ScoringEngine.calculate``.
        verdict: The overall verdict for the scan.
        output_dir: Directory to write the report file into.

    Returns:
        The ``Path`` to the written JSON file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    findings = ctx.state.findings
    engine = ScoringEngine()
    prioritized = engine.get_prioritized_fixes(findings)

    # Group findings by effective domain
    domain_findings: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        domain_findings[f.domain].append(f)

    # Build per-domain block
    domains_block: dict[str, Any] = {}
    domain_scores = scores.get("domains", {})
    for domain, weight in DOMAIN_WEIGHTS.items():
        d_findings = domain_findings.get(domain, [])
        domains_block[domain] = {
            "score": domain_scores.get(domain, 100.0),
            "weight": weight,
            "findings_count": len(d_findings),
            "findings": [_serialize_finding(f) for f in d_findings],
        }

    report: dict[str, Any] = {
        "scan_metadata": {
            "url": ctx.config.target_url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(ctx.state.elapsed_seconds, 2),
            "version": __version__,
            "pages_analyzed": ctx.state.pages_analyzed,
            "total_findings": ctx.state.total_findings,
        },
        "overall_score": scores.get("overall", ctx.state.overall_score),
        "verdict": verdict.value,
        "domains": domains_block,
        "findings": [_serialize_finding(f) for f in findings],
        "navigation_graph": generate_nav_graph_data(ctx),
        "recommendations": [
            _serialize_recommendation(f, rank)
            for rank, f in enumerate(prioritized, 1)
        ],
    }

    output_path = output_dir / "serenity-report.json"
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return output_path


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize_finding(f: Finding) -> dict[str, Any]:
    """Convert a Finding to a JSON-safe dictionary."""
    return {
        "id": f.id,
        "domain": f.domain,
        "severity": f.severity.value,
        "title": f.title,
        "description": f.description,
        "url": f.url,
        "element_selector": f.element_selector,
        "screenshot_path": f.screenshot_path,
        "fix_snippet": f.fix_snippet,
        "estimated_fix_minutes": f.estimated_fix_minutes,
        "deduction_points": f.deduction_points,
        "metadata": f.metadata,
        "timestamp": f.timestamp.isoformat() if f.timestamp else None,
    }


def _serialize_recommendation(f: Finding, rank: int) -> dict[str, Any]:
    """Convert a prioritized finding into a recommendation entry."""
    impact_ratio = (
        f.deduction_points / max(f.estimated_fix_minutes, 1)
    )
    return {
        "rank": rank,
        "finding_id": f.id,
        "title": f.title,
        "domain": f.domain,
        "severity": f.severity.value,
        "deduction_points": f.deduction_points,
        "estimated_fix_minutes": f.estimated_fix_minutes,
        "impact_ratio": round(impact_ratio, 2),
        "fix_snippet": f.fix_snippet,
    }
