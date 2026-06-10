# Claude Usage Log — Automated Outreach Pipeline

A transparent record of how I used Claude (Anthropic) as a pair-programmer and
build assistant while creating this project, the output it helped me drive, and
my honest remarks on each.

**Model:** Claude Opus 4.8 (primary; some early exploration on the default
Claude model before I switched). **Tooling:** Claude Code (agentic CLI with file
edits, a live browser preview, and shell access).

---

| # | Task / Used For | Model | Output Driven | Feedback / Notes |
|---|-----------------|-------|---------------|------------------|
| 1 | Read & break down the assignment PDF | Opus 4.8 | Clear understanding of the 4-stage flow + evaluation criteria | Parsed the brief accurately, surfaced the "safety checkpoint" hint I might have skimmed past. |
| 2 | Scaffold the 4-stage CLI pipeline (typed models, clients, orchestrator) | Opus 4.8 | `models.py`, `clients/`, `pipeline.py`, `cli.py` | Strong modular structure (one stage = one module). Saved hours of boilerplate; I directed the architecture. |
| 3 | Shared HTTP layer (retry, backoff, rate-limit, typed errors) | Opus 4.8 | `clients/base.py` | Honoured `Retry-After`, fail-fast on 4xx. Exactly the "integrations done right" criterion. |
| 4 | Build the Flask web server + live streaming (SSE) | Opus 4.8 | `server.py` with `/api/run`, `/api/stream`, safety-gate endpoints | SSE + background-thread design worked first try. Made the pipeline watchable. |
| 5 | Design the web UI (hero, search, live timeline) | Opus 4.8 | `static/index.html` | First pass was good; needed 2–3 iterations on aesthetics (see #12). |
| 6 | Fix import bug (`logging_conf` path) | Opus 4.8 | Working imports | Caught and fixed a real `ModuleNotFoundError` quickly. |
| 7 | Wire & verify API keys (`.env`) | Opus 4.8 | Working `.env`; confirmed Brevo key + verified sender via live API call | Proactively called Brevo's `/account` and `/senders` to find my real sender address. Useful. |
| 8 | Locate Apollo / Brevo / Prospeo API keys in dashboards | Opus 4.8 | Step-by-step key-retrieval guidance | Saved me hunting through dashboards. |
| 9 | Pivot Stage 3: Eazyreach → Prospeo `/enrich-person` | Opus 4.8 | `clients/prospeo_email.py` + config flag | Handled Vocallabs' mid-assignment FAQ change cleanly — drop-in swap, one flag. Great example of modularity paying off. |
| 10 | Pivot Stage 1: Ocean.io → Apollo.io fallback | Opus 4.8 | `clients/apollo.py`, `FallbackStage1Client` | Diagnosed the real "Plan version not supported" error from Ocean's free tier, then built a clean fallback behind one interface. |
| 11 | Fix Apollo credit burn (1 credit/run, ecosystem reorder) | Opus 4.8 | Rewrote `_extract_ecosystem_domains`, over-source logic | I flagged that credits were vanishing; Claude found the per-domain enrich loop and fixed it to 1 credit/run. Real cost saver. |
| 12 | Redesign frontend (floating nav, terminal section, vertical timeline, theme) | Opus 4.8 | Full `index.html` redesign | I gave a reference screenshot; output matched the aesthetic well. Iterative — took a couple rounds. |
| 13 | Looping video background + seamless crossfade | Opus 4.8 | Two-video crossfade loop in JS | Fixed the "flicker at loop point" properly with a crossfade rather than a hack. |
| 14 | `TEST_RECIPIENT` demo redirect (safe live sends) | Opus 4.8 | Redirect logic in `clients/brevo.py` | My idea, Claude implemented it well — real sourcing + copy, mail routed to my inbox, intended recipient shown in subject/body. |
| 15 | Credit-aware depth controls + early-stop | Opus 4.8 | Depth selector, search caps, prospect buffering | Tightened cost after I pushed back on wasted credits. Honest credit estimates shown in the UI. |
| 16 | Fix Stage 2 picking dud companies (0 results) | Opus 4.8 | Operating-companies-first ranking, 2× prospect buffer | I reported repeated empty runs; Claude root-caused it (subsidiaries first + lossy email resolution) and fixed both. |
| 17 | Fix mock-mode crash (`active_provider`) | Opus 4.8 | `getattr` guard in `server.py` | Quick, correct fix. |
| 18 | Diagnose "no email received" | Opus 4.8 | Checked MX records + Brevo delivery events via API | Proved (via Brevo's own event log) that sends were delivered and NO real people were emailed — reassuring and precise. |
| 19 | Production prep for hosting | Opus 4.8 | `Procfile`, `render.yaml`, gunicorn, `$PORT`, optional password gate | Made the dev server production-ready and explained the SSE/worker constraints. |
| 20 | Deploy guidance (Render + Cloudflare DNS) | Opus 4.8 | `DEPLOY.md`; live debugging of the DNS verification | Diagnosed the Render "couldn't verify" as propagation timing by checking DNS from multiple resolvers. Correct. |
| 21 | Interview playbook + product/monetisation vision | Opus 4.8 | `docs/INTERVIEW_SCRIPT.md` | Turned the build into a hire-me narrative (systems thinking, product thinking, Reach→Collaborate→Talk, billing). |

---

## Overall remarks

- **What worked best:** Claude was strongest at *systems-level structure* (the
  modular stage design is what let me swap providers twice without pain) and at
  *diagnosing real failures* against live APIs (Ocean plan error, Apollo credit
  burn, Brevo delivery events, DNS propagation).
- **Where I had to drive:** product decisions, credit-spend trade-offs, the demo
  strategy, and aesthetics needed my direction and a few iterations — Claude
  executed well once I gave clear intent and references.
- **Honest framing:** I used Claude as a fast, knowledgeable pair-programmer. The
  architecture choices, the constraint trade-offs, the API-key setup, and every
  "is this good enough to ship" judgement were mine. The result is a working,
  end-to-end product I can explain line by line.
