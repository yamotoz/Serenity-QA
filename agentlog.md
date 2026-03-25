# AGENTLOG — Diario de Bordo do Serenity QA

> Guia interno para agentes de IA e desenvolvedores que trabalham no projeto.
> Contem decisoes arquiteturais, bugs resolvidos, padroes de calibragem e direcionamentos.

---

## Arquitetura Geral

O Serenity e um agente de QA web Python + Playwright que roda em 6 fases:

1. **Phase 1 — Crawling**: Descobre paginas via sitemap + BFS link crawling
2. **Phase 2 — Analise por pagina**: Roda analyzers em cada pagina
3. **Phase 3 — Analise global**: Infraestrutura, sitemap, robots.txt, SSL, security headers
4. **Phase 4 — Modulos avancados**: Chaos engineering, memory leaks, race conditions, etc.
5. **Phase 5 — IA**: UX judge, gerador de testes, sugestoes (requer Gemini API key)
6. **Phase 6 — Relatorios**: HTML, PDF, JSON e prompt_recall.md

### Estrutura de diretorios
```
Serenity.py                  # Entry point (python Serenity.py --url ... --live)
src/serenity/
  cli.py                     # Parser de argumentos e main()
  config.py                  # ScanConfig
  constants.py               # Severity, Verdict, pesos, thresholds
  core/
    engine.py                # Orquestrador (6 fases) + auth gate detection
    crawler.py               # Crawler com normalizacao de URL
    browser_pool.py          # Pool de contextos Playwright
    cdp_manager.py           # Chrome DevTools Protocol
    event_bus.py             # Pub/sub para eventos
    state.py                 # ScanContext — estado global
  analyzers/                 # 1 arquivo por dominio
    base.py, infrastructure.py, performance.py, seo.py,
    functionality.py, responsiveness.py, accessibility.py,
    click_agent.py, content.py, forms.py
  advanced/                  # Modulos avancados (Phase 4)
    behavioral.py, cache_audit.py, chaos.py, i18n.py,
    memory_leak.py, network_analysis.py, race_condition.py, websocket_sse.py
  ai/                        # IA (Phase 5)
    gemini_client.py, suggestion_engine.py, test_generator.py, ux_judge.py
  dashboard/                 # Dashboard --live
    server.py, ws_manager.py, messages.py, static/
  reporting/                 # Relatorios (Phase 6)
    html_report.py, json_report.py, pdf_report.py,
    prompt_recall.py, nav_graph.py, screenshots.py
  scoring/                   # Score
    engine.py, finding.py, rules.py
  utils/                     # Utilitarios
templates/                   # Templates Jinja2
tests/                       # Testes unitarios e integracao
```

---

## Historico de Calibragem (v1 a v10)

O Serenity foi calibrado em 10 iteracoes contra thebuildcode.com.br:

| Scan | Problemas | Score | Status |
|------|-----------|-------|--------|
| v1 | 924 | 38.8 | REPROVADO |
| v4 | 330 | 54.3 | REPROVADO |
| v7 | 155 | 60.8 | REPROVADO |
| v10 | 78 | 70.5 | **APROVADO** |
| v11 | 67 | 72.3 | **APROVADO** |
| v13 | 58 | 74.8 | **APROVADO** |

**93.7% de reducao** (924->58). Score **+93%** (38.8->74.8).

---

## 37 Padroes de Calibragem Implementados

### Crawler e URLs
1. **Trailing slash**: URLs normalizadas (path vazio = "/", strip trailing slash)
2. **Fragment links**: `#section` verificado via DOM, nao HTTP
3. **Cross-page fragments**: `/#section` em /analytics nao e checado no DOM do /analytics

### Acessibilidade
4. **Contraste RGBA compositing**: Percorre parent chain, composita semi-transparentes
5. **Video/canvas backgrounds**: Skip contrast quando texto esta sobre `<video>` ou `<canvas>`
6. **Severity graduada**: <2.0 HIGH, 2.0-3.0 HIGH, 3.0-3.5 MEDIUM, 3.5-4.5 LOW
7. **Decorativo/incidental**: Labels <=11px em steppers/badges = LOW
8. **Agrupamento**: Images sem alt, inputs sem label, elements sem nome = 1 finding por pagina

### Responsividade
9. **Touch targets**: Ambas dimensoes <44px OU uma <24px. Conta padding do parent
10. **Sr-only exclusion**: Skip-to-content links excluidos de touch, overflow e click tests
11. **Agrupamento**: Touch targets e text too small = 1 finding por pagina

### Funcionalidade
12. **Fragment URLs**: Strip `#fragment` antes de comparar com URLs conhecidas
13. **Known OK URLs**: Links para URLs ja carregadas pelo crawler nao sao HTTP-checked

### Infraestrutura
14. **Security headers globais**: Reportados 1x com "afeta N paginas"
15. **Sensitive paths SPA**: /admin com auth gate = sempre protegido
16. **Cookies de infra**: Vercel, Cloudflare, GA filtrados
17. **.well-known**: Paths ignorados em failed resources

### Auth Gate Detection
18. **Client-side auth**: Detecta `form#form-login` ou `input[type=password]` em paginas nao-login
19. **Wait for SPA**: 1.5s timeout para JS renderizar antes de checar auth form

### Chaos Engineering
20. **Same-domain only**: Todos os 4 testes (500, malformed, empty, timeout) filtram third-party
21. **Offline mode**: So testa em apps com Service Worker/PWA manifest

### SEO
22. **First H2 not H1**: So flagra se a pagina NAO tem H1 em nenhum lugar
23. **Robots/sitemap**: Retry com browser UA se Googlebot recebe 403. LOW para bot protection

### Content
24. **Portugues**: Patterns case-sensitive (TODO:, FIXME) e non-latin (teste, exemplo) separados
25. **Progressive loading**: BlurHash/LQIP com lazy loading nao flagrado como placeholder

---

### Cache Audit
26. **Bytes nao requests**: Metrica de cache usa bytes transferidos (warm/cold), nao contagem de requests. 304 Not Modified mantem request count igual mas bytes caem

### i18n
27. **RTL skip LTR**: Testes RTL so rodam se `html lang` e ar, he, fa, ur, etc. Sites pt-BR/en sao ignorados

### Lang Detection
28. **Fallback raw HTML**: Se `document.documentElement.lang` retorna vazio, faz regex no `page.content()` raw HTML. Resolve frameworks que recriam `<html>` client-side

### Forms
29. **Auth forms skip**: Forms com email+password (login/register) nao sao flagrados por "accepts invalid data" — server-side validation e padrao de seguranca
30. **Native validation**: Se o form tem `type="email"` e a submissao nao navegou, validacao nativa provavelmente bloqueou

### Infrastructure
31. **CSP graduado**: CSP missing = LOW (nao HIGH) quando os outros 5 security headers estao presentes. CSP com inline scripts e complexo
32. **Admin SPA**: Paths em `_CLIENT_AUTH_PATHS` (/admin, /dashboard, etc.) sao SEMPRE considerados protegidos mesmo sem body content visivel ao httpx

### Responsiveness (v13)
33. **Overflow-x hidden**: Se o elemento ou ancestral tem `overflow-x: hidden`, o conteudo e cortado visualmente — nao causa scroll horizontal. Skip
34. **Overflow-x auto/scroll**: Se o elemento tem `overflow-x: auto` ou `scroll`, o scroll horizontal e INTENCIONAL (tabs, categorias). Skip

### Cache (v13)
35. **Content-hash filenames**: Assets com hash no nome (e.g. `admin.DEd9KrAh.css`) usam cache content-addressable. `max-age=0 + must-revalidate + ETag` e valido. Skip

### Accessibility (v13)
36. **Login page landmarks**: Paginas standalone de login (form com email+password, poucos links) so exigem `<main>`. Nav/header/footer nao sao obrigatorios. Severity MEDIUM

### Lang Detection (v13)
37. **HTTP fallback definitivo**: 3 metodos em cascata: (1) `document.documentElement.lang`, (2) `querySelector('html[lang]')`, (3) HTTP GET direto + regex no HTML original do servidor

---

## Boas Praticas do Projeto

### Para Analyzers
- Herdar de `BaseAnalyzer`
- Cada finding: `domain`, `severity`, `title`, `description`, `url`, `fix_snippet`
- Try/except ao redor de `page.evaluate()` e `page.query_selector()`
- Agrupar findings identicos por pagina (1 finding com count)
- Timeouts explicitos em toda interacao com browser

### Para Modulos Avancados
- `_MAX_SAMPLE_PAGES` = 2-3
- Cada modulo deve completar em <90s (timeout global 120s)
- Fechar contextos de browser no bloco `finally`
- Filtrar requests third-party em chaos tests

### Para o Dashboard
- Toda mudanca de estado emite evento via `event_bus`
- `lifespan="off"` no uvicorn para evitar CancelledError no shutdown

### Para Relatorios
- prompt_recall.md sempre em Portugues do Brasil
- HTML e o formato principal
- PDF requer WeasyPrint + GTK (pode falhar no Windows)

### Para o Score
- Pesos: Performance 25%, SEO 20%, Funcionalidade 20%, Responsividade 15%, Acessibilidade 10%, Infraestrutura 10%
- < 70 = REPROVADO, 70-90 = APROVADO, 91-100 = EXCELENTE
- Penalidade decrescente: 1o = 100%, 2o = 50%, 3o+ = 20% por tipo/pagina

---

## Flags CLI

| Flag | Descricao | Default |
|------|-----------|---------|
| `--url URL` | URL alvo (obrigatorio) | — |
| `--live` | Dashboard em tempo real | off |
| `-o NOME` | Pasta de output | — |
| `--max-pages N` | Max paginas | 100 |
| `--timeout N` | Timeout por pagina (s) | 30 |
| `--domains D1 D2` | Dominios especificos | todos |
| `--format FMT` | html, pdf, json, all | all |
| `--no-advanced` | Pula Phase 4 | off |
| `--no-ai` | Pula Phase 5 | off |
| `--headed` | Browser visivel | off |
| `-v / -vv` | Verbosidade | warning |

---

*Ultima atualizacao: 2026-03-24 — v0.1.0 calibrado e aprovado*
