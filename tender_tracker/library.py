"""Company document library: capability documents and previous proposals.

Files are stored under ``<data_dir>/library/`` with their extracted text in the
database, so they can be matched against a tender's mandatory documents and
(previous proposals) reused as reference material when drafting.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Config, now_ist
from .extract import extract
from .storage import CompanyDocument

MAX_UPLOAD_BYTES = 40_000_000
MAX_TEXT_CHARS = 400_000

# category -> (label, phrases that identify a tender's document requirement as this category)
CAPABILITY_CATEGORIES: dict[str, tuple[str, list[str]]] = {
    "incorporation": ("Certificate of incorporation / registration", ["incorporation", "registration certificate", "certificate of registration", "partnership deed", "firm registration", "llp agreement", "moa", "memorandum of association"]),
    "pan": ("PAN card", ["pan card", "pan"]),
    "gst": ("GST registration certificate", ["gst", "gstin"]),
    "udyam": ("Udyam / MSME certificate", ["udyam", "msme", "mse certificate", "nsic", "mse"]),
    "dpiit": ("DPIIT startup recognition", ["dpiit", "startup", "dipp"]),
    "turnover": ("Turnover certificate (CA)", ["turnover", "chartered accountant", "ca certificate"]),
    "financials": ("Audited financials / ITR", ["balance sheet", "audited", "itr", "income tax return", "profit and loss", "financial statement"]),
    "experience": ("Work orders / completion certificates", ["experience", "work order", "purchase order", "completion", "past performance", "similar work", "client certificate", "satisfactory performance", "contract copy"]),
    "certifications": ("ISO / CMMI / quality certificates", ["iso", "cmmi", "quality certif", "certification", "cert-in", "stqc"]),
    "oem": ("OEM authorisation", ["oem authori", "manufacturer authori", "authorisation certificate", "authorization certificate", "maf"]),
    "empanelment": ("Empanelment letters", ["empanel", "meity"]),
    "undertaking": ("Undertakings / declarations", ["undertaking", "declaration", "affidavit", "blacklist", "debar", "integrity pact", "self-certificate", "self certificate"]),
    "bank": ("Bank details / cancelled cheque", ["bank", "cancelled cheque", "mandate form"]),
    "team": ("Team CVs / manpower details", ["cv", "resume", "curriculum vitae", "manpower", "key personnel"]),
    "profile": ("Company profile / case studies", ["company profile", "brochure", "case stud", "presentation"]),
    "other": ("Other", []),
}  # fmt: skip

PROPOSAL_OUTCOMES = ("won", "lost", "submitted")
# Documents normally prepared fresh for each bid (signed on the bidder's letterhead).
PER_BID_CATEGORIES = {"undertaking"}


def library_dir(cfg: Config) -> Path:
    path = cfg.data_dir / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "document"
    return name[:120]


def add_document(
    session: Session,
    cfg: Config,
    *,
    kind: str,
    category: str,
    title: str,
    filename: str,
    content: bytes,
    content_type: str = "",
    **meta: Any,
) -> CompanyDocument:
    if kind not in ("capability", "proposal"):
        raise ValueError("kind must be 'capability' or 'proposal'")
    if kind == "capability" and category not in CAPABILITY_CATEGORIES:
        raise ValueError(f"Unknown category '{category}'")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File is larger than {MAX_UPLOAD_BYTES // 1_000_000} MB")
    if meta.get("outcome") and meta["outcome"] not in PROPOSAL_OUTCOMES:
        raise ValueError(f"outcome must be one of {PROPOSAL_OUTCOMES}")
    if meta.get("valid_until"):
        datetime.strptime(meta["valid_until"], "%Y-%m-%d")  # raises ValueError on bad dates

    stored = library_dir(cfg) / f"{uuid.uuid4().hex[:12]}_{_safe_name(filename)}"
    stored.write_bytes(content)
    try:
        extracted = extract(content, content_type, filename)
    except Exception:
        extracted = None
    text = (extracted.text if extracted else "") or ""
    doc = CompanyDocument(
        kind=kind,
        category=category if kind == "capability" else "proposal",
        title=title.strip() or Path(filename).stem,
        filename=Path(filename).name,
        path=str(stored),
        size_bytes=len(content),
        uploaded_at=now_ist(),
        text=text[:MAX_TEXT_CHARS] or None,
        text_status="unsupported" if extracted is None else ("ok" if text.strip() else "empty"),
        **{k: v for k, v in meta.items() if v not in (None, "")},
    )
    session.add(doc)
    session.commit()
    return doc


def delete_document(session: Session, doc: CompanyDocument) -> None:
    Path(doc.path).unlink(missing_ok=True)
    session.delete(doc)
    session.commit()


def is_expired(doc: CompanyDocument, on: datetime | None = None) -> bool:
    if not doc.valid_until:
        return False
    on = on or now_ist()
    return datetime.strptime(doc.valid_until, "%Y-%m-%d").date() < on.date()


# -- matching a tender's document requirements to the library ---------------------------


def _has_phrase(text: str, phrase: str) -> bool:
    # word boundary at the start only, so "certif" matches "certification" but "pan" not "company"
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase.strip())}", text) is not None


def categorise_requirement(text: str) -> str:
    low = text.lower()
    best, score = "other", 0
    for key, (_, phrases) in CAPABILITY_CATEGORIES.items():
        hits = sum(1 for p in phrases if _has_phrase(low, p))
        if hits > score:
            best, score = key, hits
    return best


def match_documents(required: list[str], docs: list[CompanyDocument], deadline: datetime | None = None) -> list[dict[str, Any]]:
    """For each required document: which library documents cover it, and whether anything is missing.

    status: available | expired | prepare (a per-bid undertaking to draft) | review | missing
    """
    by_cat: dict[str, list[CompanyDocument]] = {}
    for d in docs:
        if d.kind == "capability":
            by_cat.setdefault(d.category, []).append(d)
    out = []
    for name in required:
        cat = categorise_requirement(name)
        matches = by_cat.get(cat, []) if cat != "other" else []
        valid = [d for d in matches if not is_expired(d, deadline)]
        if valid:
            status = "available"
        elif matches:
            status = "expired"
        elif cat in PER_BID_CATEGORIES:
            status = "prepare"
        elif cat == "other":
            status = "review"  # vague ("Additional Doc 1 (Requested in ATC)"): read the ATC/SOW
        else:
            status = "missing"
        out.append(
            {
                "requirement": name,
                "category": cat,
                "category_label": CAPABILITY_CATEGORIES[cat][0],
                "status": status,
                "documents": [{"id": d.id, "title": d.title, "valid_until": d.valid_until} for d in matches],
            }
        )
    return out


# -- previous proposals as reference material -------------------------------------------

_STOP = set(
    "the and for with from that this shall will be of to in on by an a or as is are at it its any all such other "
    "bidder bid tender buyer seller service services supply contract work works per may must should".split()
)


def _terms(text: str) -> Counter:
    words = re.findall(r"[a-z][a-z0-9\-]{2,}", (text or "").lower())
    return Counter(w for w in words if w not in _STOP)


def relevant_proposals(session: Session, tender_text: str, limit: int = 2) -> list[CompanyDocument]:
    """Previous proposals most similar to the tender (simple term-overlap ranking; won bids first on ties)."""
    proposals = list(session.scalars(select(CompanyDocument).where(CompanyDocument.kind == "proposal")))
    if not proposals:
        return []
    tender = _terms(tender_text[:200_000])
    top = {w for w, _ in tender.most_common(300)}

    def score(doc: CompanyDocument) -> tuple[float, int]:
        terms = _terms((doc.text or "")[:200_000] + " " + doc.title)
        overlap = sum(min(terms[w], 5) for w in top if w in terms)
        return overlap, 1 if doc.outcome == "won" else 0

    ranked = sorted((p for p in proposals if p.text), key=score, reverse=True)
    return [p for p in ranked[:limit] if score(p)[0] > 0]
