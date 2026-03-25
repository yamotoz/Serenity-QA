"""Serenity QA constants — scoring weights, thresholds, severity levels."""

from enum import Enum

# ---------------------------------------------------------------------------
# Severity levels
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# Severity deduction rules
# ---------------------------------------------------------------------------

SEVERITY_DEDUCTIONS: dict[Severity, float] = {
    Severity.CRITICAL: 25.0,
    Severity.HIGH: 10.0,
    Severity.MEDIUM: 5.0,
    Severity.LOW: 2.0,
}

SEVERITY_CAPS: dict[Severity, float] = {
    Severity.CRITICAL: 50.0,
    Severity.HIGH: 40.0,
    Severity.MEDIUM: 30.0,
    Severity.LOW: 20.0,
}

# ---------------------------------------------------------------------------
# Domain weights for overall score
# ---------------------------------------------------------------------------

DOMAIN_WEIGHTS: dict[str, float] = {
    "performance": 0.25,
    "seo": 0.20,
    "functionality": 0.10,
    "click_agent": 0.10,
    "responsiveness": 0.15,
    "accessibility": 0.10,
    "infrastructure": 0.10,
}

# Content findings count under SEO, Forms findings count under functionality.
DOMAIN_ALIASES: dict[str, str] = {
    "content": "seo",
    "forms": "functionality",
}

# ---------------------------------------------------------------------------
# Score verdicts
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    FAILED = "REPROVADO"
    APPROVED = "APROVADO"
    EXCELLENT = "EXCELENTE"


VERDICT_THRESHOLDS: dict[Verdict, tuple[float, float]] = {
    Verdict.EXCELLENT: (91.0, 100.0),
    Verdict.APPROVED: (70.0, 90.99),
    Verdict.FAILED: (0.0, 69.99),
}

# ---------------------------------------------------------------------------
# Viewports for responsiveness testing
# ---------------------------------------------------------------------------

VIEWPORTS: dict[str, dict[str, int]] = {
    "mobile": {"width": 375, "height": 812},
    "tablet": {"width": 768, "height": 1024},
    "desktop": {"width": 1280, "height": 900},
}

# ---------------------------------------------------------------------------
# Default limits
# ---------------------------------------------------------------------------

MAX_PAGES_DEFAULT = 100
MAX_FORM_FIELDS = 50
MAX_INTERACTIVE_ELEMENTS = 200
PAGE_TIMEOUT_MS = 30_000
ELEMENT_TIMEOUT_MS = 5_000
HTTP_TIMEOUT_S = 15
GEMINI_TIMEOUT_S = 60
SCAN_TIMEOUT_S = 3600

# ---------------------------------------------------------------------------
# Security headers to check
# ---------------------------------------------------------------------------

SECURITY_HEADERS = [
    "content-security-policy",
    "strict-transport-security",
    "x-frame-options",
    "x-content-type-options",
    "permissions-policy",
    "referrer-policy",
]

# ---------------------------------------------------------------------------
# Sensitive paths to probe
# ---------------------------------------------------------------------------

SENSITIVE_PATHS = [
    "/.env",
    "/.git/config",
    "/admin",
    "/api",
    "/wp-admin",
    "/wp-login.php",
    "/.DS_Store",
    "/server-status",
    "/phpinfo.php",
    "/debug",
    "/.well-known/security.txt",
]

# ---------------------------------------------------------------------------
# Placeholder patterns for content analysis
# ---------------------------------------------------------------------------

# NOTE: Patterns must not match legitimate Portuguese words.
# "todo" = "every/all" in PT-BR, "teste" = "test/trial" in PT-BR.

# Case-INSENSITIVE patterns (safe in any language)
PLACEHOLDER_PATTERNS = [
    r"lorem\s+ipsum",
    r"\bplaceholder\b",
    r"\bsample\s+text\b",
    r"99999[\-\.]?9999",
    r"xxx@",
    r"foo@bar",
    r"test@test",
    r"john\.?doe",
]

# Case-SENSITIVE patterns (must be searched WITHOUT re.IGNORECASE)
# These use uppercase intentionally to avoid matching Portuguese words
PLACEHOLDER_PATTERNS_CASE_SENSITIVE = [
    r"\bTODO\s*[:\-!]",        # "TODO:", "TODO!", "TODO -" (not "todo" in PT-BR)
    r"<!--\s*TODO\b",          # HTML comment TODO
    r"//\s*TODO\b",            # JS comment TODO
    r"\bFIXME\b",
    r"\bHACK\b",
]

# Patterns that are ONLY flagged when page lang is NOT pt/es
# (these words are natural in Portuguese/Spanish)
PLACEHOLDER_PATTERNS_NON_LATIN = [
    r"\bteste?\b",
    r"\bexemplo\b",
    r"\bem\s+breve\b",
    r"\bsample\b",
]

# ---------------------------------------------------------------------------
# Dashboard WebSocket
# ---------------------------------------------------------------------------

DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8765
