"""Command-line entrypoint — the single program that runs all four stages.

    python run.py run --domain stripe.com

One domain in; lookalikes -> decision-makers -> verified emails -> a summary
you approve -> sends. No copy-paste between stages.

Key flags:
    --mock        run fully offline with canned data (no keys, no network)
    --dry-run     real sourcing + email resolution, but DON'T actually send
    --yes         skip the interactive confirmation (for non-interactive runs)
    --resume ID   continue a previous run without re-spending credits
"""
from __future__ import annotations

import csv
import re
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from .clients.base import ApiError, AuthError
from .clients.brevo import BrevoClient, DryRunBrevoClient
from .clients.eazyreach import EazyreachClient, MockEazyreachClient
from .clients.ocean import OceanClient
from .clients.apollo import FallbackStage1Client
from .clients.prospeo import ProspeoClient
from .clients.prospeo_email import ProspeoEmailClient
from .config import settings
from .emailing import render_email
from .logging_conf import console, get_logger, setup_logging
from .mocks import MockOceanClient, MockProspeoClient
from .models import RunState
from .pipeline import Pipeline
from . import store

app = typer.Typer(add_completion=False, help="Automated cold-outreach pipeline.")
log = get_logger(__name__)


def _normalise_domain(raw: str) -> str:
    raw = raw.strip().lower()
    raw = re.sub(r"^https?://", "", raw)
    raw = raw.split("/")[0]
    raw = re.sub(r"^www\.", "", raw)
    return raw


def _new_run_id(domain: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]", "-", domain)
    return f"{ts}-{slug}"


@app.command()
def run(
    domain: str = typer.Option(..., "--domain", "-d", help="Seed company domain, e.g. stripe.com"),
    max_companies: int = typer.Option(None, help="Max lookalike companies (Stage 1)."),
    max_prospects_per_company: int = typer.Option(None, help="Max decision-makers per company (Stage 2)."),
    max_emails: int = typer.Option(None, help="Hard cap on emails sent (Stage 4)."),
    min_score: float = typer.Option(None, help="Ocean.io lookalike similarity threshold (0-1)."),
    seniority: Optional[list[str]] = typer.Option(None, help="Prospeo seniority filter; repeatable."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do everything except actually send."),
    mock: bool = typer.Option(False, "--mock", help="Run fully offline with canned data."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    resume: Optional[str] = typer.Option(None, help="Resume an existing run id."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    setup_logging(verbose)

    # resolve effective settings (CLI overrides .env defaults)
    max_companies = max_companies or settings.default_max_companies
    max_prospects_per_company = max_prospects_per_company or settings.default_max_prospects_per_company
    max_emails = max_emails or settings.default_max_emails
    min_score = min_score if min_score is not None else settings.default_min_score
    seniorities = seniority or None

    domain = _normalise_domain(domain)

    # preflight: make sure we have what a real run needs
    if not mock:
        missing = settings.missing_keys_for_real_run()
        if dry_run:
            missing = [m for m in missing if not m.startswith("BREVO") and not m.startswith("SENDER")]
        if missing:
            console.print(Panel(
                "Missing configuration:\n  - " + "\n  - ".join(missing) +
                "\n\nCopy .env.example to .env and fill these in, or use --mock to demo offline.",
                title="Setup needed", border_style="red"))
            raise typer.Exit(code=1)

    console.print(Panel(
        f"[bold]Seed domain:[/bold] {domain}\n"
        f"[bold]Mode:[/bold] {_mode_label(mock, dry_run)}\n"
        f"[bold]Caps:[/bold] {max_companies} companies · "
        f"{max_prospects_per_company} people/co · {max_emails} emails max",
        title="Outreach pipeline", border_style="cyan"))

    with ExitStack() as stack:
        ocean, prospeo, eazyreach, brevo = _make_clients(stack, mock, dry_run)

        # sender preflight for a real send
        if not mock and not dry_run:
            acct = brevo.verify_account()
            if acct:
                log.info("Brevo authenticated as %s", acct)

        pipe = Pipeline(ocean=ocean, prospeo=prospeo, eazyreach=eazyreach,
                        brevo=brevo, seniorities=seniorities)

        # load or create run state
        if resume and store.run_exists(resume):
            state = store.load_state(resume)
            console.print(f"[yellow]Resuming run {resume}[/yellow]")
        else:
            state = RunState(run_id=_new_run_id(domain), seed_domain=domain)
            store.save_state(state)

        # --- Stages 1-3 (sourcing) ---
        try:
            with console.status("[bold green]Sourcing prospects (stages 1-3)..."):
                pipe.source(
                    state,
                    max_companies=max_companies,
                    max_prospects_per_company=max_prospects_per_company,
                    min_score=min_score,
                    max_emails=max_emails,
                )
        except AuthError as exc:
            console.print(Panel(str(exc), title="Authentication failed", border_style="red"))
            raise typer.Exit(code=1)
        except ApiError as exc:
            console.print(Panel(str(exc), title="API error", border_style="red"))
            raise typer.Exit(code=1)

        _print_funnel(state)

        if not state.contacts:
            console.print(Panel("No deliverable contacts found. Nothing to send.\n"
                                f"Run id saved as {state.run_id} (resumable).",
                                title="Done", border_style="yellow"))
            raise typer.Exit(code=0)

        # --- SAFETY CHECKPOINT ---
        _print_checkpoint(state, dry_run)

        if dry_run:
            console.print("[yellow]--dry-run: simulating sends, nothing will leave your account.[/yellow]")
        elif not yes:
            if not Confirm.ask(f"Send {len(state.contacts)} email(s) now?", default=False):
                console.print(Panel(f"Aborted before sending. Resume later with:\n"
                                    f"  python run.py run -d {domain} --resume {state.run_id}",
                                    title="Cancelled", border_style="yellow"))
                raise typer.Exit(code=0)

        # --- Stage 4 (send) ---
        with console.status("[bold green]Sending (stage 4)..."):
            pipe.dispatch(state)

        _print_results(state)
        out = _export(state)
        console.print(Panel(f"Run id: {state.run_id}\nArtifacts: {out}",
                            title="Finished", border_style="green"))


def _mode_label(mock: bool, dry_run: bool) -> str:
    if mock:
        return "MOCK (offline, canned data)"
    if dry_run:
        return "DRY-RUN (real sourcing, no send)"
    return "LIVE"


def _make_clients(stack: ExitStack, mock: bool, dry_run: bool):
    if mock:
        ocean = stack.enter_context(MockOceanClient())
        prospeo = stack.enter_context(MockProspeoClient())
        eazyreach = stack.enter_context(MockEazyreachClient())
        brevo = stack.enter_context(DryRunBrevoClient())
        return ocean, prospeo, eazyreach, brevo

    ocean = stack.enter_context(
        FallbackStage1Client(settings.ocean_api_token, settings.apollo_api_key)
    )
    prospeo = stack.enter_context(ProspeoClient(settings.prospeo_api_key))

    # Stage 3: Prospeo email finder (recommended) > real Eazyreach > mock
    if settings.use_prospeo_email and settings.prospeo_api_key:
        eazyreach = stack.enter_context(ProspeoEmailClient(settings.prospeo_api_key))
        console.print("[dim]Stage 3: using Prospeo email finder (Eazyreach replacement)[/dim]")
    elif settings.eazyreach_mock or not settings.eazyreach_api_key:
        eazyreach = stack.enter_context(MockEazyreachClient())
        console.print("[yellow]Stage 3: mock emails (set USE_PROSPEO_EMAIL=true to use real data)[/yellow]")
    else:
        eazyreach = stack.enter_context(EazyreachClient(
            settings.eazyreach_api_key,
            base_url=settings.eazyreach_base_url,
            resolve_path=settings.eazyreach_resolve_path,
            auth_style=settings.eazyreach_auth_style,
        ))

    if dry_run:
        brevo = stack.enter_context(DryRunBrevoClient())
    else:
        brevo = stack.enter_context(BrevoClient(
            settings.brevo_api_key, settings.sender_email,
            settings.sender_name, settings.reply_to_email,
            test_recipient=settings.test_recipient,
        ))
    return ocean, prospeo, eazyreach, brevo


def _print_funnel(state: RunState) -> None:
    table = Table(title="Pipeline funnel", show_lines=False)
    table.add_column("Stage", style="bold")
    table.add_column("In", justify="right")
    table.add_column("Out", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Errors", justify="right", style="red")
    for s in state.stages:
        table.add_row(s.name, str(s.inputs), str(s.outputs), str(s.skipped), str(s.errors))
    console.print(table)
    for s in state.stages:
        for note in s.notes[:5]:
            console.print(f"  [dim]{s.name.split('.')[0]}: {note}[/dim]")


def _print_checkpoint(state: RunState, dry_run: bool) -> None:
    title = "SAFETY CHECKPOINT — review before sending"
    table = Table(title=title, border_style="magenta", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("Name")
    table.add_column("Title")
    table.add_column("Company")
    table.add_column("Email")
    table.add_column("Status")
    for i, c in enumerate(state.contacts, 1):
        table.add_row(str(i), c.display_name(), c.title or "-",
                      c.company_name or c.company_domain, c.email, c.email_status)
    console.print(table)
    if state.contacts:
        subj, _, _ = render_email(state.contacts[0])
        console.print(Panel(f"[bold]Example subject:[/bold] {subj}", border_style="magenta"))


def _print_results(state: RunState) -> None:
    sent = sum(1 for r in state.results if r.sent)
    dry = sum(1 for r in state.results if r.dry_run)
    failed = sum(1 for r in state.results if not r.sent and not r.dry_run)
    table = Table(title="Send results")
    table.add_column("Email")
    table.add_column("Outcome")
    table.add_column("Detail", overflow="fold")
    for r in state.results:
        if r.sent:
            outcome, detail = "[green]sent[/green]", r.message_id or ""
        elif r.dry_run:
            outcome, detail = "[yellow]dry-run[/yellow]", "not sent"
        else:
            outcome, detail = "[red]failed[/red]", r.error or ""
        table.add_row(r.email, outcome, detail)
    console.print(table)
    console.print(f"[bold]Totals:[/bold] sent={sent} dry-run={dry} failed={failed}")


def _export(state: RunState) -> str:
    out_dir = store.RUNS_DIR / state.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    contacts_csv = out_dir / "contacts.csv"
    with contacts_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "title", "company", "domain", "linkedin", "email", "status"])
        for c in state.contacts:
            w.writerow([c.display_name(), c.title or "", c.company_name or "",
                        c.company_domain, c.linkedin_url or "", c.email, c.email_status])
    results_csv = out_dir / "results.csv"
    with results_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["email", "sent", "dry_run", "message_id", "error", "at"])
        for r in state.results:
            w.writerow([r.email, r.sent, r.dry_run, r.message_id or "", r.error or "", r.at])
    return str(out_dir)


if __name__ == "__main__":
    app()
