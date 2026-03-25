"""Text analysis utilities — placeholder detection, fake contact info, and
hardcoded secret scanning."""

from __future__ import annotations

import re
from typing import Any

from serenity.constants import PLACEHOLDER_PATTERNS

# ---------------------------------------------------------------------------
# Fake-contact detection patterns
# ---------------------------------------------------------------------------

_FAKE_PHONE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\(0{2,3}\)\s*0{4,5}[\-\.]?0{4}", re.IGNORECASE),
    re.compile(r"555[\-\.]?\d{4}"),
    re.compile(r"\+?1[\-\.]?555[\-\.]?\d{3}[\-\.]?\d{4}"),
    re.compile(r"123[\-\.]?456[\-\.]?789\d?"),
    re.compile(r"99999[\-\.]?9999"),
]

_FAKE_EMAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"test@test\.\w+", re.IGNORECASE),
    re.compile(r"foo@bar\.\w+", re.IGNORECASE),
    re.compile(r"user@example\.\w+", re.IGNORECASE),
    re.compile(r"admin@example\.\w+", re.IGNORECASE),
    re.compile(r"xxx@\w+\.\w+", re.IGNORECASE),
    re.compile(r"john\.?doe@\w+\.\w+", re.IGNORECASE),
    re.compile(r"email@email\.\w+", re.IGNORECASE),
    re.compile(r"nome@email\.\w+", re.IGNORECASE),
]

_FAKE_ADDRESS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"123\s+main\s+st(?:reet)?", re.IGNORECASE),
    re.compile(r"rua\s+exemplo", re.IGNORECASE),
    re.compile(r"av(?:enida)?\.?\s+exemplo", re.IGNORECASE),
    re.compile(r"000[\-\.]?000", re.IGNORECASE),
    re.compile(r"12345[\-\.]?678", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Secret detection patterns
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "AWS Access Key",
        re.compile(r"""(?:['"])(AKIA[0-9A-Z]{16})(?:['"])"""),
    ),
    (
        "AWS Secret Key",
        re.compile(
            r"""(?:aws_secret_access_key|secret_key)\s*[:=]\s*['"]([A-Za-z0-9/+=]{40})['"]""",
            re.IGNORECASE,
        ),
    ),
    (
        "Generic API Key",
        re.compile(
            r"""(?:api[_\-]?key|apikey)\s*[:=]\s*['"]([A-Za-z0-9_\-]{20,})['"]""",
            re.IGNORECASE,
        ),
    ),
    (
        "Generic Secret/Token",
        re.compile(
            r"""(?:secret|token|password|passwd|pwd)\s*[:=]\s*['"]([^'"]{8,})['"]""",
            re.IGNORECASE,
        ),
    ),
    (
        "Google API Key",
        re.compile(r"""AIza[0-9A-Za-z\-_]{35}"""),
    ),
    (
        "GitHub Token",
        re.compile(r"""(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}"""),
    ),
    (
        "Slack Token",
        re.compile(r"""xox[bpors][\-][A-Za-z0-9\-]{10,}"""),
    ),
    (
        "Stripe Key",
        re.compile(r"""(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{20,}"""),
    ),
    (
        "JWT",
        re.compile(r"""eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"""),
    ),
    (
        "Private Key",
        re.compile(r"""-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"""),
    ),
    (
        "Bearer Token in Code",
        re.compile(
            r"""['"](Bearer\s+[A-Za-z0-9_\-\.]{20,})['"]""",
            re.IGNORECASE,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_placeholder_text(text: str) -> list[dict[str, Any]]:
    """Search *text* for placeholder patterns defined in
    :data:`serenity.constants.PLACEHOLDER_PATTERNS`.

    Args:
        text: The text (e.g. page body content) to scan.

    Returns:
        A list of dicts, each with keys *pattern* (the regex source),
        *match* (the matched string), and *position* (char offset).
    """
    findings: list[dict[str, Any]] = []
    for raw_pattern in PLACEHOLDER_PATTERNS:
        try:
            compiled = re.compile(raw_pattern, re.IGNORECASE)
        except re.error:
            continue

        for m in compiled.finditer(text):
            findings.append(
                {
                    "pattern": raw_pattern,
                    "match": m.group(0),
                    "position": m.start(),
                }
            )
    return findings


def detect_fake_contacts(text: str) -> list[dict[str, Any]]:
    """Detect fake or placeholder phone numbers, email addresses, and physical
    addresses in *text*.

    Args:
        text: The text to analyse.

    Returns:
        A list of dicts with keys *type* (``"phone"``, ``"email"``, or
        ``"address"``), *match*, and *position*.
    """
    findings: list[dict[str, Any]] = []

    for pattern in _FAKE_PHONE_PATTERNS:
        for m in pattern.finditer(text):
            findings.append(
                {"type": "phone", "match": m.group(0), "position": m.start()}
            )

    for pattern in _FAKE_EMAIL_PATTERNS:
        for m in pattern.finditer(text):
            findings.append(
                {"type": "email", "match": m.group(0), "position": m.start()}
            )

    for pattern in _FAKE_ADDRESS_PATTERNS:
        for m in pattern.finditer(text):
            findings.append(
                {"type": "address", "match": m.group(0), "position": m.start()}
            )

    return findings


def detect_hardcoded_secrets(js_content: str) -> list[dict[str, Any]]:
    """Scan JavaScript source code for hardcoded API keys, tokens, and
    passwords.

    Args:
        js_content: JavaScript source text.

    Returns:
        A list of dicts with keys *type* (description of the secret kind),
        *match* (the matched value — first 12 chars + ``"..."`` for safety),
        and *position* (char offset).
    """
    findings: list[dict[str, Any]] = []

    for secret_type, pattern in _SECRET_PATTERNS:
        for m in pattern.finditer(js_content):
            raw = m.group(0)
            # Expose only a safe prefix to avoid leaking full credentials in
            # reports.
            redacted = raw[:12] + "..." if len(raw) > 15 else raw
            findings.append(
                {
                    "type": secret_type,
                    "match": redacted,
                    "position": m.start(),
                }
            )

    return findings
