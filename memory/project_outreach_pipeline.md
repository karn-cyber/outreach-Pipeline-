---
name: project-outreach-pipeline
description: Adrexon B2B outreach pipeline — architecture, CRM, auth, MongoDB, known state
metadata:
  type: project
---

Full-stack B2B outreach SaaS. Flask backend + single-page HTML frontends. Deployed on Render.

**Why:** Vocallabs SDE internship take-home, evolved into a real B2B SaaS product with CRM.

## Architecture

**Backend:** `server.py` (Flask, ~600 lines)
- 4-stage pipeline: Apollo.io → Prospeo → Prospeo Email Finder → Brevo
- SSE streaming of pipeline events to browser
- Clerk multi-tenant auth (`CLERK_PUBLISHABLE_KEY` + `CLERK_SECRET_KEY`)
- MongoDB Atlas CRM persistence (`MONGODB_URI`)
- Auth returns `(user_id, org_id, error)` 3-tuple; workspace_id = org_id or user_id

**Frontend:**
- `static/index.html` — marketing SPA + pipeline runner (1800 lines, vanilla JS)
- `static/dashboard.html` — full CRM dashboard SPA (vanilla JS, no build step)

**Python packages:** `outreach/` — models (Pydantic), clients (Apollo, Prospeo, Brevo, Ocean), config, emailing, CLI

## CRM (added June 2026)

MongoDB Atlas: `mongodb+srv://LeadDatabase:Akriti6699@leaddatabase.vygn8dg.mongodb.net/`
- DB name: `adrexon`
- Collections: `contacts` (upserted per run by workspace+email), `runs`
- Data saved automatically after every pipeline Stage 4 completes

**CRM API routes:**
- `GET /api/crm/overview` — stats (contacts, sent, runs, label breakdown, followups_due)
- `GET /api/crm/contacts?page&label&search` — paginated, filterable
- `PATCH /api/crm/contacts/:id/label` — update label
- `PATCH /api/crm/contacts/:id/notes` — update notes
- `POST /api/crm/contacts/:id/followup` — set follow_up_date
- `GET /api/crm/runs` — campaign history
- `GET /api/crm/followups` — contacts with upcoming follow_up_date

**Labels:** no_response, interested, not_interested, meeting_booked, replied, bounced, unsubscribed

## Dashboard views

- **Overview** — 4 stat cards + recent contacts + response label breakdown
- **Contacts** — full table with inline label select, follow-up date picker, notes modal, search + filter tabs, pagination
- **Campaigns** — run history table
- **Follow-ups** — grouped by Overdue / Today / Upcoming, mark-done button
- **Team** — shows Clerk org members, manage team via Clerk

## Auth flow

- Clerk JS loaded dynamically from `/api/config` response
- Dashboard shows sign-in gate if not authenticated
- Nav shows "Dashboard" link after sign-in; post-pipeline results show "View in CRM →" button

## Key env vars

```
MONGODB_URI=mongodb+srv://LeadDatabase:Akriti6699@leaddatabase.vygn8dg.mongodb.net/
CLERK_PUBLISHABLE_KEY=pk_test_...
CLERK_SECRET_KEY=sk_test_...
APOLLO_API_KEY=...
PROSPEO_API_KEY=...
BREVO_API_KEY=...
SENDER_EMAIL=neelanshu@neelanshukarn.online
```

**How to apply:** When suggesting changes, keep the no-build-step constraint (vanilla HTML/JS). CRM routes require MongoDB to be reachable. Pipeline data only persists after Stage 4 completes and user confirms.
