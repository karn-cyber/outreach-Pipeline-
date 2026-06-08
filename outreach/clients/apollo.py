"""Stage 1 alternative — Apollo.io lookalike company discovery.

Ocean.io's lookalike search requires a paid plan. Per Vocallabs FAQ:
"you can use any ocean.io's alternative."

Apollo.io free plan (https://app.apollo.io/#/register, no credit card) gives:
  ● GET /v1/organizations/enrich — company attributes from a domain
  ● POST /v1/mixed_companies/search — search companies by industry + size

Two-step lookalike process:
  1. Enrich the seed domain → extract industry, size, keywords
  2. Search for companies matching those attributes → return domains

Note: /mixed_companies/search requires a paid plan on Apollo.io.
      /v1/organizations/search works on the free tier and is used here.

Auth: pass api_key in the JSON body (Apollo's preferred method for free plans).
"""
from __future__ import annotations

import re
from typing import Any

from .base import ApiError, BaseClient
from .ocean import OceanClient
from ..logging_conf import get_logger
from ..models import Company

log = get_logger(__name__)


class ApolloClient(BaseClient):
    """Stage 1: Apollo.io — free-tier lookalike company discovery."""

    def __init__(self, api_key: str) -> None:
        super().__init__(
            base_url="https://api.apollo.io",
            headers={
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
                "X-Api-Key": api_key,   # Apollo requires key in header
            },
            name="Apollo.io",
            min_interval=0.6,
        )
        self._key = api_key

    def find_lookalikes(
        self,
        seed_domain: str,
        *,
        max_results: int = 10,
        min_score: float = 0.80,
    ) -> list[Company]:
        # Step 1: enrich seed domain — returns industry, keywords, AND ecosystem companies
        attrs = self._enrich(seed_domain)
        log.info(
            "Apollo.io seed: %s → industry=%r employees=%s eco_companies=%d",
            seed_domain, attrs.get("industry"), attrs.get("num_employees"),
            len(attrs.get("ecosystem_domains", [])),
        )

        # Step 2: use ecosystem companies as lookalikes (much more relevant than keyword search)
        companies = self._build_from_ecosystem(attrs, seed_domain, max_results)

        # Step 3: if ecosystem didn't give enough, top up with keyword search
        if len(companies) < max_results:
            extra = self._search_similar(attrs, seed_domain, max_results - len(companies),
                                          exclude={c.domain for c in companies})
            companies.extend(extra)

        log.info("Apollo.io: %d lookalike companies for %s", len(companies), seed_domain)
        return companies

    # ── Private ──────────────────────────────────────────────────────────────

    def _enrich(self, domain: str) -> dict:
        try:
            data = self.request(
                "GET", "/v1/organizations/enrich",
                params={"domain": domain},
            )
            org: dict = data.get("organization") or {}
            eco = _extract_ecosystem_domains(org)
            return {
                "industry":          org.get("industry") or "",
                "industry_tag_id":   org.get("industry_tag_id") or "",
                "num_employees":     int(org.get("estimated_num_employees") or 0) or 100,
                "keywords":          org.get("keywords") or [],
                "name":              org.get("name") or "",
                "ecosystem_domains": eco,
            }
        except ApiError as exc:
            log.warning("Apollo.io enrich failed (%s), using defaults.", exc)
            return {"industry": "", "industry_tag_id": "", "num_employees": 100,
                    "keywords": [], "name": "", "ecosystem_domains": []}

    def _employee_ranges(self, n: int) -> list[str]:
        """Map employee count to Apollo's range strings."""
        if n < 10:    return ["1,10"]
        if n < 50:    return ["1,50", "11,50"]
        if n < 200:   return ["51,200", "51,500"]
        if n < 1000:  return ["201,1000", "501,1000"]
        if n < 5000:  return ["1001,5000"]
        return ["5001,10000", "10001,100000"]

    def _build_from_ecosystem(
        self, attrs: dict, seed_domain: str, max_results: int
    ) -> list[Company]:
        """Build lookalikes from the seed's ecosystem — ZERO extra Apollo credits.

        The seed's single enrich response already contains suborganizations
        (with names) and technology_names. We use those directly instead of
        enriching each candidate domain, so a whole run costs exactly 1 Apollo
        enrich credit regardless of how many companies we surface.
        """
        eco = attrs.get("ecosystem_domains", [])   # list of {domain, name}
        seen = {seed_domain.lower()}
        companies: list[Company] = []

        for item in eco:
            if len(companies) >= max_results:
                break
            domain = item.get("domain", "")
            if not domain or domain in seen:
                continue
            seen.add(domain)
            # Derive a readable name: given name, else the domain's main word
            name = item.get("name") or domain.split(".")[0].title()
            companies.append(Company(
                domain=domain,
                name=name,
                relevance="A",
                score=0.88,
            ))

        return companies

    def _search_similar(
        self,
        attrs: dict,
        seed_domain: str,
        max_results: int,
        exclude: set | None = None,
    ) -> list[Company]:
        body: dict[str, Any] = {
            "page": 1,
            "per_page": min(max_results * 2 + 5, 25),
            "organization_num_employees_ranges": self._employee_ranges(attrs["num_employees"]),
        }

        # Strategy: company-name search using the seed's primary domain word
        # (e.g. "stripe" → finds squareup, braintree, adyen etc.)
        # Combined with industry tag for precision.
        seed_word = seed_domain.split(".")[0]   # "stripe" from "stripe.com"
        body["q_organization_name"] = seed_word

        # Use the exact industry tag ID if available — much more precise than string
        tag_id = attrs.get("industry_tag_id", "")
        if tag_id:
            body["organization_industry_tag_ids"] = [tag_id]

        # Exclude the seed itself
        body["q_not_organization_domains"] = [seed_domain]

        try:
            # /v1/organizations/search works on free plan;
            # /v1/mixed_companies/search requires paid plan
            data = self.request("POST", "/v1/organizations/search", json=body)
        except ApiError as exc:
            log.warning("Apollo.io search failed: %s", exc)
            return []

        companies: list[Company] = []
        seen: set[str] = (exclude or set()) | {seed_domain.lower()}

        for org in (data.get("organizations") or data.get("accounts") or []):
            domain = _extract_domain(org)
            if not domain or domain == seed_domain.lower() or domain in seen:
                continue
            seen.add(domain)

            companies.append(Company(
                domain=domain,
                name=org.get("name"),
                description=org.get("short_description"),
                size=str(org.get("estimated_num_employees", "")),
                country=org.get("country"),
                relevance="A",
                score=0.85,
            ))

            if len(companies) >= max_results:
                break

        return companies


# ── Fallback wrapper ─────────────────────────────────────────────────────────

class FallbackStage1Client:
    """Tries Ocean.io first; silently falls back to Apollo.io on plan errors.

    This is the single client both cli.py and server.py use for Stage 1.
    The caller never needs to know which provider actually ran.
    """

    name = "Stage1"

    def __init__(self, ocean_token: str | None, apollo_key: str | None) -> None:
        self._ocean = OceanClient(ocean_token) if ocean_token else None
        self._apollo = ApolloClient(apollo_key) if apollo_key else None
        self._provider = "none"

    @property
    def active_provider(self) -> str:
        return self._provider

    def find_lookalikes(
        self,
        seed_domain: str,
        *,
        max_results: int = 10,
        min_score: float = 0.80,
    ) -> list[Company]:
        # Try Ocean.io first
        if self._ocean:
            try:
                result = self._ocean.find_lookalikes(
                    seed_domain, max_results=max_results, min_score=min_score
                )
                if result:
                    self._provider = "Ocean.io"
                    return result
            except ApiError as exc:
                if "Plan version" in str(exc) or "plan" in str(exc).lower():
                    log.info("Ocean.io plan limitation — switching to Apollo.io fallback.")
                else:
                    raise  # real error (auth, network) — don't swallow

        # Fallback to Apollo.io
        if self._apollo:
            result = self._apollo.find_lookalikes(seed_domain, max_results=max_results)
            self._provider = "Apollo.io"
            return result

        raise ApiError(
            "No Stage 1 provider available. "
            "Set OCEAN_API_TOKEN or APOLLO_API_KEY in .env"
        )

    def close(self) -> None:
        if self._ocean:
            self._ocean.close()
        if self._apollo:
            self._apollo.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ── Helpers ──────────────────────────────────────────────────────────────────

# Technology names that are clearly infrastructure/tools, not companies
_INFRA_NAMES = {
    "python","javascript","typescript","react","react native","next.js","node.js",
    "aws","amazon aws","amazon s3","amazon ec2","google cloud platform","azure",
    "kubernetes","docker","redis","mysql","mongodb","postgresql","nginx","kafka",
    "git","github","slack","jira","figma","grafana","datadog","splunk","tableau",
    "salesforce","hubspot","workday","marketo","stripe","stripe billing",
    "stripe connect","stripe terminal","css","html","rest","grpc","graphql","api",
    "php","ruby on rails","java","kotlin","swift","golang","scala","r",
    "tensorflow","pytorch","langchain","openai","chatgpt","anthropic claude",
    "android","ios","chrome","firefox","linux","macos","windows",
}

def _extract_ecosystem_domains(org: dict) -> list[dict]:
    """Extract {domain, name} of companies in the seed company's ecosystem.

    IMPORTANT (credit-saving): we capture the NAME here from the seed's single
    enrich response, so Stage 1 never has to spend an Apollo enrich credit per
    candidate company. One enrich call per run, full stop.

    Sources:
    1. suborganizations (acquired companies / subsidiaries) — have names + URLs
    2. technology_names filtered to company-like names (Affirm, Klarna, Shopify…)
    """
    seed_name = (org.get("name") or "").lower()
    operating: list[dict] = []   # independent companies — likely to have contacts
    acquired:  list[dict] = []   # subsidiaries/acquisitions — often no Prospeo records
    seen: set[str] = set()

    # Phrases that signal a company is a subsidiary of the seed (rarely has
    # its own decision-makers in a contact DB), so we search those LAST.
    _ACQ_HINTS = ("acquired", "joined", "a stripe company", "subsidiary")

    def _is_acquired(name: str) -> bool:
        n = (name or "").lower()
        if any(h in n for h in _ACQ_HINTS):
            return True
        # "Recko | A Stripe company", "TaxJar | A …" — seed name after a pipe/paren
        if seed_name and seed_name in n and n != seed_name:
            return True
        return False

    def _add(domain: str, name: str | None) -> None:
        if not domain or domain in seen:
            return
        seen.add(domain)
        entry = {"domain": domain, "name": name}
        (acquired if _is_acquired(name) else operating).append(entry)

    # 1. Suborganizations (subsidiaries / acquisitions) — name is already given
    for sub in (org.get("suborganizations") or []):
        url = sub.get("website_url") or ""
        if not url:
            continue
        d = _extract_domain({"website_url": url})
        _add(d, sub.get("name"))

    # 2. Technology names that look like real product companies, not infra.
    #    We drop anything containing infra keywords (DNS, CDN, cloud, hosting,
    #    analytics, etc.) — those are tools, not companies you'd email.
    _INFRA_KEYWORDS = (
        "dns", "cdn", "cloud", "hosting", "host", "analytics", "ssl", "tls",
        "cache", "server", "database", "db", "sdk", "api", "framework",
        "javascript", "css", "html", "compute", "storage", "kubernetes",
        "docker", "monitoring", "logging", "tag manager", "cookie", "captcha",
        "load balancer", "web", "http", "smtp", "email delivery", "fonts",
    )
    for tech in (org.get("technology_names") or []):
        t = tech.strip()
        tl = t.lower()
        if not t or tl in _INFRA_NAMES:
            continue
        if any(kw in tl for kw in _INFRA_KEYWORDS):
            continue
        if len(t) < 3 or len(t) > 40:
            continue
        words = t.split()
        if len(words) == 1:  # single-word brands resolve cleanly to a .com
            guessed = words[0].lower() + ".com"
            _add(guessed, t)

    # Operating companies first (best chance of real decision-makers),
    # subsidiaries last — so Stage 2 hits the good ones before any credit cap.
    return (operating + acquired)[:30]


def _extract_domain(org: dict) -> str:
    raw = (
        org.get("primary_domain")
        or org.get("website_url")
        or ""
    )
    raw = re.sub(r"^https?://", "", str(raw).lower())
    raw = raw.split("/")[0]
    raw = re.sub(r"^www\.", "", raw)
    return raw.strip()
