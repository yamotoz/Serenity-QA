"""Suggestion Engine — enrich existing findings with AI-generated fix advice.

Takes the highest-severity findings, groups them by domain, and asks Gemini
for actionable fix suggestions including code snippets and time estimates.
The suggestions are written back onto the :class:`Finding` objects (mutating
``fix_snippet`` and ``estimated_fix_minutes``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from serenity.constants import Severity

if TYPE_CHECKING:
    from serenity.ai.gemini_client import GeminiClient
    from serenity.core.state import ScanContext
    from serenity.scoring.finding import Finding

logger = logging.getLogger("serenity.ai.suggestions")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MAX_FINDINGS_TO_ENHANCE = 20
_MAX_FINDINGS_PER_BATCH = 10  # avoid overly large prompts

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_SUGGESTION_PROMPT = """\
Você é um engenheiro frontend sênior. Para cada problema abaixo, sugira a correção mais eficiente.
Forneça: snippet de código corrigido (se aplicável), estimativa de tempo, e prioridade.

Problemas:
{problems}

Responda APENAS em JSON (sem texto adicional) com esta estrutura:
[{{"finding_id": "...", "fix_snippet": "...", "estimated_minutes": N, "priority_explanation": "..."}}]
"""

# ---------------------------------------------------------------------------
# Severity ordering for prioritisation
# ---------------------------------------------------------------------------

_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}


class SuggestionEngine:
    """Enhance existing findings with AI-generated fix suggestions."""

    def __init__(self, client: GeminiClient) -> None:
        self._client = client

    async def enhance_findings(self, ctx: ScanContext) -> None:
        """Mutate the top findings in ``ctx.state.findings`` with AI suggestions.

        This method does **not** create new findings — it enriches existing ones
        with ``fix_snippet`` and ``estimated_fix_minutes`` from the AI.
        """
        try:
            findings = self._select_findings(ctx.state.findings)
            if not findings:
                logger.info("No findings to enhance with AI suggestions")
                return

            logger.info("Enhancing %d findings with AI suggestions", len(findings))

            # Process in batches to keep prompt size manageable
            for batch in self._batched(findings, _MAX_FINDINGS_PER_BATCH):
                await self._process_batch(batch)

        except Exception:
            logger.exception("Suggestion engine encountered an unexpected error")

    # ------------------------------------------------------------------
    # Finding selection
    # ------------------------------------------------------------------

    @staticmethod
    def _select_findings(all_findings: list[Finding]) -> list[Finding]:
        """Pick the top N highest-severity findings for enhancement."""
        # Sort by severity (critical first), then by deduction (highest first)
        prioritised = sorted(
            all_findings,
            key=lambda f: (
                _SEVERITY_ORDER.get(f.severity, 99),
                -f.deduction_points,
            ),
        )
        return prioritised[:_MAX_FINDINGS_TO_ENHANCE]

    @staticmethod
    def _batched(
        items: list[Finding],
        size: int,
    ) -> list[list[Finding]]:
        """Split a list into chunks of ``size``."""
        return [items[i : i + size] for i in range(0, len(items), size)]

    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------

    async def _process_batch(self, findings: list[Finding]) -> None:
        """Send a batch of findings to Gemini and apply suggestions."""
        try:
            prompt = self._build_prompt(findings)
            data = await self._client.generate_json(prompt)

            if not data:
                logger.warning("Empty response from Gemini for suggestion batch")
                return

            suggestions = self._extract_suggestions(data)
            self._apply_suggestions(findings, suggestions)

        except Exception:
            logger.exception("Failed to process suggestion batch")

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(findings: list[Finding]) -> str:
        """Build the suggestion prompt from a list of findings."""
        lines: list[str] = []
        for f in findings:
            lines.append(
                f"- ID: {f.id} | Domínio: {f.domain} | Severidade: {f.severity.value}\n"
                f"  Título: {f.title}\n"
                f"  Descrição: {f.description}"
            )
            if f.url:
                lines.append(f"  URL: {f.url}")
            if f.element_selector:
                lines.append(f"  Seletor: {f.element_selector}")

        problems_text = "\n".join(lines)
        return _SUGGESTION_PROMPT.format(problems=problems_text)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_suggestions(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract the suggestion list from the (possibly wrapped) JSON."""
        # generate_json may wrap a list in {"items": [...]}
        if "items" in data and isinstance(data["items"], list):
            return data["items"]

        # If for some reason the model returned a dict with finding IDs as keys
        if all(isinstance(v, dict) for v in data.values()):
            return list(data.values())

        return []

    @staticmethod
    def _apply_suggestions(
        findings: list[Finding],
        suggestions: list[dict[str, Any]],
    ) -> None:
        """Write AI suggestions back onto the corresponding Finding objects."""
        # Build a lookup by finding ID for O(1) matching
        findings_by_id: dict[str, Finding] = {f.id: f for f in findings}

        matched = 0
        for suggestion in suggestions:
            try:
                finding_id = str(suggestion.get("finding_id", "")).strip()
                finding = findings_by_id.get(finding_id)
                if finding is None:
                    continue

                # Update fix snippet
                snippet = suggestion.get("fix_snippet")
                if snippet and isinstance(snippet, str) and snippet.strip():
                    finding.fix_snippet = snippet.strip()

                # Update estimated time
                minutes = suggestion.get("estimated_minutes")
                if minutes is not None:
                    try:
                        minutes_int = int(minutes)
                        if 1 <= minutes_int <= 480:  # sanity: 1 min to 8 hours
                            finding.estimated_fix_minutes = minutes_int
                    except (TypeError, ValueError):
                        pass

                # Store priority explanation in metadata
                explanation = suggestion.get("priority_explanation")
                if explanation and isinstance(explanation, str):
                    finding.metadata["ai_priority_explanation"] = explanation.strip()

                finding.metadata["ai_enhanced"] = True
                matched += 1

            except Exception:
                logger.debug("Failed to apply suggestion: %s", suggestion)
                continue

        logger.info(
            "Applied AI suggestions to %d/%d findings",
            matched,
            len(findings),
        )
