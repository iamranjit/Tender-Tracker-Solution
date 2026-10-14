from datetime import datetime, timedelta

import pytest

from tender_tracker.config import CompanyProfile, FilterConfig, ScoringWeights
from tender_tracker.filter_scorer import (
    ELIGIBLE,
    ELIGIBLE_EXEMPT,
    NOT_CHECKED,
    NOT_ELIGIBLE,
    REVIEW,
    BidForScoring,
    check_eligibility,
    keyword_relevance,
    score_bid,
    suggest_exclude_keywords,
)
from tender_tracker.pdf_parser import BidDocumentInfo

NOW = datetime(2026, 10, 14, 8, 0)


@pytest.fixture
def filters() -> FilterConfig:
    return FilterConfig(
        include_keywords={"software development": 3, "web portal": 3, "mobile app": 3, "AI": 2, "dashboard": 2},
        exclude_keywords=["printer", "desktop", "housekeeping"],
        min_days_until_closing=5,
        closing_penalty_days=7,
        closing_penalty_points=15,
        score_threshold=50,
    )


@pytest.fixture
def company() -> CompanyProfile:
    return CompanyProfile(is_startup=True, is_mse=True, turnover_lakh=40, experience_years=2, past_govt_projects=0)


def bid(title: str, description: str = "", days: float = 15, **kw) -> BidForScoring:
    return BidForScoring(bid_number="GEM/2026/B/1", title=title, description=description,
                         end_date=NOW + timedelta(days=days), **kw)  # fmt: skip


def doc(**kw) -> BidDocumentInfo:
    return BidDocumentInfo(bid_number="GEM/2026/B/1", **kw)


# -- keywords -------------------------------------------------------------------------


def test_title_match_scores_higher_than_description(filters):
    in_title = keyword_relevance(bid("Web Portal Development"), filters)
    in_desc = keyword_relevance(bid("Services", description="design of a web portal"), filters)
    assert in_title.score > in_desc.score > 0
    assert in_title.matched == ["web portal"]


def test_keywords_match_whole_words_and_plurals(filters):
    assert keyword_relevance(bid("Mobile Apps for citizens"), filters).matched == ["mobile app"]
    # "AI" must not match inside "Repair" or "maintenance"
    assert keyword_relevance(bid("Repair and maintenance of chairs"), filters).matched == []
    assert "AI" in keyword_relevance(bid("AI based grievance analysis"), filters).matched


def test_exclude_keyword_in_title_drops_bid(filters):
    r = keyword_relevance(bid("Multifunction Printer"), filters)
    assert r.excluded_reason and "printer" in r.excluded_reason


def test_exclude_word_does_not_drop_bid_with_include_in_title(filters, company):
    # "desktop" is excluded, but this is clearly a software bid
    r = score_bid(bid("Desktop and mobile app development"), filters, ScoringWeights(), company, now=NOW)
    assert r.excluded_reason is None
    assert any("excluded word" in a for a in r.adjustments)


# -- eligibility ----------------------------------------------------------------------


def test_eligible_when_no_criteria(filters, company):
    assert check_eligibility(doc(), company, filters) == (ELIGIBLE, [])


def test_eligible_when_we_meet_criteria(filters, company):
    status, _ = check_eligibility(doc(min_turnover_lakh=20, experience_years=1), company, filters)
    assert status == ELIGIBLE


def test_startup_relaxation_covers_turnover_shortfall(filters, company):
    d = doc(min_turnover_lakh=200, experience_years=3, startup_relaxation=True, startup_relaxation_scope="Complete")
    status, reasons = check_eligibility(d, company, filters)
    assert status == ELIGIBLE_EXEMPT
    assert "Startup" in reasons[0]


def test_mse_relaxation_only_counts_if_we_are_mse(filters, company):
    d = doc(min_turnover_lakh=200, mse_relaxation=True, mse_relaxation_scope="Complete")
    assert check_eligibility(d, company, filters)[0] == ELIGIBLE_EXEMPT
    not_mse = CompanyProfile(is_startup=False, is_mse=False, turnover_lakh=40, experience_years=2)
    status, reasons = check_eligibility(d, not_mse, filters)
    assert status == NOT_ELIGIBLE
    assert "turnover" in reasons[0]


def test_not_eligible_without_relaxation(filters, company):
    d = doc(min_turnover_lakh=100, startup_relaxation=False, mse_relaxation=False)
    status, reasons = check_eligibility(d, company, filters)
    assert status == NOT_ELIGIBLE
    assert "₹100L" in reasons[0]


def test_partial_relaxation_needs_review(filters, company):
    d = doc(experience_years=5, startup_relaxation=True, startup_relaxation_scope="Partial")
    assert check_eligibility(d, company, filters)[0] == REVIEW


def test_past_performance_without_history(filters, company):
    assert check_eligibility(doc(past_performance_pct=50), company, filters)[0] == NOT_ELIGIBLE
    with_relax = doc(past_performance_pct=50, startup_relaxation=True, startup_relaxation_scope="Complete")
    assert check_eligibility(with_relax, company, filters)[0] == REVIEW
    veteran = CompanyProfile(turnover_lakh=40, experience_years=2, past_govt_projects=3)
    assert check_eligibility(doc(past_performance_pct=50), veteran, filters)[0] == ELIGIBLE


def test_oem_authorisation_policy(filters, company):
    d = doc(oem_authorization_required=True, oem_turnover_lakh=50)
    assert check_eligibility(d, company, filters)[0] == REVIEW
    filters.oem_authorization_policy = "reject"
    assert check_eligibility(d, company, filters)[0] == NOT_ELIGIBLE
    filters.oem_authorization_policy = "ignore"
    assert check_eligibility(d, company, filters)[0] == ELIGIBLE


def test_missing_or_unreadable_document(filters, company):
    assert check_eligibility(None, company, filters)[0] == NOT_CHECKED
    assert check_eligibility(BidDocumentInfo(), company, filters)[0] == REVIEW


# -- total score ----------------------------------------------------------------------


def test_relevant_eligible_bid_is_shortlisted(filters, company):
    r = score_bid(bid("Software Development for Web Portal"), filters, ScoringWeights(), company, doc=doc(), now=NOW)
    assert r.shortlisted
    assert r.total >= 80
    assert r.eligibility_status == ELIGIBLE


def test_semantic_component_is_optional(filters, company):
    b = bid("Software Development")
    without = score_bid(b, filters, ScoringWeights(), company, doc=doc(), now=NOW)
    with_high = score_bid(b, filters, ScoringWeights(), company, doc=doc(), semantic=1.0, now=NOW)
    with_low = score_bid(b, filters, ScoringWeights(), company, doc=doc(), semantic=0.0, now=NOW)
    assert with_low.total < without.total <= with_high.total


def test_semantic_only_match_is_not_excluded(filters, company):
    b = bid("Digital transformation of grievance redressal")
    assert score_bid(b, filters, ScoringWeights(), company, now=NOW).excluded_reason == "No include keyword matched"
    r = score_bid(b, filters, ScoringWeights(), company, doc=doc(), semantic=0.9, now=NOW)
    assert r.excluded_reason is None


def test_closing_too_soon_is_excluded(filters, company):
    r = score_bid(bid("Software Development", days=2), filters, ScoringWeights(), company, doc=doc(), now=NOW)
    assert not r.shortlisted
    assert "Closes in 2.0 days" in r.excluded_reason


def test_closing_penalty_applies_within_window(filters, company):
    far = score_bid(bid("Software Development", days=20), filters, ScoringWeights(), company, doc=doc(), now=NOW)
    near = score_bid(bid("Software Development", days=6), filters, ScoringWeights(), company, doc=doc(), now=NOW)
    assert far.total - near.total == pytest.approx(15)
    assert near.excluded_reason is None


def test_not_eligible_bid_falls_below_threshold(filters, company):
    d = doc(min_turnover_lakh=500, startup_relaxation=False, mse_relaxation=False)
    r = score_bid(bid("Dashboard"), filters, ScoringWeights(), company, doc=d, now=NOW)
    assert r.eligibility_status == NOT_ELIGIBLE
    assert not r.shortlisted
    assert r.eligibility_label.startswith("Not eligible (")


def test_value_and_preference_filters(filters, company):
    filters.max_value = 10_00_000
    filters.preferred_states = ["Karnataka"]
    big = bid("Software Development", estimated_value=50_00_000)
    assert "above maximum" in score_bid(big, filters, ScoringWeights(), company, doc=doc(), now=NOW).excluded_reason
    local = bid("Dashboard", buyer_location="Bengaluru, Karnataka")
    elsewhere = bid("Dashboard", buyer_location="Patna, Bihar")
    a = score_bid(local, filters, ScoringWeights(), company, doc=doc(), now=NOW)
    b = score_bid(elsewhere, filters, ScoringWeights(), company, doc=doc(), now=NOW)
    assert a.total - b.total == pytest.approx(filters.preferred_bonus)


# -- feedback suggestions -------------------------------------------------------------


def test_suggest_exclude_keywords():
    rejected = [
        "Hiring of Data Entry Operator",
        "Data Entry Operator services",
        "Manpower for data entry work",
        "Video conferencing equipment",
    ]
    liked = ["Software development for data dashboard", "Mobile app development"]
    out = suggest_exclude_keywords(rejected, liked, ["software", "dashboard"], ["printer"])
    terms = [t for t, _, _ in out]
    assert "data entry" in terms
    assert "software" not in terms
    # "data" alone also appears in a liked title, so the bigram is the better suggestion
    assert terms.index("data entry") < (terms.index("data") if "data" in terms else 999)


def test_eligibility_checklist_rows(filters, company):
    from tender_tracker.filter_scorer import eligibility_checklist

    d = doc(min_turnover_lakh=200, experience_years=1, past_performance_pct=30, emd_amount=50000,
            startup_relaxation=True, startup_relaxation_scope="Complete", mse_relaxation=False)  # fmt: skip
    rows = {r["criterion"]: r for r in eligibility_checklist(d, company, filters)}
    assert rows["Average annual turnover"]["result"] == "exempt"
    assert rows["Years of experience"]["result"] == "pass"
    assert rows["Past performance (govt supply)"]["result"] == "check"
    assert rows["Startup relaxation"]["result"] == "pass"
    assert rows["MSE relaxation"]["result"] == "na"
    assert rows["EMD (bid security)"]["result"] == "exempt"
    assert eligibility_checklist(None, company, filters) == []
