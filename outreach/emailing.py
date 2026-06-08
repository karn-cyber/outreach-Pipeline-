"""Outreach copy.

Kept in one module so the messaging is easy to find, review and tweak (the
interviewers may ask you to change copy on the spot). Two short variants are
rotated deterministically per contact, and every email is personalised with the
person's first name, their title, and their company.

Good cold email = short, specific, one clear ask. No walls of text.
"""
from __future__ import annotations

from .config import settings
from .models import Contact


def _first_name(contact: Contact) -> str:
    if contact.first_name:
        return contact.first_name
    if contact.full_name:
        return contact.full_name.split(" ")[0]
    return "there"


def _company(contact: Contact) -> str:
    return contact.company_name or contact.company_domain


def render_email(contact: Contact) -> tuple[str, str, str]:
    """Return (subject, html, text) personalised for this contact."""
    first = _first_name(contact)
    company = _company(contact)
    me = settings.your_company_name
    one_liner = settings.your_one_liner
    cal = settings.your_calendar_link

    # rotate variant deterministically so re-runs are stable
    variant = (sum(ord(c) for c in contact.email)) % 2

    if variant == 0:
        subject = f"Quick idea for {company}"
        opener = (
            f"Hi {first} — I came across {company} while looking at teams in your space "
            f"and your work as {contact.title or 'a leader there'} stood out."
        )
    else:
        subject = f"{company} + {me}?"
        opener = (
            f"Hi {first}, I'll keep this short. I lead {me}, and {company} looks like "
            f"exactly the kind of team we tend to help."
        )

    cta = (
        f"Worth a quick 15 minutes to see if it's relevant? {cal}"
        if cal
        else "Worth a quick 15 minutes to see if it's relevant? Happy to send a couple of times."
    )

    text = "\n\n".join(
        [
            opener,
            f"At {me}, {one_liner}.",
            cta,
            f"— {settings.sender_name}",
        ]
    )

    html = (
        "<html><body style=\"font-family:Arial,Helvetica,sans-serif;font-size:15px;"
        "color:#1a1a1a;line-height:1.5\">"
        f"<p>{opener}</p>"
        f"<p>At <strong>{me}</strong>, {one_liner}.</p>"
        f"<p>{cta}</p>"
        f"<p>— {settings.sender_name}</p>"
        "</body></html>"
    )
    return subject, html, text
