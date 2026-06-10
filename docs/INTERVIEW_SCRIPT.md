# Interview Playbook — Automated Outreach Pipeline

> See also: **§11 Code walkthrough** (what to open & say) and
> **§12 Product roadmap** (auth + payments + the reach/collaborate/talk vision).

> The brief said *"This isn't an assignment. It's the job."* So the story to tell
> is not "I finished a task" — it's "I shipped a working product and made real
> engineering calls under real constraints." That mindset is what gets hired.

---

## 0. The one-line pitch (memorise this)

> "One seed domain in, personalised outreach out — fully autonomous. It finds
> lookalike companies, surfaces their decision-makers, resolves verified work
> emails, and sends personalised mail, with a single human checkpoint before
> anything fires. I built it to be resilient and credit-aware, because every API
> call is real money and every email touches a real person's reputation — and
> mine."

---

## 1. The build story (the narrative arc)

Tell it as a sequence of **decisions under constraints** — this is the gold.

1. **I read the whole flow first, then the docs, then wrote code.** The brief
   literally asks for that order. Four stages, each one's output is the next
   one's input: `domain → companies → people → emails → sent`.

2. **I modelled it as typed transformations.** Each stage turns one Pydantic
   model into the next (`Company → Prospect → Contact → EmailResult`). Bad data
   from an API gets caught at the *seam* with a clear error, not three stages
   later inside the send loop.

3. **Then reality hit, and I adapted — three times:**
   - **Ocean.io** lookalike search needs a *paid* plan. The free token only
     authenticates. → I built a **fallback to Apollo.io** behind one interface,
     so the pipeline doesn't care which provider answers.
   - **Eazyreach** couldn't give credits (Vocallabs' own FAQ said so mid-way). →
     I **swapped Stage 3 to Prospeo's enrichment** as a drop-in — same contract,
     different provider, one config flag.
   - **Free-tier limits everywhere** (Apollo 85/mo, Prospeo 50/day). → I made the
     whole thing **credit-aware**: 1 flat Apollo credit per run, early-stop on
     Prospeo, depth presets with honest credit estimates.

4. **I added the judgment layer the brief hinted at:** a safety checkpoint, a
   suppression list, a demo-redirect so I can show a live send without spamming
   real prospects, and a web UI on top of the CLI so the pipeline is *watchable*.

> The punchline: **"Half the job was reading docs and handling the gap between
> what the docs promise and what the free tier actually does."** That's the real
> job, and I'm showing you I can do it.

---

## 2. The live demo script (90 seconds)

Have the site warm (open it a minute early — free tier sleeps).

1. **Mock run first** (zero credits, never fails):
   "Watch the flow end-to-end — Apollo finds lookalikes, Prospeo finds the
   C-suite, resolves their emails, and here's the **safety checkpoint**: it
   stops and shows me exactly who it's about to email before anything sends."

2. **Real dry-run** on a live domain (e.g. `figma.com`):
   "Now it's hitting the real APIs. Same checkpoint, but with *real* people and
   *real* verified emails. Notice it sourced a pool of companies for one Apollo
   credit, then stopped Prospeo the moment it had enough — that's the
   credit-awareness."

3. **The real send** (Depth = Minimal):
   "And here's a real send — but I set a `TEST_RECIPIENT`, so every email routes
   to my own inbox instead of the prospect. The subject and body show exactly
   who it *would* have gone to. In production you blank that one variable and it
   mails the real prospects."

> Always have **Mock mode** as the fallback. *"A clean mock run beats a
> half-broken live one"* — that's literally in their evaluation criteria.

---

## 3. Walking the code — systems thinking

The 30-second map:

> "It's a series of typed transformations defined in `models.py`. Each stage is
> one client module with one method — `find_lookalikes`, `find_decision_makers`,
> `resolve_email`, `send`. `base.py` holds the shared HTTP layer — retry,
> backoff, rate-limit, typed errors — so the stage clients stay tiny and
> identical in behaviour. `pipeline.py` orchestrates and isolates failures.
> `cli.py` and `server.py` are two thin UIs over the same core."

Systems-thinking points to land:

- **One stage = one module = one swappable unit.** That's *why* I could swap
  Eazyreach→Prospeo and Ocean→Apollo without touching the orchestrator. Loose
  coupling paid off the moment reality changed the requirements.
- **The shared HTTP layer is written once.** Retries honour the server's
  `Retry-After` header (not a guess); only transient failures (429, 5xx, network)
  retry; 4xx fails fast because retrying a bad key just burns credits.
- **Two UIs, one engine.** The web server (Flask + Server-Sent Events) streams
  the same pipeline the CLI runs. I didn't fork logic — the SSE layer just emits
  events as each stage progresses.
- **Failure isolation.** Every per-company / per-person call is in its own
  try/except. One dud company or one missing email is logged, counted, and
  skipped — the run continues. A run that emails 4 of 6 contacts is a *success*,
  not a crash.

---

## 4. Product thinking — say these out loud

- **The safety checkpoint is a product decision, not a feature.** Sending cold
  email is the one irreversible, reputation-affecting action in the system, so it
  is the one thing gated behind explicit human approval. Sourcing and sending are
  deliberately *split* for exactly this reason.
- **Suppression list = protecting the customer's most valuable asset.** A B2B
  company's sending domain reputation is fragile. I record every address ever
  emailed (across all runs) and never email anyone twice. That's me thinking
  about the *operator's* long-term outcome, not just "did the email send."
- **Deliverability guardrails.** Only `VERIFIED` emails get mailed; sends are
  throttled and capped; the sender is preflighted against Brevo. A brand-new
  domain has zero reputation and is trivially easy to burn — I treated that as a
  first-class concern.
- **Credit-awareness as UX.** The "Depth" selector shows the *actual* API cost
  ("~1 Apollo · ~7 Prospeo") before you run. I'm surfacing the real-world cost of
  an action to the person taking it. That's product empathy.
- **Demo-redirect (`TEST_RECIPIENT`).** I built a way to show a *real* send
  safely. That's thinking about the whole lifecycle — including "how does someone
  trust this before pointing it at real customers."

---

## 5. Fintech / domain awareness

- **This IS the GTM engine fintech & B2B companies pay for.** My demo targets
  were Stripe, Paystack, Figma — exactly the kind of revenue-led companies whose
  growth depends on outbound. The pipeline is the unglamorous infrastructure
  behind every "we 10x'd our pipeline" story.
- **Email + reputation is a compliance-adjacent problem.** Suppression lists,
  verified-only sending, sender preflight, throttling — these are the same
  hygiene principles regulated industries care about. I built them in by default.
- **Cost-per-action thinking.** In fintech every transaction has a unit cost;
  here every API call does too. I treated API credits like a budget to be spent
  deliberately — caps, early-stop, 1-credit sourcing. That's the same discipline.
- **Idempotency & state.** Runs are checkpointed to disk and resumable
  (`--resume`), so a crash never double-charges credits or double-emails a
  person — the same "exactly-once" instinct money systems require.

---

## 6. Edge-case Q&A — they WILL ask these

- **Rate limits?** Per-client min-interval throttle + retry on 429 with
  exponential backoff and jitter, honouring `Retry-After`. Free Prospeo is
  50/day, so I also cap searches per run and stop early once I have enough.
- **De-duplication?** Companies by domain, people by `person_id`/LinkedIn URL,
  emails within a run, and a persistent cross-run suppression list.
- **Undeliverable / no email found?** Prospeo only has emails for ~half the
  people it finds, especially at small firms. So I gather a 2× prospect buffer,
  resolve until I hit the target, and skip the rest. Failures are counted, never
  fatal.
- **A company has 50 VPs?** `max_prospects_per_company` caps it; `max_emails`
  caps the whole run.
- **Bad / fake domain input?** Normalised (strip protocol, www, path) and
  validated before any API call.
- **Why Apollo not Ocean?** Ocean's lookalike endpoint is paid-plan only — I
  proved it with the actual `"Plan version not supported"` response and fell back
  to Apollo's free tier. Honest about the trade-off: Apollo free can't do true
  semantic lookalikes, so I derive them from the seed's ecosystem (subsidiaries +
  tech stack) and filter out infrastructure noise.

---

## 7. The honesty moments (these EARN trust)

Say these proactively — the brief explicitly rewards honesty:

- *"Prospeo deprecated their old endpoint; I migrated to their current
  `/enrich-person` API — that's why I know I read the live docs, not a stale
  tutorial."*
- *"Apollo's free tier can't do real competitor search, so Stage 1 is an
  approximation from the company's ecosystem. With a paid Ocean/Apollo plan it
  becomes true lookalike search — the interface doesn't change, just the
  provider behind it."*
- *"Email resolution is lossy on free tiers — I designed around it with buffering
  rather than pretending every prospect resolves."*

> If something breaks live: stay calm, reach for `--mock` and `--verbose`, and
> say *"here's what I'd check first."* Calm debugging reads better than a perfect
> run.

---

## 8. "What would you build next?" (shows vision)

- Reply/bounce handling via Brevo webhooks → auto-update the suppression list.
- A/B subject lines with open-rate tracking (the data loop).
- Per-domain warm-up schedule + send throttling for a fresh sending domain.
- SQLite/Postgres contact store so multiple operators share state and history.
- A paid Ocean/Apollo tier for true semantic lookalikes — already abstracted.
- A small test suite using the mock clients (the pipeline is built to allow it).

---

## 9. If they ask for a live tweak — where things live

- **Who we target** → `clients/prospeo.py`, `DEFAULT_SENIORITIES`.
- **Email copy** → `emailing.py`, `render_email()` (two variants, personalised).
- **Add a Stage-1 filter** (country, size) → Apollo/Ocean client.
- **Add a 5th stage** (e.g. push to a CRM) → new client + one call in
  `pipeline.py`. The typed models make the seam obvious.
- **Make sends concurrent** → the `dispatch()` loop; throttling already lives in
  `base.py`.

---

## 10. Close strong

> "I treated this exactly like production: real constraints, real money, real
> people on the other end of every email. When a provider fell through I swapped
> it behind an interface; when free tiers were tight I made it credit-aware; and
> I never let the system do the one irreversible thing — send — without a human
> saying yes. That's the engineer I'd be on your team."

---

## 11. Code walkthrough — open these files, say these things

Drive the tour top-down: **the shape first, then one stage, then the clever bits.**

### A. `outreach/models.py` — "the whole system in 20 lines"
> "Everything is a typed transformation. `Company → Prospect → Contact →
> EmailResult`. These Pydantic models ARE the contract between stages. If an API
> hands back junk, it fails validation right at the seam with a clear error —
> not deep inside the send loop."
Point at: `Contact` extends `Prospect` and just *adds* the verified email — so the
type system encodes "a contact is a prospect we resolved an email for."

### B. `outreach/clients/base.py` — "shared HTTP, written once"
Show the `request()` method and `_handle_error_status()`:
> "Retry only transient failures — 429 and 5xx and network blips — with
> exponential backoff plus jitter, and I honour the server's `Retry-After`
> header instead of guessing. A 401 or 400 fails fast, because retrying a bad key
> just burns credits. Typed exceptions (`AuthError`, `RateLimitError`,
> `NotFoundError`) let each caller react precisely."
This is the **integrations-done-right** criterion, in one file.

### C. `outreach/clients/apollo.py` — "the resilience story"
Show `FallbackStage1Client`:
> "This tries Ocean.io first and silently falls back to Apollo if the plan can't
> do lookalike search. The orchestrator never knows which provider answered."
Then show `_extract_ecosystem_domains`:
> "Apollo free can't do semantic lookalikes, so I derive them from the seed's
> ecosystem — subsidiaries and tech stack — and I filter out infrastructure
> noise like DNS/CDN, and rank real operating companies ahead of acquired subs,
> because those are the ones with reachable decision-makers. **And it costs one
> Apollo credit total** — the whole company list comes from a single enrich call."

### D. `outreach/clients/prospeo_email.py` — "the swap"
> "When Eazyreach credits fell through, Stage 3 became this — Prospeo's
> `/enrich-person`. Same input, same `Contact` output. One config flag,
> `USE_PROSPEO_EMAIL`, switches it. That's the payoff of one-stage-one-module."
Point at the defensive parser (`_extract_email`): handles multiple response
shapes and skips `revealed:false` so we never claim an email we can't see.

### E. `outreach/pipeline.py` — "orchestration + the judgment split"
> "`source()` runs Stages 1–3. `dispatch()` runs Stage 4. They're split ON
> PURPOSE — the safety checkpoint lives between them. Every per-company and
> per-person call is isolated, so one dud company is skipped, not fatal. And note
> the credit guard: I gather a 2× prospect buffer because email resolution is
> lossy, then stop — bounded by a search cap."
State is saved after every stage → `--resume` continues without re-spending.

### F. `outreach/clients/brevo.py` — "the irreversible action, handled carefully"
Show `send()` and the `test_recipient` redirect:
> "Sending is the one action you can't undo. So: only verified emails, throttled,
> and when `TEST_RECIPIENT` is set every send routes to my own inbox — same real
> sourcing and copy, just a safe destination — with the intended recipient shown
> in the subject and body. Blank one env var and it mails the real prospects."

### G. `server.py` — "two UIs, one engine"
> "The CLI and the web run the *same* pipeline core. The web layer is Flask plus
> Server-Sent Events — `/api/run` starts a background job, `/api/stream/<id>`
> streams each stage event live, and `/api/confirm` releases the safety gate.
> The checkpoint is a `threading.Event` the send blocks on until the browser
> says go." Mention: in-memory job store → single worker; SSE has heartbeats.

### H. `static/index.html` — "watchable, on purpose"
> "I made the pipeline *visible* — each stage lights up, results stream in, the
> checkpoint is a real review table. A pipeline you can watch is a pipeline you
> can trust and debug."

### Things you can literally point at on screen during the demo
- The **funnel table** (In / Out / Skipped / Errors) — resilience made visible.
- The **provider auto-switch** (Ocean → Apollo notice) — the fallback, live.
- The **safety checkpoint table** — names, titles, companies, verified emails.
- The **depth selector** showing real credit cost before you spend it.
- The **email in your inbox** addressed to the real person, with the demo banner.
- `runs/<id>/state.json` + `suppression.json` — persisted state & dedupe, on disk.

---

## 12. Product roadmap — "what the real shipped product becomes"

Frame the take-home as **the working core of a real SaaS** — and show you know
how to turn an engineering demo into a business.

### One-stop platform: **Reach → Collaborate → Talk**
> "Today it does **Reach** — sourcing and personalised email. The real product
> extends along two more axes that turn it into one place businesses run their
> entire top-of-funnel:"

- **Reach** *(built)* — find lookalike companies, decision-makers, verified
  emails, and send personalised outreach. Autonomous, with a human gate.
- **Collaborate** *(next)* — team workspaces, a shared contact + suppression
  store, campaign history, reply/bounce tracking via Brevo webhooks, a lightweight
  CRM so a whole team works the same pipeline without stepping on each other.
- **Talk** *(the Vocallabs tie-in)* — warm leads flow straight into **voice**:
  an AI voice agent (Vocallabs' core) auto-calls or follows up on contacts who
  open/reply. Email opens the door; voice closes it. *This is why I'm excited
  about Vocallabs specifically — my pipeline is the perfect feeder for a voice
  product.*

### Authentication (multi-tenant)
> "Right now it's single-operator. The real product needs auth — Clerk or Auth0 —
> so every business gets its own workspace, its own campaigns, its own
> suppression list and sending identity. Auth is the boundary that makes
> everything else (billing, collaboration, data isolation) possible."
- Each tenant isolated: their domains, their contacts, their sender, their history.
- Role-based access for teams (admin approves sends; reps build campaigns).

### Payments & monetisation (this is the part that makes it *real*)
> "The honest truth from building this: every run costs money — Apollo, Prospeo,
> Brevo all bill per call. So the product has to be monetised, not just to profit
> but to *cover its own API bills*. I'd add a payment gateway — **Stripe or
> Razorpay** — with a credit/usage model."
- **Credit-based billing**: users buy credits; each run debits credits mapped to
  the real underlying API cost, **plus a margin**. The credit-awareness I already
  built (1 Apollo credit/run, Prospeo caps, cost shown in the UI) is *exactly*
  the metering layer billing sits on top of.
- **Tiers**: free trial (mock + a few real runs), then usage-based or monthly
  plans. Enterprise = bring-your-own API keys + SSO.
- **Why this matters**: it turns the pipeline from a cost centre into a
  self-funding product. The unit economics are clean because I already track
  cost-per-run.

> The close: *"I didn't just build a script — I built the core of a product I
> can see the business model for. Reach today, Collaborate and Talk next, with
> auth and usage-based billing turning the API costs I hit into the revenue model.
> And the 'Talk' layer is Vocallabs — which is exactly where I want to build it."*
