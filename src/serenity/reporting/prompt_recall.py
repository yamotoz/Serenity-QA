"""Gera prompt_recall.md — documento de engenharia de prompt avancada.

Este arquivo e projetado para ser copiado e colado no contexto de um
agente de IA para que ele entenda cada pagina, cada erro, e possa
corrigir tudo de forma sistematica.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from serenity.constants import Severity, Verdict
from serenity.scoring.engine import ScoringEngine

logger = logging.getLogger("serenity.reporting.prompt_recall")


async def generate_prompt_recall(
    ctx: Any,
    scores: dict[str, Any],
    verdict: Verdict,
    output_dir: Path,
) -> Path:
    """Gera prompt_recall.md com ou sem enriquecimento da Gemini."""
    content = _build_base_document(ctx, scores, verdict)

    if ctx.config.gemini_api_key:
        try:
            enhanced = await _enhance_with_gemini(ctx, scores, verdict, content)
            if enhanced:
                content = enhanced
        except Exception as e:
            logger.warning("Enriquecimento com Gemini falhou, usando documento base: %s", e)

    output_path = output_dir / "prompt_recall.md"
    output_path.write_text(content, encoding="utf-8")
    logger.info("prompt_recall.md gerado em %s", output_path)
    return output_path


def _build_base_document(ctx: Any, scores: dict[str, Any], verdict: Verdict) -> str:
    """Constroi o prompt_recall.md completo a partir dos dados do scan."""
    state = ctx.state
    config = ctx.config
    scoring = ScoringEngine()
    prioritized = scoring.get_prioritized_fixes(state.findings)

    # Agrupar findings por pagina
    findings_by_page: dict[str, list] = defaultdict(list)
    global_findings: list = []
    for f in state.findings:
        if f.url:
            findings_by_page[f.url].append(f)
        else:
            global_findings.append(f)

    # Agrupar findings por dominio
    findings_by_domain: dict[str, list] = defaultdict(list)
    for f in state.findings:
        findings_by_domain[f.domain].append(f)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []

    # ── Cabecalho ─────────────────────────────────────────────────
    lines.append("# PROMPT RECALL — Relatorio de Analise Serenity QA")
    lines.append("")
    lines.append("> **Objetivo**: Este documento e um prompt avancado projetado para ser")
    lines.append("> entregue a um agente de IA programador. Ele contem uma descricao")
    lines.append("> completa e estruturada de cada pagina analisada e cada problema")
    lines.append("> encontrado. Copie este documento inteiro no contexto do seu agente")
    lines.append("> de IA para que ele possa corrigir todos os problemas sistematicamente.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Contexto ──────────────────────────────────────────────────
    lines.append("## CONTEXTO")
    lines.append("")
    lines.append(f"- **URL Alvo**: `{config.target_url}`")
    lines.append(f"- **Data do Scan**: {now}")
    lines.append(f"- **Paginas Analisadas**: {state.pages_analyzed}")
    lines.append(f"- **Total de Problemas**: {state.total_findings}")
    lines.append(f"- **Score Geral**: {scores['overall']}/100 — **{verdict.value}**")
    lines.append(f"- **Duracao do Scan**: {state.elapsed_seconds:.0f}s")
    lines.append("")

    # ── Score por Dominio ─────────────────────────────────────────
    lines.append("## SCORE POR DOMINIO")
    lines.append("")
    lines.append("| Dominio | Score | Status | Problemas |")
    lines.append("|---------|-------|--------|-----------|")
    for domain, score in sorted(scores["domains"].items(), key=lambda x: x[1]):
        status = "APROVADO" if score >= 70 else "REPROVADO"
        count = len(findings_by_domain.get(domain, []))
        lines.append(f"| {domain} | {score}/100 | {status} | {count} |")
    lines.append("")

    # ── Ordem de Correcao Prioritaria ─────────────────────────────
    lines.append("## ORDEM DE CORRECAO PRIORITARIA")
    lines.append("")
    lines.append("Corrija estes problemas nesta ordem exata para maximizar a melhoria")
    lines.append("do score com o minimo de esforco (ordenado por pontos-recuperados-por-minuto):")
    lines.append("")
    for i, f in enumerate(prioritized[:30], 1):
        sev = f.severity.value.upper()
        lines.append(f"{i}. **[{sev}]** {f.title}")
        if f.url:
            lines.append(f"   - Pagina: `{f.url}`")
        lines.append(f"   - Impacto: -{f.deduction_points}pts | Tempo estimado: ~{f.estimated_fix_minutes}min")
        if f.fix_snippet:
            lines.append(f"   - Correcao: `{f.fix_snippet[:200]}`")
        lines.append("")

    # ── Analise Pagina por Pagina ─────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## ANALISE PAGINA POR PAGINA")
    lines.append("")
    lines.append("Abaixo esta cada pagina analisada com todos os problemas encontrados.")
    lines.append("Use isto para entender o escopo completo de trabalho por pagina.")
    lines.append("")

    for url in sorted(findings_by_page.keys()):
        page_findings = findings_by_page[url]
        page_data = state.page_data.get(url)

        lines.append(f"### `{url}`")
        lines.append("")

        if page_data:
            lines.append(f"- Status HTTP: {page_data.status_code}")
            if page_data.title:
                lines.append(f"- Titulo: \"{page_data.title}\"")
            if page_data.ttfb_ms:
                lines.append(f"- TTFB: {page_data.ttfb_ms:.0f}ms")
            if page_data.load_time_ms:
                lines.append(f"- Tempo de carregamento: {page_data.load_time_ms:.0f}ms")
        lines.append("")

        # Agrupar por severidade
        by_sev: dict[str, list] = defaultdict(list)
        for f in page_findings:
            by_sev[f.severity.value].append(f)

        sev_labels = {
            "critical": "CRITICO",
            "high": "ALTO",
            "medium": "MEDIO",
            "low": "BAIXO",
        }

        for sev_name in ["critical", "high", "medium", "low"]:
            sev_findings = by_sev.get(sev_name, [])
            if not sev_findings:
                continue

            label = sev_labels[sev_name]
            lines.append(f"**{label} ({len(sev_findings)}):**")
            lines.append("")
            for f in sev_findings:
                lines.append(f"- **{f.title}**")
                if f.description and f.description != f.title:
                    desc = f.description[:300].replace("\n", " ")
                    lines.append(f"  - {desc}")
                if f.element_selector:
                    lines.append(f"  - Elemento: `{f.element_selector}`")
                if f.fix_snippet:
                    lines.append(f"  - Correcao sugerida:")
                    lines.append(f"    ```")
                    for fix_line in f.fix_snippet.split("\n")[:10]:
                        lines.append(f"    {fix_line}")
                    lines.append(f"    ```")
            lines.append("")

    # ── Problemas Globais ─────────────────────────────────────────
    if global_findings:
        lines.append("### Problemas Globais (nao vinculados a uma pagina especifica)")
        lines.append("")
        for f in global_findings:
            sev = f.severity.value.upper()
            lines.append(f"- **[{sev}]** {f.title}")
            if f.description and f.description != f.title:
                lines.append(f"  - {f.description[:200]}")
        lines.append("")

    # ── Instrucoes por Dominio ────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## INSTRUCOES DE CORRECAO POR DOMINIO")
    lines.append("")

    domain_instructions = {
        "infrastructure": (
            "## Correcoes de Infraestrutura\n\n"
            "Estas sao correcoes de nivel servidor/deploy:\n"
            "- Adicione os headers de seguranca faltantes na config do seu servidor (nginx/apache/vercel.json)\n"
            "- Garanta que HTTP redireciona para HTTPS (redirect 301)\n"
            "- Verifique validade e renovacao do certificado SSL\n"
            "- Remova ou proteja arquivos sensiveis expostos (.env, .git)\n"
            "- Reduza o TTFB otimizando o tempo de resposta do servidor"
        ),
        "performance": (
            "## Correcoes de Performance\n\n"
            "Foco nos Core Web Vitals:\n"
            "- LCP: Otimize o maior elemento de conteudo (preload da imagem hero, reduzir tempo do servidor)\n"
            "- CLS: Adicione width/height explicitos em imagens, evite inserir conteudo acima do fold\n"
            "- Reduza recursos que bloqueiam renderizacao: adicione async/defer nos scripts, use media queries no CSS\n"
            "- Ative lazy loading para imagens abaixo do fold: `loading=\"lazy\"`\n"
            "- Adicione `font-display: swap` nas regras @font-face\n"
            "- Comprima imagens (WebP/AVIF), minifique CSS/JS"
        ),
        "seo": (
            "## Correcoes de SEO\n\n"
            "Cada pagina precisa de:\n"
            "- Tag `<title>` unica (50-60 caracteres)\n"
            "- `<meta name=\"description\">` unico (120-160 caracteres)\n"
            "- Exatamente um `<h1>` por pagina\n"
            "- Hierarquia correta de headings (h1 > h2 > h3, sem pular niveis)\n"
            "- Tags Open Graph: og:title, og:description, og:image\n"
            "- URL canonica: `<link rel=\"canonical\" href=\"...\">`\n"
            "- sitemap.xml valido em /sitemap.xml\n"
            "- robots.txt em /robots.txt"
        ),
        "accessibility": (
            "## Correcoes de Acessibilidade (WCAG 2.1)\n\n"
            "- Todas as imagens precisam do atributo `alt` com texto descritivo\n"
            "- Garanta contraste minimo de 4.5:1 para texto normal, 3:1 para texto grande\n"
            "- Todo input de formulario precisa de um `<label>` associado\n"
            "- Adicione atributo `lang` na tag `<html>`\n"
            "- Adicione link de skip navigation como primeiro elemento focavel\n"
            "- Garanta ordem logica de foco na navegacao por teclado\n"
            "- Adicione landmarks ARIA: `<main>`, `<nav>`, `<header>`, `<footer>`"
        ),
        "responsiveness": (
            "## Correcoes de Responsividade\n\n"
            "- Corrija overflow horizontal: verifique elementos mais largos que o viewport\n"
            "- Garanta touch targets de pelo menos 44x44px no mobile\n"
            "- Nao bloqueie zoom: remova `user-scalable=no` da meta viewport\n"
            "- Adicione `width` e `height` em todas as `<img>` para prevenir CLS\n"
            "- Garanta texto de pelo menos 16px no mobile (minimo absoluto 12px)\n"
            "- Teste nos breakpoints 375px, 768px e 1280px"
        ),
        "functionality": (
            "## Correcoes de Funcionalidade\n\n"
            "- Corrija todos os links internos quebrados (404)\n"
            "- Corrija ou remova links externos quebrados\n"
            "- Resolva erros de JavaScript no console\n"
            "- Adicione flags HttpOnly, Secure e SameSite nos cookies\n"
            "- Remova dados sensiveis do localStorage (tokens JWT, PII)\n"
            "- Corrija loops de redirecionamento"
        ),
        "click_agent": (
            "## Correcoes de Interacao\n\n"
            "- Remova elementos falso-clicaveis (cursor:pointer sem acao)\n"
            "- Corrija paginas sem saida (adicione navegacao de volta)\n"
            "- Conecte paginas orfas (adicione links para elas no site)\n"
            "- Garanta que botoes desabilitados tenham feedback visual"
        ),
        "content": (
            "## Correcoes de Conteudo\n\n"
            "- Substitua todo texto placeholder (Lorem ipsum, TODO, FIXME)\n"
            "- Substitua imagens placeholder\n"
            "- Atualize o ano do copyright no footer\n"
            "- Corrija links de redes sociais quebrados\n"
            "- Remova chaves de API/tokens hardcoded no JavaScript do frontend"
        ),
        "forms": (
            "## Correcoes de Formularios\n\n"
            "- Adicione validacao client-side em todos os formularios\n"
            "- Mostre mensagens de erro descritivas perto do campo\n"
            "- Use aria-live nos containers de mensagem de erro\n"
            "- Garanta que erros desaparecam quando o usuario corrige o input\n"
            "- Proteja contra SQL injection e XSS"
        ),
    }

    for domain, count_list in findings_by_domain.items():
        if not count_list:
            continue
        instruction = domain_instructions.get(
            domain,
            f"## Correcoes de {domain.title()}\n\nRevise e corrija todos os {len(count_list)} problemas listados acima."
        )
        lines.append(instruction)
        lines.append("")

    # ── Instrucoes para o Agente de IA ────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## INSTRUCOES PARA O AGENTE DE IA")
    lines.append("")
    lines.append("Voce foi designado para corrigir todos os problemas descritos acima.")
    lines.append("Siga estas regras:")
    lines.append("")
    lines.append("1. **Trabalhe pagina por pagina** — corrija todos os problemas de uma pagina antes de passar para a proxima")
    lines.append("2. **Siga a ordem de prioridade** — corrija problemas de alto impacto e baixo esforco primeiro")
    lines.append("3. **Teste apos cada correcao** — verifique se a correcao nao quebrou nada")
    lines.append("4. **Seja minimalista** — altere apenas o necessario, nao refatore codigo nao relacionado")
    lines.append("5. **Preserve funcionalidades existentes** — nao remova features")
    lines.append("6. **Use HTML semantico** — prefira `<button>` em vez de `<div onclick>`")
    lines.append("7. **Siga WCAG 2.1 AA** — acessibilidade nao e opcional")
    lines.append(f"8. **Score alvo: 70+** — score atual e {scores['overall']}/100")
    lines.append("")
    lines.append(f"Total de problemas a corrigir: **{state.total_findings}**")
    lines.append(f"Tempo total estimado de correcao: **{sum(f.estimated_fix_minutes for f in state.findings)}min**")
    lines.append("")
    lines.append("---")
    lines.append(f"*Gerado pelo Serenity QA v0.1.0 em {now}*")

    return "\n".join(lines)


async def _enhance_with_gemini(
    ctx: Any,
    scores: dict[str, Any],
    verdict: Verdict,
    base_content: str,
) -> str | None:
    """Usa Gemini para enriquecer o prompt_recall com insights de IA."""
    try:
        from serenity.ai.gemini_client import GeminiClient
        client = GeminiClient(ctx.config.gemini_api_key)

        summary_lines = []
        summary_lines.append(f"Site: {ctx.config.target_url}")
        summary_lines.append(f"Score: {scores['overall']}/100 - {verdict.value}")
        summary_lines.append(f"Paginas: {ctx.state.pages_analyzed}")
        summary_lines.append(f"Problemas: {ctx.state.total_findings}")
        summary_lines.append("")
        summary_lines.append("Scores por dominio:")
        for domain, score in scores["domains"].items():
            summary_lines.append(f"  {domain}: {score}/100")
        summary_lines.append("")
        summary_lines.append("Top 20 problemas criticos:")
        for f in sorted(ctx.state.findings, key=lambda x: x.deduction_points, reverse=True)[:20]:
            summary_lines.append(f"  [{f.severity.value}] {f.title} (pagina: {f.url or 'global'})")

        summary = "\n".join(summary_lines)

        prompt = f"""Voce e um engenheiro de QA senior brasileiro. Analise este relatorio de QA de um site e gere uma secao chamada "## ANALISE ESTRATEGICA DA IA" que sera adicionada ao final do documento.

Essa secao deve conter:
1. **Diagnostico geral**: Em 3-5 frases, explique o estado do site e os problemas mais graves
2. **Padroes identificados**: Agrupe os problemas em padroes (ex: "o site inteiro nao tem meta descriptions", "todos os formularios falham na validacao")
3. **Plano de ataque em 5 passos**: Os 5 passos mais impactantes para subir o score, em ordem
4. **Quick wins**: Lista de coisas que podem ser corrigidas em menos de 5 minutos cada
5. **Riscos criticos**: Problemas que podem afetar seguranca, SEO ranking, ou experiencia do usuario de forma grave

Escreva em portugues do Brasil. Seja direto e tecnico. Use markdown.

Dados do scan:
{summary}"""

        response = await client.generate(prompt)
        if response and len(response) > 100:
            return base_content + "\n\n" + response

    except Exception as e:
        logger.debug("Enriquecimento com Gemini falhou: %s", e)

    return None
