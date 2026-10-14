"""Relevance filtering, eligibility checks and 0-100 scoring.

score = (keyword * w_kw + semantic * w_sem + eligibility * w_elig) / (sum of active weights) * 100
        + bonuses (preferred ministry/state) - penalties (closing soon, excluded word in description)

Each component is in [0, 1]. If semantic scoring is unavailable its weight is
dropped and the others are renormalised, so thresholds stay comparable.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

from .config import CompanyProfile, FilterConfig, ScoringWeights, SemanticConfig, now_ist
from .pdf_parser import BidDocumentInfo

log = logging.getLogger(__name__)

ELIGIBLE = "Eligible"
ELIGIBLE_EXEMPT = "Eligible via startup/MSE exemption"
NOT_ELIGIBLE = "Not eligible"
REVIEW = "Needs manual review"
NOT_CHECKED = "Not checked"

ELIGIBILITY_FACTOR = {ELIGIBLE: 1.0, ELIGIBLE_EXEMPT: 0.9, REVIEW: 0.5, NOT_CHECKED: 0.5, NOT_ELIGIBLE: 0.0}


@dataclass
class BidForScoring:
    bid_number: str
    title: str
    description: str = ""
    ministry: str = ""
    department: str = ""
    organisation: str = ""
    buyer_location: str = ""
    end_date: datetime | None = None
    estimated_value: float | None = None


@dataclass
class KeywordResult:
    score: float  # 0..1
    matched: list[str] = field(default_factory=list)
    excluded_reason: str | None = None
    description_excludes: list[str] = field(default_factory=list)


@dataclass
class ScoreResult:
    total: float
    keyword_score: float
    semantic_score: float | None
    eligibility_status: str
    eligibility_reasons: list[str]
    matched_keywords: list[str]
    adjustments: list[str]
    excluded_reason: str | None
    shortlisted: bool
    days_left: float | None

    @property
    def eligibility_label(self) -> str:
        if self.eligibility_status == NOT_ELIGIBLE and self.eligibility_reasons:
            return f"{NOT_ELIGIBLE} ({'; '.join(self.eligibility_reasons)})"
        return self.eligibility_status


# -- keywords -----------------------------------------------------------------------------


@lru_cache(maxsize=2048)
def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive match; spaces/hyphens interchangeable; optional plural."""
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", keyword.strip()) if p]
    body = r"[\s\-/]*".join(parts)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?:s|es)?(?![A-Za-z0-9])", re.IGNORECASE)


def find_keywords(text: str, keywords: list[str] | dict[str, float]) -> list[str]:
    return [kw for kw in keywords if keyword_pattern(kw).search(text or "")]


def keyword_relevance(bid: BidForScoring, filters: FilterConfig) -> KeywordResult:
    include = filters.include_keywords
    title_hits = find_keywords(bid.title, include)
    desc_hits = [kw for kw in find_keywords(bid.description, include) if kw not in title_hits]
    raw = sum(include[kw] * filters.title_weight for kw in title_hits) + sum(
        include[kw] * filters.description_weight for kw in desc_hits
    )
    score = min(1.0, raw / filters.keyword_saturation) if filters.keyword_saturation > 0 else float(raw > 0)
    result = KeywordResult(score=score, matched=title_hits + desc_hits)

    title_excludes = find_keywords(bid.title, filters.exclude_keywords)
    if title_excludes and not title_hits:
        result.excluded_reason = f"Excluded keyword in title: {', '.join(title_excludes)}"
    elif title_excludes:
        # e.g. "Desktop application development": keep, but penalise like a description hit
        result.description_excludes = title_excludes
    result.description_excludes += [
        kw for kw in find_keywords(bid.description, filters.exclude_keywords) if kw not in title_excludes
    ]
    return result


# -- semantic -----------------------------------------------------------------------------


class SemanticScorer:
    """Cosine similarity between our service lines and bid titles (sentence-transformers)."""

    def __init__(self, cfg: SemanticConfig, services: list[str]) -> None:
        self.cfg = cfg
        self.services = [s for s in services if s.strip()]
        self._model = None
        self._service_emb = None
        self.available = cfg.enabled and bool(self.services)
        if self.available:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(cfg.model)
                self._service_emb = self._model.encode(self.services, normalize_embeddings=True)
            except Exception as exc:  # ImportError, model download failure, ...
                log.warning("Semantic scoring disabled: %s", exc)
                self.available = False

    def scale(self, similarity: float) -> float:
        lo, hi = self.cfg.floor, self.cfg.ceiling
        return max(0.0, min(1.0, (similarity - lo) / (hi - lo))) if hi > lo else float(similarity >= lo)

    def similarities(self, texts: list[str]) -> list[float | None]:
        if not self.available or not texts:
            return [None] * len(texts)
        emb = self._model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
        sims = emb @ self._service_emb.T  # cosine, since both are normalised
        return [self.scale(float(row.max())) for row in sims]


# -- eligibility --------------------------------------------------------------------------


def _relaxation_available(doc: BidDocumentInfo, company: CompanyProfile) -> tuple[list[str], list[str]]:
    """Return (complete relaxations we qualify for, partial ones)."""
    complete: list[str] = []
    partial: list[str] = []
    for label, ours, allowed, scope in (
        ("Startup", company.startup_valid_on(doc.bid_end), doc.startup_relaxation, doc.startup_relaxation_scope),
        ("MSE", company.mse_benefits, doc.mse_relaxation, doc.mse_relaxation_scope),
    ):
        if ours and allowed:
            (complete if (scope or "complete").lower() == "complete" else partial).append(label)
    return complete, partial


def check_eligibility(
    doc: BidDocumentInfo | None, company: CompanyProfile, filters: FilterConfig
) -> tuple[str, list[str]]:
    if doc is None:
        return NOT_CHECKED, ["Bid document not analysed yet"]
    if not doc.is_reliable:
        return REVIEW, ["Could not read the bid document"] + doc.warnings

    shortfalls: list[str] = []
    if doc.min_turnover_lakh and company.turnover_lakh < doc.min_turnover_lakh:
        shortfalls.append(f"turnover ₹{doc.min_turnover_lakh:g}L required (ours ₹{company.turnover_lakh:g}L)")
    if doc.experience_years and company.experience_years < doc.experience_years:
        shortfalls.append(f"{doc.experience_years:g} yrs experience required (ours {company.experience_years:g})")

    complete, partial = _relaxation_available(doc, company)
    notes: list[str] = []
    status = ELIGIBLE
    if shortfalls:
        if complete:
            status = ELIGIBLE_EXEMPT
            notes.append(f"{'/'.join(complete)} relaxation covers: {'; '.join(shortfalls)}")
        elif partial:
            status = REVIEW
            notes.append(f"Only partial {'/'.join(partial)} relaxation for: {'; '.join(shortfalls)}")
        else:
            return NOT_ELIGIBLE, shortfalls

    if doc.past_performance_pct and company.past_govt_projects <= 0:
        # Startup/MSE relaxation in GeM covers experience & turnover; past performance
        # is a separate criterion, so with no govt history this is usually a blocker.
        msg = f"past performance {doc.past_performance_pct:g}% of quantity required (no govt orders yet)"
        if complete or partial:
            status = REVIEW
            notes.append(msg + " - check ATC whether relaxation applies")
        else:
            return NOT_ELIGIBLE, [msg]

    if doc.past_experience_required and company.experience_years <= 0:
        status = REVIEW if status in (ELIGIBLE, ELIGIBLE_EXEMPT) else status
        notes.append("past experience of similar services required")

    if doc.oem_authorization_required and filters.oem_authorization_policy != "ignore":
        msg = "OEM authorisation certificate required"
        if doc.oem_turnover_lakh:
            msg += f" (OEM turnover ₹{doc.oem_turnover_lakh:g}L)"
        if filters.oem_authorization_policy == "reject":
            return NOT_ELIGIBLE, [msg]
        status = REVIEW if status in (ELIGIBLE, ELIGIBLE_EXEMPT) else status
        notes.append(msg)

    return status, notes


PASS, FAIL, EXEMPT, CHECK, NA = "pass", "fail", "exempt", "check", "na"


def eligibility_checklist(
    doc: BidDocumentInfo | None, company: CompanyProfile, filters: FilterConfig
) -> list[dict[str, str]]:
    """Criterion-by-criterion comparison of the bid's requirements with our profile, for display."""
    if doc is None or not doc.is_reliable:
        return []
    complete, partial = _relaxation_available(doc, company)
    relaxed = EXEMPT if complete else (CHECK if partial else FAIL)
    relax_note = (
        f"Covered by {'/'.join(complete)} relaxation"
        if complete
        else (f"Only partial {'/'.join(partial)} relaxation" if partial else "No relaxation for us")
    )
    rows: list[dict[str, str]] = []

    def add(criterion: str, required: str, ours: str, result: str, note: str = "") -> None:
        rows.append({"criterion": criterion, "required": required, "ours": ours, "result": result, "note": note})

    if doc.min_turnover_lakh:
        ok = company.turnover_lakh >= doc.min_turnover_lakh
        add("Average annual turnover", f"₹{doc.min_turnover_lakh:g} lakh", f"₹{company.turnover_lakh:g} lakh",
            PASS if ok else relaxed, "" if ok else relax_note)  # fmt: skip
    else:
        add("Average annual turnover", "Not specified", f"₹{company.turnover_lakh:g} lakh", NA)
    if doc.experience_years:
        ok = company.experience_years >= doc.experience_years
        add("Years of experience", f"{doc.experience_years:g} years", f"{company.experience_years:g} years",
            PASS if ok else relaxed, "" if ok else relax_note)  # fmt: skip
    else:
        add("Years of experience", "Not specified", f"{company.experience_years:g} years", NA)
    if doc.past_experience_required is not None:
        add("Past experience of similar services", "Required" if doc.past_experience_required else "Not required",
            f"{company.experience_years:g} years", CHECK if doc.past_experience_required else NA,
            "Keep completion certificates of similar work ready" if doc.past_experience_required else "")  # fmt: skip
    if doc.past_performance_pct:
        ok = company.past_govt_projects > 0
        add("Past performance (govt supply)", f"{doc.past_performance_pct:g}% of bid quantity",
            f"{company.past_govt_projects} govt order(s)", CHECK if ok else (CHECK if complete or partial else FAIL),
            "Verify order values against bid quantity" if ok else "Not covered by startup/MSE relaxation")  # fmt: skip
    startup_ok = company.startup_valid_on(doc.bid_end)
    if company.is_startup and not startup_ok:
        startup_ours, startup_note = "Expired", f"DPIIT recognition valid until {company.dpiit_valid_until}"
    else:
        startup_ours = (company.dpiit_number or "Yes") if company.is_startup else "No"
        startup_note = ""
    if company.is_mse and not company.mse_benefits:
        mse_ours, mse_note = "Medium", "MSE relaxations apply to micro and small enterprises only"
    else:
        cat = f" ({company.msme_category})" if company.msme_category else ""
        mse_ours, mse_note = ((company.udyam_number or "Yes") + cat if company.is_mse else "No"), ""
    for label, allowed, scope, ours, ours_text, note in (
        ("Startup relaxation", doc.startup_relaxation, doc.startup_relaxation_scope, startup_ok, startup_ours, startup_note),
        ("MSE relaxation", doc.mse_relaxation, doc.mse_relaxation_scope, company.mse_benefits, mse_ours, mse_note),
    ):
        offered = "Not stated" if allowed is None else (f"Yes ({scope or 'Complete'})" if allowed else "No")
        add(label, offered, ours_text, PASS if (allowed and ours) else NA, note)
    if doc.oem_authorization_required:
        req = "OEM authorisation certificate" + (
            f" + OEM turnover ₹{doc.oem_turnover_lakh:g} lakh" if doc.oem_turnover_lakh else ""
        )
        add("OEM authorisation", req, "-", FAIL if filters.oem_authorization_policy == "reject" else CHECK,
            "Needed if you offer another company's product")  # fmt: skip
    if doc.emd_amount:
        via = [x for x, ok in (("MSE", company.mse_benefits), ("Startup", startup_ok)) if ok]
        add("EMD (bid security)", f"₹{doc.emd_amount:,.0f}", "/".join(via) or "-", EXEMPT if via else CHECK,
            ("Upload your " + " / ".join(x for x, ok in (("Udyam certificate", company.mse_benefits),
                                                       ("DPIIT certificate", startup_ok)) if ok) + " with the bid")
            if via else "")  # fmt: skip
    else:
        add("EMD (bid security)", "Not required", "-", NA)
    if doc.epbg_pct:
        add("Performance security (ePBG)", f"{doc.epbg_pct:g}% of contract value", "-", CHECK,
            "Payable by the winning bidder")  # fmt: skip
    return rows


# -- scoring ------------------------------------------------------------------------------


def days_until(end: datetime | None, now: datetime) -> float | None:
    return None if end is None else (end - now).total_seconds() / 86400


def _contains_any(text: str, needles: list[str]) -> str | None:
    low = (text or "").lower()
    return next((n for n in needles if n.lower() in low), None)


def relevance(keyword: KeywordResult, semantic: float | None, weights: ScoringWeights) -> float:
    """Keyword + semantic only (0..1) - used to decide whether a PDF is worth downloading."""
    if keyword.excluded_reason:
        return 0.0
    parts = [(keyword.score, weights.keyword)]
    if semantic is not None:
        parts.append((semantic, weights.semantic))
    total_w = sum(w for _, w in parts)
    return sum(v * w for v, w in parts) / total_w if total_w else 0.0


def score_bid(
    bid: BidForScoring,
    filters: FilterConfig,
    weights: ScoringWeights,
    company: CompanyProfile,
    doc: BidDocumentInfo | None = None,
    semantic: float | None = None,
    now: datetime | None = None,
    keyword: KeywordResult | None = None,
) -> ScoreResult:
    now = now or now_ist()
    kw = keyword or keyword_relevance(bid, filters)
    status, reasons = check_eligibility(doc, company, filters)
    days_left = days_until(bid.end_date, now)

    parts = [(kw.score, weights.keyword), (ELIGIBILITY_FACTOR[status], weights.eligibility)]
    if semantic is not None:
        parts.append((semantic, weights.semantic))
    total_w = sum(w for _, w in parts)
    total = sum(v * w for v, w in parts) / total_w * 100 if total_w else 0.0

    adjustments: list[str] = []
    if kw.description_excludes:
        total -= filters.exclude_in_description_penalty
        adjustments.append(
            f"-{filters.exclude_in_description_penalty:g} excluded word(s): {', '.join(kw.description_excludes)}"
        )
    if days_left is not None and days_left < filters.closing_penalty_days:
        total -= filters.closing_penalty_points
        adjustments.append(f"-{filters.closing_penalty_points:g} closes in {days_left:.1f} days")
    org_text = " ".join([bid.ministry, bid.department, bid.organisation])
    if filters.preferred_ministries and (hit := _contains_any(org_text, filters.preferred_ministries)):
        total += filters.preferred_bonus
        adjustments.append(f"+{filters.preferred_bonus:g} preferred buyer ({hit})")
    if filters.preferred_states and (hit := _contains_any(bid.buyer_location, filters.preferred_states)):
        total += filters.preferred_bonus
        adjustments.append(f"+{filters.preferred_bonus:g} preferred state ({hit})")
    total = round(max(0.0, min(100.0, total)), 1)

    excluded = kw.excluded_reason
    if excluded is None and days_left is not None:
        if days_left < 0:
            excluded = "Bid has closed"
        elif days_left < filters.min_days_until_closing:
            excluded = f"Closes in {days_left:.1f} days (minimum {filters.min_days_until_closing:g})"
    if excluded is None and bid.estimated_value is not None:
        if filters.min_value is not None and bid.estimated_value < filters.min_value:
            excluded = f"Estimated value ₹{bid.estimated_value:,.0f} below minimum"
        elif filters.max_value is not None and bid.estimated_value > filters.max_value:
            excluded = f"Estimated value ₹{bid.estimated_value:,.0f} above maximum"
    if excluded is None and filters.hide_not_eligible and status == NOT_ELIGIBLE:
        excluded = "Not eligible"
    if excluded is None and not kw.matched and (semantic is None or semantic == 0):
        excluded = "No include keyword matched"

    return ScoreResult(
        total=total,
        keyword_score=round(kw.score, 3),
        semantic_score=None if semantic is None else round(semantic, 3),
        eligibility_status=status,
        eligibility_reasons=reasons,
        matched_keywords=kw.matched,
        adjustments=adjustments,
        excluded_reason=excluded,
        shortlisted=excluded is None and total >= filters.score_threshold,
        days_left=None if days_left is None else round(days_left, 2),
    )


# -- feedback -----------------------------------------------------------------------------

_STOPWORDS = set(
    """a an and as at by for from in into of on or per the to with without under over upon via
    supply supplies purchase procurement service services item items various misc other others
    no nos set sets qty quantity unit units type model make new old year years month months
    hiring based providing provision work works job annual rate contract""".split()
)


def _terms(text: str) -> set[str]:
    words = [w for w in re.findall(r"[a-z][a-z0-9\-]+", (text or "").lower()) if len(w) > 2]
    unigrams = {w for w in words if w not in _STOPWORDS}
    bigrams = {
        f"{a} {b}" for a, b in zip(words, words[1:]) if a not in _STOPWORDS and b not in _STOPWORDS
    }
    return unigrams | bigrams


def suggest_exclude_keywords(
    not_relevant_titles: list[str],
    relevant_titles: list[str],
    include_keywords: list[str] | dict[str, float],
    exclude_keywords: list[str],
    min_count: int = 2,
    top_n: int = 15,
) -> list[tuple[str, int, int]]:
    """Terms common in bids marked "Not relevant" but rare in bids we liked.

    Returns (term, count in not-relevant, count in relevant), best first. Terms that
    are already excluded, or that overlap an include keyword, are never suggested.
    """
    bad = Counter(t for title in not_relevant_titles for t in _terms(title))
    good = Counter(t for title in relevant_titles for t in _terms(title))
    include_low = [k.lower() for k in include_keywords]
    exclude_low = {k.lower() for k in exclude_keywords}
    out: list[tuple[str, int, int]] = []
    for term, n_bad in bad.items():
        if n_bad < min_count or term in exclude_low:
            continue
        if any(term in inc or inc in term for inc in include_low):
            continue
        n_good = good.get(term, 0)
        if n_good * 3 >= n_bad:  # must be at least 3x more common among rejected bids
            continue
        out.append((term, n_bad, n_good))
    # prefer bigrams (more specific) and frequent terms
    out.sort(key=lambda t: (t[1] - t[2], " " in t[0]), reverse=True)
    pruned: list[tuple[str, int, int]] = []
    for item in out:
        if any(item[0] in kept[0].split() and kept[1] >= item[1] for kept in pruned):
            continue  # a bigram containing this word with the same support is already listed
        pruned.append(item)
    return pruned[:top_n]
