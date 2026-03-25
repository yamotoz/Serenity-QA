"""Scoring rules — maps finding types to deduction values and fix time estimates."""

from __future__ import annotations

from dataclasses import dataclass

from serenity.constants import Severity


@dataclass
class ScoringRule:
    """Defines deduction and fix estimate for a finding type."""

    severity: Severity
    deduction: float
    estimated_fix_minutes: int
    title_template: str


# ---------------------------------------------------------------------------
# Infrastructure rules
# ---------------------------------------------------------------------------

INFRASTRUCTURE_RULES = {
    "ssl_expired": ScoringRule(Severity.CRITICAL, 25, 30, "Certificado SSL expirado"),
    "ssl_expiring_soon": ScoringRule(Severity.HIGH, 10, 30, "Certificado SSL expira em {days} dias"),
    "no_https_redirect": ScoringRule(Severity.HIGH, 10, 15, "Sem redirect HTTP → HTTPS"),
    "missing_security_header": ScoringRule(Severity.MEDIUM, 5, 10, "Header de segurança ausente: {header}"),
    "sensitive_file_exposed": ScoringRule(Severity.CRITICAL, 25, 15, "Arquivo sensível exposto: {path}"),
    "server_error_500": ScoringRule(Severity.CRITICAL, 25, 60, "Erro 500 no servidor: {url}"),
    "high_ttfb": ScoringRule(Severity.MEDIUM, 5, 30, "TTFB alto ({ttfb}ms): {url}"),
}

# ---------------------------------------------------------------------------
# Performance rules
# ---------------------------------------------------------------------------

PERFORMANCE_RULES = {
    "lcp_slow": ScoringRule(Severity.HIGH, 10, 60, "LCP lento ({lcp}s): {url}"),
    "cls_high": ScoringRule(Severity.HIGH, 10, 45, "CLS alto ({cls}): {url}"),
    "inp_slow": ScoringRule(Severity.MEDIUM, 5, 30, "INP lento ({inp}ms): {url}"),
    "render_blocking_resource": ScoringRule(Severity.MEDIUM, 5, 20, "Recurso bloqueante: {resource}"),
    "large_image_uncompressed": ScoringRule(Severity.MEDIUM, 5, 15, "Imagem sem compressão: {url}"),
    "no_lazy_load": ScoringRule(Severity.LOW, 2, 10, "Imagem sem lazy load: {url}"),
    "blocking_font": ScoringRule(Severity.MEDIUM, 5, 15, "Fonte bloqueante: {font}"),
    "large_page_size": ScoringRule(Severity.MEDIUM, 5, 30, "Página muito grande ({size}KB): {url}"),
    "excessive_requests": ScoringRule(Severity.MEDIUM, 5, 30, "Muitas requisições ({count}): {url}"),
}

# ---------------------------------------------------------------------------
# SEO rules
# ---------------------------------------------------------------------------

SEO_RULES = {
    "missing_title": ScoringRule(Severity.HIGH, 10, 5, "Título ausente: {url}"),
    "title_too_long": ScoringRule(Severity.LOW, 2, 5, "Título muito longo ({length} chars): {url}"),
    "title_too_short": ScoringRule(Severity.LOW, 2, 5, "Título muito curto ({length} chars): {url}"),
    "missing_meta_description": ScoringRule(Severity.MEDIUM, 5, 5, "Meta description ausente: {url}"),
    "meta_description_wrong_length": ScoringRule(Severity.LOW, 2, 5, "Meta description fora do tamanho ideal: {url}"),
    "missing_h1": ScoringRule(Severity.HIGH, 10, 10, "H1 ausente: {url}"),
    "multiple_h1": ScoringRule(Severity.MEDIUM, 5, 10, "Múltiplos H1 ({count}): {url}"),
    "missing_og_tags": ScoringRule(Severity.MEDIUM, 5, 15, "Open Graph incompleto: {url}"),
    "missing_sitemap": ScoringRule(Severity.MEDIUM, 5, 30, "Sitemap.xml ausente"),
    "missing_robots_txt": ScoringRule(Severity.LOW, 2, 15, "Robots.txt ausente"),
    "missing_canonical": ScoringRule(Severity.MEDIUM, 5, 10, "URL canônica ausente: {url}"),
    "missing_schema_org": ScoringRule(Severity.LOW, 2, 30, "Dados estruturados ausentes: {url}"),
    "heading_hierarchy_broken": ScoringRule(Severity.MEDIUM, 5, 15, "Hierarquia de headings quebrada: {url}"),
}

# ---------------------------------------------------------------------------
# Functionality rules
# ---------------------------------------------------------------------------

FUNCTIONALITY_RULES = {
    "broken_internal_link": ScoringRule(Severity.HIGH, 10, 10, "Link interno quebrado: {url} → {target}"),
    "broken_external_link": ScoringRule(Severity.LOW, 2, 5, "Link externo quebrado: {url} → {target}"),
    "js_console_error": ScoringRule(Severity.HIGH, 10, 30, "Erro JavaScript: {error}"),
    "redirect_loop": ScoringRule(Severity.CRITICAL, 25, 30, "Loop de redirecionamento: {url}"),
    "resource_404": ScoringRule(Severity.MEDIUM, 5, 10, "Recurso 404: {resource}"),
    "cookie_no_httponly": ScoringRule(Severity.MEDIUM, 5, 10, "Cookie sem HttpOnly: {cookie}"),
    "cookie_no_secure": ScoringRule(Severity.MEDIUM, 5, 10, "Cookie sem Secure: {cookie}"),
    "cookie_no_samesite": ScoringRule(Severity.LOW, 2, 10, "Cookie sem SameSite: {cookie}"),
    "sensitive_localstorage": ScoringRule(Severity.HIGH, 10, 30, "Dado sensível em localStorage: {key}"),
}

# ---------------------------------------------------------------------------
# Responsiveness rules
# ---------------------------------------------------------------------------

RESPONSIVENESS_RULES = {
    "horizontal_overflow": ScoringRule(Severity.HIGH, 10, 20, "Overflow horizontal em {viewport}: {url}"),
    "small_touch_target": ScoringRule(Severity.MEDIUM, 5, 15, "Touch target < 44px: {selector}"),
    "zoom_disabled": ScoringRule(Severity.HIGH, 10, 5, "Zoom bloqueado (user-scalable=no): {url}"),
    "image_no_dimensions": ScoringRule(Severity.MEDIUM, 5, 10, "Imagem sem width/height: {selector}"),
    "text_too_small": ScoringRule(Severity.MEDIUM, 5, 10, "Texto < 12px em {viewport}: {selector}"),
    "overlapping_elements": ScoringRule(Severity.HIGH, 10, 20, "Elementos sobrepostos em {viewport}: {url}"),
}

# ---------------------------------------------------------------------------
# Accessibility rules
# ---------------------------------------------------------------------------

ACCESSIBILITY_RULES = {
    "low_contrast": ScoringRule(Severity.HIGH, 10, 15, "Contraste baixo ({ratio}): {selector}"),
    "missing_alt": ScoringRule(Severity.HIGH, 10, 5, "Imagem sem alt: {selector}"),
    "missing_label": ScoringRule(Severity.HIGH, 10, 10, "Formulário sem label: {selector}"),
    "missing_lang": ScoringRule(Severity.MEDIUM, 5, 2, "Atributo lang ausente no <html>: {url}"),
    "missing_skip_nav": ScoringRule(Severity.MEDIUM, 5, 15, "Skip navigation ausente: {url}"),
    "focus_order_issue": ScoringRule(Severity.MEDIUM, 5, 20, "Ordem de foco inconsistente: {url}"),
    "missing_aria_label": ScoringRule(Severity.MEDIUM, 5, 10, "Elemento interativo sem aria-label: {selector}"),
    "missing_landmarks": ScoringRule(Severity.LOW, 2, 15, "ARIA landmarks ausentes: {url}"),
    "keyboard_trap": ScoringRule(Severity.CRITICAL, 25, 30, "Armadilha de teclado: {selector}"),
}

# ---------------------------------------------------------------------------
# Content rules
# ---------------------------------------------------------------------------

CONTENT_RULES = {
    "placeholder_text": ScoringRule(Severity.MEDIUM, 5, 5, "Texto placeholder: '{text}' em {url}"),
    "placeholder_image": ScoringRule(Severity.MEDIUM, 5, 10, "Imagem placeholder: {selector}"),
    "fake_contact": ScoringRule(Severity.LOW, 2, 5, "Contato falso detectado: {text}"),
    "outdated_copyright": ScoringRule(Severity.LOW, 2, 2, "Copyright desatualizado ({year}): {url}"),
    "broken_social_link": ScoringRule(Severity.LOW, 2, 5, "Link de rede social quebrado: {url}"),
    "hardcoded_secret": ScoringRule(Severity.CRITICAL, 25, 15, "Secret hardcoded no JS: {pattern}"),
}

# ---------------------------------------------------------------------------
# Click agent rules
# ---------------------------------------------------------------------------

CLICK_AGENT_RULES = {
    "fake_clickable": ScoringRule(Severity.MEDIUM, 5, 15, "Elemento fake clicável: {selector}"),
    "dead_end_page": ScoringRule(Severity.MEDIUM, 5, 20, "Página dead end (sem saída): {url}"),
    "orphan_page": ScoringRule(Severity.LOW, 2, 15, "Página órfã (sem entrada): {url}"),
    "interaction_error": ScoringRule(Severity.HIGH, 10, 30, "Erro ao interagir com: {selector}"),
    "disabled_no_feedback": ScoringRule(Severity.LOW, 2, 10, "Botão desabilitado sem feedback: {selector}"),
}

# ---------------------------------------------------------------------------
# Form rules
# ---------------------------------------------------------------------------

FORM_RULES = {
    "form_no_validation": ScoringRule(Severity.HIGH, 10, 30, "Formulário aceita envio vazio: {url}"),
    "form_accepts_invalid": ScoringRule(Severity.MEDIUM, 5, 20, "Formulário aceita dados inválidos: {field}"),
    "form_poor_error_msg": ScoringRule(Severity.LOW, 2, 15, "Mensagem de erro genérica: {url}"),
    "form_error_no_aria": ScoringRule(Severity.MEDIUM, 5, 10, "Erro de form sem aria-live: {url}"),
    "form_sql_injection": ScoringRule(Severity.CRITICAL, 25, 60, "Possível SQL injection: {url}"),
}
