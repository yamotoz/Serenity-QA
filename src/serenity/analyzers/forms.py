"""Domain 5 — Form Testing analyzer.

Finds every ``<form>`` on a page and exercises it with empty submissions,
invalid data, overly long input, and common injection payloads.  Validates
that client-side validation fires correctly and that error messages are
accessible and descriptive.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from serenity.analyzers.base import BaseAnalyzer
from serenity.constants import MAX_FORM_FIELDS, Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import ElementHandle, Page

    from serenity.core.state import ScanContext

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MAX_FORMS_PER_PAGE = 10

INVALID_DATA_MAP: dict[str, str] = {
    "email": "notanemail",
    "tel": "abc",
    "number": "not-a-number",
    "url": "not-a-url",
    "date": "99-99-9999",
}

LONG_STRING = "A" * 10_000

INJECTION_PAYLOADS: list[str] = [
    "' OR '1'='1",
    "<script>alert(1)</script>",
]

# ── Helpers ──────────────────────────────────────────────────────────────────


async def _form_selector(form: ElementHandle, index: int) -> str:
    """Generate a CSS selector for a form element."""
    try:
        sel: str = await form.evaluate(
            """(el) => {
                if (el.id) return 'form#' + CSS.escape(el.id);
                if (el.name) return 'form[name="' + el.name + '"]';
                if (el.action) return 'form[action="' + el.action + '"]';
                return null;
            }"""
        )
        if sel:
            return sel
    except Exception:
        pass
    return f"form:nth-of-type({index + 1})"


async def _get_input_type(el: ElementHandle) -> str:
    """Return the type attribute (lowered) of an input element."""
    try:
        val = await el.get_attribute("type")
        return (val or "text").lower()
    except Exception:
        return "text"


async def _get_tag_name(el: ElementHandle) -> str:
    try:
        return (await el.evaluate("el => el.tagName.toLowerCase()")) or ""
    except Exception:
        return ""


async def _clear_field(field: ElementHandle) -> None:
    """Best-effort clearing of a single form field."""
    try:
        tag = await _get_tag_name(field)
        if tag == "select":
            # Reset to first option
            await field.evaluate("el => { if (el.options.length) el.selectedIndex = 0; }")
        elif tag in ("input", "textarea"):
            input_type = await _get_input_type(field)
            if input_type in ("checkbox", "radio"):
                await field.evaluate("el => { el.checked = false; }")
            else:
                await field.evaluate("el => { el.value = ''; }")
    except Exception:
        pass


async def _fill_field(field: ElementHandle, value: str) -> None:
    """Best-effort filling of a single form field."""
    try:
        tag = await _get_tag_name(field)
        if tag == "select":
            return  # Skip selects for text-fill tests
        input_type = await _get_input_type(field)
        if input_type in ("checkbox", "radio", "submit", "button", "hidden", "file", "image", "reset"):
            return
        # Use evaluate for speed over type() on long strings
        await field.evaluate("(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); }", value)
    except Exception:
        pass


async def _count_invalid_fields(form: ElementHandle) -> int:
    """Count fields currently matching the :invalid pseudo-class."""
    try:
        return await form.evaluate(
            "el => el.querySelectorAll(':invalid').length"
        )
    except Exception:
        return 0


async def _check_validation_message(form: ElementHandle) -> str | None:
    """Return the first non-empty validationMessage inside the form."""
    try:
        msg: str = await form.evaluate(
            """el => {
                for (const f of el.elements) {
                    if (f.validationMessage) return f.validationMessage;
                }
                return '';
            }"""
        )
        return msg if msg else None
    except Exception:
        return None


async def _detect_error_elements(form: ElementHandle, page: Page) -> list[dict[str, Any]]:
    """Find visible error message elements near the form."""
    try:
        errors: list[dict[str, Any]] = await form.evaluate(
            """el => {
                const selectors = [
                    '.error', '.field-error', '.form-error', '.invalid-feedback',
                    '.help-block.error', '[role="alert"]', '.validation-message',
                    '.error-message', '.err-msg', '.form-text.text-danger',
                ];
                const found = [];
                for (const sel of selectors) {
                    const matches = el.querySelectorAll(sel);
                    for (const m of matches) {
                        const style = getComputedStyle(m);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            found.push({
                                text: (m.textContent || '').trim().slice(0, 200),
                                hasAriaLive: m.hasAttribute('aria-live')
                                    || !!m.closest('[aria-live]'),
                                selector: m.tagName.toLowerCase()
                                    + (m.className ? '.' + m.className.split(' ')[0] : ''),
                            });
                        }
                    }
                }
                return found;
            }"""
        )
        return errors
    except Exception:
        return []


async def _has_client_side_validation(form: ElementHandle) -> dict[str, Any]:
    """Check whether the form uses HTML5 validation attributes."""
    try:
        info: dict[str, Any] = await form.evaluate(
            """el => {
                let required = 0, pattern = 0, typed = 0, novalidate = el.hasAttribute('novalidate');
                for (const f of el.elements) {
                    if (f.hasAttribute('required')) required++;
                    if (f.hasAttribute('pattern')) pattern++;
                    const t = (f.getAttribute('type') || '').toLowerCase();
                    if (['email', 'url', 'tel', 'number', 'date'].includes(t)) typed++;
                }
                return {required, pattern, typed, novalidate};
            }"""
        )
        return info
    except Exception:
        return {"required": 0, "pattern": 0, "typed": 0, "novalidate": False}


async def _try_submit(form: ElementHandle, page: Page) -> dict[str, Any]:
    """Attempt to submit the form and return what happened.

    Returns a dict with:
        submitted (bool): whether navigation/request fired
        validation_fired (bool): whether :invalid fields appeared
        validation_message (str | None): first HTML5 validation message
        url_before / url_after
    """
    url_before = page.url
    invalid_before = await _count_invalid_fields(form)

    # Try clicking a submit button first; fall back to form.submit()
    try:
        submit_btn = await form.query_selector(
            "button[type='submit'], input[type='submit'], button:not([type])"
        )
        if submit_btn:
            await submit_btn.click(timeout=5000)
        else:
            await form.evaluate("el => el.requestSubmit ? el.requestSubmit() : el.submit()")
    except Exception:
        # Fallback: JS submit
        try:
            await form.evaluate("el => el.submit()")
        except Exception:
            pass

    await asyncio.sleep(0.8)

    url_after = page.url
    invalid_after = await _count_invalid_fields(form)
    validation_msg = await _check_validation_message(form)

    submitted = url_after != url_before
    validation_fired = invalid_after > invalid_before or validation_msg is not None

    # Go back if page navigated
    if submitted:
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=10000)
            await asyncio.sleep(0.3)
        except Exception:
            pass

    return {
        "submitted": submitted,
        "validation_fired": validation_fired,
        "validation_message": validation_msg,
        "url_before": url_before,
        "url_after": url_after,
        "invalid_count": invalid_after,
    }


# ── Analyzer ─────────────────────────────────────────────────────────────────


class FormAnalyzer(BaseAnalyzer):
    """Exercise every form with boundary and malicious inputs."""

    domain: str = "forms"
    weight: float = 0.10

    async def analyze_page(
        self, ctx: ScanContext, url: str, page: Page
    ) -> list[Finding]:
        findings: list[Finding] = []

        try:
            forms = await page.query_selector_all("form")
        except Exception:
            logger.warning("forms: failed to query forms on %s", url)
            return findings

        if not forms:
            return findings

        forms = forms[:MAX_FORMS_PER_PAGE]
        logger.info("forms: %s — testing %d form(s)", url, len(forms))

        for idx, form in enumerate(forms):
            form_sel = await _form_selector(form, idx)
            form_findings = await self._test_single_form(ctx, page, form, form_sel, url)
            findings.extend(form_findings)

        return findings

    # ------------------------------------------------------------------ #
    # Form test orchestrator                                               #
    # ------------------------------------------------------------------ #

    async def _test_single_form(
        self,
        ctx: ScanContext,
        page: Page,
        form: ElementHandle,
        form_sel: str,
        url: str,
    ) -> list[Finding]:
        findings: list[Finding] = []

        # Gather fields
        try:
            fields = await form.query_selector_all(
                "input, textarea, select"
            )
        except Exception:
            return findings

        fields = fields[:MAX_FORM_FIELDS]
        if not fields:
            return findings

        # Check validation attributes upfront
        validation_info = await _has_client_side_validation(form)

        # ── Test 1: Empty submission ──────────────────────────────────
        empty_findings = await self._test_empty_submission(
            page, form, form_sel, fields, url, validation_info
        )
        findings.extend(empty_findings)

        # Re-query form in case page changed (best effort)
        form = await self._re_query_form(page, form_sel, form)

        # ── Test 2: Invalid data ──────────────────────────────────────
        invalid_findings = await self._test_invalid_data(
            page, form, form_sel, fields, url
        )
        findings.extend(invalid_findings)

        form = await self._re_query_form(page, form_sel, form)

        # ── Test 3: Long data ─────────────────────────────────────────
        long_findings = await self._test_long_data(
            page, form, form_sel, fields, url
        )
        findings.extend(long_findings)

        form = await self._re_query_form(page, form_sel, form)

        # ── Test 4: Injection payloads ────────────────────────────────
        injection_findings = await self._test_injection(
            page, form, form_sel, fields, url
        )
        findings.extend(injection_findings)

        form = await self._re_query_form(page, form_sel, form)

        # ── Test 5: Error message quality ─────────────────────────────
        error_findings = await self._check_error_message_quality(
            page, form, form_sel, url, validation_info
        )
        findings.extend(error_findings)

        return findings

    # ------------------------------------------------------------------ #
    # Individual tests                                                     #
    # ------------------------------------------------------------------ #

    async def _test_empty_submission(
        self,
        page: Page,
        form: ElementHandle,
        form_sel: str,
        fields: list[ElementHandle],
        url: str,
        validation_info: dict[str, Any],
    ) -> list[Finding]:
        """Clear all fields and submit — validation should fire."""
        findings: list[Finding] = []
        try:
            for f in fields:
                await _clear_field(f)

            result = await _try_submit(form, page)

            if result["submitted"] and not result["validation_fired"]:
                # Form submitted with empty fields and no validation
                if validation_info.get("required", 0) == 0:
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.HIGH,
                            title="Form accepts empty submission without validation",
                            description=(
                                f"The form '{form_sel}' was submitted with all fields "
                                "empty and no client-side validation triggered. Required "
                                "fields should use the 'required' attribute."
                            ),
                            url=url,
                            element_selector=form_sel,
                            fix_snippet='<input type="text" name="name" required>',
                            estimated_fix_minutes=10,
                            metadata={
                                "issue_type": "empty_submission_accepted",
                                "validation_info": validation_info,
                            },
                        )
                    )
        except Exception:
            logger.debug("forms: empty submission test failed for %s", form_sel, exc_info=True)

        return findings

    async def _test_invalid_data(
        self,
        page: Page,
        form: ElementHandle,
        form_sel: str,
        fields: list[ElementHandle],
        url: str,
    ) -> list[Finding]:
        """Fill type-specific fields with intentionally invalid data."""
        findings: list[Finding] = []
        try:
            # Re-query fields from the (possibly refreshed) form
            fields = await form.query_selector_all("input, textarea, select")
            fields = fields[:MAX_FORM_FIELDS]

            filled_invalid = False
            for f in fields:
                tag = await _get_tag_name(f)
                if tag != "input":
                    continue
                input_type = await _get_input_type(f)
                if input_type in INVALID_DATA_MAP:
                    await _fill_field(f, INVALID_DATA_MAP[input_type])
                    filled_invalid = True

            if not filled_invalid:
                return findings

            result = await _try_submit(form, page)

            if result["submitted"] and not result["validation_fired"]:
                # Check if the form already uses HTML5 typed inputs (type="email",
                # type="tel", etc.).  Native browser validation fires a tooltip on
                # invalid input but does NOT create :invalid pseudo-class until after
                # interaction.  If the form has proper input types AND the submit did
                # NOT navigate (url_before == url_after), native validation likely
                # blocked the submit silently via the constraint validation API.
                has_typed_inputs = await form.evaluate("""el => {
                    const types = ['email', 'tel', 'url', 'number', 'date'];
                    return Array.from(el.querySelectorAll('input')).some(
                        i => types.includes(i.type)
                    );
                }""")

                # If form has typed inputs and didn't actually navigate, native
                # validation likely worked — the browser showed a tooltip that
                # Playwright can't detect as a DOM change.
                if has_typed_inputs and not result["submitted"]:
                    return findings

                # For login/auth forms (email + password), server-side validation
                # is the security best practice — don't flag as missing validation.
                is_auth_form = await form.evaluate("""el => {
                    const inputs = Array.from(el.querySelectorAll('input'));
                    const hasEmail = inputs.some(i => i.type === 'email' || i.name === 'email');
                    const hasPassword = inputs.some(i => i.type === 'password');
                    return hasEmail && hasPassword;
                }""")
                if is_auth_form:
                    return findings

                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.HIGH,
                        title="Form accepts invalid data without validation",
                        description=(
                            f"The form '{form_sel}' was submitted with intentionally "
                            "invalid data (e.g. 'notanemail' in an email field) and no "
                            "validation error appeared. Use HTML5 input types and the "
                            "'pattern' attribute for client-side validation."
                        ),
                        url=url,
                        element_selector=form_sel,
                        fix_snippet='<input type="email" name="email" required>',
                        estimated_fix_minutes=10,
                        metadata={"issue_type": "invalid_data_accepted"},
                    )
                )
        except Exception:
            logger.debug("forms: invalid-data test failed for %s", form_sel, exc_info=True)

        return findings

    async def _test_long_data(
        self,
        page: Page,
        form: ElementHandle,
        form_sel: str,
        fields: list[ElementHandle],
        url: str,
    ) -> list[Finding]:
        """Fill text inputs with 10 000 characters."""
        findings: list[Finding] = []
        console_errors: list[str] = []

        def _on_error(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        page.on("console", _on_error)

        try:
            fields = await form.query_selector_all("input, textarea, select")
            fields = fields[:MAX_FORM_FIELDS]

            for f in fields:
                tag = await _get_tag_name(f)
                input_type = await _get_input_type(f)
                if tag in ("input", "textarea") and input_type not in (
                    "checkbox", "radio", "submit", "button", "hidden", "file", "image", "reset",
                ):
                    await _fill_field(f, LONG_STRING)

            result = await _try_submit(form, page)

            if console_errors:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title="Form produces console errors with long input",
                        description=(
                            f"The form '{form_sel}' generated JavaScript console errors "
                            f"when submitted with 10,000-character input: "
                            f"{console_errors[0][:200]}"
                        ),
                        url=url,
                        element_selector=form_sel,
                        fix_snippet='<input type="text" name="field" maxlength="500">',
                        estimated_fix_minutes=10,
                        metadata={
                            "issue_type": "long_data_error",
                            "console_errors": console_errors[:5],
                        },
                    )
                )
        except Exception:
            logger.debug("forms: long-data test failed for %s", form_sel, exc_info=True)
        finally:
            page.remove_listener("console", _on_error)

        return findings

    async def _test_injection(
        self,
        page: Page,
        form: ElementHandle,
        form_sel: str,
        fields: list[ElementHandle],
        url: str,
    ) -> list[Finding]:
        """Fill fields with SQL injection and XSS payloads."""
        findings: list[Finding] = []

        for payload in INJECTION_PAYLOADS:
            try:
                fields = await form.query_selector_all("input, textarea, select")
                fields = fields[:MAX_FORM_FIELDS]

                for f in fields:
                    tag = await _get_tag_name(f)
                    input_type = await _get_input_type(f)
                    if tag in ("input", "textarea") and input_type not in (
                        "checkbox", "radio", "submit", "button", "hidden", "file", "image", "reset",
                    ):
                        await _fill_field(f, payload)

                result = await _try_submit(form, page)

                # After submission, check if the payload appears unescaped in the page
                if result["submitted"]:
                    try:
                        body_html = await page.evaluate("() => document.body.innerHTML")
                        if payload in body_html and "<script>" in payload:
                            findings.append(
                                Finding(
                                    domain=self.domain,
                                    severity=Severity.CRITICAL,
                                    title="Possible XSS vulnerability — script tag reflected",
                                    description=(
                                        f"The form '{form_sel}' reflected a <script> "
                                        "payload in the page HTML without escaping. This "
                                        "is a potential Cross-Site Scripting vulnerability."
                                    ),
                                    url=url,
                                    element_selector=form_sel,
                                    fix_snippet=(
                                        "# Server-side: always escape user input\n"
                                        "from markupsafe import escape\n"
                                        "safe_value = escape(user_input)"
                                    ),
                                    estimated_fix_minutes=30,
                                    metadata={
                                        "issue_type": "xss_reflected",
                                        "payload": payload,
                                    },
                                )
                            )
                    except Exception:
                        pass

                    # Navigate back after successful submission
                    try:
                        await page.go_back(wait_until="domcontentloaded", timeout=10000)
                        await asyncio.sleep(0.3)
                    except Exception:
                        try:
                            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        except Exception:
                            pass

                    # Re-query the form after navigation
                    form = await self._re_query_form(page, form_sel, form)

            except Exception:
                logger.debug(
                    "forms: injection test failed for %s with payload %s",
                    form_sel, payload[:30], exc_info=True,
                )

        return findings

    async def _check_error_message_quality(
        self,
        page: Page,
        form: ElementHandle,
        form_sel: str,
        url: str,
        validation_info: dict[str, Any],
    ) -> list[Finding]:
        """Trigger validation and inspect error message quality."""
        findings: list[Finding] = []

        try:
            # Clear fields and submit to trigger validation errors
            fields = await form.query_selector_all("input, textarea, select")
            fields = fields[:MAX_FORM_FIELDS]
            for f in fields:
                await _clear_field(f)

            await _try_submit(form, page)

            # Detect error elements
            errors = await _detect_error_elements(form, page)

            if validation_info.get("required", 0) > 0 and not errors:
                findings.append(
                    Finding(
                        domain=self.domain,
                        severity=Severity.MEDIUM,
                        title="Form validation errors are not visible to users",
                        description=(
                            f"The form '{form_sel}' has required fields but no visible "
                            "error messages appear after submitting empty data. Provide "
                            "clear, inline error messages near each invalid field."
                        ),
                        url=url,
                        element_selector=form_sel,
                        fix_snippet=(
                            '<span class="error" role="alert" aria-live="polite">\n'
                            "  This field is required.\n"
                            "</span>"
                        ),
                        estimated_fix_minutes=15,
                        metadata={"issue_type": "no_visible_errors"},
                    )
                )

            # Check aria-live on error messages
            for err in errors:
                if not err.get("hasAriaLive"):
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.MEDIUM,
                            title="Form error message lacks aria-live for screen readers",
                            description=(
                                f"An error message in '{form_sel}' "
                                f"('{err.get('text', '')[:80]}') is not wrapped in an "
                                "aria-live region. Screen reader users will not be "
                                "notified of the error."
                            ),
                            url=url,
                            element_selector=form_sel,
                            fix_snippet=(
                                '<div role="alert" aria-live="assertive">\n'
                                "  Error message here\n"
                                "</div>"
                            ),
                            estimated_fix_minutes=5,
                            metadata={
                                "issue_type": "missing_aria_live",
                                "error_text": err.get("text", ""),
                            },
                        )
                    )
                    break  # One finding per form is enough

            # ── Test that errors disappear when corrected ─────────────
            if errors and fields:
                # Fill required fields with valid data
                for f in fields:
                    tag = await _get_tag_name(f)
                    input_type = await _get_input_type(f)
                    if tag in ("input", "textarea") and input_type not in (
                        "checkbox", "radio", "submit", "button", "hidden", "file", "image", "reset",
                    ):
                        valid_value = "test@example.com" if input_type == "email" else "Valid input"
                        await _fill_field(f, valid_value)
                    elif input_type in ("checkbox", "radio"):
                        try:
                            await f.evaluate("el => { el.checked = true; }")
                        except Exception:
                            pass

                await asyncio.sleep(0.5)
                errors_after_correction = await _detect_error_elements(form, page)

                if len(errors_after_correction) >= len(errors):
                    findings.append(
                        Finding(
                            domain=self.domain,
                            severity=Severity.LOW,
                            title="Form errors do not clear when fields are corrected",
                            description=(
                                f"The form '{form_sel}' still shows {len(errors_after_correction)} "
                                "error message(s) after fields have been filled with valid data. "
                                "Error messages should disappear once the user corrects the input."
                            ),
                            url=url,
                            element_selector=form_sel,
                            estimated_fix_minutes=15,
                            metadata={"issue_type": "errors_persist_after_correction"},
                        )
                    )

        except Exception:
            logger.debug(
                "forms: error-message quality check failed for %s", form_sel,
                exc_info=True,
            )

        return findings

    # ------------------------------------------------------------------ #
    # Utilities                                                            #
    # ------------------------------------------------------------------ #

    async def _re_query_form(
        self, page: Page, form_sel: str, fallback: ElementHandle
    ) -> ElementHandle:
        """Re-query the form after possible navigation; return fallback if it fails."""
        try:
            el = await page.query_selector(form_sel)
            if el:
                return el
        except Exception:
            pass
        return fallback
