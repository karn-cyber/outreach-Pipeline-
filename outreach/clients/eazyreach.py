"""Stage 3 — Eazyreach: LinkedIn profile URL -> verified work email.

HONEST NOTE: Eazyreach publishes its API behind the dashboard login, so unlike
the other three stages I couldn't verify the exact endpoint/auth/response shape
from public docs. Rather than hard-code a guess everywhere, ALL Eazyreach
specifics live in this one file and in `.env`:

    EAZYREACH_BASE_URL       e.g. https://api.eazyreach.app
    EAZYREACH_RESOLVE_PATH   e.g. /v1/enrich/email
    EAZYREACH_AUTH_STYLE     api-key | x-api-key | bearer

Open your Eazyreach dashboard (Settings -> API / Developers), confirm those
three values, and the rest of the pipeline doesn't change. The response parser
below is defensive: it accepts the email under any of several common key names.

If you haven't wired the real API yet, set EAZYREACH_MOCK=true (or pass --mock)
to use `MockEazyreachClient`, which fabricates a plausible address from the
person's name + company domain so you can still demo the full chain.
"""
from __future__ import annotations

import re
from typing import Optional

from .base import BaseClient, NotFoundError
from ..logging_conf import get_logger
from ..models import Contact, Prospect

log = get_logger(__name__)

# Keys we'll look for in the response, in priority order.
_EMAIL_KEYS = ("email", "work_email", "professional_email", "verified_email", "emailAddress")
_STATUS_KEYS = ("status", "email_status", "verification", "result")


def _auth_headers(api_key: str, style: str) -> dict[str, str]:
    style = (style or "api-key").lower()
    if style == "bearer":
        return {"Authorization": f"Bearer {api_key}"}
    if style == "x-api-key":
        return {"x-api-key": api_key}
    return {"api-key": api_key}


class EazyreachClient(BaseClient):
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        resolve_path: str,
        auth_style: str = "api-key",
    ) -> None:
        super().__init__(
            base_url=base_url,
            headers={**_auth_headers(api_key, auth_style), "Content-Type": "application/json"},
            name="Eazyreach",
            min_interval=0.4,
        )
        self.resolve_path = resolve_path

    def resolve_email(self, prospect: Prospect) -> Optional[Contact]:
        if not prospect.linkedin_url:
            return None
        try:
            data = self.request(
                "POST", self.resolve_path, json={"linkedin_url": prospect.linkedin_url}
            )
        except NotFoundError:
            log.info("Eazyreach: no email for %s", prospect.display_name())
            return None

        email = _extract_email(data)
        if not email:
            log.info("Eazyreach: no email returned for %s", prospect.display_name())
            return None

        return _to_contact(prospect, email, _extract_status(data), source="eazyreach")


class MockEazyreachClient:
    """Deterministic fake so the full pipeline can run without credits/keys."""

    name = "Eazyreach(mock)"

    def close(self) -> None:  # match the BaseClient interface
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def resolve_email(self, prospect: Prospect) -> Optional[Contact]:
        first = (prospect.first_name or (prospect.full_name or "lead").split(" ")[0]).lower()
        last_parts = (prospect.full_name or "").split(" ")
        last = (prospect.last_name or (last_parts[-1] if len(last_parts) > 1 else "")).lower()
        local = f"{first}.{last}".strip(".") or "hello"
        local = re.sub(r"[^a-z0-9.]", "", local)
        email = f"{local}@{prospect.company_domain}"
        log.info("Eazyreach(mock): %s -> %s", prospect.display_name(), email)
        return _to_contact(prospect, email, "VERIFIED", source="eazyreach-mock")


# --- shared parsing helpers -------------------------------------------------

def _extract_email(data) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    # response might be flat or wrapped under data/result/person
    for container in (data, data.get("data"), data.get("result"), data.get("person")):
        if not isinstance(container, dict):
            continue
        for k in _EMAIL_KEYS:
            v = container.get(k)
            if isinstance(v, str) and "@" in v:
                return v.strip().lower()
    return None


def _extract_status(data) -> str:
    if not isinstance(data, dict):
        return "UNKNOWN"
    for container in (data, data.get("data"), data.get("result")):
        if not isinstance(container, dict):
            continue
        for k in _STATUS_KEYS:
            v = container.get(k)
            if isinstance(v, str):
                return v.upper()
    return "UNKNOWN"


def _to_contact(prospect: Prospect, email: str, status: str, *, source: str) -> Optional[Contact]:
    try:
        return Contact(
            **prospect.model_dump(),
            email=email,
            email_status=status,
            email_source=source,
        )
    except Exception as exc:  # invalid email shape, etc.
        log.warning("Eazyreach: discarding invalid email %r (%s)", email, exc)
        return None
