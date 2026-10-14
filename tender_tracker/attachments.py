"""Fetch documents linked from a bid (SOW, BoQ, specifications) and extract their text.

Only hosts whose robots.txt allows it are fetched. In practice
``mkp.gem.gov.in`` (BoQ/specification uploads) and ``fulfilment.gem.gov.in``
(service/SOW documents) are allowed, while buyer uploads under
``bidplus.gem.gov.in/resources/`` are disallowed. Those stay as links that
the user opens in their own browser.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .config import now_ist
from .extract import extract
from .fetcher import FetchError, GemClient
from .storage import Bid, BidAttachment

log = logging.getLogger(__name__)

# Kinds worth reading for scope of work / eligibility; GTC and policy documents are generic.
TEXT_KINDS = ("sow", "spec", "boq", "eligibility", "buyer_doc", "sla", "other")
MAX_TEXT_CHARS = 60_000


def extract_document_text(content: bytes, content_type: str, url: str) -> str | None:
    """Text of a PDF/DOCX/CSV/XLSX/HTML document, or None if the format isn't supported."""
    result = extract(content, content_type, url, max_pdf_pages=40)
    return None if result is None else result.text


def fetch_attachment(client: GemClient, att: BidAttachment) -> None:
    att.fetched_at = now_ist()
    if not client.robots_allowed(att.url):
        att.status, att.error = "blocked_by_robots", "The host's robots.txt disallows automated access; open the link."
        return
    try:
        result = client.download(att.url)
    except FetchError as exc:
        att.status, att.error = "error", str(exc)
        return
    if result is None:
        att.status, att.error = "unavailable", "Not available (closed bid, removed, or larger than 15 MB)."
        return
    content, ctype = result
    att.content_type = ctype[:128] or None
    try:
        result = extract(content, ctype, att.url, max_pdf_pages=40)
    except Exception as exc:  # corrupt files
        log.warning("Could not extract %s: %s", att.url, exc)
        att.status, att.error = "error", f"Could not read file: {exc}"
        return
    text = None if result is None else result.text
    if result is not None and result.rows:
        att.table = result.rows[:2000]  # BoQ line items etc.
    if text is None:
        att.status, att.error = "unsupported", f"Unsupported file type ({ctype or 'unknown'})."
        return
    if len(text.strip()) < 20:
        att.status, att.error = "unsupported", "No extractable text (scanned document?)."
        return
    att.text = text[:MAX_TEXT_CHARS]
    att.status, att.error = "fetched", None


def fetch_bid_attachments(
    session: Session, client: GemClient, bid: Bid, limit: int = 10, retry: bool = False
) -> int:
    """Fetch text for a bid's useful attachments. Returns the number of downloads attempted
    (links blocked by robots.txt cost no request and don't count)."""
    todo = [
        a
        for a in bid.attachments
        if a.kind in TEXT_KINDS
        and (a.status == "not_fetched" or (retry and a.status in ("error", "unavailable", "unsupported")))
    ]
    downloads = 0
    for att in todo:
        if downloads >= limit:
            break
        fetch_attachment(client, att)
        session.commit()
        log.info("Attachment %s (%s): %s", att.label, bid.bid_number, att.status)
        if att.status != "blocked_by_robots":
            downloads += 1
    return downloads
