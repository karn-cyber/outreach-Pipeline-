"""Stage 3 replacement — Prospeo Person Enrichment (email resolution).

Vocallabs FAQ note (June 2026):
    "Use Prospeo itself as a replacement for Eazyreach to find people
     (and their LinkedIn and email IDs)."

Uses Prospeo's current /enrich-person endpoint (the /linkedin-email-finder
endpoint was deprecated and removed in 2026).

Current endpoint (verified against prospeo.io/api-docs):
    POST https://api.prospeo.io/enrich-person
    Header: X-KEY: <key>
    Body:   {"data": {
                "linkedin_url": "https://linkedin.com/in/...",   ← primary
                "first_name": "...", "last_name": "...",
                "company_website": "domain.com"
            }}
    Returns {"response": {"email": {"value": "...", "type": "professional"}, ...}}

Minimum required: linkedin_url alone  OR  (first/last name + company identifier).
"""
from __future__ import annotations

from typing import Optional

from .base import BaseClient, NotFoundError
from .eazyreach import _to_contact
from ..logging_conf import get_logger
from ..models import Contact, Prospect

log = get_logger(__name__)


class ProspeoEmailClient(BaseClient):
    """Drop-in replacement for EazyreachClient — uses Prospeo's email finder."""

    def __init__(self, api_key: str) -> None:
        super().__init__(
            base_url="https://api.prospeo.io",
            headers={"X-KEY": api_key, "Content-Type": "application/json"},
            name="Prospeo(email)",
            min_interval=0.6,
        )

    def resolve_email(self, prospect: Prospect) -> Optional[Contact]:
        """Prospect → verified work email via Prospeo /enrich-person."""
        # Build the data payload — more fields = better accuracy
        data_payload: dict = {}

        if prospect.linkedin_url:
            data_payload["linkedin_url"] = prospect.linkedin_url
        if prospect.first_name:
            data_payload["first_name"] = prospect.first_name
        if prospect.last_name:
            data_payload["last_name"] = prospect.last_name
        if prospect.company_domain:
            data_payload["company_website"] = prospect.company_domain
        if prospect.company_name:
            data_payload["company_name"] = prospect.company_name

        # Need at least: linkedin_url  OR  (name + company identifier)
        has_linkedin = bool(data_payload.get("linkedin_url"))
        has_name_co  = bool(
            (data_payload.get("first_name") or data_payload.get("last_name"))
            and (data_payload.get("company_website") or data_payload.get("company_name"))
        )
        if not has_linkedin and not has_name_co:
            log.info("Prospeo(email): not enough data to enrich %s", prospect.display_name())
            return None

        try:
            data = self.request("POST", "/enrich-person", json={"data": data_payload})
        except NotFoundError:
            log.info("Prospeo(email): no result for %s", prospect.display_name())
            return None

        email = _extract_email(data)
        if not email:
            log.info("Prospeo(email): no email in response for %s", prospect.display_name())
            return None

        log.info("Prospeo(email): %s -> %s", prospect.display_name(), email)
        return _to_contact(prospect, email, _extract_status(data), source="prospeo-enrich")


# ── Response parsers ─────────────────────────────────────────────────────────

def _extract_email(data: dict) -> str:
    """Parse email value from any Prospeo response shape.

    /enrich-person returns:  data["person"]["email"]["email"]
    Older endpoints returned: data["response"]["email"]["value"]
    We check all containers to handle both shapes.
    """
    if not isinstance(data, dict):
        return ""
    # Check every plausible container in priority order
    containers = [
        data.get("person"),          # /enrich-person  ← primary
        data.get("response"),        # older endpoints
        data,
        data.get("data"),
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        field = container.get("email")
        if isinstance(field, dict):
            # /enrich-person: {"email": "shola@paystack.com", "status": "VERIFIED", "revealed": true}
            # Only use when revealed=True (Prospeo hides email when over quota)
            if field.get("revealed") is False:
                continue
            direct = field.get("email", "")
            if direct and "@" in str(direct):
                return str(direct).strip().lower()
            # older shape: {"value": "...", "type": "..."}
            value = field.get("value", "")
            if value and "@" in str(value):
                return str(value).strip().lower()
        if isinstance(field, str) and "@" in field:
            return field.strip().lower()
    return ""


def _extract_status(data: dict) -> str:
    """Parse verification status from any Prospeo response shape."""
    containers = [data.get("person"), data.get("response"), data]
    for container in containers:
        if not isinstance(container, dict):
            continue
        field = container.get("email")
        if isinstance(field, dict):
            # /enrich-person: {"status": "VERIFIED", "revealed": true, ...}
            status = field.get("status", "")
            if status:
                return str(status).upper()
            # older shape: {"type": "professional"}
            t = field.get("type", "")
            if t in ("professional", "personal"):
                return "VERIFIED"
    return "UNKNOWN"
