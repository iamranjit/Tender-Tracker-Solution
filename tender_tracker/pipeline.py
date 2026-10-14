"""One daily run: fetch -> store/diff -> relevance -> PDFs -> eligibility/score -> digest."""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from . import notifier
from .config import Config, now_ist
from .fetcher import BlockedError, GemClient, ListingBid, PortalChangedError
from .filter_scorer import (
    BidForScoring,
    SemanticScorer,
    keyword_relevance,
    relevance,
    score_bid,
)
from .attachments import fetch_bid_attachments
from .pdf_parser import PARSER_VERSION, parse_pdf
from .progress import NullProgress, RunProgress
from .report import build_digest, export_excel
from .storage import (
    Bid,
    RunLog,
    Score,
    init_db,
    make_engine,
    record_change,
    save_document,
    session_factory,
    upsert_listing,
)

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    run_id: int
    status: str
    stats: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    excel_path: Path | None = None
    channels_sent: list[str] = field(default_factory=list)


def to_scoring(bid: Bid) -> BidForScoring:
    return BidForScoring(
        bid_number=bid.bid_number,
        title=bid.title or "",
        description=bid.items or "",
        ministry=bid.ministry or "",
        department=bid.department or "",
        organisation=bid.organisation or "",
        buyer_location=bid.buyer_location or "",
        end_date=bid.end_date,
        estimated_value=bid.estimated_value,
    )


def _process(
    session: Session,
    cfg: Config,
    client: GemClient,
    listings: dict[str, ListingBid],
    run: RunLog,
    semantic: SemanticScorer,
    now: datetime,
    progress: RunProgress | None = None,
) -> dict[str, int]:
    progress = progress or NullProgress()
    stats = {"fetched": len(listings), "new": 0, "changed": 0, "pdfs": 0, "pdf_failures": 0, "shortlisted": 0}
    bids: list[Bid] = []
    changed_now: set[str] = set()
    progress.phase("store", total=len(listings), message=f"Saving {len(listings)} bids and checking for changes")
    for listing in listings.values():
        bid, is_new, changes = upsert_listing(session, listing, now)
        bids.append(bid)
        stats["new"] += is_new
        if changes:
            stats["changed"] += 1
            changed_now.add(bid.bid_number)
    session.commit()
    log.info("Stored %d bids (%d new, %d changed)", len(bids), stats["new"], stats["changed"])
    progress.event(
        f"{stats['new']} new bids, {stats['changed']} changed since last run", new=stats["new"], changed=stats["changed"]
    )

    # Stage 1: cheap relevance on listing data only
    scoring = {b.id: to_scoring(b) for b in bids}
    keywords = {b.id: keyword_relevance(scoring[b.id], cfg.filters) for b in bids}
    sims = semantic.similarities([f"{b.title}. {(b.items or '')[:300]}" for b in bids])
    semantic_by_id = {b.id: s for b, s in zip(bids, sims)}
    rel = {b.id: relevance(keywords[b.id], semantic_by_id[b.id], cfg.scoring) for b in bids}

    # Stage 2: PDFs only for relevant bids that are still worth preparing for
    min_days = cfg.filters.min_days_until_closing
    candidates = [
        b
        for b in bids
        if rel[b.id] >= cfg.filters.pdf_min_relevance
        and b.gem_status == "active"
        and b.end_date is not None
        and (b.end_date - now).total_seconds() / 86400 >= min(min_days, cfg.notifications.closing_soon_days)
    ]
    candidates.sort(key=lambda b: rel[b.id], reverse=True)
    pdf_dir = cfg.data_dir / "pdfs"
    budget = cfg.search.max_pdfs_per_run
    progress.phase("documents", total=len(candidates), message=f"Reading documents of {len(candidates)} relevant bids")
    for n, bid in enumerate(candidates, 1):
        progress.step(done=n - 1, message=f"Reading {bid.bid_number}: {bid.title[:70]}",
                      pdfs=stats["pdfs"], requests=client.requests_made)  # fmt: skip
        if bid.shortlisted_at and cfg.search.check_corrigendum and not bid.has_corrigendum:
            details = client.bid_other_details(bid.gem_id)
            if details and details.get("corrigendum"):
                bid.has_corrigendum = True
                changes = ["Corrigendum issued"]
                if bid.bid_number in changed_now and bid.change_summary:
                    changes.append(bid.change_summary)
                else:
                    stats["changed"] += 1
                record_change(bid, changes, now)
                changed_now.add(bid.bid_number)
                progress.event(f"Corrigendum issued for {bid.bid_number}")
        doc = bid.document
        if doc is not None and doc.parsed_ok and bid.bid_number not in changed_now:
            if (doc.parser_version or 1) < PARSER_VERSION and doc.pdf_path and Path(doc.pdf_path).exists():
                save_document(session, bid, parse_pdf(Path(doc.pdf_path)), Path(doc.pdf_path), now)  # parser upgrade
                session.commit()
                scoring[bid.id] = to_scoring(bid)
            continue
        if budget <= 0:
            log.info("PDF budget (%d) exhausted for this run", cfg.search.max_pdfs_per_run)
            break
        budget -= 1
        path = client.download_pdf(bid.doc_url, pdf_dir / f"{bid.gem_id}.pdf")
        if path is None:
            stats["pdf_failures"] += 1
            continue
        info = parse_pdf(path)
        if info.bid_number and info.bid_number != bid.bid_number:
            info.warnings.append(f"PDF bid number {info.bid_number} differs from listing {bid.bid_number}")
        save_document(session, bid, info, path, now)
        stats["pdfs"] += 1
        if not info.is_reliable:
            stats["pdf_failures"] += 1
        session.commit()
        scoring[bid.id] = to_scoring(bid)  # organisation/location/value now known

    progress.event(f"Read {stats['pdfs']} new bid documents", pdfs=stats["pdfs"])

    # Stage 3: final scores
    progress.phase("scoring", total=len(bids), message=f"Scoring {len(bids)} bids against your profile")
    for n, bid in enumerate(bids, 1):
        if n % 100 == 0:
            progress.step(done=n, shortlisted=stats["shortlisted"])
        doc = bid.document.to_info() if bid.document is not None else None
        result = score_bid(
            scoring[bid.id],
            cfg.filters,
            cfg.scoring,
            cfg.company,
            doc=doc,
            semantic=semantic_by_id[bid.id],
            now=now,
            keyword=keywords[bid.id],
        )
        session.add(
            Score(
                bid=bid,
                run_id=run.id,
                scored_at=now,
                total=result.total,
                keyword_score=result.keyword_score,
                semantic_score=result.semantic_score,
                eligibility_status=result.eligibility_status,
                eligibility_reasons=result.eligibility_reasons,
                matched_keywords=result.matched_keywords,
                adjustments=result.adjustments,
                excluded_reason=result.excluded_reason,
                shortlisted=result.shortlisted,
                days_left=result.days_left,
            )
        )
        if result.shortlisted:
            stats["shortlisted"] += 1
            bid.shortlisted_at = bid.shortlisted_at or now
    session.commit()
    progress.event(f"{stats['shortlisted']} bids shortlisted", shortlisted=stats["shortlisted"])

    # Stage 4: scope-of-work attachments for shortlisted bids (robots.txt permitting)
    stats["attachments"] = 0
    if cfg.search.fetch_attachments:
        budget = cfg.search.max_attachments_per_run
        targets = sorted((b for b in bids if b.shortlisted_at and b.gem_status == "active"), key=lambda b: -rel[b.id])
        progress.phase(
            "attachments", total=len(targets), message=f"Reading scope-of-work files of {len(targets)} shortlisted bids"
        )
        for n, bid in enumerate(targets, 1):
            if budget <= 0:
                break
            progress.step(done=n - 1, message=f"Documents of {bid.bid_number}", files=stats["attachments"],
                          requests=client.requests_made)  # fmt: skip
            done = fetch_bid_attachments(session, client, bid, limit=min(5, budget))
            budget -= done
            stats["attachments"] += done
        progress.event(f"Read {stats['attachments']} scope-of-work files", files=stats["attachments"])
    return stats


def rescore_stored(session: Session, cfg: Config, now: datetime | None = None) -> dict[str, int]:
    """Re-score open tenders from stored data only (no requests to GeM).

    Used after settings change (company profile, keywords, thresholds) so
    eligibility and the shortlist reflect them immediately. The semantic
    similarity from the last scoring is reused, so no ML model is needed.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from .storage import latest_scores

    now = now or now_ist()
    bids = list(
        session.scalars(select(Bid).options(selectinload(Bid.document)).where(Bid.end_date >= now))
    )
    previous = latest_scores(session, [b.id for b in bids])
    shortlisted = 0
    for bid in bids:
        prev = previous.get(bid.id)
        semantic = prev.semantic_score if prev else None
        doc = bid.document.to_info() if bid.document is not None else None
        r = score_bid(to_scoring(bid), cfg.filters, cfg.scoring, cfg.company, doc=doc, semantic=semantic, now=now)
        session.add(
            Score(
                bid=bid, run_id=None, scored_at=now, total=r.total, keyword_score=r.keyword_score,
                semantic_score=r.semantic_score, eligibility_status=r.eligibility_status,
                eligibility_reasons=r.eligibility_reasons, matched_keywords=r.matched_keywords,
                adjustments=r.adjustments, excluded_reason=r.excluded_reason, shortlisted=r.shortlisted,
                days_left=r.days_left,
            )
        )  # fmt: skip
        if r.shortlisted:
            shortlisted += 1
            bid.shortlisted_at = bid.shortlisted_at or now
    session.commit()
    log.info("Re-scored %d open tenders from stored data: %d shortlisted", len(bids), shortlisted)
    return {"rescored": len(bids), "shortlisted": shortlisted}


def _search_progress(progress: RunProgress, client: GemClient):
    """Adapter from GemClient.fetch_all's per-page callback to RunProgress."""
    pages_by_term: dict[int, int] = {}
    pages_done = 0

    def on_page(term_index: int, term_count: int, term: str, page: int, pages: int, hits: int, unique: int) -> None:
        nonlocal pages_done
        pages_done += 1
        if page == 1:
            pages_by_term[term_index] = max(pages, 1)
            progress.event(f'"{term}": {hits} open bids on GeM, reading {pages} page(s)')
        known = sum(pages_by_term.values())
        estimated_total = known + (term_count - len(pages_by_term)) * known / len(pages_by_term)
        progress.step(
            done=pages_done,
            total=max(int(estimated_total), pages_done),
            message=f'Searching "{term}" (term {term_index + 1} of {term_count}): page {page} of {pages}',
            term=f"{term_index + 1}/{term_count}",
            bids_found=unique,
            requests=client.requests_made,
        )

    return on_page


def cleanup_old_pdfs(cfg: Config, now: datetime) -> int:
    pdf_dir = cfg.data_dir / "pdfs"
    if not pdf_dir.exists():
        return 0
    cutoff = time.mktime((now - timedelta(days=cfg.storage.keep_pdfs_days)).timetuple())
    removed = 0
    for path in pdf_dir.glob("*.pdf"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def run_once(
    cfg: Config,
    *,
    notify: bool = True,
    client: GemClient | None = None,
    semantic: SemanticScorer | None = None,
    channels: list[notifier.Channel] | None = None,
    now: datetime | None = None,
) -> RunResult:
    now = now or now_ist()
    engine = make_engine(cfg.storage.effective_database_url())
    init_db(engine)
    Session_ = session_factory(engine)
    client = client or GemClient(cfg.search)

    with Session_() as session:
        run = RunLog(started_at=now, status="running")
        session.add(run)
        session.commit()
        result = RunResult(run_id=run.id, status="running")
        alert: tuple[str, str] | None = None
        progress = RunProgress(Session_, run.id)
        progress.phase("search", total=len(cfg.search.terms) * cfg.search.max_pages_per_term,
                       message=f"Searching GeM for {len(cfg.search.terms)} terms")  # fmt: skip
        try:
            listings = client.fetch_all(on_progress=_search_progress(progress, client))
            progress.event(f"Found {len(listings)} unique open bids", bids_found=len(listings))
            if not listings:
                raise PortalChangedError(
                    "GeM search returned zero ongoing bids for every search term. "
                    "The portal or its /all-bids-data API has probably changed."
                )
            semantic = semantic or SemanticScorer(cfg.semantic, cfg.company.services)
            result.stats = _process(session, cfg, client, listings, run, semantic, now, progress)
            result.status = "success"
            if result.stats["pdfs"] >= 5 and result.stats["pdf_failures"] > result.stats["pdfs"] / 2:
                alert = (
                    "Most bid PDFs could not be parsed",
                    f"{result.stats['pdf_failures']} of {result.stats['pdfs']} PDFs failed to parse. "
                    "The GeM bid document layout may have changed; eligibility results are unreliable.",
                )
        except BlockedError as exc:
            result.status, result.error = "blocked", str(exc)
            progress.event(f"Stopped: {exc}")
            alert = ("GeM blocked the scraper", f"{exc}\n\nThe run was stopped. It will retry at the next schedule.")
        except PortalChangedError as exc:
            result.status, result.error = "failed", str(exc)
            progress.event(f"Failed: {exc}")
            alert = ("GeM portal may have changed", str(exc))
        except Exception as exc:
            log.exception("Run failed")
            result.status, result.error = "failed", f"{type(exc).__name__}: {exc}"
            progress.event(f"Failed: {result.error}")
            alert = ("Daily run failed", traceback.format_exc()[-3000:])
        finally:
            session.rollback()  # discard any half-finished transaction before logging
            run = session.get(RunLog, run.id)
            run.finished_at = now_ist()
            run.status = result.status
            run.error = result.error
            run.requests_made = client.requests_made
            s = result.stats
            run.bids_fetched, run.bids_new, run.bids_changed = s.get("fetched", 0), s.get("new", 0), s.get("changed", 0)
            run.pdfs_downloaded, run.shortlisted = s.get("pdfs", 0), s.get("shortlisted", 0)
            run.details = s
            session.commit()
        log.info("Run %d finished: %s %s", run.id, result.status, result.stats or result.error)

        if notify and alert:
            notifier.send_alert(cfg, *alert, channels=channels)
        if notify and result.status == "success":
            progress.phase("notify", message="Sending the daily digest")
            digest = build_digest(session, cfg, run.id, now)
            if alert:
                digest.warnings.append(alert[0])
            digest.stats = result.stats
            if cfg.notifications.attach_excel:
                result.excel_path = export_excel(digest, cfg.data_dir / "exports" / f"gem_shortlist_{now:%Y-%m-%d}.xlsx")
            result.channels_sent = notifier.send_digest(
                session, cfg, digest, result.excel_path, run.id, channels=channels
            )
            progress.event(
                f"Digest sent via {', '.join(result.channels_sent)}"
                if result.channels_sent
                else "Digest not sent (no notification channel configured, or sending failed)"
            )
        if result.status == "success":
            progress.finish()
        removed = cleanup_old_pdfs(cfg, now)
        if removed:
            log.info("Removed %d old PDFs", removed)
    engine.dispose()
    return result
