"""Serenity QA command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
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
        "--url",
        required=True,
        help="Target URL to analyze",
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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Animated startup banner
    _show_startup_animation()

    # Logging
    setup_logging(args.verbose)

    # Build config and engine
    config = ScanConfig.from_args(args)
    engine = Engine()

    # Run scan
    try:
        result = asyncio.run(engine.run_scan(config))
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
