"""REST API + static frontend.

Run:  uvicorn tender_tracker.api:app --host 0.0.0.0 --port 8000
      (or: python -m tender_tracker serve)

Optional HTTP Basic auth: set APP_USERNAME and APP_PASSWORD in .env.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .attachments import fetch_bid_attachments
from .config import (
    DEFAULT_CONFIG_PATH,
    Config,
    add_exclude_keywords,
    config_values,
    env,
    load_config,
    now_ist,
    save_config_text,
    update_config_values,
)
from .notifier import build_channels, channel_status
from .fetcher import GemClient
from .filter_scorer import (
    ELIGIBLE,
    ELIGIBLE_EXEMPT,
    NOT_CHECKED,
    NOT_ELIGIBLE,
    REVIEW,
    eligibility_checklist,
    suggest_exclude_keywords,
)
from .storage import (
    FEEDBACK_CHOICES,
    Bid,
    BidAttachment,
    BidDocument,
    RunLog,
    Score,
    init_db,
    latest_scores,
    make_engine,
    session_factory,
    set_feedback,
)

log = logging.getLogger(__name__)

CONFIG_PATH = Path(os.environ.get("TT_CONFIG", DEFAULT_CONFIG_PATH))  # `serve --config` sets TT_CONFIG
FRONTEND_DIST = Path(os.environ.get("TT_FRONTEND_DIST", Path(__file__).resolve().parents[1] / "frontend" / "dist"))


class _State:
    cfg: Config | None = None
    cfg_mtime: float = 0.0
    Session: Any = None
    run_thread: threading.Thread | None = None
    lock = threading.Lock()


state = _State()


def get_config() -> Config:
    """Config, reloaded when config.yaml changes on disk."""
    mtime = CONFIG_PATH.stat().st_mtime
    if state.cfg is None or mtime != state.cfg_mtime:
        state.cfg, state.cfg_mtime = load_config(CONFIG_PATH), mtime
    return state.cfg


def _sessions():
    if state.Session is None:
        engine = make_engine(get_config().storage.effective_database_url())
        init_db(engine)
        state.Session = session_factory(engine)
    return state.Session


def get_session():
    with _sessions()() as session:
        yield session


app = FastAPI(title="GeM Tender Tracker", version="2.0", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    user, password = env("APP_USERNAME"), env("APP_PASSWORD")
    if user and password and request.url.path != "/api/health":
        header = request.headers.get("Authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                given_user, _, given_pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(given_user, user) and secrets.compare_digest(given_pw, password)
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="GeM Tender Tracker"'})
    return await call_next(request)


# -- helpers ------------------------------------------------------------------------------


def _days_left(end: datetime | None, now: datetime) -> float | None:
    return None if end is None else round((end - now).total_seconds() / 86400, 2)


def _summary(bid: Bid, score: Score | None, doc: BidDocument | None, now: datetime) -> dict[str, Any]:
    return {
        "id": bid.id,
        "bid_number": bid.bid_number,
        "title": bid.title,
        "organisation": (bid.organisation if (bid.organisation or "").lower() not in ("", "n/a", "na") else None)
        or bid.department,
        "ministry": bid.ministry,
        "department": bid.department,
        "location": bid.buyer_location,
        "bid_type": bid.bid_type,
        "quantity": bid.quantity,
        "published_at": doc.published_at if doc else None,
        "start_date": bid.start_date,
        "end_date": bid.end_date,
        "opening_date": doc.bid_opening if doc else None,
        "days_left": _days_left(bid.end_date, now),
        "score": score.total if score else None,
        "shortlisted": bool(score and score.shortlisted),
        "excluded_reason": score.excluded_reason if score else None,
        "eligibility_status": score.eligibility_status if score else NOT_CHECKED,
        "eligibility_reasons": (score.eligibility_reasons or []) if score else [],
        "emd_amount": doc.emd_amount if doc else None,
        "estimated_value": bid.estimated_value,
        "feedback": bid.feedback,
        "change_summary": bid.change_summary,
        "changed_at": bid.changed_at,
        "has_corrigendum": bid.has_corrigendum,
        "gem_status": bid.gem_status,
        "first_seen_at": bid.first_seen_at,
        "doc_url": bid.doc_url,
    }


def _bid_query() -> Select:
    latest = select(Score.bid_id, func.max(Score.id).label("sid")).group_by(Score.bid_id).subquery()
    return (
        select(Bid, Score, BidDocument)
        .outerjoin(latest, latest.c.bid_id == Bid.id)
        .outerjoin(Score, Score.id == latest.c.sid)
        .outerjoin(BidDocument, BidDocument.bid_id == Bid.id)
    )


def _get_bid(session: Session, bid_id: int) -> Bid:
    bid = session.scalar(
        select(Bid).where(Bid.id == bid_id).options(selectinload(Bid.document), selectinload(Bid.attachments))
    )
    if bid is None:
        raise HTTPException(404, "Bid not found")
    return bid


def _attachment(a: BidAttachment, preview: int = 600) -> dict[str, Any]:
    return {
        "id": a.id,
        "label": a.label,
        "kind": a.kind,
        "url": a.url,
        "status": a.status,
        "error": a.error,
        "fetched_at": a.fetched_at,
        "has_text": bool(a.text),
        "text_preview": (a.text or "")[:preview] or None,
        "text_length": len(a.text or ""),
    }


# -- endpoints ----------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    cfg = get_config()
    c = cfg.company
    return {
        "company": {
            "name": c.name,
            "is_startup": c.is_startup,
            "is_mse": c.is_mse,
            "turnover_lakh": c.turnover_lakh,
            "experience_years": c.experience_years,
            "past_govt_projects": c.past_govt_projects,
            "entity_type": c.entity_type,
            "gstin": c.gstin,
            "msme_category": c.msme_category,
            "mse_benefits": c.mse_benefits,
            "startup_valid": c.startup_valid_on(now_ist()),
        },
        "score_threshold": cfg.filters.score_threshold,
        "eligibility_statuses": [ELIGIBLE, ELIGIBLE_EXEMPT, REVIEW, NOT_ELIGIBLE, NOT_CHECKED],
        "feedback_choices": FEEDBACK_CHOICES,
        "closing_soon_days": cfg.notifications.closing_soon_days,
    }


@app.get("/api/stats")
def stats(session: Session = Depends(get_session)) -> dict[str, Any]:
    now = now_ist()
    cfg = get_config()
    rows = session.execute(_bid_query().where(Bid.end_date >= now)).all()
    short = [(b, s) for b, s, _ in rows if s and s.shortlisted]
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    last_run = session.scalar(select(RunLog).order_by(RunLog.id.desc()).limit(1))
    return {
        "open_bids": len(rows),
        "shortlisted_open": len(short),
        "new_today": sum(1 for b, _ in short if b.shortlisted_at and b.shortlisted_at >= today),
        "closing_soon": sum(
            1
            for b, _ in short
            if b.end_date <= now + timedelta(days=cfg.notifications.closing_soon_days)
            and b.feedback not in ("applied", "not_relevant")
        ),
        "eligible": sum(1 for _, s in short if s.eligibility_status in (ELIGIBLE, ELIGIBLE_EXEMPT)),
        "interested": sum(1 for b, _, _ in rows if b.feedback == "interested"),
        "applied": session.scalar(select(func.count()).select_from(Bid).where(Bid.feedback == "applied")),
        "changed": sum(1 for b, _ in short if b.changed_at and b.changed_at >= now - timedelta(days=2)),
        "last_run": _run(last_run) if last_run else None,
    }


@app.get("/api/bids")
def list_bids(
    q: str | None = None,
    view: str = Query("shortlisted", pattern="^(shortlisted|all|excluded)$"),
    include_closed: bool = False,
    eligibility: str | None = None,
    feedback: str | None = None,
    min_score: float | None = None,
    closing_within_days: float | None = None,
    changed_only: bool = False,
    sort: str = Query("score", pattern="^(score|closing|opening|published|newest)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    now = now_ist()
    query = _bid_query()
    if view == "shortlisted":
        query = query.where(Score.shortlisted.is_(True))
    elif view == "excluded":
        query = query.where(or_(Score.shortlisted.is_(False), Score.id.is_(None)))
    if not include_closed:
        query = query.where(Bid.end_date >= now)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            or_(
                Bid.title.ilike(like),
                Bid.items.ilike(like),
                Bid.bid_number.ilike(like),
                Bid.organisation.ilike(like),
                Bid.ministry.ilike(like),
                Bid.department.ilike(like),
                Bid.buyer_location.ilike(like),
            )
        )
    if eligibility:
        query = query.where(Score.eligibility_status.in_([e for e in eligibility.split(",") if e]))
    if feedback:
        wanted = [f for f in feedback.split(",") if f]
        conds = [Bid.feedback.in_([f for f in wanted if f != "none"])]
        if "none" in wanted:
            conds.append(Bid.feedback.is_(None))
        query = query.where(or_(*conds))
    if min_score is not None:
        query = query.where(Score.total >= min_score)
    if closing_within_days is not None:
        query = query.where(Bid.end_date <= now + timedelta(days=closing_within_days))
    if changed_only:
        query = query.where(Bid.changed_at.is_not(None))

    total = session.scalar(select(func.count()).select_from(query.subquery()))
    order = {
        "score": [Score.total.desc().nulls_last(), Bid.end_date.asc()],
        "closing": [Bid.end_date.asc()],
        "opening": [BidDocument.bid_opening.asc().nulls_last()],
        "published": [func.coalesce(BidDocument.published_at, Bid.start_date).desc()],
        "newest": [Bid.first_seen_at.desc(), Bid.id.desc()],
    }[sort]
    rows = session.execute(query.order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
    ids = [b.id for b, _, _ in rows]
    sow_ids = set(
        session.scalars(select(BidAttachment.bid_id).where(BidAttachment.bid_id.in_(ids), BidAttachment.kind == "sow"))
    )
    items = []
    for bid, score, doc in rows:
        item = _summary(bid, score, doc, now)
        item["has_sow"] = bid.id in sow_ids or bool(doc and doc.spec_sections)
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/api/bids/{bid_id}")
def bid_detail(bid_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    cfg = get_config()
    now = now_ist()
    bid = _get_bid(session, bid_id)
    doc = bid.document
    score = latest_scores(session, [bid.id]).get(bid.id)
    info = doc.to_info() if doc else None
    history = session.execute(
        select(Score.scored_at, Score.total).where(Score.bid_id == bid.id).order_by(Score.id.desc()).limit(30)
    ).all()

    spec_sections = [
        [{"group": g, "name": n, "value": v} for g, n, v in section] for section in (doc.spec_sections or [])
    ] if doc else []  # fmt: skip
    scope_rows = [
        row
        for section in spec_sections
        for row in section
        if any(k in row["name"].lower() for k in ("scope", "deliverable", "type of service", "category of service", "type of support"))
    ]
    data = _summary(bid, score, doc, now)
    data.update(
        {
            "items": bid.items,
            "office": bid.office,
            "buyer_state": doc.buyer_state if doc else None,
            "search_terms": bid.search_terms or [],
            "document": None
            if doc is None
            else {
                "parsed_ok": doc.parsed_ok,
                "fetched_at": doc.fetched_at,
                "has_local_pdf": bool(doc.pdf_path and Path(doc.pdf_path).exists()),
                "item_category": doc.item_category,
                "contract_period": doc.contract_period,
                "offer_validity_days": doc.offer_validity_days,
                "evaluation_method": doc.evaluation_method,
                "bid_packet_type": doc.bid_packet_type,
                "documents_required": doc.documents_required,
                "min_turnover_lakh": doc.min_turnover_lakh,
                "experience_years": doc.experience_years,
                "past_performance_pct": doc.past_performance_pct,
                "past_experience_required": doc.past_experience_required,
                "oem_authorization_required": doc.oem_authorization_required,
                "oem_turnover_lakh": doc.oem_turnover_lakh,
                "mse_relaxation": doc.mse_relaxation,
                "mse_relaxation_scope": doc.mse_relaxation_scope,
                "startup_relaxation": doc.startup_relaxation,
                "startup_relaxation_scope": doc.startup_relaxation_scope,
                "emd_amount": doc.emd_amount,
                "epbg_pct": doc.epbg_pct,
                "details": doc.details or [],
                "warnings": doc.warnings or [],
            },
            "eligibility": {
                "status": score.eligibility_status if score else NOT_CHECKED,
                "reasons": (score.eligibility_reasons or []) if score else [],
                "checklist": eligibility_checklist(info, cfg.company, cfg.filters),
            },
            "sow": {
                "scope_rows": scope_rows,
                "spec_sections": spec_sections,
                "atc_text": doc.atc_text if doc else None,
                "attachments": [_attachment(a) for a in bid.attachments],
            },
            "scoring": None
            if score is None
            else {
                "total": score.total,
                "threshold": cfg.filters.score_threshold,
                "keyword_score": score.keyword_score,
                "semantic_score": score.semantic_score,
                "matched_keywords": score.matched_keywords or [],
                "adjustments": score.adjustments or [],
                "excluded_reason": score.excluded_reason,
                "scored_at": score.scored_at,
                "history": [{"at": at, "total": total} for at, total in reversed(history)],
            },
            "feedback_note": bid.feedback_note,
            "feedback_at": bid.feedback_at,
            "shortlisted_at": bid.shortlisted_at,
            "last_seen_at": bid.last_seen_at,
        }
    )
    return data


@app.get("/api/bids/{bid_id}/pdf")
def bid_pdf(bid_id: int, session: Session = Depends(get_session)) -> FileResponse:
    bid = _get_bid(session, bid_id)
    if not bid.document or not bid.document.pdf_path:
        raise HTTPException(404, "No stored PDF for this bid")
    path = Path(bid.document.pdf_path).resolve()
    if not path.exists() or get_config().data_dir.resolve() not in path.parents:
        raise HTTPException(404, "Stored PDF is missing")
    return FileResponse(
        path, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{bid.gem_id}.pdf"'}
    )


@app.get("/api/attachments/{attachment_id}")
def attachment_text(attachment_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    att = session.get(BidAttachment, attachment_id)
    if att is None:
        raise HTTPException(404, "Attachment not found")
    return {**_attachment(att, preview=0), "text": att.text}


@app.post("/api/bids/{bid_id}/attachments/fetch")
def fetch_attachments(bid_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Fetch and extract the bid's SOW/BoQ/spec attachments now (takes a few seconds per file)."""
    bid = _get_bid(session, bid_id)
    client = GemClient(get_config().search)
    attempted = fetch_bid_attachments(session, client, bid, limit=10, retry=True)
    session.refresh(bid)
    return {"attempted": attempted, "attachments": [_attachment(a) for a in bid.attachments]}


class FeedbackIn(BaseModel):
    feedback: str | None = None
    note: str | None = None


@app.put("/api/bids/{bid_id}/feedback")
def update_feedback(bid_id: int, body: FeedbackIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        set_feedback(session, bid_id, body.feedback or None, body.note)
    except KeyError:
        raise HTTPException(404, "Bid not found") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    session.commit()
    bid = session.get(Bid, bid_id)
    return {"feedback": bid.feedback, "note": bid.feedback_note, "feedback_at": bid.feedback_at}


# -- runs ---------------------------------------------------------------------------------


# A running pipeline writes a heartbeat at least every few seconds while it makes requests;
# a single slow request with retries can take a few minutes, so allow generous slack.
STALE_AFTER = timedelta(minutes=15)


def _run(r: RunLog) -> dict[str, Any]:
    return {
        "id": r.id,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "status": r.status,
        "bids_fetched": r.bids_fetched,
        "bids_new": r.bids_new,
        "bids_changed": r.bids_changed,
        "pdfs_downloaded": r.pdfs_downloaded,
        "shortlisted": r.shortlisted,
        "requests_made": r.requests_made,
        "error": r.error,
        "heartbeat_at": r.heartbeat_at,
        "progress": r.progress,
    }


def _mark_stale_runs(session: Session) -> None:
    """Runs whose process died (server restart, crash) would otherwise show 'running' forever."""
    cutoff = now_ist() - STALE_AFTER
    for r in session.scalars(select(RunLog).where(RunLog.status == "running")):
        last_sign_of_life = r.heartbeat_at or r.started_at
        if last_sign_of_life < cutoff and not (state.run_thread and state.run_thread.is_alive()):
            r.status = "interrupted"
            r.finished_at = last_sign_of_life
            r.error = r.error or "The process running this was stopped before it finished (e.g. server restart)."
    session.commit()


@app.get("/api/runs")
def list_runs(limit: int = Query(30, le=200), session: Session = Depends(get_session)) -> dict[str, Any]:
    _mark_stale_runs(session)
    runs = list(session.scalars(select(RunLog).order_by(RunLog.id.desc()).limit(limit)))
    return {"items": [_run(r) for r in runs], "running": _run_in_progress(session)}


@app.get("/api/runs/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    run = session.get(RunLog, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return _run(run)


def _run_in_progress(session: Session) -> bool:
    if state.run_thread and state.run_thread.is_alive():
        return True
    _mark_stale_runs(session)  # a run in the scheduler container counts too, while it is alive
    return bool(session.scalar(select(func.count()).select_from(RunLog).where(RunLog.status == "running")))


class RunIn(BaseModel):
    notify: bool = False


@app.post("/api/runs", status_code=202)
def start_run(body: RunIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    from .pipeline import run_once

    with state.lock:
        if _run_in_progress(session):
            raise HTTPException(409, "A run is already in progress")

        def target() -> None:
            try:
                run_once(load_config(CONFIG_PATH), notify=body.notify)
            except Exception:
                log.exception("Manual run failed")

        state.run_thread = threading.Thread(target=target, name="manual-run", daemon=True)
        state.run_thread.start()
    return {"started": True}


# -- config & suggestions -----------------------------------------------------------------


class ConfigIn(BaseModel):
    text: str


@app.get("/api/config")
def read_config() -> dict[str, str]:
    return {"text": CONFIG_PATH.read_text(encoding="utf-8")}


@app.put("/api/config")
def write_config(body: ConfigIn) -> dict[str, Any]:
    try:
        save_config_text(CONFIG_PATH, body.text)
    except Exception as exc:
        raise HTTPException(400, f"Not saved: {exc}") from None
    return {"saved": True, "rescore": _after_config_change()}


def _after_config_change() -> dict[str, Any] | None:
    """Reload config and re-score stored tenders so the UI reflects new settings immediately."""
    from .pipeline import rescore_stored

    state.cfg = None
    cfg = get_config()
    with _sessions()() as session:
        if _run_in_progress(session):
            return None  # the running pipeline scores everything when it finishes
        try:
            return rescore_stored(session, cfg)
        except Exception:
            log.exception("Re-scoring after settings change failed")
            return None


def _settings_payload() -> dict[str, Any]:
    cfg = load_config(CONFIG_PATH)
    return {
        "values": config_values(cfg),
        "channels": channel_status(cfg),
        "env_file_found": Path(".env").exists(),
        "config_path": str(CONFIG_PATH.resolve()),
    }


@app.get("/api/settings")
def read_settings() -> dict[str, Any]:
    """Effective settings as structured data for the settings form."""
    return _settings_payload()


class SettingsIn(BaseModel):
    values: dict[str, Any]


@app.put("/api/settings")
def write_settings(body: SettingsIn) -> dict[str, Any]:
    """Partial update of config.yaml. Only the config file is written - stored bids are never touched."""
    allowed = set(config_values(load_config(CONFIG_PATH)))
    unknown = set(body.values) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown settings section(s): {sorted(unknown)}")
    try:
        update_config_values(CONFIG_PATH, body.values)
    except Exception as exc:
        raise HTTPException(400, f"Not saved: {exc}") from None
    rescore = _after_config_change()
    return {**_settings_payload(), "rescore": rescore}


class TestNotifyIn(BaseModel):
    channel: str | None = None


@app.post("/api/settings/test-notify")
def test_notify(body: TestNotifyIn) -> dict[str, Any]:
    """Send a short test message through each configured channel (or one of them)."""
    cfg = load_config(CONFIG_PATH)
    results = []
    for channel in build_channels(cfg):
        if body.channel and channel.name != body.channel:
            continue
        try:
            channel.send_alert("Test message", "GeM Tender Tracker can reach you on this channel.")
            results.append({"channel": channel.name, "ok": True})
        except Exception as exc:
            results.append({"channel": channel.name, "ok": False, "error": str(exc)[:300]})
    return {"results": results}


@app.get("/api/suggestions")
def suggestions(session: Session = Depends(get_session)) -> dict[str, Any]:
    cfg = get_config()
    bids = list(session.scalars(select(Bid)))
    scores = latest_scores(session)
    bad = [b.title for b in bids if b.feedback == "not_relevant"]
    good = [
        b.title
        for b in bids
        if b.feedback in ("interested", "applied")
        or (b.feedback is None and scores.get(b.id) is not None and scores[b.id].shortlisted)
    ]
    items = (
        suggest_exclude_keywords(bad, good, cfg.filters.include_keywords, cfg.filters.exclude_keywords)
        if len(bad) >= 3
        else []
    )
    return {
        "not_relevant_count": len(bad),
        "items": [{"term": t, "not_relevant": nb, "relevant": ng} for t, nb, ng in items],
    }


class TermsIn(BaseModel):
    terms: list[str]


@app.post("/api/suggestions/apply")
def apply_suggestions(body: TermsIn) -> dict[str, Any]:
    added = add_exclude_keywords(CONFIG_PATH, [t.strip() for t in body.terms if t.strip()])
    return {"added": added, "rescore": _after_config_change()}


# -- frontend -----------------------------------------------------------------------------


@app.exception_handler(404)
async def not_found(request: Request, exc: HTTPException):
    detail = getattr(exc, "detail", "Not found")
    if request.url.path.startswith("/api/") or not (FRONTEND_DIST / "index.html").exists():
        return JSONResponse({"detail": detail}, status_code=404)
    candidate = (FRONTEND_DIST / request.url.path.lstrip("/")).resolve()
    if candidate.is_file() and FRONTEND_DIST.resolve() in candidate.parents:
        return FileResponse(candidate)  # favicon etc.
    return FileResponse(FRONTEND_DIST / "index.html")  # client-side routes like /bids/12


if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def index():
    if (FRONTEND_DIST / "index.html").exists():
        return FileResponse(FRONTEND_DIST / "index.html")
    return JSONResponse({"detail": "Frontend not built. Run: cd frontend && npm install && npm run build"})
