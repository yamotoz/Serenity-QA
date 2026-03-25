"""Main scan engine -- the central orchestrator of Serenity QA."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text
from rich.live import Live

from serenity.config import ScanConfig
from serenity.constants import Severity, Verdict
from serenity.scoring.finding import Finding
from serenity.core.browser_pool import BrowserPool
from serenity.core.cdp_manager import CDPManager
from serenity.core.crawler import Crawler
from serenity.core.event_bus import EventBus
from serenity.core.state import ScanContext, ScanState
from serenity.exceptions import AnalyzerError, SerenityError
from serenity.scoring.engine import ScoringEngine

logger = logging.getLogger("serenity.engine")
console = Console(force_terminal=True)

# Timeout in seconds for each advanced module execution
_ADVANCED_MODULE_TIMEOUT = 120


@dataclass
class ScanResult:
    """Final result of a scan."""

    overall_score: float
    verdict: Verdict
    domain_scores: dict[str, float]
    total_findings: int
    total_pages: int
    elapsed_seconds: float
    report_paths: dict[str, str]


class Engine:
    """Orchestrates the entire Serenity QA scan pipeline."""

    def __init__(self) -> None:
        self._crawler = Crawler()
        self._scoring = ScoringEngine()
        self._analyzers: list[Any] = []
        self._advanced_modules: list[Any] = []
        self._shutdown_requested = False
        self._page_times: list[float] = []

    def _register_analyzers(self, config: ScanConfig) -> None:
        """Import and register all analyzer modules."""
        from serenity.analyzers import get_analyzers
        self._analyzers = get_analyzers(config.domains)

    # ------------------------------------------------------------------
    # Startup animation
    # ------------------------------------------------------------------

    def _show_startup_animation(self, config: ScanConfig) -> None:
        """Show a sleek startup panel with scan configuration."""
        console.print(Panel(
            f"[bold gold1]S E R E N I T Y   Q A[/bold gold1]\n\n"
            f"  [dim]Target:[/dim]    [cyan]{config.target_url}[/cyan]\n"
            f"  [dim]Max pages:[/dim] [white]{config.max_pages}[/white]\n"
            f"  [dim]Analyzers:[/dim] [white]{len(self._analyzers)}[/white]\n"
            f"  [dim]Dashboard:[/dim] [white]{'Enabled' if config.live else 'Disabled'}[/white]\n"
            f"  [dim]Advanced:[/dim]  [white]{'Enabled' if config.enable_advanced else 'Disabled'}[/white]",
            border_style="gold1",
            padding=(1, 4),
        ))

    # ------------------------------------------------------------------
    # ETA calculation
    # ------------------------------------------------------------------

    def _calculate_eta(self, remaining_pages: int) -> float:
        """Calculate estimated time remaining based on average page time."""
        if not self._page_times:
            return 0.0
        avg_time = sum(self._page_times) / len(self._page_times)
        return avg_time * remaining_pages

    # ------------------------------------------------------------------
    # Main scan pipeline
    # ------------------------------------------------------------------

    async def run_scan(self, config: ScanConfig) -> ScanResult:
        """Execute the complete scan pipeline."""
        self._register_analyzers(config)
        self._page_times.clear()

        # Show animated startup
        self._show_startup_animation(config)

        # Build context
        event_bus = EventBus()
        state = ScanState()
        pool = BrowserPool(config)
        cdp = CDPManager()

        browser = await pool.start()

        http_client = httpx.AsyncClient(
            timeout=config.http_timeout_s,
            follow_redirects=True,
            verify=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

        ctx = ScanContext(
            config=config,
            browser=browser,
            cdp=cdp,
            state=state,
            event_bus=event_bus,
            http_client=http_client,
            page_pool=pool,
        )

        # Start dashboard if --live
        dashboard_task = None
        if config.live:
            dashboard_task = await self._start_dashboard(ctx)

        # Register graceful shutdown
        self._register_shutdown_handler(ctx)

        report_paths: dict[str, str] = {}

        try:
            # Setup all analyzers
            for analyzer in self._analyzers:
                await analyzer.setup(ctx)

            # --- Phase 1: Crawl -----------------------------------------
            console.print("\n[bold sea_green2]> Phase 1:[/bold sea_green2] Crawling target site...")
            urls = await self._crawler.crawl(ctx)
            console.print(f"  [green][OK][/green] Discovered [bold]{len(urls)}[/bold] pages\n")

            if not urls:
                raise SerenityError("No pages discovered. Check the URL and try again.")

            total_pages = len(urls)

            # Emit scan.started with real discovered page count
            await event_bus.emit("scan.started", {
                "target_url": config.target_url,
                "total_pages": total_pages,
                "domains": list(set(
                    analyzer.domain for analyzer in self._analyzers
                    if hasattr(analyzer, "domain")
                )),
            })

            # --- Phase 2: Per-page analysis -----------------------------
            console.print("[bold sea_green2]> Phase 2:[/bold sea_green2] Analyzing pages...")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=40),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Analyzing...", total=total_pages)

                for page_index, url in enumerate(urls):
                    if self._shutdown_requested:
                        console.print("\n[yellow][!] Shutdown requested. Saving partial results...[/yellow]")
                        break

                    progress.update(task, description=f"[cyan]{self._truncate_url(url)}[/cyan]")

                    page_start = time.monotonic()
                    await self._analyze_page(ctx, url)
                    page_elapsed = time.monotonic() - page_start
                    self._page_times.append(page_elapsed)

                    # Update scores after each page
                    scores = self._scoring.calculate(state.findings)
                    state.domain_scores = scores["domains"]
                    state.overall_score = scores["overall"]

                    await event_bus.emit("score.update", {
                        "domains": scores["domains"],
                        "overall": scores["overall"],
                    })

                    # Calculate ETA and emit scan.progress with full data
                    current_page = page_index + 1
                    remaining = total_pages - current_page
                    eta_seconds = self._calculate_eta(remaining)

                    await event_bus.emit("scan.progress", {
                        "current_page": current_page,
                        "total": total_pages,
                        "eta_seconds": eta_seconds,
                        "current_url": url,
                        "pages_analyzed": state.pages_analyzed,
                        "total_findings": state.total_findings,
                    })

                    progress.advance(task)

            # Ensure progress bar shows 100% after Phase 2
            await event_bus.emit("scan.progress", {
                "current_page": total_pages,
                "total": total_pages,
                "eta_seconds": 0,
                "current_url": "",
                "pages_analyzed": state.pages_analyzed,
                "total_findings": state.total_findings,
            })

            # --- Phase 3: Global analysis -------------------------------
            console.print("\n[bold sea_green2]> Phase 3:[/bold sea_green2] Running global analysis...")

            for analyzer in self._analyzers:
                try:
                    findings = await analyzer.analyze_global(ctx)
                    for f in findings:
                        state.add_finding(f)
                        await event_bus.emit("finding.new", f.model_dump())
                except Exception as e:
                    logger.warning("Global analysis failed for %s: %s", analyzer.domain, e)

            # --- Phase 4: Advanced modules ------------------------------
            if config.enable_advanced:
                console.print("\n[bold sea_green2]> Phase 4:[/bold sea_green2] Running advanced analysis...")
                await self._run_advanced(ctx)

            # --- Phase 5: AI pass ---------------------------------------
            if config.enable_ai and config.gemini_api_key:
                console.print("\n[bold sea_green2]> Phase 5:[/bold sea_green2] AI-powered analysis...")
                await self._run_ai(ctx)

            # --- Phase 6: Final scoring ---------------------------------
            state.end_time = time.time()
            final_scores = self._scoring.calculate(state.findings)
            state.domain_scores = final_scores["domains"]
            state.overall_score = final_scores["overall"]
            verdict = self._scoring.get_verdict(final_scores["overall"])

            # --- Phase 7: Generate reports ------------------------------
            console.print("\n[bold sea_green2]> Phase 6:[/bold sea_green2] Generating reports...")
            report_paths = await self._generate_reports(ctx, final_scores, verdict)

            # --- Done ---------------------------------------------------
            await event_bus.emit("scan.completed", {
                "overall_score": final_scores["overall"],
                "verdict": verdict.value,
                "report_paths": report_paths,
            })

            self._print_summary(final_scores, verdict, state, report_paths)

            return ScanResult(
                overall_score=final_scores["overall"],
                verdict=verdict,
                domain_scores=final_scores["domains"],
                total_findings=state.total_findings,
                total_pages=state.pages_analyzed,
                elapsed_seconds=state.elapsed_seconds,
                report_paths=report_paths,
            )

        finally:
            await self._cleanup(ctx, http_client, dashboard_task)

    # ------------------------------------------------------------------
    # Per-page analysis
    # ------------------------------------------------------------------

    async def _analyze_page(self, ctx: ScanContext, url: str) -> None:
        """Run all analyzers on a single page."""
        page = await ctx.page_pool.acquire()
        try:
            response = await page.goto(url, timeout=ctx.config.page_timeout_ms, wait_until="domcontentloaded")

            # Detect auth redirect or login gate:
            # Case 1: Server redirected to a different URL (302 to /login)
            # Case 2: SPA rendered login form without URL change (client-side auth gate)
            from urllib.parse import urlparse
            final_url = page.url
            orig_path = urlparse(url).path.rstrip("/")
            final_path = urlparse(final_url).path.rstrip("/")

            is_auth_redirect = False

            # Case 1: URL changed to a known auth path
            if final_url != url and orig_path != final_path:
                is_auth_redirect = any(
                    kw in final_path.lower()
                    for kw in ("/login", "/signin", "/auth")
                )

            # Case 2: Page content shows a login form (regardless of URL change)
            # This catches SPA auth gates where /marketing renders the login form.
            # Wait briefly for JS to render (SPA may not have form in initial HTML).
            if not is_auth_redirect:
                try:
                    await page.wait_for_timeout(1500)
                    has_login_form = await page.evaluate("""() => {
                        // Check for login form indicators
                        const loginForm = document.querySelector(
                            'form#form-login, form[action*="login"], form[action*="signin"]'
                        );
                        const passwordInput = document.querySelector('input[type="password"]');
                        // Also check if page title/content indicates login
                        const title = document.title.toLowerCase();
                        const isLoginTitle = title.includes('login') || title.includes('sign in') || title.includes('acesse');
                        return !!(loginForm || passwordInput || (isLoginTitle && passwordInput !== null));
                    }""")
                    if has_login_form:
                        is_login_page = orig_path in ("/login", "/signin", "/auth")
                        if not is_login_page:
                            is_auth_redirect = True
                except Exception:
                    pass

            if is_auth_redirect:
                logger.info(
                    "Page %s detected as auth gate (login form present) — skipping",
                    url,
                )
                ctx.state.add_finding(
                    Finding(
                        domain="infrastructure",
                        severity=Severity.LOW,
                        title="Page requires authentication",
                        description=(
                            f"Accessing {url} shows a login form (auth gate). "
                            "This page requires authentication and cannot be audited "
                            "without valid credentials."
                        ),
                        url=url,
                        estimated_fix_minutes=0,
                        metadata={"redirect_to": final_url},
                    )
                )
                await ctx.event_bus.emit("page.done", {"url": url})
                await ctx.event_bus.emit("page.heatmap", {"url": url, "status": "pass"})
                return

            await ctx.event_bus.emit("page.analyzing", {"url": url})

            for analyzer in self._analyzers:
                try:
                    findings = await analyzer.analyze_page(ctx, url, page)
                    for f in findings:
                        ctx.state.add_finding(f)
                        await ctx.event_bus.emit("finding.new", f.model_dump())
                except AnalyzerError as e:
                    logger.warning("%s failed on %s: %s", analyzer.domain, url, e)
                except Exception as e:
                    logger.error("Unexpected error in %s on %s: %s", analyzer.domain, url, e)

            await ctx.event_bus.emit("page.done", {"url": url})
            await ctx.event_bus.emit("page.heatmap", {"url": url, "status": "pass"})

        except Exception as e:
            logger.error("Page navigation failed for %s: %s", url, e)
            ctx.state.mark_failed(url)
            await ctx.event_bus.emit("page.heatmap", {"url": url, "status": "fail"})

        finally:
            await ctx.page_pool.release(page)

    # ------------------------------------------------------------------
    # Advanced modules (with timeout)
    # ------------------------------------------------------------------

    async def _run_advanced(self, ctx: ScanContext) -> None:
        """Run advanced analysis modules with per-module timeout."""
        try:
            from serenity.advanced import get_advanced_modules
            modules = get_advanced_modules()
            for module in modules:
                module_name = module.__class__.__name__
                try:
                    findings = await asyncio.wait_for(
                        module.run(ctx),
                        timeout=_ADVANCED_MODULE_TIMEOUT,
                    )
                    for f in findings:
                        ctx.state.add_finding(f)
                        await ctx.event_bus.emit("finding.new", f.model_dump())
                except asyncio.TimeoutError:
                    logger.warning(
                        "Advanced module %s timed out after %ds -- skipping",
                        module_name,
                        _ADVANCED_MODULE_TIMEOUT,
                    )
                except Exception as e:
                    logger.warning("Advanced module %s failed: %s", module_name, e)
        except ImportError:
            logger.debug("Advanced modules not available")

    # ------------------------------------------------------------------
    # AI pass
    # ------------------------------------------------------------------

    async def _run_ai(self, ctx: ScanContext) -> None:
        """Run AI-powered analysis pass."""
        try:
            from serenity.ai import run_ai_analysis
            findings = await run_ai_analysis(ctx)
            for f in findings:
                ctx.state.add_finding(f)
                await ctx.event_bus.emit("finding.new", f.model_dump())
        except ImportError:
            logger.debug("AI modules not available")
        except Exception as e:
            logger.warning("AI analysis failed: %s", e)

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    async def _generate_reports(
        self, ctx: ScanContext, scores: dict[str, Any], verdict: Verdict
    ) -> dict[str, str]:
        """Generate all report formats."""
        paths: dict[str, str] = {}
        output = ctx.config.get_output_path()

        try:
            from serenity.reporting.json_report import generate_json_report
            if "json" in ctx.config.report_formats:
                json_path = await generate_json_report(ctx, scores, verdict, output)
                paths["json"] = str(json_path)
                console.print(f"  [green][OK][/green] JSON report: [dim]{json_path}[/dim]")
        except Exception as e:
            logger.error("JSON report generation failed: %s", e)

        try:
            from serenity.reporting.html_report import generate_html_report
            if "html" in ctx.config.report_formats:
                html_path = await generate_html_report(ctx, scores, verdict, output)
                paths["html"] = str(html_path)
                console.print(f"  [green][OK][/green] HTML report: [dim]{html_path}[/dim]")
        except Exception as e:
            logger.error("HTML report generation failed: %s", e)

        if "pdf" in ctx.config.report_formats:
            try:
                from serenity.reporting.pdf_report import generate_pdf_report
                pdf_path = await generate_pdf_report(ctx, scores, verdict, output)
                paths["pdf"] = str(pdf_path)
                console.print(f"  [green][OK][/green] PDF report: [dim]{pdf_path}[/dim]")
            except Exception:
                console.print(
                    "  [yellow][SKIP][/yellow] PDF report: WeasyPrint requires GTK on Windows. "
                    "HTML report is available instead."
                )

        # Always generate prompt_recall.md
        try:
            from serenity.reporting.prompt_recall import generate_prompt_recall
            recall_path = await generate_prompt_recall(ctx, scores, verdict, output)
            paths["prompt_recall"] = str(recall_path)
            console.print(f"  [green][OK][/green] Prompt recall: [dim]{recall_path}[/dim]")
        except Exception as e:
            logger.error("prompt_recall.md generation failed: %s", e)

        return paths

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    async def _start_dashboard(self, ctx: ScanContext) -> asyncio.Task[None]:
        """Start the live dashboard server."""
        from serenity.dashboard.server import start_dashboard
        task = asyncio.create_task(start_dashboard(ctx))
        console.print(
            f"[bold gold1]> Dashboard:[/bold gold1] "
            f"http://{ctx.config.dashboard_host}:{ctx.config.dashboard_port}\n"
        )
        return task

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _register_shutdown_handler(self, ctx: ScanContext) -> None:
        """Register SIGINT handler for graceful shutdown."""
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: setattr(self, "_shutdown_requested", True))
        except (NotImplementedError, OSError):
            # Windows doesn't support add_signal_handler in all cases
            pass

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _print_summary(
        self,
        scores: dict[str, Any],
        verdict: Verdict,
        state: ScanState,
        report_paths: dict[str, str],
    ) -> None:
        """Print the final scan summary."""
        overall = scores["overall"]
        domains = scores["domains"]

        if verdict == Verdict.EXCELLENT:
            color = "green"
            badge = "[PASS] EXCELENTE"
        elif verdict == Verdict.APPROVED:
            color = "yellow"
            badge = "[PASS] APROVADO"
        else:
            color = "red"
            badge = "[FAIL] REPROVADO"

        domain_lines = ""
        for domain, score in sorted(domains.items(), key=lambda x: x[1]):
            bar = self._score_bar(score)
            domain_lines += f"  {domain:<20s} {bar} {score:.1f}\n"

        console.print(Panel(
            f"[bold {color}]{badge}[/bold {color}]\n\n"
            f"[bold]Score Geral: [{color}]{overall:.1f}[/{color}]/100[/bold]\n\n"
            f"[dim]Scores por Dominio:[/dim]\n{domain_lines}\n"
            f"[dim]Paginas analisadas:[/dim] {state.pages_analyzed}\n"
            f"[dim]Findings encontrados:[/dim] {state.total_findings}\n"
            f"[dim]Tempo total:[/dim] {state.elapsed_seconds:.1f}s",
            title="[bold gold1]SERENITY QA -- Resultado Final[/bold gold1]",
            border_style="gold1",
        ))

    @staticmethod
    def _score_bar(score: float) -> str:
        filled = int(score / 5)
        empty = 20 - filled
        if score >= 91:
            color = "green"
        elif score >= 70:
            color = "yellow"
        else:
            color = "red"
        return f"[{color}]{'#' * filled}{'.' * empty}[/{color}]"

    @staticmethod
    def _truncate_url(url: str, max_len: int = 50) -> str:
        if len(url) <= max_len:
            return url
        return url[:max_len - 3] + "..."

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def _cleanup(
        self,
        ctx: ScanContext,
        http_client: httpx.AsyncClient,
        dashboard_task: asyncio.Task[None] | None,
    ) -> None:
        """Clean up all resources."""
        await ctx.cdp.close_all()
        await ctx.page_pool.shutdown()
        await http_client.aclose()
        ctx.event_bus.clear()

        if dashboard_task and not dashboard_task.done():
            dashboard_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(dashboard_task), timeout=3)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
