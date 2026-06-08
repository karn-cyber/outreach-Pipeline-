# Automated Cold-Outreach Pipeline

One seed domain in → personalised outreach emails out. Zero humans between the
stages.

```
company.domain
  → Ocean.io     lookalike companies        (similar firmographics)
  → Prospeo      decision-makers + LinkedIn  (C-suite / VP)
  → Eazyreach    LinkedIn URL → work email   (verified)
  → Brevo        personalised send
```

Every stage's output is the next stage's input. The only manual touchpoint is a
**safety checkpoint**: the program shows you exactly who it's about to email and
waits for a yes before anything sends.

---

## Quick start

```bash
# 1. install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. configure
cp .env.example .env          # then fill in your keys

# 3a. see it run end-to-end with NO keys / NO network (great first run + demo):
python run.py run --domain stripe.com --mock

# 3b. real sourcing + email resolution, but DON'T send (safe live test):
python run.py run --domain stripe.com --dry-run

# 3c. the real thing (asks you to confirm before sending):
python run.py run --domain stripe.com
```

## Modes

| Mode        | Ocean / Prospeo / Eazyreach | Brevo send | Needs keys? | Use for |
|-------------|-----------------------------|-----------|-------------|---------|
| `--mock`    | canned data (offline)       | simulated | no          | rehearsing the demo, UX |
| `--dry-run` | **real APIs**               | simulated | yes (not Brevo) | validating real data safely |
| (default)   | **real APIs**               | **real**  | yes         | actually sending |

## Useful flags

```
--domain / -d              seed domain (required)
--max-companies N          lookalikes to pull        (default from .env)
--max-prospects-per-company N
--max-emails N             hard cap on sends         (protects your domain)
--min-score 0.0-1.0        Ocean similarity threshold
--seniority "C-Suite"      repeatable; overrides the C-suite/VP default
--yes / -y                 skip the confirmation prompt (non-interactive)
--resume <run_id>          continue a previous run without re-spending credits
--verbose / -v
```

## What you get after a run

```
runs/<run_id>/state.json     full machine-readable state (resumable)
runs/<run_id>/contacts.csv    everyone we resolved an email for
runs/<run_id>/results.csv     send outcome per address
runs/suppression.json         global "already emailed" list (never double-mail)
```

## Project layout

```
run.py                     entrypoint
outreach/
  cli.py                   CLI, the safety checkpoint, reporting
  config.py                all settings from .env (no hard-coded secrets)
  models.py                typed data that flows between stages
  pipeline.py              orchestrator: runs the 4 stages, isolates failures
  store.py                 checkpointing + suppression list
  emailing.py              outreach copy / personalisation
  logging_conf.py          Rich logging
  mocks.py                 offline mocks for stages 1-2
  clients/
    base.py                shared HTTP: retry, backoff, rate-limit, errors
    ocean.py               Stage 1
    prospeo.py             Stage 2
    eazyreach.py           Stage 3 (+ mock)
    brevo.py               Stage 4 (+ dry-run)
docs/
  DESIGN.md                architecture & the "why" behind each decision
  INTERVIEW_NOTES.md       demo script + edge-case answers
```

## A note on each API (verified June 2026)

* **Ocean.io** — `POST /v2/search/companies`, header `x-api-token`, filter
  `companiesFilters.lookalikeDomains`.
* **Prospeo** — `POST /search-person`, header `X-KEY`, filter
  `company.websites.include` + `person_seniority.include`. Prospeo **removed**
  its old `/domain-search` endpoint on 1 March 2026; this code uses the current
  Search Person API. Email is *not* returned here — that's Eazyreach's job.
* **Eazyreach** — publishes its API behind the dashboard login, so the exact
  base URL / path / auth header are set in `.env`
  (`EAZYREACH_BASE_URL`, `EAZYREACH_RESOLVE_PATH`, `EAZYREACH_AUTH_STYLE`).
  Confirm them from your dashboard's API section; the rest of the code doesn't
  change. Set `EAZYREACH_MOCK=true` to fake just this stage while you wire it.
* **Brevo** — `POST /v3/smtp/email`, header `api-key`. The `SENDER_EMAIL` must
  be a **verified sender** on your domain or Brevo rejects the send.

## Troubleshooting

* *"Missing configuration"* — fill the listed keys in `.env`, or use `--mock`.
* *No lookalikes* — lower `--min-score` (e.g. `0.7`).
* *Brevo 401* — check `BREVO_API_KEY`; *send rejected* — verify your sender.
* *Eazyreach returns nothing* — confirm the endpoint/auth in `.env`; test with
  `EAZYREACH_MOCK=true` to isolate whether it's config or data.
# outreach-Pipeline-
