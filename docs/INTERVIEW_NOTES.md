# Interview notes (your cheat-sheet)

The interview is: run it live → walk the code → edge-case questions → a live
tweak → be honest. This file preps all five. **Read the code until you can
explain every file in your own words — that matters more than the code itself.**

## Live demo script (90 seconds)

1. `python run.py run --domain stripe.com --mock`
   — narrate the funnel table: "10 lookalikes → decision-makers → emails →
   here's the safety checkpoint." Show it stops and asks before sending.
2. `python run.py run --domain <a real customer domain> --dry-run`
   — now it's hitting Ocean + Prospeo + Eazyreach for real, but Brevo is
   simulated. Show the same checkpoint with *real* names/emails.
3. (If you're confident) drop `--dry-run` and send one real email to yourself /
   a teammate by setting `--max-emails 1` and a known domain.

Always have `--mock` ready as a fallback if a key or the network misbehaves
on the day. A clean mock run beats a half-broken live one.

## Walking the code (the 30-second map)

"It's a series of typed transformations: domain → Company → Prospect → Contact
→ EmailResult, defined in `models.py`. Each stage is one client module with one
method. `base.py` holds the shared HTTP — retry, backoff, rate-limit, errors —
so the stage clients stay tiny. `pipeline.py` orchestrates and isolates
failures; `cli.py` is the UX and the safety checkpoint."

## Edge-case answers (they will ask these)

* **Rate limits?** Per-client min-interval throttle + retry on 429 with
  exponential backoff, and I honour the server's `Retry-After` header. 4xx
  (other than 429) isn't retried — it's a real error, retrying wastes credits.
* **De-duplication?** Companies by domain, people by person_id/LinkedIn URL,
  emails within a run, and a persistent suppression list so re-runs never
  double-email anyone.
* **Undeliverable / no email found?** Eazyreach 404 or empty → that prospect is
  skipped and counted, run continues. Only `VERIFIED`/`UNKNOWN` get mailed. Brevo
  send failures are captured per-recipient in `results.csv`, not fatal.
* **Partial failure mid-run?** State is checkpointed after each stage; `--resume
  <id>` continues without re-spending credits.
* **A company has 50 VPs?** `--max-prospects-per-company` caps it; `--max-emails`
  caps the whole run.
* **Why split sourcing from sending?** So a human approves the recipient list
  before the one irreversible action (sending) happens.

## If they ask for a live tweak — where things are

* **Change who we target** → `clients/prospeo.py`, `DEFAULT_SENIORITIES`
  (valid values: Founder/Owner, C-Suite, Partner, Vice President, Head,
  Director, Manager, Senior, Entry, Intern). Or pass `--seniority`.
* **Change the email copy** → `emailing.py`, `render_email()`.
* **Add a filter** (e.g. only US companies, company size) → Ocean's
  `companiesFilters` in `clients/ocean.py` (it supports `companySizes`,
  `revenues`, country, etc.).
* **Add a new stage** (e.g. write to a CRM, or log to a sheet) → new client +
  one call in `pipeline.py`. The typed models make the seam obvious.
* **Make sends concurrent** → `pipeline.dispatch()` loop is where a worker pool
  would go; throttling already lives in `base.py`.

## The honesty points (these *earn* trust)

* "Prospeo deprecated `/domain-search` on 1 March 2026, so I used their current
  **Search Person API** with a company-website filter." (Shows you read the docs,
  didn't copy a stale tutorial.)
* "Eazyreach's API is behind the dashboard login — I couldn't verify the exact
  shape publicly, so I isolated all of it into `clients/eazyreach.py` + `.env`
  and added a mock + a defensive response parser. Point me at the dashboard and
  I'll confirm the three values in a minute." (Shows judgment under uncertainty.)
* If something breaks live: "Here's what I'd check first…" and reach for
  `--verbose` and `--mock`. Calm debugging reads better than a perfect run.

## What I'd build next (shows product thinking)

* Reply/bounce handling via Brevo webhooks → auto-update suppression.
* A/B subject lines with open-rate tracking.
* Per-domain send throttling + warm-up schedule for the new domain.
* SQLite contact store so multiple runs share state.
* A few unit tests using the mock clients (the pipeline is built to allow this).
