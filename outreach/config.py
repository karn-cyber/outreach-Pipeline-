"""Central configuration.

All secrets and tunables come from the environment (loaded from a local `.env`
file). Nothing is ever hard-coded in source, so the repo is safe to share and
keys are easy to rotate.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Stage 1 — Company discovery
    # Ocean.io lookalike search requires a paid plan; Apollo.io free tier works
    # as a drop-in replacement (sign up at app.apollo.io, no credit card needed).
    ocean_api_token: str = ""
    apollo_api_key: str = ""       # fallback when Ocean.io plan is insufficient

    # Stage 2 — Prospeo
    prospeo_api_key: str = ""

    # Stage 3 — Email resolution
    # Vocallabs FAQ (June 2026): use Prospeo as a drop-in Eazyreach replacement.
    # When use_prospeo_email=True, Stage 3 calls Prospeo's /linkedin-email-finder
    # instead of Eazyreach — no extra key needed, reuses PROSPEO_API_KEY.
    use_prospeo_email: bool = True

    # Legacy Eazyreach settings (kept for backwards compatibility)
    eazyreach_api_key: str = ""
    eazyreach_base_url: str = "https://api.eazyreach.app"
    eazyreach_resolve_path: str = "/v1/enrich/email"
    eazyreach_auth_style: str = "api-key"
    eazyreach_mock: bool = False

    # Stage 4 — Brevo
    brevo_api_key: str = ""

    # Sender identity (must be a verified sender on your domain)
    sender_email: str = ""
    sender_name: str = "Outreach"
    reply_to_email: str = ""

    # Demo safety: when set, every Stage-4 send is redirected to this single
    # inbox (real sourcing + real copy, just delivered to you). Leave blank
    # for a true campaign that mails the actual prospects.
    test_recipient: str = ""

    # Personalisation
    your_company_name: str = "Acme"
    your_one_liner: str = "we help teams automate their cold outreach"
    your_calendar_link: str = ""

    # Credit-aware defaults — free Apollo (85/mo) + Prospeo (50/day) burn fast.
    # Keep demo runs small; bump these only for a real campaign.
    default_min_score: float = 0.80
    default_max_companies: int = 3
    default_max_prospects_per_company: int = 2
    default_max_emails: int = 5

    def missing_keys_for_real_run(self) -> list[str]:
        """Return human-readable names of credentials required for a live run."""
        missing: list[str] = []
        if not self.ocean_api_token and not self.apollo_api_key:
            missing.append("OCEAN_API_TOKEN or APOLLO_API_KEY (Stage 1 — company discovery)")
        if not self.prospeo_api_key:
            missing.append("PROSPEO_API_KEY")
        # Stage 3 is satisfied by Prospeo (use_prospeo_email=True) OR a real Eazyreach key
        if not self.use_prospeo_email and not self.eazyreach_mock and not self.eazyreach_api_key:
            missing.append("EAZYREACH_API_KEY (or set USE_PROSPEO_EMAIL=true)")
        if not self.brevo_api_key:
            missing.append("BREVO_API_KEY")
        if not self.sender_email:
            missing.append("SENDER_EMAIL")
        return missing


settings = Settings()
