"""Stage 1 — Ocean.io: seed domain -> lookalike company domains.

Endpoint (verified against docs.ocean.io):
    POST https://api.ocean.io/v2/search/companies
    Header: x-api-token: <token>
    Body:   {"size": N, "from": M,
             "companiesFilters": {"lookalikeDomains": [seed], "minScore": 0.8}}
    Returns {"totalHits": N, "companies": [{"relevance": "A",
             "company": {"name", "domain", "size", "description",
                         "primaryCountry", ...}}]}

We page with from/size until we have enough results or exhaust totalHits.
"""
from __future__ import annotations

from typing import Optional

from .base import BaseClient
from ..logging_conf import get_logger
from ..models import Company

log = get_logger(__name__)

PAGE_SIZE = 25


class OceanClient(BaseClient):
    def __init__(self, api_token: str) -> None:
        super().__init__(
            base_url="https://api.ocean.io",
            headers={"x-api-token": api_token, "Content-Type": "application/json"},
            name="Ocean.io",
            min_interval=0.3,
        )

    def find_lookalikes(
        self,
        seed_domain: str,
        *,
        max_results: int = 10,
        min_score: float = 0.80,
    ) -> list[Company]:
        companies: list[Company] = []
        seen: set[str] = set()
        offset = 1  # Ocean.io uses 1-based pagination (from >= 1)

        while len(companies) < max_results:
            page_size = min(PAGE_SIZE, max_results - len(companies))
            body = {
                "size": page_size,
                "from": offset,
                "companiesFilters": {
                    "lookalikeDomains": [seed_domain],
                    "minScore": min_score,
                },
            }
            data = self.request("POST", "/v2/search/companies", json=body)
            rows = data.get("companies", []) or []
            total = data.get("totalHits", 0)
            if not rows:
                break

            for row in rows:
                c = row.get("company", {}) or {}
                domain = (c.get("domain") or "").strip().lower()
                if not domain or domain == seed_domain.lower() or domain in seen:
                    continue
                seen.add(domain)
                companies.append(
                    Company(
                        domain=domain,
                        name=c.get("name"),
                        description=c.get("description"),
                        size=c.get("size"),
                        country=c.get("primaryCountry"),
                        relevance=row.get("relevance"),
                        score=row.get("score"),
                    )
                )
                if len(companies) >= max_results:
                    break

            offset += page_size
            if offset >= total:
                break

        log.info("Ocean.io: %d lookalike companies for %s", len(companies), seed_domain)
        return companies
