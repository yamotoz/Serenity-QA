"""Interactive HTML report generation — the flagship Serenity QA deliverable."""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from serenity import __version__
from serenity.constants import DOMAIN_WEIGHTS, Severity, Verdict
from serenity.core.state import ScanContext
from serenity.reporting.nav_graph import generate_nav_graph_data
from serenity.scoring.engine import ScoringEngine
from serenity.scoring.finding import Finding


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_html_report(
    ctx: ScanContext,
    scores: dict[str, Any],
    verdict: Verdict,
    output_dir: Path,
) -> Path:
    """Generate a self-contained interactive HTML report.

    The report embeds all CSS and JavaScript inline so it can be opened
    from the filesystem with zero external dependencies (except CDN fonts
    and Chart.js which degrade gracefully).

    Args:
        ctx: The scan context with all collected state.
        scores: Domain scores dict from ``ScoringEngine.calculate``.
        verdict: The overall scan verdict.
        output_dir: Directory to write the report file into.

    Returns:
        The ``Path`` to the written HTML file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    overall_score = scores.get("overall", ctx.state.overall_score)
    domain_scores = scores.get("domains", {})
    findings = ctx.state.findings
    finding_counts = scores.get("finding_counts", {})

    engine = ScoringEngine()
    prioritized = engine.get_prioritized_fixes(findings)

    # Group findings by domain
    domain_findings: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        domain_findings[f.domain].append(f)

    nav_data = generate_nav_graph_data(ctx)

    document = _assemble_document(
        ctx=ctx,
        overall_score=overall_score,
        domain_scores=domain_scores,
        verdict=verdict,
        findings=findings,
        finding_counts=finding_counts,
        domain_findings=domain_findings,
        prioritized=prioritized,
        nav_data=nav_data,
    )

    output_path = output_dir / "serenity-report.html"
    output_path.write_text(document, encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Colour constants
# ---------------------------------------------------------------------------

_MARBLE = "#F5F0EB"
_GOLD = "#C9A84C"
_SEA_BLUE = "#1A3A5C"
_DEEP_BLUE = "#0F2640"
_LIGHT_BG = "#FAF8F5"

_SEVERITY_COLORS: dict[Severity, str] = {
    Severity.CRITICAL: "#7F1D1D",
    Severity.HIGH: "#DC2626",
    Severity.MEDIUM: "#EA580C",
    Severity.LOW: "#CA8A04",
}

_SEVERITY_BG: dict[Severity, str] = {
    Severity.CRITICAL: "#FEE2E2",
    Severity.HIGH: "#FEF2F2",
    Severity.MEDIUM: "#FFF7ED",
    Severity.LOW: "#FEFCE8",
}

_VERDICT_COLORS: dict[Verdict, str] = {
    Verdict.EXCELLENT: "#166534",
    Verdict.APPROVED: "#1A3A5C",
    Verdict.FAILED: "#991B1B",
}

_VERDICT_BG: dict[Verdict, str] = {
    Verdict.EXCELLENT: "#DCFCE7",
    Verdict.APPROVED: "#DBEAFE",
    Verdict.FAILED: "#FEE2E2",
}

_DOMAIN_ICONS: dict[str, str] = {
    "performance": "&#9889;",      # zap
    "seo": "&#128269;",            # magnifying glass
    "functionality": "&#9881;",    # gear
    "click_agent": "&#128433;",    # pointer
    "responsiveness": "&#128241;", # mobile phone
    "accessibility": "&#9855;",    # wheelchair
    "infrastructure": "&#128274;", # lock
}


# ---------------------------------------------------------------------------
# Full document assembly
# ---------------------------------------------------------------------------

def _assemble_document(
    *,
    ctx: ScanContext,
    overall_score: float,
    domain_scores: dict[str, float],
    verdict: Verdict,
    findings: list[Finding],
    finding_counts: dict[str, int],
    domain_findings: dict[str, list[Finding]],
    prioritized: list[Finding],
    nav_data: dict[str, Any],
) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    duration = _format_duration(ctx.state.elapsed_seconds)
    target = ctx.config.target_url
    verdict_color = _VERDICT_COLORS.get(verdict, _SEA_BLUE)
    verdict_bg = _VERDICT_BG.get(verdict, "#DBEAFE")

    # Pre-build sections
    gauge_svg = _build_gauge_svg(overall_score)
    meta_cards = _build_meta_cards(ctx, overall_score, duration, finding_counts)
    domain_sections = _build_domain_sections(domain_scores, domain_findings)
    findings_table = _build_findings_table(findings)
    recommendations_section = _build_recommendations(prioritized)
    chart_data = _build_chart_data(domain_scores)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Serenity QA — {_e(target)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700;900&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
{_build_css()}
</style>
</head>
<body>

<!-- ===== Top Navigation Bar ===== -->
<nav class="topbar">
    <div class="topbar-inner">
        <div class="topbar-brand">
            <span class="brand-icon">&#9878;</span> Serenity QA
        </div>
        <div class="topbar-links">
            <a href="#summary">Summary</a>
            <a href="#domains">Domains</a>
            <a href="#findings">Findings</a>
            <a href="#recommendations">Fixes</a>
        </div>
    </div>
</nav>

<!-- ===== Hero / Executive Summary ===== -->
<header class="hero" id="summary">
    <div class="hero-bg-pattern"></div>
    <div class="container hero-content">
        <div class="hero-left">
            <h1 class="hero-title">Quality Report</h1>
            <p class="hero-url">{_e(target)}</p>
            <p class="hero-meta">{timestamp} &middot; {duration} &middot; v{__version__}</p>
            <div class="verdict-badge" style="background:{verdict_bg};color:{verdict_color};border:2px solid {verdict_color};">
                {_e(verdict.value)}
            </div>
        </div>
        <div class="hero-right">
            {gauge_svg}
        </div>
    </div>
</header>

<!-- ===== Metric Cards ===== -->
<section class="container cards-section">
    {meta_cards}
</section>

<!-- ===== Domain Score Chart ===== -->
<section class="container chart-section">
    <div class="section-header">
        <h2 class="section-title" id="domains">Domain Breakdown</h2>
        <p class="section-subtitle">Weighted scores across all quality dimensions</p>
    </div>
    <div class="chart-row">
        <div class="chart-canvas-wrap">
            <canvas id="domainChart" width="320" height="320"></canvas>
        </div>
        <div class="domain-list">
            {domain_sections}
        </div>
    </div>
</section>

<!-- ===== Findings Table ===== -->
<section class="container findings-section" id="findings">
    <div class="section-header">
        <h2 class="section-title">Findings</h2>
        <p class="section-subtitle">{len(findings)} issues discovered across {ctx.state.pages_analyzed} pages</p>
    </div>
    <div class="filter-bar">
        <div class="filter-group">
            <label class="filter-label">Domain</label>
            <select id="filterDomain" class="filter-select" onchange="applyFilters()">
                <option value="all">All Domains</option>
                {_build_domain_options(domain_findings)}
            </select>
        </div>
        <div class="filter-group">
            <label class="filter-label">Severity</label>
            <select id="filterSeverity" class="filter-select" onchange="applyFilters()">
                <option value="all">All Severities</option>
                <option value="critical">Critical</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
            </select>
        </div>
        <div class="filter-group">
            <span id="filterCount" class="filter-count">{len(findings)} findings</span>
        </div>
    </div>
    {findings_table}
</section>

<!-- ===== Recommendations ===== -->
<section class="container recommendations-section" id="recommendations">
    <div class="section-header">
        <h2 class="section-title">Prioritized Fixes</h2>
        <p class="section-subtitle">Ordered by impact-to-effort ratio — best bang for your buck first</p>
    </div>
    {recommendations_section}
</section>

<!-- ===== Footer ===== -->
<footer class="footer">
    <div class="container footer-inner">
        <span>Generated by <strong>Serenity QA v{__version__}</strong></span>
        <span>{timestamp}</span>
    </div>
</footer>

<script>
{_build_js(chart_data)}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Gauge SVG (animated speedometer)
# ---------------------------------------------------------------------------

def _build_gauge_svg(score: float) -> str:
    """Build an SVG speedometer gauge with animated arc."""
    # Arc geometry: semicircle from 180deg to 0deg (left to right)
    # SVG arc on a circle of radius 90, center (120, 110)
    radius = 90
    cx, cy = 120, 110
    # Score mapped to 0-180 degrees
    angle = (score / 100.0) * 180.0
    # Start angle = 180 (left), end angle = 180 - angle
    import math
    start_rad = math.radians(180)
    end_rad = math.radians(180 - angle)

    x_start = cx + radius * math.cos(start_rad)
    y_start = cy - radius * math.sin(start_rad)
    x_end = cx + radius * math.cos(end_rad)
    y_end = cy - radius * math.sin(end_rad)

    large_arc = 1 if angle > 180 else 0

    # Colour based on score
    if score >= 91:
        arc_color = "#166534"
        glow_color = "#22C55E"
    elif score >= 70:
        arc_color = "#2563EB"
        glow_color = "#60A5FA"
    elif score >= 60:
        arc_color = "#EA580C"
        glow_color = "#FB923C"
    else:
        arc_color = "#DC2626"
        glow_color = "#F87171"

    # Needle angle: pointing from center, 0 score = left (180deg), 100 = right (0deg)
    needle_rad = math.radians(180 - angle)
    needle_len = 70
    nx = cx + needle_len * math.cos(needle_rad)
    ny = cy - needle_len * math.sin(needle_rad)

    # Tick marks
    ticks = ""
    for i in range(0, 101, 10):
        t_rad = math.radians(180 - (i / 100.0) * 180.0)
        inner_r = 95
        outer_r = 102
        tx1 = cx + inner_r * math.cos(t_rad)
        ty1 = cy - inner_r * math.sin(t_rad)
        tx2 = cx + outer_r * math.cos(t_rad)
        ty2 = cy - outer_r * math.sin(t_rad)
        ticks += f'<line x1="{tx1:.1f}" y1="{ty1:.1f}" x2="{tx2:.1f}" y2="{ty2:.1f}" stroke="#9CA3AF" stroke-width="{"2" if i % 50 == 0 else "1"}"/>'
        if i % 20 == 0:
            label_r = 110
            lx = cx + label_r * math.cos(t_rad)
            ly = cy - label_r * math.sin(t_rad)
            ticks += f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" dominant-baseline="middle" fill="#9CA3AF" font-size="10" font-family="Inter, sans-serif">{i}</text>'

    # Dashoffset animation: the arc path length
    arc_length = (angle / 180.0) * math.pi * radius

    return f"""<svg viewBox="0 0 240 150" class="gauge-svg" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <filter id="glow">
      <feGaussianBlur stdDeviation="3" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <linearGradient id="arcGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="{arc_color}"/>
      <stop offset="100%" stop-color="{glow_color}"/>
    </linearGradient>
  </defs>

  <!-- Background arc -->
  <path d="M {cx - radius} {cy} A {radius} {radius} 0 0 1 {cx + radius} {cy}"
        fill="none" stroke="#E5E7EB" stroke-width="14" stroke-linecap="round"/>

  <!-- Score arc (animated) -->
  <path d="M {x_start:.2f} {y_start:.2f} A {radius} {radius} 0 {large_arc} 1 {x_end:.2f} {y_end:.2f}"
        fill="none" stroke="url(#arcGrad)" stroke-width="14" stroke-linecap="round"
        filter="url(#glow)"
        stroke-dasharray="{arc_length:.1f} {math.pi * radius:.1f}"
        stroke-dashoffset="{arc_length:.1f}">
    <animate attributeName="stroke-dashoffset" from="{arc_length:.1f}" to="0"
             dur="1.4s" fill="freeze" calcMode="spline"
             keySplines="0.4 0 0.2 1"/>
  </path>

  <!-- Tick marks -->
  {ticks}

  <!-- Needle -->
  <line x1="{cx}" y1="{cy}" x2="{nx:.2f}" y2="{ny:.2f}"
        stroke="{_SEA_BLUE}" stroke-width="2.5" stroke-linecap="round">
    <animateTransform attributeName="transform" type="rotate"
        from="0 {cx} {cy}" to="{-angle} {cx} {cy}" dur="1.4s" fill="freeze"
        calcMode="spline" keySplines="0.4 0 0.2 1"/>
  </line>
  <circle cx="{cx}" cy="{cy}" r="5" fill="{_SEA_BLUE}"/>

  <!-- Score text -->
  <text x="{cx}" y="{cy + 35}" text-anchor="middle" fill="{arc_color}"
        font-family="Cinzel, serif" font-weight="700" font-size="32">
    {score:.1f}
  </text>
  <text x="{cx}" y="{cy + 48}" text-anchor="middle" fill="#9CA3AF"
        font-family="Inter, sans-serif" font-size="11">
    out of 100
  </text>
</svg>"""


# ---------------------------------------------------------------------------
# Meta cards
# ---------------------------------------------------------------------------

def _build_meta_cards(
    ctx: ScanContext,
    overall_score: float,
    duration: str,
    finding_counts: dict[str, int],
) -> str:
    critical = finding_counts.get("critical", 0)
    high = finding_counts.get("high", 0)
    medium = finding_counts.get("medium", 0)
    low = finding_counts.get("low", 0)

    cards = [
        ("Pages Analyzed", str(ctx.state.pages_analyzed), "&#128196;", _SEA_BLUE),
        ("Total Findings", str(ctx.state.total_findings), "&#128270;", "#EA580C" if ctx.state.total_findings > 0 else "#166534"),
        ("Scan Duration", duration, "&#9202;", _SEA_BLUE),
        ("Critical Issues", str(critical), "&#9888;", "#991B1B" if critical > 0 else "#166534"),
        ("High Issues", str(high), "&#9650;", "#DC2626" if high > 0 else "#166534"),
        ("Medium Issues", str(medium), "&#9670;", "#EA580C" if medium > 0 else "#166534"),
        ("Low Issues", str(low), "&#9679;", "#CA8A04" if low > 0 else "#166534"),
        ("Overall Score", f"{overall_score:.1f}", "&#9733;", _score_color(overall_score)),
    ]

    items = ""
    for label, value, icon, color in cards:
        items += f"""
        <div class="metric-card">
            <div class="metric-icon" style="color:{color};">{icon}</div>
            <div class="metric-value" style="color:{color};">{value}</div>
            <div class="metric-label">{label}</div>
        </div>"""
    return f'<div class="cards-grid">{items}\n</div>'


# ---------------------------------------------------------------------------
# Domain sections (expandable)
# ---------------------------------------------------------------------------

def _build_domain_sections(
    domain_scores: dict[str, float],
    domain_findings: dict[str, list[Finding]],
) -> str:
    sections = ""
    for domain, weight in DOMAIN_WEIGHTS.items():
        d_score = domain_scores.get(domain, 100.0)
        d_findings = domain_findings.get(domain, [])
        bar_color = _score_color(d_score)
        icon = _DOMAIN_ICONS.get(domain, "&#128300;")
        findings_html = ""
        if d_findings:
            findings_html = '<div class="domain-findings">'
            for f in d_findings:
                sev_color = _SEVERITY_COLORS.get(f.severity, "#6B7280")
                findings_html += f"""
                <div class="domain-finding-row">
                    <span class="sev-dot" style="background:{sev_color};"></span>
                    <span class="domain-finding-title">{_e(f.title)}</span>
                    <span class="domain-finding-pts">-{f.deduction_points:.1f}</span>
                </div>"""
            findings_html += "</div>"

        sections += f"""
        <div class="domain-item" data-domain="{_e(domain)}">
            <div class="domain-header" onclick="toggleDomain(this)">
                <div class="domain-name-row">
                    <span class="domain-icon">{icon}</span>
                    <span class="domain-name">{_e(domain.replace('_', ' ').title())}</span>
                    <span class="domain-weight">{weight * 100:.0f}%</span>
                    <span class="domain-finding-count">{len(d_findings)} finding{"s" if len(d_findings) != 1 else ""}</span>
                </div>
                <div class="domain-bar-row">
                    <div class="domain-bar-track">
                        <div class="domain-bar-fill" style="width:{d_score}%;background:{bar_color};"></div>
                    </div>
                    <span class="domain-score" style="color:{bar_color};">{d_score:.1f}</span>
                    <span class="domain-chevron">&#9660;</span>
                </div>
            </div>
            <div class="domain-body">
                {findings_html}
            </div>
        </div>"""
    return sections


# ---------------------------------------------------------------------------
# Findings table
# ---------------------------------------------------------------------------

def _build_findings_table(findings: list[Finding]) -> str:
    if not findings:
        return '<div class="empty-state">No findings. Excellent work!</div>'

    rows = ""
    for f in findings:
        sev_color = _SEVERITY_COLORS.get(f.severity, "#6B7280")
        sev_bg = _SEVERITY_BG.get(f.severity, "#F3F4F6")
        url_display = _e(f.url or "—")

        # Build expandable detail
        detail_parts: list[str] = []
        if f.description:
            detail_parts.append(f'<p class="finding-desc">{_e(f.description)}</p>')
        if f.element_selector:
            detail_parts.append(f'<div class="finding-meta-row"><strong>Selector:</strong> <code>{_e(f.element_selector)}</code></div>')
        if f.fix_snippet:
            detail_parts.append(f'<div class="finding-fix"><strong>Suggested Fix:</strong><pre><code>{_e(f.fix_snippet)}</code></pre></div>')
        if f.estimated_fix_minutes:
            detail_parts.append(f'<div class="finding-meta-row"><strong>Estimated fix time:</strong> {f.estimated_fix_minutes} minutes</div>')

        detail_html = "\n".join(detail_parts) if detail_parts else ""

        rows += f"""
        <div class="finding-row" data-domain="{_e(f.domain)}" data-severity="{_e(f.severity.value)}">
            <div class="finding-summary" onclick="toggleFinding(this)">
                <span class="sev-badge" style="background:{sev_bg};color:{sev_color};border:1px solid {sev_color};">{_e(f.severity.value.upper())}</span>
                <span class="finding-domain">{_e(f.domain)}</span>
                <span class="finding-title-text">{_e(f.title)}</span>
                <span class="finding-url">{url_display}</span>
                <span class="finding-points">-{f.deduction_points:.1f}</span>
                <span class="finding-chevron">&#9660;</span>
            </div>
            <div class="finding-detail">
                {detail_html}
            </div>
        </div>"""

    return f'<div class="findings-list">{rows}\n</div>'


def _build_domain_options(domain_findings: dict[str, list[Finding]]) -> str:
    # Show all domains from DOMAIN_WEIGHTS plus any extra
    all_domains = set(DOMAIN_WEIGHTS.keys()) | set(domain_findings.keys())
    opts = ""
    for d in sorted(all_domains):
        label = d.replace("_", " ").title()
        opts += f'<option value="{_e(d)}">{_e(label)}</option>\n'
    return opts


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

def _build_recommendations(prioritized: list[Finding]) -> str:
    if not prioritized:
        return '<div class="empty-state">No recommendations — the site looks great!</div>'

    rows = ""
    for rank, f in enumerate(prioritized[:20], 1):
        sev_color = _SEVERITY_COLORS.get(f.severity, "#6B7280")
        sev_bg = _SEVERITY_BG.get(f.severity, "#F3F4F6")
        impact = f.deduction_points / max(f.estimated_fix_minutes, 1)

        fix_html = ""
        if f.fix_snippet:
            fix_html = f'<pre class="rec-code"><code>{_e(f.fix_snippet)}</code></pre>'

        rows += f"""
        <div class="rec-card">
            <div class="rec-rank">#{rank}</div>
            <div class="rec-body">
                <div class="rec-top">
                    <span class="sev-badge" style="background:{sev_bg};color:{sev_color};border:1px solid {sev_color};">{_e(f.severity.value.upper())}</span>
                    <span class="rec-title">{_e(f.title)}</span>
                </div>
                <div class="rec-meta">
                    <span>&#9201; {f.estimated_fix_minutes} min</span>
                    <span>&#128200; -{f.deduction_points:.1f} pts</span>
                    <span>&#9889; Impact ratio: {impact:.2f}</span>
                </div>
                {fix_html}
            </div>
        </div>"""
    return f'<div class="rec-list">{rows}\n</div>'


# ---------------------------------------------------------------------------
# Chart data
# ---------------------------------------------------------------------------

def _build_chart_data(domain_scores: dict[str, float]) -> str:
    """Return a JS-compatible data block for Chart.js doughnut chart."""
    import json
    labels = []
    data = []
    colors = []
    palette = [
        "#1A3A5C", "#C9A84C", "#2563EB", "#166534",
        "#7C3AED", "#EA580C", "#0891B2",
    ]
    for i, (domain, _weight) in enumerate(DOMAIN_WEIGHTS.items()):
        labels.append(domain.replace("_", " ").title())
        data.append(domain_scores.get(domain, 100.0))
        colors.append(palette[i % len(palette)])

    return json.dumps({"labels": labels, "data": data, "colors": colors})


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

def _build_css() -> str:
    return f"""
/* === Reset & Base === */
*, *::before, *::after {{ margin:0; padding:0; box-sizing:border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: {_LIGHT_BG};
    color: #1F2937;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
}}

/* === Container === */
.container {{ max-width: 1120px; margin: 0 auto; padding: 0 24px; }}

/* === Typography === */
h1, h2, h3 {{ font-family: 'Cinzel', 'Georgia', serif; }}

/* === Top Bar === */
.topbar {{
    position: sticky; top: 0; z-index: 100;
    background: {_DEEP_BLUE};
    box-shadow: 0 2px 12px rgba(0,0,0,.25);
}}
.topbar-inner {{
    max-width: 1120px; margin: 0 auto; padding: 0 24px;
    display: flex; align-items: center; justify-content: space-between;
    height: 56px;
}}
.topbar-brand {{
    font-family: 'Cinzel', serif; font-weight: 700; font-size: 18px;
    color: {_GOLD}; letter-spacing: 1.5px;
    display: flex; align-items: center; gap: 8px;
}}
.brand-icon {{ font-size: 22px; }}
.topbar-links {{ display: flex; gap: 24px; }}
.topbar-links a {{
    color: rgba(255,255,255,.75); text-decoration: none; font-size: 13px;
    font-weight: 500; letter-spacing: 0.5px; text-transform: uppercase;
    transition: color .2s;
}}
.topbar-links a:hover {{ color: {_GOLD}; }}

/* === Hero === */
.hero {{
    position: relative; overflow: hidden;
    background: linear-gradient(135deg, {_DEEP_BLUE} 0%, {_SEA_BLUE} 60%, #234E78 100%);
    padding: 56px 0 48px;
}}
.hero-bg-pattern {{
    position: absolute; inset: 0; opacity: .04;
    background-image:
        radial-gradient(circle at 25% 25%, #fff 1px, transparent 1px),
        radial-gradient(circle at 75% 75%, #fff 1px, transparent 1px);
    background-size: 40px 40px;
}}
.hero-content {{
    position: relative; display: flex;
    align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 32px;
}}
.hero-left {{ flex: 1; min-width: 280px; }}
.hero-title {{
    font-size: 36px; font-weight: 900; color: #fff;
    letter-spacing: 2px; margin-bottom: 8px;
}}
.hero-url {{
    font-size: 16px; color: {_GOLD}; font-weight: 600;
    word-break: break-all; margin-bottom: 4px;
}}
.hero-meta {{ font-size: 13px; color: rgba(255,255,255,.55); margin-bottom: 16px; }}
.verdict-badge {{
    display: inline-block; padding: 8px 28px; border-radius: 8px;
    font-family: 'Cinzel', serif; font-weight: 700; font-size: 18px;
    letter-spacing: 3px;
}}
.hero-right {{ flex-shrink: 0; }}
.gauge-svg {{ width: 240px; height: 150px; }}

/* === Metric Cards === */
.cards-section {{ margin-top: -24px; position: relative; z-index: 10; }}
.cards-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 14px;
}}
.metric-card {{
    background: #fff; border-radius: 12px; padding: 18px 14px;
    text-align: center;
    box-shadow: 0 2px 10px rgba(0,0,0,.06);
    transition: transform .2s, box-shadow .2s;
}}
.metric-card:hover {{
    transform: translateY(-3px);
    box-shadow: 0 6px 20px rgba(0,0,0,.1);
}}
.metric-icon {{ font-size: 22px; margin-bottom: 4px; }}
.metric-value {{ font-size: 24px; font-weight: 700; }}
.metric-label {{ font-size: 11px; color: #6B7280; text-transform: uppercase; letter-spacing: .5px; margin-top: 2px; }}

/* === Section Headers === */
.section-header {{ margin-bottom: 20px; }}
.section-title {{
    font-size: 24px; color: {_SEA_BLUE}; margin-bottom: 4px;
    border-bottom: 3px solid {_GOLD}; display: inline-block;
    padding-bottom: 4px;
}}
.section-subtitle {{ font-size: 14px; color: #6B7280; margin-top: 6px; }}

/* === Chart Section === */
.chart-section {{ margin-top: 40px; }}
.chart-row {{
    display: flex; gap: 32px; align-items: flex-start; flex-wrap: wrap;
}}
.chart-canvas-wrap {{
    flex-shrink: 0; width: 320px; height: 320px;
    background: #fff; border-radius: 16px; padding: 20px;
    box-shadow: 0 2px 10px rgba(0,0,0,.06);
}}
.chart-canvas-wrap canvas {{ width: 100% !important; height: 100% !important; }}
.domain-list {{ flex: 1; min-width: 300px; }}

/* === Domain Items (expandable) === */
.domain-item {{
    background: #fff; border-radius: 12px; margin-bottom: 10px;
    box-shadow: 0 1px 6px rgba(0,0,0,.05);
    overflow: hidden; transition: box-shadow .2s;
}}
.domain-item:hover {{ box-shadow: 0 3px 14px rgba(0,0,0,.1); }}
.domain-header {{
    padding: 14px 18px; cursor: pointer;
    transition: background .15s;
}}
.domain-header:hover {{ background: {_MARBLE}; }}
.domain-name-row {{
    display: flex; align-items: center; gap: 10px; margin-bottom: 8px;
}}
.domain-icon {{ font-size: 18px; }}
.domain-name {{
    font-weight: 600; font-size: 15px; color: {_SEA_BLUE};
    text-transform: capitalize; flex: 1;
}}
.domain-weight {{
    font-size: 11px; color: #9CA3AF; background: #F3F4F6;
    padding: 2px 8px; border-radius: 10px;
}}
.domain-finding-count {{
    font-size: 11px; color: #6B7280;
}}
.domain-bar-row {{
    display: flex; align-items: center; gap: 10px;
}}
.domain-bar-track {{
    flex: 1; height: 8px; background: #E5E7EB; border-radius: 4px;
    overflow: hidden;
}}
.domain-bar-fill {{
    height: 100%; border-radius: 4px;
    transition: width .8s cubic-bezier(.4,0,.2,1);
}}
.domain-score {{ font-weight: 700; font-size: 14px; min-width: 38px; text-align: right; }}
.domain-chevron {{
    font-size: 10px; color: #9CA3AF;
    transition: transform .3s;
    display: inline-block;
}}
.domain-item.open .domain-chevron {{ transform: rotate(180deg); }}
.domain-body {{
    max-height: 0; overflow: hidden;
    transition: max-height .35s cubic-bezier(.4,0,.2,1), padding .35s;
    padding: 0 18px;
}}
.domain-item.open .domain-body {{
    max-height: 600px; padding: 0 18px 14px;
}}
.domain-findings {{ border-top: 1px solid #E5E7EB; padding-top: 10px; }}
.domain-finding-row {{
    display: flex; align-items: center; gap: 8px;
    padding: 4px 0; font-size: 13px;
}}
.sev-dot {{
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}}
.domain-finding-title {{ flex: 1; color: #374151; }}
.domain-finding-pts {{ color: #DC2626; font-weight: 600; font-size: 12px; }}

/* === Filter Bar === */
.findings-section {{ margin-top: 40px; }}
.filter-bar {{
    display: flex; align-items: flex-end; gap: 16px; margin-bottom: 16px;
    flex-wrap: wrap;
}}
.filter-group {{ display: flex; flex-direction: column; gap: 4px; }}
.filter-label {{ font-size: 11px; color: #6B7280; text-transform: uppercase; letter-spacing: .5px; }}
.filter-select {{
    padding: 8px 14px; border: 1px solid #D1D5DB; border-radius: 8px;
    font-size: 13px; font-family: inherit; color: {_SEA_BLUE};
    background: #fff; cursor: pointer; min-width: 160px;
    transition: border-color .2s;
}}
.filter-select:focus {{ outline: none; border-color: {_GOLD}; }}
.filter-count {{
    font-size: 13px; color: #6B7280; font-weight: 500;
    padding: 8px 0;
}}

/* === Findings List === */
.findings-list {{ display: flex; flex-direction: column; gap: 6px; }}
.finding-row {{
    background: #fff; border-radius: 10px;
    box-shadow: 0 1px 4px rgba(0,0,0,.04);
    overflow: hidden;
    transition: box-shadow .2s, opacity .3s, max-height .3s;
}}
.finding-row.hidden {{ display: none; }}
.finding-row:hover {{ box-shadow: 0 3px 12px rgba(0,0,0,.08); }}
.finding-summary {{
    display: flex; align-items: center; gap: 10px;
    padding: 12px 16px; cursor: pointer;
    transition: background .15s;
}}
.finding-summary:hover {{ background: {_MARBLE}; }}
.sev-badge {{
    display: inline-block; padding: 2px 10px; border-radius: 5px;
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .5px; white-space: nowrap; flex-shrink: 0;
}}
.finding-domain {{
    font-size: 11px; color: #6B7280; text-transform: capitalize;
    min-width: 90px; flex-shrink: 0;
}}
.finding-title-text {{ flex: 1; font-size: 13px; font-weight: 500; color: #1F2937; }}
.finding-url {{
    font-size: 11px; color: #9CA3AF; max-width: 200px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}
.finding-points {{ font-weight: 700; color: #DC2626; font-size: 13px; flex-shrink: 0; }}
.finding-chevron {{
    font-size: 10px; color: #9CA3AF;
    transition: transform .3s; display: inline-block;
}}
.finding-row.open .finding-chevron {{ transform: rotate(180deg); }}
.finding-detail {{
    max-height: 0; overflow: hidden;
    transition: max-height .35s cubic-bezier(.4,0,.2,1), padding .35s;
    padding: 0 16px; font-size: 13px; color: #374151;
}}
.finding-row.open .finding-detail {{
    max-height: 500px; padding: 0 16px 16px;
}}
.finding-desc {{ margin-bottom: 10px; line-height: 1.7; }}
.finding-meta-row {{ margin-bottom: 6px; }}
.finding-meta-row code {{
    background: #F3F4F6; padding: 2px 6px; border-radius: 4px;
    font-size: 12px;
}}
.finding-fix {{ margin-top: 8px; }}
.finding-fix pre {{
    background: {_DEEP_BLUE}; color: #E5E7EB; padding: 12px 16px;
    border-radius: 8px; overflow-x: auto; margin-top: 6px;
    font-size: 12px; line-height: 1.5;
}}

/* === Recommendations === */
.recommendations-section {{ margin-top: 40px; padding-bottom: 60px; }}
.rec-list {{ display: flex; flex-direction: column; gap: 10px; }}
.rec-card {{
    display: flex; gap: 16px; align-items: flex-start;
    background: #fff; border-radius: 12px; padding: 16px 20px;
    box-shadow: 0 1px 6px rgba(0,0,0,.05);
    transition: transform .2s, box-shadow .2s;
}}
.rec-card:hover {{
    transform: translateX(4px);
    box-shadow: 0 4px 16px rgba(0,0,0,.08);
}}
.rec-rank {{
    font-family: 'Cinzel', serif; font-weight: 700; font-size: 20px;
    color: {_GOLD}; min-width: 44px; text-align: center;
    padding-top: 2px;
}}
.rec-body {{ flex: 1; }}
.rec-top {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }}
.rec-title {{ font-weight: 600; font-size: 14px; color: {_SEA_BLUE}; }}
.rec-meta {{
    display: flex; gap: 18px; font-size: 12px; color: #6B7280;
    margin-bottom: 6px;
}}
.rec-code pre {{
    background: {_DEEP_BLUE}; color: #E5E7EB; padding: 10px 14px;
    border-radius: 8px; overflow-x: auto; font-size: 12px;
    margin-top: 6px; line-height: 1.5;
}}

/* === Empty State === */
.empty-state {{
    text-align: center; padding: 48px 20px;
    color: #9CA3AF; font-size: 16px;
    background: #fff; border-radius: 12px;
}}

/* === Footer === */
.footer {{
    background: {_DEEP_BLUE}; color: rgba(255,255,255,.5);
    padding: 20px 0; margin-top: 40px; font-size: 12px;
}}
.footer-inner {{
    display: flex; justify-content: space-between; align-items: center;
}}
.footer strong {{ color: {_GOLD}; }}

/* === Responsive === */
@media (max-width: 768px) {{
    .hero-content {{ flex-direction: column; text-align: center; }}
    .hero-right {{ margin: 0 auto; }}
    .chart-row {{ flex-direction: column; }}
    .chart-canvas-wrap {{ width: 100%; max-width: 320px; margin: 0 auto; }}
    .finding-summary {{ flex-wrap: wrap; }}
    .finding-url {{ max-width: 100%; }}
    .topbar-links {{ gap: 12px; }}
    .topbar-links a {{ font-size: 11px; }}
    .cards-grid {{ grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 8px; }}
    .metric-card {{ padding: 12px 8px; }}
    .metric-value {{ font-size: 18px; }}
    .rec-card {{ flex-direction: column; gap: 8px; }}
}}

@media (max-width: 480px) {{
    .hero-title {{ font-size: 24px; }}
    .gauge-svg {{ width: 200px; height: 125px; }}
    .topbar-links {{ display: none; }}
}}

/* === Animation on scroll === */
.domain-item, .finding-row, .rec-card, .metric-card {{
    opacity: 0; transform: translateY(12px);
    animation: fadeUp .5s ease forwards;
}}
@keyframes fadeUp {{
    to {{ opacity: 1; transform: translateY(0); }}
}}
.domain-item:nth-child(1) {{ animation-delay: .05s; }}
.domain-item:nth-child(2) {{ animation-delay: .1s; }}
.domain-item:nth-child(3) {{ animation-delay: .15s; }}
.domain-item:nth-child(4) {{ animation-delay: .2s; }}
.domain-item:nth-child(5) {{ animation-delay: .25s; }}
.domain-item:nth-child(6) {{ animation-delay: .3s; }}
.domain-item:nth-child(7) {{ animation-delay: .35s; }}
.metric-card:nth-child(1) {{ animation-delay: .05s; }}
.metric-card:nth-child(2) {{ animation-delay: .1s; }}
.metric-card:nth-child(3) {{ animation-delay: .15s; }}
.metric-card:nth-child(4) {{ animation-delay: .2s; }}
.metric-card:nth-child(5) {{ animation-delay: .25s; }}
.metric-card:nth-child(6) {{ animation-delay: .3s; }}
.metric-card:nth-child(7) {{ animation-delay: .35s; }}
.metric-card:nth-child(8) {{ animation-delay: .4s; }}
"""


# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------

def _build_js(chart_data_json: str) -> str:
    return f"""
// === Chart.js Doughnut ===
(function() {{
    var chartEl = document.getElementById('domainChart');
    if (!chartEl || typeof Chart === 'undefined') return;
    var d = {chart_data_json};
    new Chart(chartEl, {{
        type: 'doughnut',
        data: {{
            labels: d.labels,
            datasets: [{{
                data: d.data,
                backgroundColor: d.colors,
                borderWidth: 2,
                borderColor: '#fff',
                hoverBorderWidth: 3,
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: true,
            cutout: '58%',
            plugins: {{
                legend: {{
                    display: true,
                    position: 'bottom',
                    labels: {{
                        padding: 12,
                        usePointStyle: true,
                        pointStyleWidth: 10,
                        font: {{ size: 11, family: "'Inter', sans-serif" }},
                    }}
                }},
                tooltip: {{
                    callbacks: {{
                        label: function(ctx) {{
                            return ctx.label + ': ' + ctx.parsed.toFixed(1) + '/100';
                        }}
                    }}
                }}
            }},
            animation: {{
                animateRotate: true,
                duration: 1200,
            }}
        }}
    }});
}})();

// === Toggle domain expand/collapse ===
function toggleDomain(header) {{
    var item = header.closest('.domain-item');
    item.classList.toggle('open');
}}

// === Toggle finding expand/collapse ===
function toggleFinding(summary) {{
    var row = summary.closest('.finding-row');
    row.classList.toggle('open');
}}

// === Filtering ===
function applyFilters() {{
    var domain = document.getElementById('filterDomain').value;
    var severity = document.getElementById('filterSeverity').value;
    var rows = document.querySelectorAll('.finding-row');
    var visible = 0;
    rows.forEach(function(row) {{
        var dMatch = (domain === 'all') || (row.getAttribute('data-domain') === domain);
        var sMatch = (severity === 'all') || (row.getAttribute('data-severity') === severity);
        if (dMatch && sMatch) {{
            row.classList.remove('hidden');
            visible++;
        }} else {{
            row.classList.add('hidden');
            row.classList.remove('open');
        }}
    }});
    document.getElementById('filterCount').textContent = visible + ' finding' + (visible !== 1 ? 's' : '');
}}

// === Smooth scroll for nav links ===
document.querySelectorAll('.topbar-links a').forEach(function(a) {{
    a.addEventListener('click', function(e) {{
        var href = a.getAttribute('href');
        if (href && href.startsWith('#')) {{
            e.preventDefault();
            var target = document.querySelector(href);
            if (target) {{
                target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
            }}
        }}
    }});
}});
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _e(text: str) -> str:
    """Shorthand for HTML-escaping."""
    return html.escape(str(text))


def _format_duration(seconds: float) -> str:
    """Format elapsed seconds as 'Xm Ys'."""
    m, s = divmod(int(seconds), 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _score_color(score: float) -> str:
    """Return a colour for a score value."""
    if score >= 91:
        return "#166534"
    if score >= 70:
        return "#2563EB"
    if score >= 60:
        return "#EA580C"
    return "#DC2626"
