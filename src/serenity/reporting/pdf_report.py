"""PDF report generation — print-optimised scan results via WeasyPrint."""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from serenity import __version__
from serenity.constants import DOMAIN_WEIGHTS, Severity, Verdict
from serenity.core.state import ScanContext
from serenity.scoring.engine import ScoringEngine
from serenity.scoring.finding import Finding


async def generate_pdf_report(
    ctx: ScanContext,
    scores: dict[str, Any],
    verdict: Verdict,
    output_dir: Path,
) -> Path:
    """Generate a PDF report from scan results.

    Uses WeasyPrint to render a print-optimised HTML document to PDF.
    Raises ``RuntimeError`` if WeasyPrint is not installed.

    Args:
        ctx: The scan context with all collected state.
        scores: Domain scores dict from ``ScoringEngine.calculate``.
        verdict: The overall scan verdict.
        output_dir: Directory to write the report file into.

    Returns:
        The ``Path`` to the written PDF file.
    """
    try:
        from weasyprint import HTML  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "WeasyPrint is required for PDF report generation. "
            "Install it with:  pip install weasyprint\n"
            "Note: WeasyPrint also requires system libraries (Cairo, Pango, "
            "GDK-PixBuf). See https://doc.courtbouillon.org/weasyprint/stable/first_steps.html"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)

    overall_score = scores.get("overall", ctx.state.overall_score)
    domain_scores = scores.get("domains", {})
    findings = ctx.state.findings

    engine = ScoringEngine()
    prioritized = engine.get_prioritized_fixes(findings)

    # Group findings by domain
    domain_findings: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        domain_findings[f.domain].append(f)

    html_content = _build_pdf_html(
        ctx=ctx,
        overall_score=overall_score,
        domain_scores=domain_scores,
        verdict=verdict,
        findings=findings,
        domain_findings=domain_findings,
        prioritized=prioritized,
    )

    output_path = output_dir / "serenity-report.pdf"
    HTML(string=html_content).write_pdf(str(output_path))
    return output_path


# ---------------------------------------------------------------------------
# HTML builder (print-optimised, no interactive elements)
# ---------------------------------------------------------------------------

_SEVERITY_COLORS = {
    Severity.CRITICAL: "#991B1B",
    Severity.HIGH: "#DC2626",
    Severity.MEDIUM: "#EA580C",
    Severity.LOW: "#CA8A04",
}

_VERDICT_COLORS = {
    Verdict.EXCELLENT: "#166534",
    Verdict.APPROVED: "#1A3A5C",
    Verdict.FAILED: "#991B1B",
}


def _build_pdf_html(
    *,
    ctx: ScanContext,
    overall_score: float,
    domain_scores: dict[str, float],
    verdict: Verdict,
    findings: list[Finding],
    domain_findings: dict[str, list[Finding]],
    prioritized: list[Finding],
) -> str:
    """Construct a self-contained HTML string optimised for print / PDF."""

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    duration = _format_duration(ctx.state.elapsed_seconds)
    verdict_color = _VERDICT_COLORS.get(verdict, "#1A3A5C")

    # --- Domain rows ---
    domain_rows = ""
    for domain, weight in DOMAIN_WEIGHTS.items():
        d_score = domain_scores.get(domain, 100.0)
        d_count = len(domain_findings.get(domain, []))
        bar_color = _score_color(d_score)
        domain_rows += f"""
        <tr>
            <td style="text-transform:capitalize;font-weight:600;">{html.escape(domain)}</td>
            <td style="text-align:center;">{weight * 100:.0f}%</td>
            <td>
                <div style="background:#E5E7EB;border-radius:4px;height:18px;width:100%;position:relative;">
                    <div style="background:{bar_color};border-radius:4px;height:18px;width:{d_score}%;"></div>
                    <span style="position:absolute;left:50%;top:0;transform:translateX(-50%);font-size:11px;line-height:18px;color:#1F2937;font-weight:600;">{d_score:.1f}</span>
                </div>
            </td>
            <td style="text-align:center;">{d_count}</td>
        </tr>"""

    # --- Finding rows ---
    finding_rows = ""
    for f in findings:
        sev_color = _SEVERITY_COLORS.get(f.severity, "#6B7280")
        finding_rows += f"""
        <tr>
            <td><span style="display:inline-block;padding:2px 8px;border-radius:3px;background:{sev_color};color:#fff;font-size:10px;text-transform:uppercase;">{html.escape(f.severity.value)}</span></td>
            <td style="text-transform:capitalize;">{html.escape(f.domain)}</td>
            <td>{html.escape(f.title)}</td>
            <td style="font-size:11px;color:#6B7280;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{html.escape(f.url or "—")}</td>
            <td style="text-align:center;">-{f.deduction_points:.1f}</td>
        </tr>"""

    # --- Recommendation rows ---
    rec_rows = ""
    for rank, f in enumerate(prioritized[:15], 1):
        sev_color = _SEVERITY_COLORS.get(f.severity, "#6B7280")
        rec_rows += f"""
        <tr>
            <td style="text-align:center;font-weight:700;">{rank}</td>
            <td><span style="display:inline-block;padding:2px 8px;border-radius:3px;background:{sev_color};color:#fff;font-size:10px;text-transform:uppercase;">{html.escape(f.severity.value)}</span></td>
            <td>{html.escape(f.title)}</td>
            <td style="text-align:center;">{f.estimated_fix_minutes} min</td>
            <td style="text-align:center;">-{f.deduction_points:.1f}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Serenity QA Report — {html.escape(ctx.config.target_url)}</title>
<style>
    @page {{
        size: A4;
        margin: 20mm 15mm 25mm 15mm;
        @bottom-center {{
            content: "Serenity QA v{__version__} — Page " counter(page) " of " counter(pages);
            font-size: 9px;
            color: #9CA3AF;
        }}
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
        font-size: 12px;
        color: #1F2937;
        line-height: 1.5;
        background: #fff;
    }}
    h1 {{ font-size: 22px; color: #1A3A5C; margin-bottom: 4px; }}
    h2 {{
        font-size: 15px;
        color: #1A3A5C;
        border-bottom: 2px solid #C9A84C;
        padding-bottom: 4px;
        margin: 18px 0 10px 0;
        page-break-after: avoid;
    }}
    .header {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 3px solid #C9A84C;
        padding-bottom: 12px;
        margin-bottom: 16px;
    }}
    .header-right {{ text-align: right; font-size: 11px; color: #6B7280; }}
    .score-box {{
        display: inline-block;
        font-size: 36px;
        font-weight: 700;
        color: {verdict_color};
        margin-right: 16px;
    }}
    .verdict-badge {{
        display: inline-block;
        padding: 6px 18px;
        border-radius: 6px;
        font-size: 14px;
        font-weight: 700;
        color: #fff;
        background: {verdict_color};
        letter-spacing: 1px;
    }}
    .summary-row {{
        display: flex;
        align-items: center;
        gap: 20px;
        margin-bottom: 16px;
    }}
    .meta-grid {{
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 8px;
        margin-bottom: 16px;
    }}
    .meta-card {{
        background: #F5F0EB;
        border-radius: 6px;
        padding: 10px 12px;
        text-align: center;
    }}
    .meta-card .label {{ font-size: 10px; color: #6B7280; text-transform: uppercase; letter-spacing: 0.5px; }}
    .meta-card .value {{ font-size: 16px; font-weight: 700; color: #1A3A5C; }}
    table {{
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 12px;
        font-size: 11px;
    }}
    th {{
        background: #1A3A5C;
        color: #fff;
        padding: 6px 8px;
        text-align: left;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    td {{ padding: 5px 8px; border-bottom: 1px solid #E5E7EB; }}
    tr:nth-child(even) td {{ background: #F9FAFB; }}
    .page-break {{ page-break-before: always; }}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
    <div>
        <h1>Serenity QA Report</h1>
        <div style="font-size:13px;color:#6B7280;">{html.escape(ctx.config.target_url)}</div>
    </div>
    <div class="header-right">
        <div>Generated: {timestamp}</div>
        <div>Duration: {duration}</div>
        <div>Serenity QA v{__version__}</div>
    </div>
</div>

<!-- Score Summary -->
<div class="summary-row">
    <div class="score-box">{overall_score:.1f}</div>
    <div class="verdict-badge">{html.escape(verdict.value)}</div>
</div>

<!-- Metadata Cards -->
<div class="meta-grid">
    <div class="meta-card">
        <div class="label">Pages Analyzed</div>
        <div class="value">{ctx.state.pages_analyzed}</div>
    </div>
    <div class="meta-card">
        <div class="label">Total Findings</div>
        <div class="value">{ctx.state.total_findings}</div>
    </div>
    <div class="meta-card">
        <div class="label">Duration</div>
        <div class="value">{duration}</div>
    </div>
    <div class="meta-card">
        <div class="label">Overall Score</div>
        <div class="value">{overall_score:.1f}/100</div>
    </div>
</div>

<!-- Domain Breakdown -->
<h2>Domain Breakdown</h2>
<table>
    <thead>
        <tr><th>Domain</th><th style="text-align:center;">Weight</th><th>Score</th><th style="text-align:center;">Findings</th></tr>
    </thead>
    <tbody>{domain_rows}
    </tbody>
</table>

<!-- Findings Detail -->
<h2 class="page-break">Findings Detail</h2>
<table>
    <thead>
        <tr><th>Severity</th><th>Domain</th><th>Title</th><th>URL</th><th style="text-align:center;">Points</th></tr>
    </thead>
    <tbody>{finding_rows}
    </tbody>
</table>

<!-- Prioritized Recommendations -->
<h2>Prioritized Recommendations</h2>
<table>
    <thead>
        <tr><th style="text-align:center;">#</th><th>Severity</th><th>Recommendation</th><th style="text-align:center;">Est. Time</th><th style="text-align:center;">Points</th></tr>
    </thead>
    <tbody>{rec_rows}
    </tbody>
</table>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_duration(seconds: float) -> str:
    """Format elapsed seconds as 'Xm Ys'."""
    m, s = divmod(int(seconds), 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _score_color(score: float) -> str:
    """Return a colour for a score bar."""
    if score >= 91:
        return "#166534"
    if score >= 70:
        return "#2563EB"
    if score >= 60:
        return "#EA580C"
    return "#DC2626"
