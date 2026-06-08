"""Stage 2 — Prospeo: company domain -> decision-makers + LinkedIn URLs.

NOTE (good to mention in the interview): Prospeo deprecated and removed its old
`/domain-search` endpoint on 1 March 2026. The current way to do this is the
Search Person API with a company-website filter.

Endpoint (verified against prospeo.io/api-docs/search-person):
    POST https://api.prospeo.io/search-person
    Header: X-KEY: <key>
    Body:   {"page": 1,
             "filters": {
                 "company": {"websites": {"include": ["domain.com"]}},
                 "person_seniority": {"include": ["Founder/Owner","C-Suite","Vice President"]}
             }}
    Returns {"error": false, "results": [{"person": {...}, "company": {...}}],
             "pagination": {"current_page", "total_page", "total_count"}}

The Search Person API does NOT return email — that's deliberately Stage 3's job
(Eazyreach). Here we only need the person + their LinkedIn URL.
"""
from __future__ import annotations

from .base import ApiError, BaseClient
from ..logging_conf import get_logger
from ..models import Company, Prospect

log = get_logger(__name__)

# C-suite / VP-level. These strings must match Prospeo's seniority enum exactly.
DEFAULT_SENIORITIES = ["Founder/Owner", "C-Suite", "Vice President"]


class ProspeoClient(BaseClient):
    def __init__(self, api_key: str) -> None:
        super().__init__(
            base_url="https://api.prospeo.io",
            headers={"X-KEY": api_key, "Content-Type": "application/json"},
            name="Prospeo",
            min_interval=0.5,
        )

    def find_decision_makers(
        self,
        company: Company,
        *,
        seniorities: list[str] | None = None,
        max_results: int = 3,
    ) -> list[Prospect]:
        seniorities = seniorities or DEFAULT_SENIORITIES
        prospects: list[Prospect] = []
        seen: set[str] = set()
        page = 1

        while len(prospects) < max_results:
            body = {
                "page": page,
                "filters": {
                    "company": {"websites": {"include": [company.domain]}},
                    "person_seniority": {"include": seniorities},
                },
            }
            try:
                data = self.request("POST", "/search-person", json=body)
            except ApiError as exc:
                # NO_RESULTS comes back as HTTP 400 with a body; treat as "none".
                if exc.status == 400 and exc.body and "NO_RESULTS" in str(exc.body):
                    log.info("Prospeo: no decision-makers for %s", company.domain)
                    break
                raise

            if data.get("error"):
                log.warning("Prospeo error for %s: %s", company.domain, data.get("error_code"))
                break

            results = data.get("results", []) or []
            if not results:
                break

            for row in results:
                person = row.get("person", {}) or {}
                key = person.get("person_id") or person.get("linkedin_url")
                if not key or key in seen:
                    continue
                seen.add(key)
                prospects.append(
                    Prospect(
                        person_id=person.get("person_id"),
                        full_name=person.get("full_name"),
                        first_name=person.get("first_name"),
                        last_name=person.get("last_name"),
                        title=person.get("current_job_title") or person.get("headline"),
                        seniority=_main_seniority(person),
                        linkedin_url=person.get("linkedin_url"),
                        company_domain=company.domain,
                        company_name=company.name,
                    )
                )
                if len(prospects) >= max_results:
                    break

            pagination = data.get("pagination", {}) or {}
            if page >= pagination.get("total_page", page):
                break
            page += 1

        log.info("Prospeo: %d decision-makers at %s", len(prospects), company.domain)
        return prospects


def _main_seniority(person: dict) -> str | None:
    for job in person.get("job_history", []) or []:
        if job.get("current"):
            return job.get("seniority")
    return None
