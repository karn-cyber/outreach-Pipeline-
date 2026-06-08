"""Stage 4 — Brevo: send the personalised outreach email.

Endpoint (verified against developers.brevo.com):
    POST https://api.brevo.com/v3/smtp/email
    Headers: api-key: <key>, content-type: application/json
    Body:   {"sender": {"email","name"}, "to": [{"email","name"}],
             "subject": "...", "htmlContent": "...", "textContent": "...",
             "replyTo": {"email"}}
    Returns {"messageId": "..."}

The sender address must be a *verified* sender on your domain, or Brevo rejects
the send. `verify_sender()` does a preflight so we fail loudly at setup time
rather than silently at send time.
"""
from __future__ import annotations

from typing import Optional

from .base import ApiError, BaseClient
from ..logging_conf import get_logger
from ..models import Contact, EmailResult

log = get_logger(__name__)


class BrevoClient(BaseClient):
    def __init__(
        self,
        api_key: str,
        sender_email: str,
        sender_name: str,
        reply_to: str = "",
        test_recipient: str = "",
    ) -> None:
        super().__init__(
            base_url="https://api.brevo.com",
            headers={"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            name="Brevo",
            min_interval=0.3,
        )
        self.sender_email = sender_email
        self.sender_name = sender_name
        self.reply_to = reply_to or sender_email
        # Demo safety net: when set, EVERY send is redirected to this single
        # inbox instead of the real prospect. The pipeline still sources real
        # people and builds their real personalised copy — only the delivery
        # address changes — so you can demo a live send without emailing strangers.
        self.test_recipient = (test_recipient or "").strip()

    def verify_account(self) -> Optional[str]:
        """Confirm the API key works. Returns the account email on success."""
        try:
            data = self.request("GET", "/v3/account")
            return data.get("email")
        except ApiError as exc:
            log.warning("Brevo account check failed: %s", exc)
            return None

    def send(self, contact: Contact, subject: str, html: str, text: str) -> EmailResult:
        # Decide the real destination
        if self.test_recipient:
            to_email = self.test_recipient
            # Address it to the real PERSON's name so the inbox looks genuine
            to_name = contact.display_name()
            role = contact.title or "decision-maker"
            company = contact.company_name or contact.company_domain
            # Put the full intended-recipient context in the subject…
            subject = f"To {contact.display_name()} ({contact.email}) — {subject}"
            # …and a clear banner at the top of the body
            banner = (
                f"REAL RECIPIENT: {contact.display_name()} &lt;{contact.email}&gt; — "
                f"{role} at {company}. "
                f"In a live campaign this email is delivered straight to them; "
                f"you are seeing it because demo mode routes every send to your inbox."
            )
            banner_txt = (
                f"REAL RECIPIENT: {contact.display_name()} <{contact.email}> — {role} at {company}.\n"
                f"In a live campaign this goes straight to them; demo mode routes it to your inbox."
            )
            text = f"{banner_txt}\n{'─' * 60}\n\n{text}"
            html = (
                f'<div style="background:#fffbeb;border:1px solid #fcd34d;color:#92400e;'
                f'padding:12px 16px;border-radius:10px;font-family:Arial,Helvetica,sans-serif;'
                f'font-size:13px;line-height:1.5;margin-bottom:18px">{banner}</div>{html}'
            )
            log.info("Brevo: TEST redirect — %s <%s> → %s",
                     contact.display_name(), contact.email, to_email)
        else:
            to_email = contact.email
            to_name = contact.display_name()

        body = {
            "sender": {"email": self.sender_email, "name": self.sender_name},
            "to": [{"email": to_email, "name": to_name}],
            "replyTo": {"email": self.reply_to},
            "subject": subject,
            "htmlContent": html,
            "textContent": text,
            "tags": ["outreach-pipeline"] + (["demo-redirect"] if self.test_recipient else []),
        }
        try:
            data = self.request("POST", "/v3/smtp/email", json=body)
            return EmailResult(
                email=contact.email,          # report the *intended* recipient
                name=contact.display_name(),
                company_domain=contact.company_domain,
                sent=True,
                message_id=data.get("messageId"),
                subject=subject,
            )
        except ApiError as exc:
            return EmailResult(
                email=contact.email,
                name=contact.display_name(),
                company_domain=contact.company_domain,
                sent=False,
                subject=subject,
                error=str(exc),
            )


class DryRunBrevoClient:
    """Simulates a send: validates the contact, prints, but never calls Brevo."""

    name = "Brevo(dry-run)"

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def verify_account(self) -> Optional[str]:
        return "dry-run@local"

    def send(self, contact: Contact, subject: str, html: str, text: str) -> EmailResult:
        log.info("Brevo(dry-run): would send to %s — %r", contact.email, subject)
        return EmailResult(
            email=contact.email,
            name=contact.display_name(),
            company_domain=contact.company_domain,
            sent=False,
            dry_run=True,
            subject=subject,
        )
