# SERENITY QA

**Agente de Quality Assurance completo para aplicacoes web.**

Serenity analisa seu site de ponta a ponta: performance, SEO, acessibilidade, seguranca, responsividade, funcionalidade, interacoes e conteudo. Entrega um score de 0 a 100 com relatorio detalhado e dashboard em tempo real.

---

## Inicio Rapido

```bash
# 1. Clone e entre no diretorio
cd Serenity-QA

# 2. Crie o ambiente virtual e instale
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

pip install -e .
playwright install chromium

# 3. Execute
python Serenity.py --url https://seusite.com --live
```

---

## Como Usar

### Scan basico
```bash
python Serenity.py --url https://seusite.com
```

### Com dashboard em tempo real
```bash
python Serenity.py --url https://seusite.com --live
```

### Output organizado em pasta nomeada
```bash
python Serenity.py --url https://seusite.com -o meusite --live
```
Cria a pasta `./meusite/` contendo:
- `serenity-report.html` — relatorio interativo
- `serenity-report.json` — dados para CI/CD
- `serenity-report.pdf` — relatorio para cliente (requer GTK no Windows)
- `prompt_recall.md` — prompt de engenharia avancada para agente de IA corrigir tudo

### Scan rapido (sem modulos avancados)
```bash
python Serenity.py --url https://seusite.com --no-advanced --no-ai --max-pages 20
```

---

## Flags CLI

| Flag | Descricao | Default |
|------|-----------|---------|
| `--url URL` | URL alvo **(obrigatorio)** | — |
| `--live` | Dashboard em tempo real no browser | off |
| `-o NOME` | Pasta de output nomeada | — |
| `--output-dir DIR` | Diretorio de output | `./serenity-report` |
| `--max-pages N` | Maximo de paginas | 100 |
| `--timeout N` | Timeout por pagina (segundos) | 30 |
| `--domains D1 D2` | Dominios especificos | todos |
| `--format FMT` | html, pdf, json, all | all |
| `--no-advanced` | Pula analise avancada | off |
| `--no-ai` | Pula analise com IA | off |
| `--headed` | Browser visivel | off |
| `-v / -vv` | Verbosidade | warning |

---

## O Que o Serenity Analisa

### 9 Dominios de Analise

| # | Dominio | O que verifica |
|---|---------|---------------|
| 1 | **Infraestrutura** | SSL, HTTPS, headers de seguranca (CSP, HSTS), TTFB, arquivos sensiveis expostos |
| 2 | **Performance** | LCP, CLS, INP, tempo de carregamento, tamanho da pagina, render-blocking, lazy load |
| 3 | **SEO** | Title, meta description, H1, headings, Open Graph, sitemap, robots.txt, canonical, Schema.org |
| 4 | **Funcionalidade** | Links quebrados (404), JS errors, redirect loops, cookies inseguros, dados em localStorage |
| 5 | **Responsividade** | Screenshots em 375/768/1280px, overflow, touch targets, zoom, CLS por imagens |
| 6 | **Acessibilidade** | Contraste WCAG 2.1, alt text, labels, lang, skip nav, focus order, ARIA landmarks |
| 7 | **Agente Clicador** | Clica em todos os elementos interativos, mapeia navegacao, detecta dead-ends e fake clickables |
| 8 | **Conteudo** | Placeholders, Lorem ipsum, datas desatualizadas, tokens no JS, links sociais quebrados |
| 9 | **Formularios** | Envio vazio, dados invalidos, XSS/SQLi basico, mensagens de erro, validacao client/server |

### Modulos Avancados (Phase 4)

| Modulo | O que faz |
|--------|-----------|
| **Chaos Engineering** | Injeta falhas (500, timeout, JSON corrompido, offline) e observa a UI |
| **Memory Leak Detector** | Monitora heap JS via CDP em ciclos de navegacao |
| **Race Condition Detector** | Dispara interacoes em paralelo com timing conflitante |
| **Network Analysis** | Intercepta 100% do trafego, mapeia APIs, detecta dados sensiveis |
| **Cache Audit** | Compara carregamento frio vs quente, analisa headers de cache |
| **Behavioral Analysis** | Simula comportamento humano (mouse Bezier, timing Poisson) |
| **i18n Stress Test** | Pseudolocalizacao, strings longas, RTL |
| **WebSocket/SSE Analysis** | Monitora conexoes em tempo real, testa queda de conexao |

### Analise com IA (Phase 5, requer Gemini API key)

| Modulo | O que faz |
|--------|-----------|
| **UX Judge** | Envia screenshots para LLM avaliar hierarquia visual, CTA, copy |
| **Gerador de Testes** | Gera arquivo de testes Playwright automaticamente |
| **Motor de Sugestoes** | Sugestoes de melhoria priorizadas por impacto |

---

## Sistema de Score

Score de **0 a 100** calculado com pesos por dominio:

| Dominio | Peso |
|---------|------|
| Performance | 25% |
| SEO | 20% |
| Funcionalidade + Clicador | 20% |
| Responsividade | 15% |
| Acessibilidade | 10% |
| Infraestrutura | 10% |

### Veredictos

| Score | Veredicto |
|-------|-----------|
| 0-69 | **REPROVADO** |
| 70-90 | **APROVADO** |
| 91-100 | **EXCELENTE** |

### Calibragem inteligente

O Serenity inclui 20+ padroes de calibragem para evitar falsos positivos:
- Deteccao de auth gates (paginas com login form sao skipadas)
- Agrupamento de issues identicos por pagina (penalidade decrescente)
- Contraste com compositing RGBA e skip de video/canvas backgrounds
- Touch targets com padding do parent element
- Chaos engineering apenas em API calls same-domain
- Severity graduada para contraste (nao tudo e HIGH)
- Tolerancia a bot protection (Vercel/Cloudflare)

---

## Dashboard --live

O dashboard abre automaticamente no browser em `http://127.0.0.1:8765` e mostra em tempo real:

- Velocimetro de score geral (0-100)
- Progresso do scan (paginas/total)
- Findings por severidade (critico, alto, medio, baixo)
- Scores por dominio com barras de progresso
- Heatmap de paginas (passou/analisando/falhou)
- Log de findings em tempo real com filtros

Design inspirado na Grecia antiga: marmore, ouro e azul do mar.

---

## Relatorios

### HTML (`serenity-report.html`)
Relatorio interativo e navegavel. Filtravel por dominio e severidade.

### PDF (`serenity-report.pdf`)
Relatorio formatado para enviar ao cliente. Requer GTK/libgobject no Windows.

### JSON (`serenity-report.json`)
Dados estruturados para integracao com pipelines CI/CD.

### Prompt Recall (`prompt_recall.md`)
Documento de engenharia de prompt avancada em Portugues do Brasil. Copie e cole no seu agente de IA para que ele corrija todos os problemas sistematicamente.

---

## Variaveis de Ambiente

```env
GEMINI_API_KEY=sua-chave-aqui    # Habilita modulos de IA (Phase 5)
SUPABASE_URL=url                  # Persistencia de scans (opcional)
SUPABASE_KEY=key                  # Persistencia de scans (opcional)
```

---

## Stack Tecnologica

| Tecnologia | Uso |
|------------|-----|
| Python 3.12+ | Core, CLI, orquestracao, relatorios |
| Playwright | Browser real, interacoes, screenshots, Core Web Vitals |
| FastAPI + WebSocket | Dashboard --live em tempo real |
| Rich | Output bonito no terminal |
| Jinja2 | Templates HTML (dashboard e relatorios) |
| WeasyPrint | Conversao HTML -> PDF |
| httpx | Requests HTTP assincronos |
| Pydantic | Validacao de dados e modelos |
| Google Gemini | Analise de IA (opcional) |

---

## Requisitos

- Python 3.12+
- Chromium (instalado via `playwright install chromium`)
- GTK/libgobject (apenas para PDF no Windows — opcional)

---

## Licenca

MIT

---

*Serenity QA v0.1.0*
