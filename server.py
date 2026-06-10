"""Flask web server — HTTP & SSE layer over the four-stage outreach pipeline.

Routes:
    GET  /                   → serves the SPA (static/index.html)
    POST /api/run            → starts pipeline job; returns {job_id}
    GET  /api/stream/<id>    → SSE stream of pipeline events
    POST /api/confirm/<id>   → releases the Stage-4 safety gate
    POST /api/cancel/<id>    → cancels at the safety checkpoint
    GET  /api/health         → liveness check

Each job runs in a daemon thread. Events flow into a thread-safe Queue that
the SSE generator reads from. The checkpoint is a threading.Event — the Stage-4
send blocks on it until the browser POSTs /confirm or /cancel.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import ExitStack
from queue import Empty, Queue
from typing import Optional

from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS

from outreach.clients.base import ApiError, AuthError
from outreach.clients.brevo import BrevoClient, DryRunBrevoClient
from outreach.clients.eazyreach import EazyreachClient, MockEazyreachClient
from outreach.clients.ocean import OceanClient
from outreach.clients.apollo import ApolloClient, FallbackStage1Client
from outreach.clients.prospeo import ProspeoClient
from outreach.clients.prospeo_email import ProspeoEmailClient
from outreach.config import settings
from outreach.emailing import render_email
from outreach.mocks import MockOceanClient, MockProspeoClient

# Silence the root Rich logger so it doesn't bleed into the Flask log output.
logging.basicConfig(level=logging.WARNING)

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)


# ── Optional password gate ────────────────────────────────────────────────────
# When APP_PASSWORD is set (recommended for a public deploy), the whole app
# requires HTTP Basic auth. The browser prompts once and reuses the credentials
# for the SSE stream too. Leave APP_PASSWORD blank to run fully open (local dev).
_APP_USERNAME = os.getenv("APP_USERNAME", "admin")
_APP_PASSWORD = os.getenv("APP_PASSWORD", "")


@app.before_request
def _require_auth():
    if not _APP_PASSWORD:
        return  # auth disabled
    auth = request.authorization
    if auth and auth.username == _APP_USERNAME and auth.password == _APP_PASSWORD:
        return
    return Response(
        "Authentication required.", 401,
        {"WWW-Authenticate": 'Basic realm="Outreach Pipeline"'},
    )

# In-memory job store — keyed by job_id.
# Each entry: {queue, confirm_event, confirmed, created_at, domain}
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

_JOB_TTL_SECONDS = 3600  # clean up jobs older than 1 hour


# ── Helpers ──────────────────────────────────────────────────────────────────

def _normalise_domain(raw: str) -> str:
    raw = raw.strip().lower()
    raw = re.sub(r"^https?://", "", raw)
    raw = raw.split("/")[0]
    return re.sub(r"^www\.", "", raw)


def _valid_email(addr: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", addr))


def _cleanup_old_jobs() -> None:
    cutoff = time.time() - _JOB_TTL_SECONDS
    with _jobs_lock:
        stale = [jid for jid, j in _jobs.items() if j.get("created_at", 0) < cutoff]
        for jid in stale:
            _jobs.pop(jid, None)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.after_request
def _no_cache(resp):
    # Dev tool: never cache the SPA/HTML so UI edits always show on refresh.
    if resp.mimetype in ("text/html", "application/javascript", "text/css"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "jobs": len(_jobs)})


@app.route("/api/run", methods=["POST"])
def start_run():
    _cleanup_old_jobs()

    data = request.get_json(silent=True) or {}
    domain = _normalise_domain(data.get("domain", ""))
    use_mock = bool(data.get("mock", False))
    dry_run = bool(data.get("dry_run", True))

    if not domain:
        return jsonify({"error": "domain is required"}), 400
    if "." not in domain:
        return jsonify({"error": "enter a valid domain like stripe.com"}), 400

    # Credit-aware caps — bound how many API calls a single run can make.
    # Free Apollo (85/mo) + Prospeo (50/day) burn fast, so keep demos small.
    def _clamp(v, default, lo, hi):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return default

    caps = {
        "max_companies":   _clamp(data.get("max_companies"), settings.default_max_companies, 1, 10),
        "max_searches":    _clamp(data.get("max_searches"), 4, 1, 10),
        "max_prospects":   _clamp(data.get("max_prospects"), settings.default_max_prospects_per_company, 1, 5),
        "max_emails":      _clamp(data.get("max_emails"), settings.default_max_emails, 1, 25),
    }

    # Per-run demo redirect: if the visitor gives their own email, every send
    # for THIS run goes to them instead of the default TEST_RECIPIENT. Lets a
    # hiring manager watch real outreach land in their own inbox, live.
    demo_email = str(data.get("demo_email", "")).strip()
    if demo_email and not _valid_email(demo_email):
        return jsonify({"error": "that doesn't look like a valid email"}), 400

    job_id = str(uuid.uuid4())
    ev_queue: Queue = Queue()
    confirm_event = threading.Event()

    with _jobs_lock:
        _jobs[job_id] = {
            "queue": ev_queue,
            "confirm_event": confirm_event,
            "confirmed": None,
            "domain": domain,
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=_pipeline_thread,
        args=(job_id, domain, use_mock, dry_run, caps, demo_email),
        daemon=True,
        name=f"pipeline-{job_id[:8]}",
    )
    thread.start()

    return jsonify({"job_id": job_id, "domain": domain, "caps": caps})


@app.route("/api/stream/<job_id>")
def stream(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404

    def _generate():
        q: Queue = job["queue"]
        while True:
            try:
                ev = q.get(timeout=25)
            except Empty:
                # Keep-alive heartbeat so browser doesn't close the connection.
                yield "data: {\"type\":\"heartbeat\"}\n\n"
                continue

            if ev is None:
                yield "data: {\"type\":\"done\"}\n\n"
                break

            yield f"data: {json.dumps(ev)}\n\n"

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/confirm/<job_id>", methods=["POST"])
def confirm(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    job["confirmed"] = True
    job["confirm_event"].set()
    return jsonify({"ok": True})


@app.route("/api/cancel/<job_id>", methods=["POST"])
def cancel(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    job["confirmed"] = False
    job["confirm_event"].set()
    return jsonify({"ok": True})


# ── Pipeline thread ───────────────────────────────────────────────────────────

def _pipeline_thread(job_id: str, domain: str, use_mock: bool, dry_run: bool,
                     caps: dict | None = None, demo_email: str = "") -> None:
    caps = caps or {
        "max_companies": settings.default_max_companies,
        "max_searches": 4,
        "max_prospects": settings.default_max_prospects_per_company,
        "max_emails": settings.default_max_emails,
    }
    MAX_COMPANIES = caps["max_companies"]
    MAX_SEARCHES  = caps.get("max_searches", 4)
    MAX_PROSPECTS = caps["max_prospects"]
    MAX_EMAILS    = caps["max_emails"]
    # Per-run demo recipient overrides the configured TEST_RECIPIENT
    redirect_to = demo_email or settings.test_recipient
    job = _jobs.get(job_id, {})
    q: Queue = job.get("queue", Queue())
    confirm_event: threading.Event = job.get("confirm_event", threading.Event())

    def emit(ev: Optional[dict]) -> None:
        q.put(ev)

    emit({"type": "start", "domain": domain, "mock": use_mock, "dry_run": dry_run})

    try:
        with ExitStack() as stack:
            # ── Wire up clients ──────────────────────────────────────────────
            if use_mock:
                ocean = stack.enter_context(MockOceanClient())
                prospeo = stack.enter_context(MockProspeoClient())
                eazyreach = stack.enter_context(MockEazyreachClient())
                brevo = stack.enter_context(DryRunBrevoClient())
            else:
                if not settings.ocean_api_token and not settings.apollo_api_key:
                    emit({"type": "fatal", "error": "No Stage 1 provider. Set OCEAN_API_TOKEN or APOLLO_API_KEY in .env"})
                    return
                if not settings.prospeo_api_key:
                    emit({"type": "fatal", "error": "PROSPEO_API_KEY not set. Add it to .env or use mock mode."})
                    return

                ocean = stack.enter_context(
                    FallbackStage1Client(settings.ocean_api_token, settings.apollo_api_key)
                )
                prospeo = stack.enter_context(ProspeoClient(settings.prospeo_api_key))

                # Stage 3: Prospeo email finder (recommended) > Eazyreach > mock
                if settings.use_prospeo_email and settings.prospeo_api_key:
                    eazyreach = stack.enter_context(ProspeoEmailClient(settings.prospeo_api_key))
                elif settings.eazyreach_mock or not settings.eazyreach_api_key:
                    eazyreach = stack.enter_context(MockEazyreachClient())
                    emit({"type": "warn", "msg": "Stage 3 using mock emails — set USE_PROSPEO_EMAIL=true to resolve real addresses"})
                else:
                    eazyreach = stack.enter_context(EazyreachClient(
                        settings.eazyreach_api_key,
                        base_url=settings.eazyreach_base_url,
                        resolve_path=settings.eazyreach_resolve_path,
                        auth_style=settings.eazyreach_auth_style,
                    ))

                if dry_run or not settings.brevo_api_key or not settings.sender_email:
                    brevo = stack.enter_context(DryRunBrevoClient())
                    if not dry_run:
                        emit({"type": "warn", "msg": "Brevo in dry-run mode (set BREVO_API_KEY + SENDER_EMAIL to send for real)"})
                else:
                    brevo = stack.enter_context(BrevoClient(
                        settings.brevo_api_key,
                        settings.sender_email,
                        settings.sender_name,
                        settings.reply_to_email,
                        test_recipient=redirect_to,
                    ))
                    if redirect_to:
                        emit({"type": "warn",
                              "msg": f"Demo mode — every email is delivered to {redirect_to}, not the real prospects."})

            # ── Stage 1: Lookalike companies ─────────────────────────────────
            # Apollo.io is the working provider (Ocean.io's free plan can't do
            # lookalike search). FallbackStage1Client still tries Ocean first
            # internally, but Apollo is what actually returns results.
            stage1_label = "Apollo.io"
            emit({"type": "stage_start", "stage": 1, "service": stage1_label,
                  "desc": "Searching for lookalike companies..."})
            # Apollo costs exactly 1 enrich credit no matter how many companies
            # it returns (ecosystem comes from the single seed enrich), so the
            # company count here doesn't affect Apollo spend — only Prospeo
            # (Stage 2), which is bounded by MAX_SEARCHES below.
            try:
                companies = ocean.find_lookalikes(
                    domain,
                    max_results=MAX_COMPANIES,
                    min_score=settings.default_min_score,
                )
            except AuthError as exc:
                emit({"type": "stage_error", "stage": 1,
                      "error": f"Stage 1 auth failed: {exc}"})
                return
            except ApiError as exc:
                emit({"type": "stage_error", "stage": 1, "error": str(exc)})
                return

            for co in companies:
                emit({"type": "log", "stage": 1,
                      "msg": f"Found {co.name or co.domain} ({co.country or '?'}, {co.size or '?'} employees)"})

            emit({"type": "stage_done", "stage": 1, "count": len(companies),
                  "items": [{"domain": c.domain, "name": c.name, "size": c.size,
                              "country": c.country, "score": c.score} for c in companies]})

            if not companies:
                emit({"type": "info", "msg": "No lookalike companies found. Try a lower similarity score or a different seed."})
                return

            # ── Stage 2: Prospeo — decision-makers ──────────────────────────
            emit({"type": "stage_start", "stage": 2, "service": "Prospeo",
                  "desc": "Finding C-suite & VP decision-makers..."})
            prospects = []
            seen_ids: set[str] = set()
            s2_errors = 0

            # Credit guard: Prospeo only has emails for ~half of the people it
            # finds, so we gather a 2x buffer of prospects before resolving, then
            # stop. Never run more than MAX_SEARCHES Prospeo company-searches.
            TARGET_PROSPECTS = MAX_EMAILS * 2 + 1
            SEARCH_CAP = min(len(companies), MAX_SEARCHES)
            searched = 0

            for co in companies:
                if len(prospects) >= TARGET_PROSPECTS or searched >= SEARCH_CAP:
                    break
                searched += 1
                try:
                    found = prospeo.find_decision_makers(
                        co, max_results=MAX_PROSPECTS
                    )
                    for p in found:
                        key = p.person_id or p.linkedin_url
                        if key and key in seen_ids:
                            continue
                        if key:
                            seen_ids.add(key)
                        prospects.append(p)
                        emit({"type": "log", "stage": 2,
                              "msg": f"{p.display_name()} — {p.title or 'exec'} @ {p.company_name or co.domain}"})
                except ApiError as exc:
                    s2_errors += 1
                    emit({"type": "log", "stage": 2,
                          "msg": f"[skip] {co.domain}: {exc}", "level": "warn"})

            emit({"type": "stage_done", "stage": 2, "count": len(prospects), "errors": s2_errors})

            if not prospects:
                emit({"type": "info", "msg": "No decision-makers found across these companies."})
                return

            # ── Stage 3: Eazyreach — verified emails ─────────────────────────
            stage3_service = "Prospeo" if settings.use_prospeo_email else "Eazyreach"
            emit({"type": "stage_start", "stage": 3, "service": stage3_service,
                  "desc": "Resolving LinkedIn profiles → verified work emails..."})
            contacts = []
            seen_emails: set[str] = set()
            s3_errors = 0

            for p in prospects:
                if len(contacts) >= MAX_EMAILS:
                    emit({"type": "log", "stage": 3,
                          "msg": f"Hit max-emails cap ({MAX_EMAILS}), stopping."})
                    break
                if not p.linkedin_url:
                    continue
                try:
                    contact = eazyreach.resolve_email(p)
                    if contact and contact.email.lower() not in seen_emails:
                        seen_emails.add(contact.email.lower())
                        contacts.append(contact)
                        emit({"type": "log", "stage": 3,
                              "msg": f"{contact.email}  [{contact.email_status}]  ← {p.display_name()}"})
                except ApiError as exc:
                    s3_errors += 1
                    emit({"type": "log", "stage": 3,
                          "msg": f"[skip] {p.display_name()}: {exc}", "level": "warn"})

            emit({"type": "stage_done", "stage": 3, "count": len(contacts), "errors": s3_errors})

            if not contacts:
                emit({"type": "info", "msg": "No verified emails resolved."})
                return

            # ── Safety checkpoint ────────────────────────────────────────────
            subject_preview, _, _ = render_email(contacts[0])
            is_dry = dry_run or not settings.brevo_api_key or not settings.sender_email or use_mock
            emit({
                "type": "checkpoint",
                "contacts": [{
                    "name": c.display_name(),
                    "title": c.title or "—",
                    "company": c.company_name or c.company_domain,
                    "email": c.email,
                    "status": c.email_status,
                } for c in contacts],
                "subject_preview": subject_preview,
                "dry_run": is_dry,
            })

            # Block until the frontend posts /confirm or /cancel.
            confirm_event.wait(timeout=300)  # auto-cancel after 5 min

            if not job.get("confirmed"):
                emit({"type": "cancelled", "msg": "Pipeline cancelled at safety checkpoint."})
                return

            # ── Stage 4: Brevo — send ────────────────────────────────────────
            emit({"type": "stage_start", "stage": 4, "service": "Brevo",
                  "desc": "Sending personalised outreach emails..."})
            sent = failed = dry_count = 0

            for contact in contacts:
                subject, html_body, text_body = render_email(contact)
                result = brevo.send(contact, subject, html_body, text_body)
                if result.sent:
                    sent += 1
                    emit({"type": "log", "stage": 4,
                          "msg": f"Sent → {result.email}  [{result.message_id or 'ok'}]"})
                elif result.dry_run:
                    dry_count += 1
                    emit({"type": "log", "stage": 4,
                          "msg": f"[dry-run] Would send → {result.email}"})
                else:
                    failed += 1
                    emit({"type": "log", "stage": 4,
                          "msg": f"[fail] {result.email}: {result.error}", "level": "error"})

            total_ok = sent + dry_count
            emit({"type": "stage_done", "stage": 4, "count": total_ok, "errors": failed})
            emit({
                "type": "complete",
                "stats": {
                    "companies": len(companies),
                    "prospects": len(prospects),
                    "contacts": len(contacts),
                    "sent": total_ok,
                    "failed": failed,
                    "dry_run": is_dry,
                },
            })

    except Exception as exc:
        emit({"type": "fatal", "error": f"Unexpected error: {exc}"})
    finally:
        emit(None)  # sentinel — closes the SSE stream


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Local dev. In production a WSGI server (gunicorn) imports `app` instead.
    port = int(os.getenv("PORT", "5050"))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    print(f"\n  >_ Outreach Pipeline  —  http://localhost:{port}\n")
    app.run(host="0.0.0.0", debug=debug, threaded=True, port=port, use_reloader=False)
