# Resume Entry — Outreach Pipeline

## Primary version (3 bullets, matches LXOS density)

**Outreach Pipeline — Autonomous Cold-Outreach Engine** — Python, Flask, Server-Sent Events, REST API Integration, Apollo.io, Prospeo, Brevo, Pydantic, Gunicorn · ↗
outreach.neelanshukarn.online                                          **Founder & Full-Stack Owner**

- Designed and shipped a **fully autonomous 4-stage outreach engine** — one company domain in, personalised verified-email outreach out — chaining **company discovery → decision-maker sourcing → email resolution → personalised send** across **4 integrated third-party APIs** with **zero manual steps** and a single human-approval checkpoint before any email fires.
- Engineered a **fault-tolerant integration layer** — exponential-backoff retries honouring `Retry-After`, per-provider rate-limit throttling, typed error handling, and **multi-provider fallback** (auto-switches Ocean.io → Apollo.io on plan limits) — cutting Stage-1 sourcing to **1 API credit per run** and making partial failures non-fatal (skips bad records, never crashes the run).
- Built **two interfaces over one core engine** — a CLI and a **real-time web app (Flask + Server-Sent Events)** that streams every stage live — plus an idempotent **resumable run-state store** and a **cross-run suppression list** guaranteeing no contact is emailed twice; **deployed to production** on a custom domain (Render + Cloudflare, HTTPS).

---

## Compact version (2 bullets, if space-constrained)

**Outreach Pipeline — Autonomous Cold-Outreach Engine** — Python, Flask, SSE, REST APIs, Apollo.io, Prospeo, Brevo · ↗ outreach.neelanshukarn.online        **Founder & Full-Stack Owner**

- Shipped a **fully autonomous 4-stage outreach engine** (company discovery → decision-makers → verified emails → personalised send) across **4 third-party APIs**, **one domain in, zero manual steps**, with a human-approval gate before any send.
- Engineered **fault-tolerant, credit-aware integrations** — backoff retries, rate-limit throttling, **multi-provider fallback**, **1 API credit/run** sourcing, cross-run de-duplication — and a **real-time web app (Flask + Server-Sent Events)** deployed to production on a custom domain.

---

## ATS keywords covered
Python · Flask · Server-Sent Events (SSE) · REST API integration · third-party API
integration · fault tolerance · retry / exponential backoff · rate limiting ·
multi-provider fallback · idempotency · resumable state · de-duplication ·
email deliverability · real-time streaming · Gunicorn · Render · Cloudflare ·
CI/deployment · modular architecture · Pydantic · full-stack.

## Notes
- Swap **"Founder & Full-Stack Owner"** for "Full-Stack Engineer" if you prefer.
- Keep the live link — a working, deployed URL recruiters can click is a huge
  differentiator and signals "this person ships."
