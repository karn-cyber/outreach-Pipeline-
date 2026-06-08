"""The typed data that flows through the pipeline.

The whole pipeline is just a series of transformations between these models:

    seed domain
        -> Company        (Stage 1: Ocean.io)
        -> Prospect       (Stage 2: Prospeo)
        -> Contact        (Stage 3: Eazyreach, adds a verified email)
        -> EmailResult    (Stage 4: Brevo)

Using Pydantic means malformed upstream data is caught at the stage boundary
(with a clear error) instead of blowing up deep inside the send loop.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Company(BaseModel):
    """A lookalike company discovered from the seed domain."""

    domain: str
    name: Optional[str] = None
    description: Optional[str] = None
    size: Optional[str] = None
    country: Optional[str] = None
    relevance: Optional[str] = None  # Ocean's A/B/C relevance bucket
    score: Optional[float] = None


class Prospect(BaseModel):
    """A decision-maker at a company, with a LinkedIn URL but no email yet."""

    person_id: Optional[str] = None
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    title: Optional[str] = None
    seniority: Optional[str] = None
    linkedin_url: Optional[str] = None
    company_domain: str
    company_name: Optional[str] = None

    def display_name(self) -> str:
        return self.full_name or self.first_name or "there"


class Contact(Prospect):
    """A prospect upgraded with a resolved, verified work email."""

    email: str
    email_status: str = "UNKNOWN"  # VERIFIED | RISKY | UNKNOWN
    email_source: str = "eazyreach"

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError(f"not a valid email: {v!r}")
        return v


class EmailResult(BaseModel):
    """The outcome of attempting to send to one contact."""

    email: str
    name: Optional[str] = None
    company_domain: Optional[str] = None
    sent: bool = False
    dry_run: bool = False
    message_id: Optional[str] = None
    subject: Optional[str] = None
    error: Optional[str] = None
    at: str = Field(default_factory=_utcnow)


class StageStats(BaseModel):
    name: str
    inputs: int = 0
    outputs: int = 0
    skipped: int = 0
    errors: int = 0
    notes: list[str] = Field(default_factory=list)


class RunState(BaseModel):
    """Everything about a single run — persisted so a run can resume."""

    run_id: str
    seed_domain: str
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)

    companies: list[Company] = Field(default_factory=list)
    prospects: list[Prospect] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    results: list[EmailResult] = Field(default_factory=list)

    stages: list[StageStats] = Field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = _utcnow()
