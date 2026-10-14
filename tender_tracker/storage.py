"""Persistence: SQLAlchemy models and helpers (SQLite by default, PostgreSQL via DATABASE_URL)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import now_ist
from .fetcher import ListingBid
from .pdf_parser import PARSER_VERSION, BidDocumentInfo

FEEDBACK_CHOICES = {"interested": "Interested", "applied": "Applied", "not_relevant": "Not relevant"}


class Base(DeclarativeBase):
    pass


class Bid(Base):
    __tablename__ = "bids"

    id: Mapped[int] = mapped_column(primary_key=True)
    bid_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    gem_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[str] = mapped_column(Text, default="")
    ministry: Mapped[str | None] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(255))
    organisation: Mapped[str | None] = mapped_column(String(255))
    office: Mapped[str | None] = mapped_column(String(255))
    buyer_location: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[datetime | None] = mapped_column(DateTime)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    estimated_value: Mapped[float | None] = mapped_column(Float)
    bid_type: Mapped[str] = mapped_column(String(16), default="Bid")
    doc_url: Mapped[str] = mapped_column(String(255))
    gem_status: Mapped[str] = mapped_column(String(16), default="active")  # active | cancelled
    has_corrigendum: Mapped[bool] = mapped_column(Boolean, default=False)
    search_terms: Mapped[list[str] | None] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    change_summary: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    shortlisted_at: Mapped[datetime | None] = mapped_column(DateTime)
    feedback: Mapped[str | None] = mapped_column(String(32), index=True)
    feedback_note: Mapped[str | None] = mapped_column(Text)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    document: Mapped[BidDocument | None] = relationship(
        back_populates="bid", uselist=False, cascade="all, delete-orphan"
    )
    scores: Mapped[list[Score]] = relationship(back_populates="bid", cascade="all, delete-orphan")
    attachments: Mapped[list[BidAttachment]] = relationship(
        back_populates="bid", cascade="all, delete-orphan", order_by="BidAttachment.position"
    )

    @property
    def link(self) -> str:
        return self.doc_url


class BidDocument(Base):
    __tablename__ = "bid_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    bid_id: Mapped[int] = mapped_column(ForeignKey("bids.id", ondelete="CASCADE"), unique=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    pdf_path: Mapped[str | None] = mapped_column(String(512))
    sha256: Mapped[str | None] = mapped_column(String(64))
    parsed_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    min_turnover_lakh: Mapped[float | None] = mapped_column(Float)
    oem_turnover_lakh: Mapped[float | None] = mapped_column(Float)
    experience_years: Mapped[float | None] = mapped_column(Float)
    past_performance_pct: Mapped[float | None] = mapped_column(Float)
    mse_relaxation: Mapped[bool | None] = mapped_column(Boolean)
    mse_relaxation_scope: Mapped[str | None] = mapped_column(String(64))
    startup_relaxation: Mapped[bool | None] = mapped_column(Boolean)
    startup_relaxation_scope: Mapped[str | None] = mapped_column(String(64))
    emd_amount: Mapped[float | None] = mapped_column(Float)
    epbg_pct: Mapped[float | None] = mapped_column(Float)
    estimated_value: Mapped[float | None] = mapped_column(Float)
    oem_authorization_required: Mapped[bool] = mapped_column(Boolean, default=False)
    bid_packet_type: Mapped[str | None] = mapped_column(String(64))
    buyer_location: Mapped[str | None] = mapped_column(Text)
    buyer_state: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    bid_opening: Mapped[datetime | None] = mapped_column(DateTime)
    offer_validity_days: Mapped[int | None] = mapped_column(Integer)
    contract_period: Mapped[str | None] = mapped_column(String(128))
    item_category: Mapped[str | None] = mapped_column(Text)
    evaluation_method: Mapped[str | None] = mapped_column(String(128))
    past_experience_required: Mapped[bool | None] = mapped_column(Boolean)
    documents_required: Mapped[str | None] = mapped_column(Text)
    atc_text: Mapped[str | None] = mapped_column(Text)
    spec_sections: Mapped[list[Any] | None] = mapped_column(JSON)
    details: Mapped[list[Any] | None] = mapped_column(JSON)
    parser_version: Mapped[int | None] = mapped_column(Integer)
    fields: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    warnings: Mapped[list[str] | None] = mapped_column(JSON)

    bid: Mapped[Bid] = relationship(back_populates="document")

    def to_info(self) -> BidDocumentInfo:
        info = BidDocumentInfo(
            bid_number=self.bid.bid_number if self.parsed_ok else None,
            warnings=list(self.warnings or []),
        )
        for name in DOCUMENT_FIELDS:
            setattr(info, name, getattr(self, name))
        info.oem_authorization_required = bool(self.oem_authorization_required)
        return info


# Fields copied 1:1 between BidDocumentInfo and BidDocument.
DOCUMENT_FIELDS = (
    "min_turnover_lakh", "oem_turnover_lakh", "experience_years", "past_performance_pct",
    "mse_relaxation", "mse_relaxation_scope", "startup_relaxation", "startup_relaxation_scope",
    "emd_amount", "epbg_pct", "estimated_value", "oem_authorization_required", "bid_packet_type",
    "buyer_location", "buyer_state", "published_at", "bid_opening", "offer_validity_days",
    "contract_period", "item_category", "evaluation_method", "past_experience_required",
    "documents_required", "atc_text",
)  # fmt: skip


class BidAttachment(Base):
    """A document linked from the bid PDF (SOW, BoQ, specification, ATC, ...)."""

    __tablename__ = "bid_attachments"
    __table_args__ = (Index("ix_attachment_bid_url", "bid_id", "url", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bid_id: Mapped[int] = mapped_column(ForeignKey("bids.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1024))
    label: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer, default=0)
    # not_fetched | fetched | blocked_by_robots | unavailable | unsupported | error
    status: Mapped[str] = mapped_column(String(32), default="not_fetched")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    content_type: Mapped[str | None] = mapped_column(String(128))
    text: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    bid: Mapped[Bid] = relationship(back_populates="attachments")


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    bid_id: Mapped[int] = mapped_column(ForeignKey("bids.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run_logs.id"), index=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    total: Mapped[float] = mapped_column(Float)
    keyword_score: Mapped[float] = mapped_column(Float, default=0)
    semantic_score: Mapped[float | None] = mapped_column(Float)
    eligibility_status: Mapped[str] = mapped_column(String(64))
    eligibility_reasons: Mapped[list[str] | None] = mapped_column(JSON)
    matched_keywords: Mapped[list[str] | None] = mapped_column(JSON)
    adjustments: Mapped[list[str] | None] = mapped_column(JSON)
    excluded_reason: Mapped[str | None] = mapped_column(Text)
    shortlisted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    days_left: Mapped[float | None] = mapped_column(Float)

    bid: Mapped[Bid] = relationship(back_populates="scores")


class NotificationSent(Base):
    __tablename__ = "notifications_sent"
    __table_args__ = (Index("ix_notifications_bid_kind", "bid_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bid_id: Mapped[int | None] = mapped_column(ForeignKey("bids.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run_logs.id"))
    channel: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))  # new | closing | changed | digest | alert
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)


class RunLog(Base):
    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | success | failed | blocked
    bids_fetched: Mapped[int] = mapped_column(Integer, default=0)
    bids_new: Mapped[int] = mapped_column(Integer, default=0)
    bids_changed: Mapped[int] = mapped_column(Integer, default=0)
    pdfs_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    shortlisted: Mapped[int] = mapped_column(Integer, default=0)
    requests_made: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # live status while running
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)


# -- engine / session --------------------------------------------------------------


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")  # scheduler and dashboard share the file
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine: Engine) -> None:
    """Minimal forward migration: add nullable columns introduced after a table was created."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing:
                    col_type = column.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {col_type}'))


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


# -- bids ----------------------------------------------------------------------------


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%d-%m-%Y %H:%M") if dt else "?"


def describe_changes(bid: Bid, listing: ListingBid) -> list[str]:
    changes: list[str] = []
    if listing.end_date and bid.end_date and listing.end_date != bid.end_date:
        verb = "extended" if listing.end_date > bid.end_date else "brought forward"
        changes.append(f"Deadline {verb}: {_fmt_dt(bid.end_date)} -> {_fmt_dt(listing.end_date)}")
    if listing.quantity is not None and bid.quantity is not None and listing.quantity != bid.quantity:
        changes.append(f"Quantity changed: {bid.quantity} -> {listing.quantity}")
    if listing.title != bid.title or listing.items != bid.items:
        changes.append("Items/title changed")
    if listing.cancelled and bid.gem_status != "cancelled":
        changes.append("Bid cancelled")
    return changes


def upsert_listing(session: Session, listing: ListingBid, now: datetime | None = None) -> tuple[Bid, bool, list[str]]:
    """Insert or update a bid from the listing. Returns (bid, is_new, changes)."""
    now = now or now_ist()
    bid = session.scalar(select(Bid).where(Bid.bid_number == listing.bid_number))
    fingerprint = listing.fingerprint()
    changes: list[str] = []
    is_new = bid is None
    if bid is None:
        bid = Bid(bid_number=listing.bid_number, first_seen_at=now)
        session.add(bid)
    elif bid.fingerprint != fingerprint:
        changes = describe_changes(bid, listing)
        if changes:
            record_change(bid, changes, now)

    bid.gem_id = listing.b_id
    bid.title = listing.title
    bid.items = listing.items
    bid.quantity = listing.quantity
    bid.ministry = listing.ministry or bid.ministry
    bid.department = listing.department or bid.department
    bid.start_date = listing.start_date
    bid.end_date = listing.end_date
    bid.bid_type = listing.bid_type
    bid.doc_url = listing.doc_url
    bid.gem_status = "cancelled" if listing.cancelled else "active"
    bid.search_terms = sorted(set((bid.search_terms or []) + listing.search_terms))
    bid.fingerprint = fingerprint
    bid.last_seen_at = now
    bid.raw = listing.raw
    return bid, is_new, changes


def record_change(bid: Bid, changes: list[str], now: datetime | None = None) -> None:
    bid.change_summary = "; ".join(changes)
    bid.changed_at = now or now_ist()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def save_document(
    session: Session, bid: Bid, info: BidDocumentInfo, pdf_path: Path | None, now: datetime | None = None
) -> BidDocument:
    doc = bid.document
    if doc is None:
        doc = BidDocument()
        bid.document = doc  # set from the parent side so the save cascade applies
        session.add(doc)
    doc.fetched_at = now or now_ist()
    doc.pdf_path = str(pdf_path) if pdf_path else None
    doc.sha256 = file_sha256(pdf_path) if pdf_path and pdf_path.exists() else None
    doc.parsed_ok = info.is_reliable
    for name in DOCUMENT_FIELDS:
        setattr(doc, name, getattr(info, name))
    doc.spec_sections = [[[r.group, r.name, r.value] for r in section] for section in info.spec_sections]
    doc.details = [list(pair) for pair in info.details]
    doc.parser_version = PARSER_VERSION
    doc.fields = info.to_dict()
    doc.warnings = info.warnings

    # Fields only the PDF carries are copied onto the bid for searching/filtering.
    bid.organisation = info.organisation or bid.organisation
    bid.office = info.office or bid.office
    bid.buyer_location = info.buyer_location or info.buyer_state or bid.buyer_location
    bid.estimated_value = info.estimated_value or bid.estimated_value
    sync_attachments(session, bid, info)
    return doc


def sync_attachments(session: Session, bid: Bid, info: BidDocumentInfo) -> None:
    """Add newly linked documents; keep already-fetched text for links that still exist."""
    existing = {a.url: a for a in bid.attachments}
    wanted = {a.url for a in info.attachments}
    for att in list(bid.attachments):
        if att.url not in wanted:
            bid.attachments.remove(att)
    for position, a in enumerate(info.attachments):
        row = existing.get(a.url)
        if row is None:
            row = BidAttachment(url=a.url, label=a.label[:255], kind=a.kind, position=position)
            bid.attachments.append(row)
            session.add(row)
        else:
            row.label, row.kind, row.position = a.label[:255], a.kind, position


def latest_scores(session: Session, bid_ids: list[int] | None = None) -> dict[int, Score]:
    """Most recent score per bid."""
    sub = select(Score.bid_id, func.max(Score.id).label("max_id")).group_by(Score.bid_id)
    if bid_ids is not None:
        sub = sub.where(Score.bid_id.in_(bid_ids))
    sub = sub.subquery()
    rows = session.scalars(select(Score).join(sub, Score.id == sub.c.max_id))
    return {s.bid_id: s for s in rows}


def set_feedback(session: Session, bid_id: int, feedback: str | None, note: str | None = None) -> None:
    if feedback is not None and feedback not in FEEDBACK_CHOICES:
        raise ValueError(f"feedback must be one of {list(FEEDBACK_CHOICES)}")
    bid = session.get(Bid, bid_id)
    if bid is None:
        raise KeyError(bid_id)
    bid.feedback = feedback
    if note is not None:
        bid.feedback_note = note
    bid.feedback_at = now_ist()


def was_notified(session: Session, bid_id: int, kind: str) -> bool:
    return (
        session.scalar(
            select(NotificationSent.id)
            .where(NotificationSent.bid_id == bid_id, NotificationSent.kind == kind)
            .limit(1)
        )
        is not None
    )


def last_successful_digest(session: Session) -> datetime | None:
    return session.scalar(select(func.max(NotificationSent.sent_at)).where(NotificationSent.kind == "digest"))
