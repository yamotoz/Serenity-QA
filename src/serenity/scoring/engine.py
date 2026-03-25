"""Scoring engine — calculates weighted scores per domain and overall."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from serenity.constants import (
    DOMAIN_ALIASES,
    DOMAIN_WEIGHTS,
    SEVERITY_CAPS,
    Severity,
    Verdict,
)
from serenity.scoring.finding import Finding


class ScoringEngine:
    """Calculates domain scores and overall score from findings."""

    def calculate(self, findings: list[Finding]) -> dict[str, Any]:
        """Calculate all scores from a list of findings.

        Returns:
            {
                "overall": float,
                "domains": {"performance": float, ...},
                "finding_counts": {"critical": int, ...},
            }
        """
        # Group findings by effective domain
        domain_findings: dict[str, list[Finding]] = defaultdict(list)
        for f in findings:
            effective = DOMAIN_ALIASES.get(f.domain, f.domain)
            domain_findings[effective].append(f)

        # Calculate per-domain scores
        domain_scores: dict[str, float] = {}
        for domain in DOMAIN_WEIGHTS:
            domain_scores[domain] = self._calculate_domain_score(
                domain_findings.get(domain, [])
            )

        # Calculate weighted overall
        overall = sum(
            domain_scores.get(domain, 100.0) * weight
            for domain, weight in DOMAIN_WEIGHTS.items()
        )

        # Count findings by severity
        finding_counts: dict[str, int] = defaultdict(int)
        for f in findings:
            finding_counts[f.severity.value] += 1

        return {
            "overall": round(overall, 1),
            "domains": {k: round(v, 1) for k, v in domain_scores.items()},
            "finding_counts": dict(finding_counts),
        }

    def _calculate_domain_score(self, findings: list[Finding]) -> float:
        """Calculate score for a single domain (starts at 100, deductions applied).

        Uses diminishing penalties: for findings with the same title on the same
        page, the first occurrence costs full points, subsequent ones cost less.
        This prevents a page with 15 identical issues from destroying the score.
        """
        if not findings:
            return 100.0

        # Apply diminishing penalties for duplicate finding types per page
        seen_counts: dict[tuple[str, str], int] = defaultdict(int)  # (title, url) -> count
        severity_totals: dict[Severity, float] = defaultdict(float)

        for f in findings:
            key = (f.title, f.url or "")
            seen_counts[key] += 1
            count = seen_counts[key]

            # Diminishing deduction: 1st = full, 2nd = 50%, 3rd+ = 20%, cap at -25pts per type/page
            if count == 1:
                points = f.deduction_points
            elif count == 2:
                points = f.deduction_points * 0.5
            else:
                points = f.deduction_points * 0.2

            severity_totals[f.severity] += points

        total_deduction = 0.0
        for severity, total in severity_totals.items():
            cap = SEVERITY_CAPS.get(severity, 50.0)
            total_deduction += min(total, cap)

        return max(0.0, 100.0 - total_deduction)

    def get_verdict(self, overall_score: float) -> Verdict:
        """Determine the verdict from the overall score."""
        if overall_score >= 91:
            return Verdict.EXCELLENT
        elif overall_score >= 70:
            return Verdict.APPROVED
        else:
            return Verdict.FAILED

    def get_prioritized_fixes(self, findings: list[Finding]) -> list[Finding]:
        """Sort findings by impact/effort ratio (best bang for buck first)."""
        def priority_key(f: Finding) -> float:
            if f.estimated_fix_minutes <= 0:
                return f.deduction_points * 1000
            return f.deduction_points / f.estimated_fix_minutes

        return sorted(findings, key=priority_key, reverse=True)
