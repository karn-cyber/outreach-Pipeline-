"""The orchestrator.

`Pipeline.source()` runs stages 1-3 (sourcing through email resolution) and
persists state after each one. `Pipeline.dispatch()` runs stage 4 (the send).
They're split on purpose: the safety checkpoint lives *between* them, so emails
only fire after a human has seen and approved the summary.

Resilience is the theme here. Every per-company / per-person call is isolated
in try/except: one company with no contacts, a 404, or a rate-limit hiccup is
logged and skipped, and the run keeps going. Counts of skips and errors land in
the run summary instead of crashing the program.
"""
from __future__ import annotations

from .clients.base import ApiError
from .emailing import render_email
from .logging_conf import get_logger
from .models import Company, Contact, EmailResult, Prospect, RunState, StageStats
from . import store

log = get_logger(__name__)


class Pipeline:
    def __init__(
        self,
        *,
        ocean,
        prospeo,
        eazyreach,
        brevo,
        seniorities: list[str] | None = None,
        require_verified: bool = True,
    ) -> None:
        self.ocean = ocean
        self.prospeo = prospeo
        self.eazyreach = eazyreach
        self.brevo = brevo
        self.seniorities = seniorities
        self.require_verified = require_verified

    # -- Stages 1-3 ---------------------------------------------------------

    def source(
        self,
        state: RunState,
        *,
        max_companies: int,
        max_prospects_per_company: int,
        min_score: float,
        max_emails: int,
        progress=None,
    ) -> RunState:
        if not state.companies:
            # Apollo is 1 flat credit regardless of count; we respect the caller's
            # company count here. Reordering (operating cos first) avoids duds.
            state.companies = self._stage1(state, max_companies, min_score)
            store.save_state(state)
        if not state.prospects:
            state.prospects = self._stage2(state, max_prospects_per_company, max_emails)
            store.save_state(state)
        if not state.contacts:
            state.contacts = self._stage3(state, max_emails)
            store.save_state(state)
        return state

    def _stage1(self, state: RunState, max_companies: int, min_score: float) -> list[Company]:
        stats = StageStats(name="1. Apollo.io — lookalike companies", inputs=1)
        try:
            companies = self.ocean.find_lookalikes(
                state.seed_domain, max_results=max_companies, min_score=min_score
            )
        except ApiError as exc:
            stats.errors += 1
            stats.notes.append(str(exc))
            state.stages.append(stats)
            raise
        stats.outputs = len(companies)
        if not companies:
            stats.notes.append("No lookalikes returned — try a lower --min-score.")
        state.stages.append(stats)
        return companies

    def _stage2(self, state: RunState, max_per_company: int, max_emails: int) -> list[Prospect]:
        stats = StageStats(name="2. Prospeo — decision-makers", inputs=len(state.companies))
        prospects: list[Prospect] = []
        seen: set[str] = set()

        # Credit guard: Prospeo only resolves emails for ~half of people found,
        # so gather a 2x prospect buffer, then stop. Bound company-searches too.
        target_prospects = max_emails * 2 + 1
        search_cap = min(len(state.companies), max_emails + 4)
        searched = 0

        for company in state.companies:
            if len(prospects) >= target_prospects or searched >= search_cap:
                break
            searched += 1
            try:
                found = self.prospeo.find_decision_makers(
                    company, seniorities=self.seniorities, max_results=max_per_company
                )
            except ApiError as exc:
                stats.errors += 1
                stats.notes.append(f"{company.domain}: {exc}")
                log.warning("Skipping %s: %s", company.domain, exc)
                continue
            if not found:
                stats.skipped += 1
                continue
            for p in found:
                key = p.person_id or p.linkedin_url
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                prospects.append(p)
        stats.outputs = len(prospects)
        state.stages.append(stats)
        return prospects

    def _stage3(self, state: RunState, max_emails: int) -> list[Contact]:
        stats = StageStats(name="3. Eazyreach — verified emails", inputs=len(state.prospects))
        suppressed = store.load_suppression()
        contacts: list[Contact] = []
        seen_emails: set[str] = set()

        for prospect in state.prospects:
            if len(contacts) >= max_emails:
                stats.notes.append(f"Hit --max-emails cap ({max_emails}); stopped resolving.")
                break
            if not prospect.linkedin_url:
                stats.skipped += 1
                continue
            try:
                contact = self.eazyreach.resolve_email(prospect)
            except ApiError as exc:
                stats.errors += 1
                stats.notes.append(f"{prospect.display_name()}: {exc}")
                continue
            if contact is None:
                stats.skipped += 1
                continue
            email = contact.email.lower()
            if email in seen_emails:
                stats.skipped += 1
                continue
            if email in suppressed:
                stats.skipped += 1
                stats.notes.append(f"Suppressed (already emailed): {email}")
                continue
            if self.require_verified and contact.email_status not in ("VERIFIED", "UNKNOWN"):
                stats.skipped += 1
                continue
            seen_emails.add(email)
            contacts.append(contact)

        stats.outputs = len(contacts)
        state.stages.append(stats)
        return contacts

    # -- Stage 4 ------------------------------------------------------------

    def dispatch(self, state: RunState, progress=None) -> RunState:
        stats = StageStats(name="4. Brevo — send", inputs=len(state.contacts))
        sent_addresses: list[str] = []
        for contact in state.contacts:
            subject, html, text = render_email(contact)
            result: EmailResult = self.brevo.send(contact, subject, html, text)
            state.results.append(result)
            if result.sent:
                stats.outputs += 1
                sent_addresses.append(result.email)
            elif result.dry_run:
                stats.notes.append(f"dry-run: {result.email}")
            else:
                stats.errors += 1
                stats.notes.append(f"{result.email}: {result.error}")

        # Only suppress addresses we actually emailed for real.
        if sent_addresses:
            store.add_to_suppression(sent_addresses)

        state.stages.append(stats)
        store.save_state(state)
        return state
