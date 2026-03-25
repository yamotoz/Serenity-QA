"""Deep network analysis — intercept all traffic, map APIs, detect leaks."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from serenity.constants import Severity
from serenity.scoring.finding import Finding

if TYPE_CHECKING:
    from playwright.async_api import Page, Request, Response

    from serenity.core.state import ScanContext

logger = logging.getLogger("serenity.advanced.network_analysis")

_MAX_SAMPLE_PAGES = 3

# Patterns that suggest sensitive data in query strings or URLs.
_SENSITIVE_PATTERNS = [
    (r"(?:api[_-]?key|apikey)\s*=\s*\S+", "API key"),
    (r"(?:token|access_token|auth_token|bearer)\s*=\s*\S+", "auth token"),
    (r"(?:password|passwd|pwd)\s*=\s*\S+", "password"),
    (r"(?:secret|client_secret)\s*=\s*\S+", "secret"),
    (r"(?:session|sessionid|sid)\s*=\s*[a-f0-9]{16,}", "session ID"),
    (r"(?:private[_-]?key)\s*=\s*\S+", "private key"),
    (r"(?:credit.?card|ccnum)\s*=\s*\d+", "credit card"),
    (r"(?:ssn|social.?security)\s*=\s*\d+", "SSN"),
]

# Stack trace indicators in response bodies.
_STACK_TRACE_PATTERNS = [
    r"Traceback \(most recent call last\)",
    r"at\s+\S+\.java:\d+",
    r"at\s+\S+\s+\(\S+\.js:\d+:\d+\)",
    r"File\s+\"[^\"]+\",\s+line\s+\d+",
    r"Exception in thread",
    r"System\.NullReferenceException",
    r"Fatal error:.*in\s+/\S+\.php\s+on\s+line\s+\d+",
    r"SQLSTATE\[",
    r"pg_query\(\):",
    r"mysql_fetch",
    r"ORA-\d{5}",
]


class NetworkAnalyzer:
    """Intercept and analyze all network traffic during navigation."""

    async def run(self, ctx: ScanContext) -> list[Finding]:
        logger.info("Starting network analysis")
        findings: list[Finding] = []

        urls = _sample_urls(ctx)
        if not urls:
            logger.info("No URLs available for network analysis")
            return findings

        page = await ctx.page_pool.acquire()
        try:
            endpoint_map, traffic_log = await self._capture_traffic(page, urls)
            findings.extend(self._detect_sensitive_query_params(traffic_log))
            findings.extend(self._detect_stack_traces(traffic_log))
            findings.extend(self._detect_schema_inconsistencies(traffic_log))
            api_type = self._detect_api_type(traffic_log)

            # Build structured endpoint data for state.
            structured_endpoints = self._build_endpoint_list(endpoint_map, api_type)
            ctx.state.api_endpoints.extend(structured_endpoints)

            if api_type:
                logger.info("Detected API type: %s", api_type)

        except Exception:
            logger.exception("Network analysis failed")
        finally:
            await ctx.page_pool.release(page)

        logger.info("Network analysis complete: %d findings", len(findings))
        return findings

    # ------------------------------------------------------------------
    # Traffic Capture
    # ------------------------------------------------------------------

    async def _capture_traffic(
        self, page: Page, urls: list[str]
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """Navigate through pages and capture all network traffic."""
        endpoint_map: dict[str, dict[str, Any]] = {}
        traffic_log: list[dict[str, Any]] = []

        async def on_response(response: Response) -> None:
            try:
                request = response.request
                parsed = urlparse(request.url)
                # Normalize URL by removing query string for pattern grouping.
                base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                # Replace numeric path segments with {id} for pattern matching.
                pattern = re.sub(r"/\d+(?=/|$)", "/{id}", base_url)

                entry: dict[str, Any] = {
                    "method": request.method,
                    "url": request.url,
                    "pattern": pattern,
                    "status": response.status,
                    "content_type": response.headers.get("content-type", ""),
                    "query_string": parsed.query,
                    "resource_type": request.resource_type,
                    "body": None,
                    "response_body": None,
                }

                # Capture response body for API calls.
                if request.resource_type in ("fetch", "xhr") and response.status < 500:
                    try:
                        body = await response.text()
                        entry["response_body"] = body[:5000]  # Cap at 5KB.
                    except Exception:
                        pass

                # Capture error response bodies (4xx, 5xx).
                if response.status >= 400:
                    try:
                        body = await response.text()
                        entry["response_body"] = body[:5000]
                    except Exception:
                        pass

                traffic_log.append(entry)

                # Build endpoint map.
                if pattern not in endpoint_map:
                    endpoint_map[pattern] = {
                        "methods": set(),
                        "status_codes": set(),
                        "content_types": set(),
                        "hit_count": 0,
                        "sample_url": request.url,
                    }
                ep = endpoint_map[pattern]
                ep["methods"].add(request.method)
                ep["status_codes"].add(response.status)
                ep["content_types"].add(
                    response.headers.get("content-type", "unknown").split(";")[0].strip()
                )
                ep["hit_count"] += 1

            except Exception:
                pass  # Never crash the listener.

        page.on("response", on_response)

        try:
            for url in urls:
                try:
                    await page.goto(url, wait_until="networkidle", timeout=20_000)
                    await asyncio.sleep(1.0)
                except Exception:
                    logger.debug("Navigation failed for %s during network capture", url)
                    continue
        finally:
            page.remove_listener("response", on_response)

        return endpoint_map, traffic_log

    # ------------------------------------------------------------------
    # Detection: Sensitive Data in Query Strings
    # ------------------------------------------------------------------

    def _detect_sensitive_query_params(
        self, traffic_log: list[dict[str, Any]]
    ) -> list[Finding]:
        findings: list[Finding] = []
        seen_patterns: set[str] = set()

        for entry in traffic_log:
            query = entry.get("query_string", "")
            url = entry.get("url", "")
            if not query:
                continue

            for regex, label in _SENSITIVE_PATTERNS:
                if re.search(regex, query, re.IGNORECASE):
                    dedup_key = f"{label}:{entry.get('pattern', '')}"
                    if dedup_key in seen_patterns:
                        continue
                    seen_patterns.add(dedup_key)

                    # Redact the actual value for the finding.
                    redacted_url = _redact_url(url)

                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.CRITICAL,
                            title=f"Sensitive data in URL query string: {label}",
                            description=(
                                f"A {label} was detected in the query string of a "
                                f"network request: {redacted_url}. Query parameters "
                                "are logged in server access logs, browser history, "
                                "proxy logs, and can leak via the Referer header. "
                                "Sensitive data must be sent in request headers or body."
                            ),
                            url=redacted_url,
                            metadata={
                                "type": "sensitive_query_param",
                                "data_type": label,
                                "method": entry.get("method"),
                                "endpoint_pattern": entry.get("pattern"),
                            },
                            fix_snippet=(
                                "// Move sensitive data from query string to headers:\n"
                                "fetch('/api/resource', {\n"
                                "  headers: { 'Authorization': `Bearer ${token}` }\n"
                                "});"
                            ),
                        )
                    )

        return findings

    # ------------------------------------------------------------------
    # Detection: Stack Traces in Responses
    # ------------------------------------------------------------------

    def _detect_stack_traces(
        self, traffic_log: list[dict[str, Any]]
    ) -> list[Finding]:
        findings: list[Finding] = []
        seen_urls: set[str] = set()

        for entry in traffic_log:
            body = entry.get("response_body")
            if not body or entry.get("status", 200) < 400:
                continue

            url = entry.get("url", "")
            pattern = entry.get("pattern", "")
            if pattern in seen_urls:
                continue

            for regex in _STACK_TRACE_PATTERNS:
                match = re.search(regex, body)
                if match:
                    seen_urls.add(pattern)
                    snippet = body[max(0, match.start() - 50): match.end() + 200]
                    snippet = snippet[:300]

                    findings.append(
                        Finding(
                            domain="advanced",
                            severity=Severity.HIGH,
                            title="Server stack trace exposed in error response",
                            description=(
                                f"An error response from {pattern} (status "
                                f"{entry.get('status')}) contains a server-side stack "
                                f"trace: '...{snippet}...'. Stack traces reveal "
                                "internal paths, library versions, and code structure "
                                "that aid attackers."
                            ),
                            url=url,
                            metadata={
                                "type": "stack_trace_leak",
                                "status": entry.get("status"),
                                "pattern_match": match.group(0)[:100],
                            },
                            fix_snippet=(
                                "// In production, return generic error messages:\n"
                                "app.use((err, req, res, next) => {\n"
                                "  console.error(err);\n"
                                "  res.status(500).json({ error: 'Internal server error' });\n"
                                "});"
                            ),
                        )
                    )
                    break

        return findings

    # ------------------------------------------------------------------
    # Detection: Schema Inconsistencies
    # ------------------------------------------------------------------

    def _detect_schema_inconsistencies(
        self, traffic_log: list[dict[str, Any]]
    ) -> list[Finding]:
        """Detect API endpoints returning different JSON shapes for same pattern."""
        findings: list[Finding] = []
        pattern_schemas: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for entry in traffic_log:
            body = entry.get("response_body")
            ct = entry.get("content_type", "")
            if not body or "json" not in ct:
                continue

            try:
                parsed = json.loads(body)
                schema_sig = _json_shape(parsed)
                pattern_schemas[entry["pattern"]].append(
                    {
                        "schema": schema_sig,
                        "status": entry.get("status"),
                        "url": entry.get("url"),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue

        for pattern, entries in pattern_schemas.items():
            schemas = [e["schema"] for e in entries]
            unique_schemas = set(schemas)
            # Only flag if same endpoint returns structurally different responses
            # (excluding error responses vs success responses).
            success_entries = [e for e in entries if 200 <= (e.get("status") or 200) < 400]
            success_schemas = set(e["schema"] for e in success_entries)

            if len(success_schemas) > 1 and len(success_entries) >= 2:
                findings.append(
                    Finding(
                        domain="advanced",
                        severity=Severity.MEDIUM,
                        title="Inconsistent API response schema",
                        description=(
                            f"The API endpoint pattern '{pattern}' returned "
                            f"{len(success_schemas)} different response shapes across "
                            f"{len(success_entries)} successful requests. Inconsistent "
                            "schemas make the API fragile for consumers and may "
                            "indicate a bug or missing response normalization."
                        ),
                        url=entries[0].get("url"),
                        metadata={
                            "type": "schema_inconsistency",
                            "pattern": pattern,
                            "schemas": list(success_schemas),
                            "request_count": len(entries),
                        },
                    )
                )

        return findings

    # ------------------------------------------------------------------
    # API Type Detection
    # ------------------------------------------------------------------

    def _detect_api_type(self, traffic_log: list[dict[str, Any]]) -> str | None:
        """Heuristically determine if the API is REST, GraphQL, or RPC."""
        graphql_indicators = 0
        rest_indicators = 0
        rpc_indicators = 0

        for entry in traffic_log:
            url = entry.get("url", "")
            method = entry.get("method", "")
            ct = entry.get("content_type", "")

            # GraphQL signals.
            if "/graphql" in url.lower():
                graphql_indicators += 3
            if method == "POST" and "json" in ct:
                body = entry.get("response_body", "")
                if body and '"data"' in body and '"errors"' not in body[:50]:
                    graphql_indicators += 1

            # REST signals: varied HTTP methods, resource-style paths.
            if method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                parsed = urlparse(url)
                path_parts = [p for p in parsed.path.split("/") if p]
                if len(path_parts) >= 2 and re.match(r"^(api|v\d)", path_parts[0]):
                    rest_indicators += 1

            # RPC signals: single endpoint, action in body or URL.
            if re.search(r"\.(do|action|rpc|call|invoke)\b", url, re.IGNORECASE):
                rpc_indicators += 2

        if graphql_indicators > rest_indicators and graphql_indicators > rpc_indicators:
            return "GraphQL"
        if rpc_indicators > rest_indicators and rpc_indicators > graphql_indicators:
            return "RPC"
        if rest_indicators > 0:
            return "REST"
        return None

    # ------------------------------------------------------------------
    # Build Structured Endpoints
    # ------------------------------------------------------------------

    def _build_endpoint_list(
        self,
        endpoint_map: dict[str, dict[str, Any]],
        api_type: str | None,
    ) -> list[dict[str, Any]]:
        """Convert endpoint map to serializable list for ctx.state."""
        endpoints: list[dict[str, Any]] = []
        for pattern, info in endpoint_map.items():
            # Filter to API-like endpoints only.
            ct_set = info.get("content_types", set())
            is_api = any(
                "json" in c or "xml" in c or "protobuf" in c
                for c in ct_set
            )
            if not is_api:
                continue

            endpoints.append(
                {
                    "pattern": pattern,
                    "methods": sorted(info["methods"]),
                    "status_codes": sorted(info["status_codes"]),
                    "content_types": sorted(info["content_types"]),
                    "hit_count": info["hit_count"],
                    "sample_url": info["sample_url"],
                    "api_type": api_type,
                }
            )

        return endpoints


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _redact_url(url: str) -> str:
    """Redact sensitive parameter values from a URL."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    params = parse_qs(parsed.query, keep_blank_values=True)
    redacted_parts = []
    for key, values in params.items():
        key_lower = key.lower()
        is_sensitive = any(
            kw in key_lower
            for kw in ("token", "key", "secret", "password", "auth", "session", "credit", "ssn")
        )
        if is_sensitive:
            redacted_parts.append(f"{key}=***REDACTED***")
        else:
            redacted_parts.append(f"{key}={'&'.join(values)}")
    redacted_query = "&".join(redacted_parts)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{redacted_query}"


def _json_shape(obj: Any, depth: int = 0) -> str:
    """Generate a structural signature of a JSON value for comparison."""
    if depth > 3:
        return "..."
    if isinstance(obj, dict):
        keys = sorted(obj.keys())
        inner = ",".join(f"{k}:{_json_shape(obj[k], depth + 1)}" for k in keys[:20])
        return "{" + inner + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        return "[" + _json_shape(obj[0], depth + 1) + "]"
    if isinstance(obj, bool):
        return "bool"
    if isinstance(obj, int):
        return "int"
    if isinstance(obj, float):
        return "float"
    if isinstance(obj, str):
        return "str"
    if obj is None:
        return "null"
    return "unknown"


def _sample_urls(ctx: ScanContext) -> list[str]:
    """Pick a representative sample of discovered URLs."""
    urls = list(ctx.state.discovered_urls)
    if not urls:
        return [ctx.config.target_url]
    random.shuffle(urls)
    return urls[:_MAX_SAMPLE_PAGES]
