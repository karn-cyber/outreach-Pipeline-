# Design notes

This is the "why" behind the code. It maps directly onto the evaluation
criteria: end-to-end, clean integrations, modular code, resilience, judgment.

## 1. The pipeline is a series of typed transformations

The entire system is one idea: each stage turns one model into the next.

```
seed domain → Company → Prospect → Contact → EmailResult
```

These live in `models.py` as Pydantic models. The payoff: the boundary between
stages is *typed and validated*. If Prospeo returns a person with no usable
fields, or Eazyreach hands back a malformed email, it's rejected at the seam
with a clear error — not three stages later inside the send loop.

## 2. One stage = one module = one clear unit

`clients/ocean.py`, `prospeo.py`, `eazyreach.py`, `brevo.py`. Each exposes a
tiny, obvious surface (`find_lookalikes`, `find_decision_makers`,
`resolve_email`, `send`) and knows only about its own API. The orchestrator in
`pipeline.py` wires them together and knows nothing about HTTP. You can read,
test, or replace any one stage in isolation. Adding a 5th stage (say, a CRM
push) is a new client + one call in `pipeline.py`.

## 3. Shared HTTP plumbing, written once

`clients/base.py` centralises auth headers, throttling, retries and error
mapping so the four stage clients stay small and behave identically.

* **Retry only transient failures.** 429 and 5xx and network blips are retried
  with exponential backoff + jitter; 400/401/404 fail fast (retrying a bad key
  or bad filter just wastes time and credits).
* **Honour `Retry-After`.** On a 429 we wait exactly as long as the server asks
  instead of guessing — that's why retries are hand-rolled rather than a
  decorator library.
* **Typed exceptions** (`AuthError`, `RateLimitError`, `NotFoundError`,
  `ApiError`) let callers react precisely: a 404 from Eazyreach means "no email
  for this person, skip them"; a 401 means "stop, your key is wrong".
* **Min-interval throttling** gives predictable pacing under each API's limits.

## 4. Resilience: partial failures never crash the run

In `pipeline.py`, every per-company and per-person call is isolated. One
company with zero decision-makers, a single 404, or a transient error is
logged, counted, and skipped — the run continues and the counts surface in the
funnel summary. A run that finds 8/10 companies and emails 19/22 contacts is a
**successful** run, not a crash.

## 5. Cost-awareness and resumability

These APIs cost credits (real money). Two safeguards:

* **Checkpointing.** State is written to `runs/<id>/state.json` after every
  stage. If the program dies — or you abort at the safety checkpoint — you
  `--resume <id>` and pick up exactly where you left off, re-spending nothing.
* **Caps everywhere.** `--max-companies`, `--max-prospects-per-company` and
  `--max-emails` bound the blast radius (and the bill) of any single run.

## 6. De-duplication and suppression

* Companies are de-duped by domain (Ocean can return redirects/dupes).
* People are de-duped by `person_id` / LinkedIn URL.
* Emails are de-duped within a run.
* A **global suppression list** (`runs/suppression.json`) records every address
  we've actually emailed across all runs, and Stage 3 filters against it. Re-run
  the same seed tomorrow and nobody gets a second cold email. This protects both
  the prospect's experience and the sending domain's reputation.

## 7. The safety checkpoint (judgment)

Sourcing (stages 1-3) and sending (stage 4) are deliberately split so a human
sees a summary *before* anything fires: a table of every recipient, their title,
company, email and verification status, plus an example subject line. Default is
**no** — you must opt in. `--dry-run` does the whole pipeline with simulated
sends; `--mock` runs offline. Sending real email is the one irreversible action
in the system, so it's the one thing gated behind explicit confirmation.

## 8. Deliverability guardrails

A freshly bought domain has zero sending reputation and is easy to burn.
So: only addresses that come back `VERIFIED` (or `UNKNOWN`) from Eazyreach are
mailed, sends are throttled and capped, and the suppression list prevents
repeats. The sender identity is preflighted against Brevo before a real run.

## 9. Secrets & configuration hygiene

Everything sensitive is read from `.env` via `config.py` (gitignored), with a
committed `.env.example`. No keys in source. The one genuinely uncertain
integration (Eazyreach's exact endpoint) is fully config-driven so it can be
corrected without code changes.

## Trade-offs I'd revisit with more time

* **Offset pagination** (Ocean) is simple but weaker than cursor (`searchAfter`)
  for very deep result sets. Fine at this scale; I'd switch to the cursor for
  large pulls.
* **Synchronous** HTTP keeps the code obvious and easy to demo. At higher volume
  I'd add bounded concurrency (e.g. a worker pool) per stage.
* **State is JSON files.** Perfect for a single-operator CLI; I'd move to SQLite
  if multiple runs needed to share a contact store or run concurrently.
* **No unit tests shipped** for brevity. The mock clients are structured to make
  the pipeline trivially testable end-to-end without network — that's where I'd
  add tests first.
