"""Flask web server — HTTP & SSE layer over the four-stage outreach pipeline.

Routes:
    GET  /                      → serves the SPA (static/index.html)
    GET  /dashboard             → serves the CRM dashboard (static/dashboard.html)
    POST /api/run               → starts pipeline job; returns {job_id}
    GET  /api/stream/<id>       → SSE stream of pipeline events
    POST /api/confirm/<id>      → releases the Stage-4 safety gate
    POST /api/cancel/<id>       → cancels at the safety checkpoint
    GET  /api/health            → liveness check
    GET  /api/crm/overview      → CRM stats for the signed-in workspace
    GET  /api/crm/contacts      → paginated contacts list
    PATCH /api/crm/contacts/<id>/label    → update contact CRM label
    PATCH /api/crm/contacts/<id>/notes   → update contact notes
    POST  /api/crm/contacts/<id>/followup → set follow-up date
    GET  /api/crm/runs          → pipeline run history
    GET  /api/crm/followups     → contacts with upcoming follow-up dates

Each job runs in a daemon thread. Events flow into a thread-safe Queue that
the SSE generator reads from. The checkpoint is a threading.Event — the Stage-4
send blocks on it until the browser POSTs /confirm or /cancel.
"""
from __future__ import annotations

import datetime
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

try:
    from pymongo import MongoClient, DESCENDING
    from bson import ObjectId
    _PYMONGO = True
except ImportError:
    _PYMONGO = False

logging.basicConfig(level=logging.WARNING)

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)


# ── MongoDB (CRM persistence) ─────────────────────────────────────────────────

_mongo_client = None
_mongo_db = None


def _get_db():
    global _mongo_client, _mongo_db
    if not _PYMONGO:
        return None
    if _mongo_db is not None:
        return _mongo_db
    uri = settings.mongodb_uri or os.getenv("MONGODB_URI", "")
    if not uri:
        return None
    try:
        _mongo_client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        _mongo_db = _mongo_client["adrexon"]
        _mongo_db.contacts.create_index([("workspace_id", 1), ("email", 1)], unique=True)
        _mongo_db.contacts.create_index([("workspace_id", 1), ("last_emailed_at", -1)])
        _mongo_db.contacts.create_index([("workspace_id", 1), ("label", 1)])
        _mongo_db.contacts.create_index([("workspace_id", 1), ("follow_up_date", 1)])
        _mongo_db.runs.create_index([("workspace_id", 1), ("created_at", -1)])
        logging.info("MongoDB connected: adrexon")
    except Exception as exc:
        logging.warning("MongoDB init failed: %s", exc)
        _mongo_db = None
    return _mongo_db


def _save_run_to_db(job_id: str, domain: str, user_id: str, org_id: Optional[str],
                    companies: list, contacts: list, sent: int, dry_run: bool) -> None:
    db = _get_db()
    if db is None:
        return
    workspace_id = org_id or user_id
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    try:
        db.runs.insert_one({
            "run_id": job_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "org_id": org_id,
            "domain": domain,
            "stats": {
                "companies": len(companies),
                "contacts": len(contacts),
                "sent": sent,
                "dry_run": dry_run,
            },
            "created_at": now,
        })
        for contact in contacts:
            db.contacts.update_one(
                {"workspace_id": workspace_id, "email": contact.email},
                {
                    "$set": {
                        "name": contact.full_name or contact.first_name or "",
                        "first_name": contact.first_name or "",
                        "last_name": contact.last_name or "",
                        "title": contact.title or "",
                        "company_name": contact.company_name or "",
                        "company_domain": contact.company_domain,
                        "linkedin_url": contact.linkedin_url or "",
                        "email_status": contact.email_status,
                        "last_run_id": job_id,
                        "last_run_domain": domain,
                        "last_emailed_at": now,
                    },
                    "$setOnInsert": {
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                        "email": contact.email,
                        "label": "no_response",
                        "notes": "",
                        "follow_up_date": None,
                        "created_at": now,
                    },
                },
                upsert=True,
            )
    except Exception as exc:
        logging.warning("MongoDB save failed: %s", exc)


def _serialize_doc(doc: dict) -> dict:
    """Convert a MongoDB document to a JSON-safe dict."""
    for k, v in list(doc.items()):
        if _PYMONGO and isinstance(v, ObjectId):
            doc[k] = str(v)
        elif isinstance(v, datetime.datetime):
            doc[k] = v.isoformat()
    return doc


# ── Clerk authentication (multi-tenant) ──────────────────────────────────────
import base64

_CLERK_PUBLISHABLE = (
    settings.clerk_publishable_key
    or os.getenv("CLERK_PUBLISHABLE_KEY")
    or os.getenv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "")
)
_CLERK_SECRET = settings.clerk_secret_key or os.getenv("CLERK_SECRET_KEY", "")


def _derive_clerk_frontend_api(pk: str) -> str:
    try:
        b64 = pk.split("_", 2)[2]
        decoded = base64.b64decode(b64 + "===").decode("utf-8")
        return "https://" + decoded.rstrip("$")
    except Exception:
        return ""


_CLERK_FRONTEND_API = _derive_clerk_frontend_api(_CLERK_PUBLISHABLE)
_CLERK_ENABLED = bool(_CLERK_PUBLISHABLE and _CLERK_SECRET and _CLERK_FRONTEND_API)

_jwks_client = None
if _CLERK_ENABLED:
    try:
        from jwt import PyJWKClient
        _jwks_client = PyJWKClient(_CLERK_FRONTEND_API + "/.well-known/jwks.json")
    except Exception as exc:
        logging.warning("Clerk JWKS init failed (%s) — auth disabled", exc)
        _CLERK_ENABLED = False


def _verify_clerk_request():
    """Return (user_id, org_id, None) if authed, or (None, None, error_response) if not."""
    if not _CLERK_ENABLED:
        return "local-dev", None, None
    import jwt
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else ""
    if not token:
        return None, None, (jsonify({"error": "Please sign in to run a pipeline."}), 401)
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, signing_key.key, algorithms=["RS256"],
            options={"verify_aud": False},
            leeway=10,
        )
        user_id = claims.get("sub", "unknown")
        org_id = claims.get("org_id") or claims.get("o")
        return user_id, org_id, None
    except Exception as exc:
        logging.info("Clerk token rejected: %s", exc)
        return None, None, (jsonify({"error": "Your session is invalid or expired — sign in again."}), 401)


# ── Optional HTTP-Basic password gate (legacy, off when Clerk is on) ──────────
_APP_USERNAME = os.getenv("APP_USERNAME", "admin")
_APP_PASSWORD = os.getenv("APP_PASSWORD", "")


@app.before_request
def _require_basic_auth():
    if not _APP_PASSWORD or _CLERK_ENABLED:
        return
    auth = request.authorization
    if auth and auth.username == _APP_USERNAME and auth.password == _APP_PASSWORD:
        return
    return Response(
        "Authentication required.", 401,
        {"WWW-Authenticate": 'Basic realm="Adrexon"'},
    )


@app.route("/api/config")
def public_config():
    return jsonify({
        "app_name": "Adrexon",
        "clerk_enabled": _CLERK_ENABLED,
        "clerk_publishable_key": _CLERK_PUBLISHABLE if _CLERK_ENABLED else "",
        "clerk_frontend_api": _CLERK_FRONTEND_API if _CLERK_ENABLED else "",
    })


# In-memory job store — keyed by job_id.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_JOB_TTL_SECONDS = 3600


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


@app.route("/dashboard")
def dashboard():
    return app.send_static_file("dashboard.html")


@app.after_request
def _no_cache(resp):
    if resp.mimetype in ("text/html", "application/javascript", "text/css"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.route("/api/health")
def health():
    db = _get_db()
    return jsonify({"ok": True, "jobs": len(_jobs), "db": db is not None})


@app.route("/api/run", methods=["POST"])
def start_run():
    user_id, org_id, err = _verify_clerk_request()
    if err:
        return err

    _cleanup_old_jobs()

    data = request.get_json(silent=True) or {}
    domain = _normalise_domain(data.get("domain", ""))
    use_mock = bool(data.get("mock", False))
    dry_run = bool(data.get("dry_run", True))

    if not domain:
        return jsonify({"error": "domain is required"}), 400
    if "." not in domain:
        return jsonify({"error": "enter a valid domain like stripe.com"}), 400

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
            "user_id": user_id,
            "org_id": org_id,
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
    user_id, org_id, err = _verify_clerk_request()
    if err:
        return err
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if _CLERK_ENABLED and job.get("user_id") != user_id:
        return jsonify({"error": "not your run"}), 403
    job["confirmed"] = True
    job["confirm_event"].set()
    return jsonify({"ok": True})


@app.route("/api/cancel/<job_id>", methods=["POST"])
def cancel(job_id: str):
    user_id, org_id, err = _verify_clerk_request()
    if err:
        return err
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if _CLERK_ENABLED and job.get("user_id") != user_id:
        return jsonify({"error": "not your run"}), 403
    job["confirmed"] = False
    job["confirm_event"].set()
    return jsonify({"ok": True})


# ── CRM routes ────────────────────────────────────────────────────────────────

@app.route("/api/crm/overview")
def crm_overview():
    user_id, org_id, err = _verify_clerk_request()
    if err:
        return err
    workspace_id = org_id or user_id
    db = _get_db()
    if db is None:
        return jsonify({"error": "Database not configured. Add MONGODB_URI to your environment."}), 503

    total_contacts = db.contacts.count_documents({"workspace_id": workspace_id})
    total_runs = db.runs.count_documents({"workspace_id": workspace_id})

    agg = list(db.runs.aggregate([
        {"$match": {"workspace_id": workspace_id}},
        {"$group": {"_id": None, "total_sent": {"$sum": "$stats.sent"}}},
    ]))
    total_sent = agg[0]["total_sent"] if agg else 0

    label_counts = {}
    for label in ["no_response", "interested", "not_interested", "meeting_booked", "replied", "bounced", "unsubscribed"]:
        label_counts[label] = db.contacts.count_documents({"workspace_id": workspace_id, "label": label})

    followups_due = db.contacts.count_documents({
        "workspace_id": workspace_id,
        "follow_up_date": {"$ne": None, "$lte": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).date().isoformat()},
    })

    recent = list(
        db.contacts.find({"workspace_id": workspace_id})
        .sort("last_emailed_at", DESCENDING)
        .limit(8)
    )
    recent = [_serialize_doc(c) for c in recent]

    return jsonify({
        "total_contacts": total_contacts,
        "total_sent": total_sent,
        "total_runs": total_runs,
        "label_counts": label_counts,
        "followups_due": followups_due,
        "recent_contacts": recent,
    })


@app.route("/api/crm/contacts")
def crm_contacts():
    user_id, org_id, err = _verify_clerk_request()
    if err:
        return err
    workspace_id = org_id or user_id
    db = _get_db()
    if db is None:
        return jsonify({"error": "Database not configured. Add MONGODB_URI to your environment."}), 503

    page = max(1, int(request.args.get("page", 1)))
    per_page = min(50, max(5, int(request.args.get("per_page", 25))))
    label_filter = request.args.get("label", "")
    search = request.args.get("search", "").strip()

    query: dict = {"workspace_id": workspace_id}
    if label_filter:
        query["label"] = label_filter
    if search:
        query["$or"] = [
            {"email": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
            {"company_name": {"$regex": search, "$options": "i"}},
        ]

    total = db.contacts.count_documents(query)
    docs = list(
        db.contacts.find(query)
        .sort("last_emailed_at", DESCENDING)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    contacts = [_serialize_doc(c) for c in docs]

    return jsonify({"contacts": contacts, "total": total, "page": page, "per_page": per_page})


@app.route("/api/crm/contacts/<contact_id>/label", methods=["PATCH"])
def crm_update_label(contact_id: str):
    user_id, org_id, err = _verify_clerk_request()
    if err:
        return err
    workspace_id = org_id or user_id
    db = _get_db()
    if db is None:
        return jsonify({"error": "Database not configured."}), 503

    data = request.get_json(silent=True) or {}
    label = data.get("label", "")
    valid_labels = {"no_response", "interested", "not_interested", "meeting_booked", "replied", "bounced", "unsubscribed"}
    if label not in valid_labels:
        return jsonify({"error": f"invalid label '{label}'"}), 400

    try:
        result = db.contacts.update_one(
            {"_id": ObjectId(contact_id), "workspace_id": workspace_id},
            {"$set": {"label": label, "label_updated_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)}},
        )
    except Exception:
        return jsonify({"error": "invalid contact id"}), 400

    if result.matched_count == 0:
        return jsonify({"error": "contact not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/crm/contacts/<contact_id>/notes", methods=["PATCH"])
def crm_update_notes(contact_id: str):
    user_id, org_id, err = _verify_clerk_request()
    if err:
        return err
    workspace_id = org_id or user_id
    db = _get_db()
    if db is None:
        return jsonify({"error": "Database not configured."}), 503

    data = request.get_json(silent=True) or {}
    notes = str(data.get("notes", ""))[:4000]

    try:
        result = db.contacts.update_one(
            {"_id": ObjectId(contact_id), "workspace_id": workspace_id},
            {"$set": {"notes": notes}},
        )
    except Exception:
        return jsonify({"error": "invalid contact id"}), 400

    if result.matched_count == 0:
        return jsonify({"error": "contact not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/crm/contacts/<contact_id>/followup", methods=["POST"])
def crm_set_followup(contact_id: str):
    user_id, org_id, err = _verify_clerk_request()
    if err:
        return err
    workspace_id = org_id or user_id
    db = _get_db()
    if db is None:
        return jsonify({"error": "Database not configured."}), 503

    data = request.get_json(silent=True) or {}
    follow_up_date = data.get("follow_up_date")  # ISO date string or null/None

    try:
        result = db.contacts.update_one(
            {"_id": ObjectId(contact_id), "workspace_id": workspace_id},
            {"$set": {"follow_up_date": follow_up_date}},
        )
    except Exception:
        return jsonify({"error": "invalid contact id"}), 400

    if result.matched_count == 0:
        return jsonify({"error": "contact not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/crm/runs")
def crm_runs():
    user_id, org_id, err = _verify_clerk_request()
    if err:
        return err
    workspace_id = org_id or user_id
    db = _get_db()
    if db is None:
        return jsonify({"error": "Database not configured. Add MONGODB_URI to your environment."}), 503

    page = max(1, int(request.args.get("page", 1)))
    per_page = 20
    total = db.runs.count_documents({"workspace_id": workspace_id})
    docs = list(
        db.runs.find({"workspace_id": workspace_id})
        .sort("created_at", DESCENDING)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    runs = [_serialize_doc(r) for r in docs]
    return jsonify({"runs": runs, "total": total, "page": page})


@app.route("/api/crm/followups")
def crm_followups():
    user_id, org_id, err = _verify_clerk_request()
    if err:
        return err
    workspace_id = org_id or user_id
    db = _get_db()
    if db is None:
        return jsonify({"error": "Database not configured. Add MONGODB_URI to your environment."}), 503

    today = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).date().isoformat()
    docs = list(
        db.contacts.find({
            "workspace_id": workspace_id,
            "follow_up_date": {"$ne": None, "$gte": today},
        })
        .sort("follow_up_date", 1)
        .limit(100)
    )
    contacts = [_serialize_doc(c) for c in docs]
    return jsonify({"followups": contacts, "today": today})


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
    redirect_to = demo_email or settings.test_recipient
    job = _jobs.get(job_id, {})
    q: Queue = job.get("queue", Queue())
    confirm_event: threading.Event = job.get("confirm_event", threading.Event())
    user_id: str = job.get("user_id", "unknown")
    org_id: Optional[str] = job.get("org_id")

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
            stage1_label = "Apollo.io"
            emit({"type": "stage_start", "stage": 1, "service": stage1_label,
                  "desc": "Searching for lookalike companies..."})
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

            TARGET_PROSPECTS = MAX_EMAILS * 2 + 1
            SEARCH_CAP = min(len(companies), MAX_SEARCHES)
            searched = 0

            for co in companies:
                if len(prospects) >= TARGET_PROSPECTS or searched >= SEARCH_CAP:
                    break
                searched += 1
                try:
                    found = prospeo.find_decision_makers(co, max_results=MAX_PROSPECTS)
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

            confirm_event.wait(timeout=300)

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

            # Persist run + contacts to MongoDB CRM
            try:
                _save_run_to_db(job_id, domain, user_id, org_id, companies, contacts, total_ok, is_dry)
            except Exception as exc:
                logging.warning("CRM save failed: %s", exc)

    except Exception as exc:
        emit({"type": "fatal", "error": f"Unexpected error: {exc}"})
    finally:
        emit(None)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5050"))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    print(f"\n  >_ Adrexon  —  http://localhost:{port}\n")
    app.run(host="0.0.0.0", debug=debug, threaded=True, port=port, use_reloader=False)
