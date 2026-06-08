"""Fully offline mocks for stages 1 and 2.

`--mock` swaps in these (plus the mock Eazyreach and dry-run Brevo) so the
end-to-end flow — including the safety checkpoint and the summary tables — can
be demonstrated with no API keys and no network. Great for rehearsing the demo
or showing the UX before credits are wired up.
"""
from __future__ import annotations

from .models import Company, Prospect


class MockOceanClient:
    name = "Ocean.io(mock)"

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def find_lookalikes(self, seed_domain, *, max_results=10, min_score=0.8):
        base = [
            ("northwind.io", "Northwind", "Sales automation for mid-market B2B", "51-200", "us"),
            ("acmedata.com", "Acme Data", "Data enrichment APIs", "11-50", "us"),
            ("flowleads.co", "FlowLeads", "Outbound lead gen platform", "11-50", "gb"),
            ("pipelinely.com", "Pipelinely", "Pipeline analytics", "51-200", "us"),
            ("reachgrid.ai", "ReachGrid", "AI SDR tooling", "1-10", "in"),
        ]
        out = []
        for i, (dom, nm, desc, size, country) in enumerate(base[:max_results]):
            out.append(
                Company(domain=dom, name=nm, description=desc, size=size,
                         country=country, relevance="A", score=0.9 - i * 0.02)
            )
        return out


class MockProspeoClient:
    name = "Prospeo(mock)"

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def find_decision_makers(self, company, *, seniorities=None, max_results=3):
        people = [
            ("Jordan Lee", "Jordan", "Lee", "CEO", "C-Suite"),
            ("Sam Rivera", "Sam", "Rivera", "VP Sales", "Vice President"),
            ("Priya Nair", "Priya", "Nair", "Co-founder", "Founder/Owner"),
        ]
        out = []
        for i, (full, first, last, title, sen) in enumerate(people[:max_results]):
            slug = f"{first}-{last}".lower()
            out.append(
                Prospect(
                    person_id=f"mock-{company.domain}-{i}",
                    full_name=full, first_name=first, last_name=last,
                    title=title, seniority=sen,
                    linkedin_url=f"https://www.linkedin.com/in/{slug}",
                    company_domain=company.domain, company_name=company.name,
                )
            )
        return out
