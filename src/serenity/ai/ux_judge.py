"""UX Judge — AI-powered visual and UX analysis of page screenshots.

For each sampled page the judge sends the screenshot to Gemini with a
structured prompt (in Portuguese) and converts the model's assessment into
:class:`Finding` objects.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from serenity.constants import Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from serenity.ai.gemini_client import GeminiClient
    from serenity.core.state import ScanContext

logger = logging.getLogger("serenity.ai.ux_judge")

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_UX_PROMPT = """\
Você é um QA sênior com 10 anos de experiência em produto digital, especialista em UX e conversão.
Analise este screenshot de uma página web e avalie:
1. Hierarquia visual (1-10): A organização visual faz sentido?
2. CTA principal (1-10): O call-to-action é óbvio e claro?
3. Qualidade do copy (1-10): Os textos são claros ou ambíguos?
4. Fricção no fluxo (1-10): Existe fricção desnecessária?
5. Problemas visuais: Desalinhamentos, espaçamentos inconsistentes, layouts quebrados

Responda APENAS em JSON com esta estrutura (sem texto adicional):
{"scores": {"hierarchy": N, "cta": N, "copy": N, "friction": N}, "issues": [{"title": "...", "description": "...", "severity": "high|medium|low"}], "overall_assessment": "texto"}
"""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MAX_PAGES_TO_JUDGE = 10
_SCORE_THRESHOLD_FOR_FINDING = 5  # scores <= this create findings

_SEVERITY_MAP: dict[str, Severity] = {
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
}

_DIMENSION_LABELS: dict[str, str] = {
    "hierarchy": "Hierarquia visual",
    "cta": "CTA principal",
    "copy": "Qualidade do copy",
    "friction": "Fricção no fluxo",
}


class UXJudge:
    """Send page screenshots to Gemini for AI-powered UX review."""

    def __init__(self, client: GeminiClient) -> None:
        self._client = client

    async def analyze(self, ctx: ScanContext) -> list[Finding]:
        """Run UX analysis on a sample of pages.

        Returns a list of :class:`Finding` objects (domain ``"content"``).
        """
        try:
            pages = self._select_pages(ctx)
            if not pages:
                logger.info("No screenshots available for UX analysis")
                return []

            logger.info("UX judge analysing %d pages", len(pages))

            all_findings: list[Finding] = []
            for url, screenshot_path in pages:
                findings = await self._judge_page(url, screenshot_path)
                all_findings.extend(findings)

            return all_findings

        except Exception:
            logger.exception("UX judge encountered an unexpected error")
            return []

    # ------------------------------------------------------------------
    # Page selection
    # ------------------------------------------------------------------

    def _select_pages(self, ctx: ScanContext) -> list[tuple[str, str]]:
        """Choose up to ``_MAX_PAGES_TO_JUDGE`` pages that have screenshots.

        Returns a list of ``(url, screenshot_path)`` tuples.  Prefers desktop
        viewport screenshots when available.
        """
        candidates: list[tuple[str, str]] = []

        for url, viewports in ctx.state.screenshots.items():
            # Prefer desktop, fall back to whatever is available
            path = viewports.get("desktop") or next(iter(viewports.values()), None)
            if path and Path(path).exists():
                candidates.append((url, path))

        # Sort for determinism and take the first N
        candidates.sort(key=lambda c: c[0])
        return candidates[:_MAX_PAGES_TO_JUDGE]

    # ------------------------------------------------------------------
    # Single-page judgement
    # ------------------------------------------------------------------

    async def _judge_page(self, url: str, screenshot_path: str) -> list[Finding]:
        """Send one screenshot to Gemini and parse the response into Findings."""
        try:
            data = await self._client.generate_json(_UX_PROMPT, screenshot_path)
            if not data:
                logger.warning("Empty response from Gemini for %s", url)
                return []

            return self._parse_response(url, screenshot_path, data)

        except Exception:
            logger.exception("Failed to judge page %s", url)
            return []

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        url: str,
        screenshot_path: str,
        data: dict[str, Any],
    ) -> list[Finding]:
        """Convert Gemini's JSON response into Finding objects."""
        findings: list[Finding] = []

        # 1. Score-based findings — low scores become findings
        scores = data.get("scores", {})
        for dimension, label in _DIMENSION_LABELS.items():
            try:
                score = int(scores.get(dimension, 10))
            except (TypeError, ValueError):
                continue

            if score <= _SCORE_THRESHOLD_FOR_FINDING:
                severity = self._severity_from_score(score)
                findings.append(
                    Finding(
                        domain="content",
                        severity=severity,
                        title=f"UX: {label} — nota {score}/10",
                        description=(
                            f"A análise de IA atribuiu nota {score}/10 para "
                            f'"{label}" nesta página. '
                            f"Avaliação geral: {data.get('overall_assessment', 'N/A')}"
                        ),
                        url=url,
                        screenshot_path=screenshot_path,
                        metadata={
                            "ai_source": "ux_judge",
                            "dimension": dimension,
                            "score": score,
                            "all_scores": scores,
                        },
                    )
                )

        # 2. Explicit issues reported by the model
        issues = data.get("issues", [])
        if isinstance(issues, list):
            for issue in issues:
                try:
                    finding = self._issue_to_finding(url, screenshot_path, issue)
                    if finding is not None:
                        findings.append(finding)
                except Exception:
                    logger.debug("Skipping malformed issue entry: %s", issue)
                    continue

        return findings

    @staticmethod
    def _issue_to_finding(
        url: str,
        screenshot_path: str,
        issue: dict[str, Any],
    ) -> Finding | None:
        """Convert a single issue dict from the AI response to a Finding."""
        title = issue.get("title")
        description = issue.get("description")
        if not title or not description:
            return None

        severity_str = str(issue.get("severity", "medium")).lower().strip()
        severity = _SEVERITY_MAP.get(severity_str, Severity.MEDIUM)

        return Finding(
            domain="content",
            severity=severity,
            title=f"UX: {title}",
            description=description,
            url=url,
            screenshot_path=screenshot_path,
            metadata={"ai_source": "ux_judge"},
        )

    @staticmethod
    def _severity_from_score(score: int) -> Severity:
        """Map a 1-10 score to a Severity level."""
        if score <= 2:
            return Severity.CRITICAL
        if score <= 4:
            return Severity.HIGH
        if score <= 5:
            return Severity.MEDIUM
        return Severity.LOW
