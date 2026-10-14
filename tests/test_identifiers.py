from datetime import datetime

import pytest

from tender_tracker.config import CompanyProfile, FilterConfig, config_from_dict
from tender_tracker.filter_scorer import ELIGIBLE_EXEMPT, NOT_ELIGIBLE, check_eligibility, eligibility_checklist
from tender_tracker.identifiers import (
    gstin_check_digit,
    gstin_problem,
    identifier_problems,
    pan_problem,
    registration_problem,
    udyam_problem,
)
from tender_tracker.pdf_parser import BidDocumentInfo

VALID_GSTIN = "27AAPFU0939F1ZV"  # example GSTIN from GSTN documentation


def test_gstin_checksum_and_format():
    assert gstin_check_digit(VALID_GSTIN[:14]) == "V"
    assert gstin_problem(VALID_GSTIN) is None
    assert "check digit" in gstin_problem(VALID_GSTIN[:14] + "W")
    assert "15 characters" in gstin_problem("27AAPFU0939F1Z")
    assert "state code" in gstin_problem("99AAPFU0939F1ZV")


def test_pan_udyam_and_registration_formats():
    assert pan_problem("AAPFU0939F") is None
    assert pan_problem("AAPF0939F") is not None
    assert udyam_problem("UDYAM-KR-03-0012345") is None
    assert udyam_problem("UDYAM-KR-3-12345") is not None
    assert registration_problem("U72900KA2023PTC123456", "Private Limited Company") is None
    assert registration_problem("U72900KA2023PTC12345", "Private Limited Company") is not None
    assert registration_problem("AAB-1234", "Limited Liability Partnership") is None
    assert registration_problem("anything goes", "Partnership Firm") is None


def test_config_normalises_and_rejects_bad_identifiers():
    cfg = config_from_dict({"company": {"pan": " aapfu0939f ", "gstin": "27aapfu0939f1zv", "incorporation_date": datetime(2023, 5, 10).date()}})
    assert cfg.company.pan == "AAPFU0939F"
    assert cfg.company.gstin == VALID_GSTIN
    assert cfg.company.incorporation_date == "2023-05-10"
    with pytest.raises(ValueError, match="PAN"):
        config_from_dict({"company": {"pan": "12345"}})
    with pytest.raises(ValueError, match="date"):
        config_from_dict({"company": {"dpiit_valid_until": "next year"}})
    assert identifier_problems({"pan": "", "gstin": ""}) == []


def _doc(**kw) -> BidDocumentInfo:
    return BidDocumentInfo(bid_number="GEM/2026/B/1", bid_end=datetime(2026, 11, 1, 15, 0), min_turnover_lakh=200, **kw)


def test_medium_enterprise_gets_no_mse_relaxation():
    doc = _doc(mse_relaxation=True, mse_relaxation_scope="Complete")
    small = CompanyProfile(is_mse=True, msme_category="small", turnover_lakh=40)
    medium = CompanyProfile(is_mse=True, msme_category="medium", turnover_lakh=40)
    assert check_eligibility(doc, small, FilterConfig())[0] == ELIGIBLE_EXEMPT
    assert check_eligibility(doc, medium, FilterConfig())[0] == NOT_ELIGIBLE
    row = {r["criterion"]: r for r in eligibility_checklist(doc, medium, FilterConfig())}["MSE relaxation"]
    assert row["ours"] == "Medium" and "micro and small" in row["note"]


def test_dpiit_recognition_must_be_valid_on_closing_date():
    doc = _doc(startup_relaxation=True, startup_relaxation_scope="Complete")
    valid = CompanyProfile(is_startup=True, dpiit_number="DIPP12345", dpiit_valid_until="2030-03-31", turnover_lakh=40)
    expired = CompanyProfile(is_startup=True, dpiit_valid_until="2026-10-01", turnover_lakh=40)
    assert check_eligibility(doc, valid, FilterConfig())[0] == ELIGIBLE_EXEMPT
    assert check_eligibility(doc, expired, FilterConfig())[0] == NOT_ELIGIBLE
    rows = {r["criterion"]: r for r in eligibility_checklist(doc, valid, FilterConfig())}
    assert rows["Startup relaxation"]["ours"] == "DIPP12345"
    assert {r["criterion"]: r for r in eligibility_checklist(doc, expired, FilterConfig())}["Startup relaxation"]["ours"] == "Expired"
