"""Serenity QA command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import time

# Fix Windows console encoding for Unicode
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

from rich.console import Console
from rich.text import Text
from rich.style import Style

from pathlib import Path

from serenity import __version__
from serenity.config import ScanConfig
from serenity.core.engine import Engine

console = Console(force_terminal=True)

# ASCII art banner lines (no unicode, no emojis -- pure ASCII)
_BANNER_LINES = [
    r"   _____ ______ _____  ______ _   _ _____ _________     __",
    r"  / ____|  ____|  __ \|  ____| \ | |_   _|__   __\ \   / /",
    r" | (___ | |__  | |__) | |__  |  \| | | |    | |   \ \_/ / ",
    r"  \___ \|  __| |  _  /|  __| | . ` | | |    | |    \   /  ",
    r"  ____) | |____| | \ \| |____| |\  |_| |_   | |     | |   ",
    r" |_____/|______|_|  \_\______|_| \_|_____|  |_|     |_|   ",
]

# Gold gradient palette (top to bottom) using Rich named colors
_GOLD_GRADIENT = [
    "bright_yellow",
    "gold1",
    "gold1",
    "dark_goldenrod",
    "orange3",
    "dark_orange3",
]


def _show_startup_animation() -> None:
    """Display an animated startup sequence using Rich."""
    # 1. Clear screen effect
    console.print("\n" * 3)
    time.sleep(0.05)

    # 2. Render banner line by line with gold gradient
    for i, line in enumerate(_BANNER_LINES):
        color = _GOLD_GRADIENT[i % len(_GOLD_GRADIENT)]
        styled = Text(line, style=Style(color=color, bold=True))
        console.print(styled)
        time.sleep(0.12)

    console.print()

    # 3. Subtitle
    subtitle = Text("  QUALITY ASSURANCE", style=Style(dim=True))
    console.print(subtitle)
    time.sleep(0.15)

    # 4. Version line
    ver_text = Text(
        f"  >> Advanced Web QA Agent -- v{__version__}", style=Style(dim=True)
    )
    console.print(ver_text)
    time.sleep(0.2)

    console.print()

    # 5. Initializing with animated dots
    msg = "  Initializing systems"
    for dot_count in range(1, 4):
        console.print(
            Text(msg + "." * dot_count, style=Style(color="grey70")),
            end="\r",
        )
        time.sleep(0.25)

    # Final line -- overwrite with complete message
    console.print(
        Text(
            msg + "... ready.                ",
            style=Style(color="green"),
        )
    )
    console.print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="serenity",
        description="Serenity QA -- Advanced Web Quality Assurance Agent",
    )

    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Update Serenity QA to the latest version (git pull + pip install)",
    )

    parser.add_argument(
        "--url",
        default=None,
        help="Target URL to analyze",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_only",
        help="Output a single consolidated JSON to stdout (API mode, no extra files)",
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Launch real-time dashboard with WebSocket",
    )

    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output folder name (e.g., -o buildcode creates ./buildcode/ with all reports)",
    )

    parser.add_argument(
        "--output-dir",
        default="./serenity-report",
        help="Report output directory (default: ./serenity-report)",
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=100,
        help="Maximum pages to crawl (default: 100)",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Page timeout in seconds (default: 30)",
    )

    parser.add_argument(
        "--domains",
        nargs="*",
        help="Specific analysis domains to run (e.g., seo performance accessibility)",
    )

    parser.add_argument(
        "--format",
        choices=["html", "pdf", "json", "all"],
        default="all",
        help="Report format (default: all)",
    )

    parser.add_argument(
        "--no-advanced",
        action="store_true",
        help="Skip advanced analysis modules",
    )

    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip AI-powered analysis",
    )

    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode (visible)",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v, -vv, -vvv)",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"Serenity QA {__version__}",
    )

    return parser


def setup_logging(verbosity: int) -> None:
    levels = {
        0: logging.WARNING,
        1: logging.INFO,
        2: logging.DEBUG,
    }
    level = levels.get(min(verbosity, 2), logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _run_upgrade() -> None:
    """Update Serenity QA: git pull + pip install -r requirements.txt."""
    repo_root = Path(__file__).resolve().parent.parent.parent  # src/serenity -> repo root

    console.print("[bold gold1]> Serenity QA -- Upgrade[/bold gold1]\n")

    # Step 1: git pull
    console.print("  [dim]Pulling latest changes...[/dim]")
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            console.print(f"  [green][OK][/green] {result.stdout.strip()}")
        else:
            console.print(f"  [red][FAIL][/red] git pull failed:\n{result.stderr.strip()}")
            sys.exit(1)
    except FileNotFoundError:
        console.print("  [red][FAIL][/red] git not found. Make sure git is installed.")
        sys.exit(1)

    # Step 2: pip install
    console.print("  [dim]Installing dependencies...[/dim]")
    req_file = repo_root / "requirements.txt"
    if req_file.exists():
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file), "-q"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            console.print("  [green][OK][/green] Dependencies up to date")
        else:
            console.print(f"  [yellow][WARN][/yellow] pip install issues:\n{result.stderr.strip()}")
    else:
        console.print("  [dim]No requirements.txt found, skipping pip install[/dim]")

    # Step 3: playwright install (browsers)
    console.print("  [dim]Checking Playwright browsers...[/dim]")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode == 0:
        console.print("  [green][OK][/green] Playwright browsers ready")
    else:
        console.print("  [yellow][WARN][/yellow] Playwright browser install issue")

    console.print("\n[bold green]Upgrade complete![/bold green]")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # --upgrade: update and exit
    if args.upgrade:
        _run_upgrade()
        sys.exit(0)

    # --url is required for scanning
    if not args.url:
        parser.error("--url is required (unless using --upgrade)")

    # Animated startup banner (skip in json-only mode for clean output)
    if not args.json_only:
        _show_startup_animation()

    # Logging
    setup_logging(args.verbose)

    # In --json mode, force json-only format and suppress console output
    if args.json_only:
        args.format = "json"

    # Build config and engine
    config = ScanConfig.from_args(args)

    # Override for --json mode: only JSON report
    if args.json_only:
        config.report_formats = ["json"]
        config.json_only = True

    engine = Engine()

    # Run scan
    try:
        result = asyncio.run(engine.run_scan(config))

        # In --json mode, read the generated JSON and print to stdout
        if args.json_only and "json" in result.report_paths:
            json_path = Path(result.report_paths["json"])
            print(json_path.read_text(encoding="utf-8"))

        sys.exit(0 if result.verdict.value != "REPROVADO" else 1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Scan interrupted by user.[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}")
        if args.verbose > 0:
            console.print_exception()
        sys.exit(1)


if __name__ == "__main__":
    main()
