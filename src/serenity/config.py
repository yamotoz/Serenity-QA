"""Serenity QA configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from serenity.constants import (
    DASHBOARD_HOST,
    DASHBOARD_PORT,
    HTTP_TIMEOUT_S,
    MAX_PAGES_DEFAULT,
    PAGE_TIMEOUT_MS,
    SCAN_TIMEOUT_S,
)


class ScanConfig(BaseModel):
    """Configuration for a Serenity QA scan."""

    target_url: str
    output_dir: str = "./serenity-report"
    max_pages: int = Field(default=MAX_PAGES_DEFAULT, ge=1, le=5000)
    page_timeout_ms: int = Field(default=PAGE_TIMEOUT_MS, ge=1000)
    http_timeout_s: int = Field(default=HTTP_TIMEOUT_S, ge=1)
    scan_timeout_s: int = Field(default=SCAN_TIMEOUT_S, ge=60)
    live: bool = False
    verbose: int = 0
    report_formats: list[str] = Field(default=["html", "pdf", "json"])
    domains: list[str] | None = None  # None = all domains
    enable_advanced: bool = True
    enable_ai: bool = True
    headless: bool = True
    json_only: bool = False  # --json mode: single JSON output, no extra files

    # Dashboard
    dashboard_host: str = DASHBOARD_HOST
    dashboard_port: int = DASHBOARD_PORT

    # API Keys (loaded from env)
    gemini_api_key: str = ""
    supabase_url: str = ""
    supabase_key: str = ""

    @field_validator("target_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            v = f"https://{v}"
        return v.rstrip("/")

    @classmethod
    def from_args(cls, args: object) -> ScanConfig:
        """Build config from CLI arguments + environment variables."""
        load_dotenv()

        formats = ["html", "pdf", "json"]
        fmt = getattr(args, "format", "all")
        if fmt != "all":
            formats = [fmt]

        # -o saves inside Scans/ folder, --output-dir uses custom path
        output = getattr(args, "output", None)
        if output:
            # Resolve relative to the repo root (where Serenity.py lives)
            repo_root = Path(__file__).resolve().parent.parent.parent
            output_dir = str(repo_root / "Scans" / output)
        else:
            output_dir = getattr(args, "output_dir", "./serenity-report")

        return cls(
            target_url=getattr(args, "url", ""),
            output_dir=output_dir,
            max_pages=getattr(args, "max_pages", MAX_PAGES_DEFAULT),
            page_timeout_ms=getattr(args, "timeout", 30) * 1000,
            live=getattr(args, "live", False),
            verbose=getattr(args, "verbose", 0),
            report_formats=formats,
            domains=getattr(args, "domains", None),
            enable_advanced=not getattr(args, "no_advanced", False),
            enable_ai=not getattr(args, "no_ai", False),
            headless=not getattr(args, "headed", False),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_key=os.getenv("SUPABASE_KEY", ""),
        )

    def get_output_path(self) -> Path:
        """Get and ensure output directory exists."""
        path = Path(self.output_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
