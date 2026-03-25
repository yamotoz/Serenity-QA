"""AI-powered analysis modules — optional Gemini-based UX review and suggestions.

All AI features are gated behind a valid ``GEMINI_API_KEY``.  When the key is
absent every public entry-point returns gracefully with empty results so the
rest of the pipeline is unaffected.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from serenity.core.state import ScanContext
    from serenity.scoring.finding import Finding

logger = logging.getLogger("serenity.ai")


async def run_ai_analysis(ctx: ScanContext) -> list[Finding]:
    """Run all AI modules and return aggregated findings.

    This is the single entry-point called by the scan engine.  It orchestrates
    the UX judge (screenshot-based review) and the suggestion engine (fix
    recommendations attached to existing findings).

    Returns an empty list when the Gemini API key is not configured or when any
    unrecoverable error occurs — the scan must never fail because of AI.
    """
    if not ctx.config.gemini_api_key:
        logger.info("GEMINI_API_KEY not set — skipping AI analysis")
        return []

    if not ctx.config.enable_ai:
        logger.info("AI analysis disabled via config — skipping")
        return []

    try:
        from serenity.ai.gemini_client import GeminiClient
        from serenity.ai.suggestion_engine import SuggestionEngine
        from serenity.ai.ux_judge import UXJudge

        client = GeminiClient(ctx.config.gemini_api_key)
        findings: list[Finding] = []

        # UX analysis — generates new findings from screenshot review
        ux_judge = UXJudge(client)
        ux_findings = await ux_judge.analyze(ctx)
        findings.extend(ux_findings)
        logger.info("UX judge produced %d findings", len(ux_findings))

        # Suggestion engine — enriches existing findings with fix snippets
        suggestion_engine = SuggestionEngine(client)
        await suggestion_engine.enhance_findings(ctx)
        logger.info("Suggestion engine finished enhancing findings")

        return findings

    except Exception:
        logger.exception("AI analysis failed — returning empty results")
        return []
