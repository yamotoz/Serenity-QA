"""Test Generator — auto-generate Playwright test cases from scan results.

Collects navigation data, forms, interactive elements, and critical findings,
then asks Gemini to produce a complete ``pytest`` / ``playwright`` test file
saved to the scan's output directory.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from serenity.constants import Severity

if TYPE_CHECKING:
    from serenity.ai.gemini_client import GeminiClient
    from serenity.core.state import ScanContext

logger = logging.getLogger("serenity.ai.test_gen")

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_TEST_GEN_PROMPT = """\
Gere um arquivo de testes Playwright em Python (pytest) para esta aplicação web.
O código deve ser completo, pronto para executar, e usar boas práticas.

URL base: {base_url}

Páginas descobertas:
{urls}

Formulários encontrados:
{forms}

Problemas críticos encontrados:
{critical_findings}

Grafo de navegação (arestas):
{nav_edges}

Gere testes que cobrem:
1. Navegação por todas as páginas (smoke test)
2. Validação de formulários
3. Verificação de elementos interativos
4. Assertions de acessibilidade básica (alt em imagens, labels em inputs, lang no html)

Regras:
- Use fixtures do pytest (@pytest.fixture)
- Use page objects quando fizer sentido
- Use assertions claras e descritivas
- Adicione docstrings nos testes
- Use async/await com pytest-playwright
- Importe de pytest_playwright: page, browser, context
- Gere APENAS o código Python, sem explicação
"""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MAX_URLS_IN_PROMPT = 30
_MAX_FINDINGS_IN_PROMPT = 15
_MAX_EDGES_IN_PROMPT = 30
_MAX_FORMS_IN_PROMPT = 10
_OUTPUT_FILENAME = "generated_tests.py"


async def generate_tests(ctx: ScanContext, client: GeminiClient) -> str:
    """Generate a Playwright test file and save it to the output directory.

    Parameters
    ----------
    ctx:
        The current scan context with accumulated state.
    client:
        An initialised :class:`GeminiClient`.

    Returns
    -------
    str
        The generated Python test code, or an empty string on failure.
    """
    try:
        prompt = _build_prompt(ctx)
        logger.info("Requesting test generation from Gemini")

        code = await client.generate(prompt)
        if not code:
            logger.warning("Gemini returned empty test code")
            return ""

        code = _clean_code_response(code)

        # Persist to disk
        output_path = _save_tests(ctx, code)
        logger.info("Generated tests saved to %s", output_path)

        return code

    except Exception:
        logger.exception("Test generation failed")
        return ""


# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------


def _build_prompt(ctx: ScanContext) -> str:
    """Assemble the test-generation prompt from scan state."""
    urls_text = _format_urls(ctx)
    forms_text = _format_forms(ctx)
    findings_text = _format_critical_findings(ctx)
    edges_text = _format_nav_edges(ctx)

    return _TEST_GEN_PROMPT.format(
        base_url=ctx.config.target_url,
        urls=urls_text or "(nenhuma página descoberta)",
        forms=forms_text or "(nenhum formulário encontrado)",
        critical_findings=findings_text or "(nenhum problema crítico)",
        nav_edges=edges_text or "(nenhuma aresta de navegação)",
    )


def _format_urls(ctx: ScanContext) -> str:
    """Format discovered URLs for the prompt."""
    urls = sorted(ctx.state.discovered_urls)[:_MAX_URLS_IN_PROMPT]
    if not urls:
        return ""
    return "\n".join(f"- {url}" for url in urls)


def _format_forms(ctx: ScanContext) -> str:
    """Extract form information from page data for the prompt."""
    form_entries: list[str] = []

    for url, page_data in ctx.state.page_data.items():
        # Check if page_data has form-related metadata
        html = page_data.html_content
        if not html:
            continue

        # Simple heuristic: detect forms by looking for <form in HTML
        if "<form" in html.lower():
            form_entries.append(f"- Página: {url} (contém formulário)")

        if len(form_entries) >= _MAX_FORMS_IN_PROMPT:
            break

    # Also check findings for form-related issues
    for finding in ctx.state.findings:
        if finding.domain == "forms" and finding.url:
            entry = f"- Formulário em: {finding.url}"
            if finding.element_selector:
                entry += f" (seletor: {finding.element_selector})"
            if entry not in form_entries:
                form_entries.append(entry)

        if len(form_entries) >= _MAX_FORMS_IN_PROMPT:
            break

    return "\n".join(form_entries)


def _format_critical_findings(ctx: ScanContext) -> str:
    """Format critical/high-severity findings for the prompt."""
    critical = [
        f
        for f in ctx.state.findings
        if f.severity in (Severity.CRITICAL, Severity.HIGH)
    ]

    # Sort by severity then deduction
    critical.sort(
        key=lambda f: (0 if f.severity == Severity.CRITICAL else 1, -f.deduction_points),
    )

    entries: list[str] = []
    for f in critical[:_MAX_FINDINGS_IN_PROMPT]:
        line = f"- [{f.severity.value.upper()}] {f.title}"
        if f.url:
            line += f" ({f.url})"
        if f.description:
            # Truncate long descriptions
            desc = f.description[:150]
            if len(f.description) > 150:
                desc += "..."
            line += f"\n  {desc}"
        entries.append(line)

    return "\n".join(entries)


def _format_nav_edges(ctx: ScanContext) -> str:
    """Format navigation graph edges for the prompt."""
    edges = ctx.state.nav_edges[:_MAX_EDGES_IN_PROMPT]
    if not edges:
        return ""

    entries: list[str] = []
    for edge in edges:
        line = f"- {edge.source_url} -> {edge.target_url}"
        if edge.trigger_text:
            line += f' (via "{edge.trigger_text}")'
        entries.append(line)

    return "\n".join(entries)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _clean_code_response(raw: str) -> str:
    """Strip markdown fences and non-code preamble from Gemini's response."""
    text = raw.strip()

    # Remove markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove opening fence (```python or ```)
        lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Ensure the file starts with a valid Python construct
    # If there's text before the first import/from/def/class, strip it
    valid_starts = ("import ", "from ", "def ", "class ", "#", '"""', "'''", "@")
    result_lines: list[str] = []
    found_code = False
    for line in text.split("\n"):
        stripped = line.strip()
        if not found_code:
            if stripped and any(stripped.startswith(s) for s in valid_starts):
                found_code = True
            elif not stripped:
                continue  # skip leading blank lines
            else:
                continue  # skip non-code preamble
        result_lines.append(line)

    return "\n".join(result_lines) if result_lines else text


def _save_tests(ctx: ScanContext, code: str) -> Path:
    """Write the generated tests to the output directory.

    Returns the path to the saved file.
    """
    output_dir = Path(ctx.config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _OUTPUT_FILENAME

    output_path.write_text(code, encoding="utf-8")
    return output_path
