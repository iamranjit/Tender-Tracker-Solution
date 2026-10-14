from datetime import datetime
from pathlib import Path

import pytest

from tender_tracker.pdf_parser import clean_text, parse_pdf, parse_text

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def sample():
    # Text extracted by pdfplumber from a real GeM bid document (emails/phones anonymised).
    return parse_text((FIXTURES / "sample_bid_raw.txt").read_text(encoding="utf-8"))


def test_header_fields(sample):
    assert sample.bid_number == "GEM/2026/B/7950933"
    assert sample.bid_end == datetime(2026, 10, 14, 13, 0, 0)
    assert sample.ministry == "Ministry Of Defence"
    assert sample.department == "Department Of Military Affairs"
    assert sample.organisation == "Indian Army"
    assert sample.office is None  # masked as *********** in the document
    assert sample.total_quantity == 4
    assert sample.is_reliable


def test_eligibility_fields(sample):
    assert sample.min_turnover_lakh == 17.25
    assert sample.oem_turnover_lakh == 50  # value printed before its label in this layout
    assert sample.experience_years == 3
    assert sample.past_performance_pct == 50
    assert sample.mse_relaxation is True and sample.mse_relaxation_scope == "Complete"
    assert sample.startup_relaxation is True and sample.startup_relaxation_scope == "Complete"
    assert sample.oem_authorization_required is True


def test_money_and_location(sample):
    assert sample.emd_amount == 105000
    assert sample.epbg_pct == 3.0
    assert sample.estimated_value is None  # only mentioned in a clause, never with a figure
    assert sample.bid_packet_type == "Two Packet Bid"
    assert sample.buyer_location == "Bathinda"


SERVICE_BID = """\
(cid:1)बड सं(cid:64)या/Bid Number: GEM/2026/B/1234567
(cid:65)दनांक /Dated: 01-10-2026
/Bid End Date/Time 25-10-2026 15:00:00
ममं(cid:32)ं/Ministry/State Name Ministry Of Electronics And Information Technology
/Department Name Department Of Electronics And Information Technology
/Organisation Name National Informatics Centre
/Office Name NIC Hyderabad
/Total Quantity 1
/Minimum Average Annual Turnover of the bidder (For 3 Years) 1.5 Crore (s)
/Years of Past Experience Required for same/similar service 5 Year (s)
/MSE Relaxation for Years Of Experience and Turnover No
/Startup Relaxation for Years Of Experience and Turnover No
/Estimated Bid Value 2500000
/Type of Bid Single Packet Bid
Consignees/Reporting Officer and Quantity
S.No. Consignee Address Quantity Days
Reporting/Officer
1 ******** Gachibowli, Hyderabad, Telangana 1 365
"""


def test_service_bid_variant_layout():
    info = parse_text(SERVICE_BID)
    assert info.bid_number == "GEM/2026/B/1234567"
    assert info.organisation == "National Informatics Centre"
    assert info.office == "NIC Hyderabad"
    assert info.min_turnover_lakh == 150  # 1.5 crore
    assert info.experience_years == 5
    assert info.mse_relaxation is False and info.mse_relaxation_scope is None
    assert info.startup_relaxation is False
    assert info.past_performance_pct is None
    assert info.emd_amount is None
    assert info.estimated_value == 2_500_000
    assert info.oem_authorization_required is False
    assert info.buyer_state == "Telangana"
    assert "Gachibowli" in info.buyer_location


def test_clean_text_removes_cid_and_devanagari():
    assert clean_text("(cid:1)बड सं(cid:64)या/Bid Number: X\n\n  a   b ") == "/Bid Number: X\na b"


def test_non_bid_text_is_flagged_unreliable():
    info = parse_text("This is a corrigendum notice with no standard fields. " * 10)
    assert not info.is_reliable
    assert info.warnings


def test_parse_pdf_handles_broken_file(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"<html>Internal Server Error</html>")
    info = parse_pdf(bad)
    assert not info.is_reliable
    assert info.warnings


# -- table layer, dates, attachments (v2 parser) ----------------------------------------

from tender_tracker.pdf_parser import BidDocumentInfo, apply_tables, classify_links, state_from_pin  # noqa: E402

# Rows as pdfplumber's extract_tables() returns them for a real services bid (GEM/2026/B/8079186).
BID_GRID = [
    ["(cid:1)(cid:1)बबडड/Bid Details", ""],
    ["(cid:1)/Bid End Date/Time", "20-10-2026 11:00:00"],
    ["(cid:1)/Bid Opening Date/Time", "21-10-2026 11:00:00"],
    ["(( ))/Bid Offer Validity (From End Date)", "90 (Days)"],
    ["/Organisation Name", "Northern Railway"],
    ["/Contract Period", "1 Year(s)"],
    ["((33 )) /Minimum Average Annual Turnover of the bidder (For 3 Years)", "1.25 Crore (s)"],
    ["/Past Experience of Similar Services required", "Yes"],
    ["/MSE Relaxation for Years of Experience and Turnover", "No"],
    ["/Startup Relaxation for Years of Experience and Turnover", "Yes | Complete"],
    ["/Document required from seller", "Experience Criteria,Bidder Turnover,OEM Authorization Certificate"],
    ["(( )) / Estimated Bid Value in INR (Inclusive of all taxes)", "997100"],
    ["/EMD Amount", "19950"],
]
SPEC_GRID = [
    ["/ Specification", "/ Values"],
    ["/ Core", ""],
    ["Type of Support Service Required", "Implementation Services , Maintenance Services"],
    ["Scope of work", "Software Enhancements , Technical Modifications"],
]
SPEC_GRID_3COL = [
    ["/Specification", "/Specification Name", "/Bid Requirement (Allowed Values)"],
    ["Generic", "Solution Scope", "Recruitment Management Software"],
    ["", "Licence Type", "Perpetual"],
]


def test_table_layer_fields():
    info = BidDocumentInfo(bid_number="GEM/2026/B/8079186")
    apply_tables(info, [BID_GRID, SPEC_GRID, SPEC_GRID_3COL])
    assert info.bid_end == datetime(2026, 10, 20, 11, 0)
    assert info.bid_opening == datetime(2026, 10, 21, 11, 0)
    assert info.offer_validity_days == 90
    assert info.organisation == "Northern Railway"
    assert info.contract_period == "1 Year(s)"
    assert info.min_turnover_lakh == 125
    assert info.past_experience_required is True
    assert info.mse_relaxation is False
    assert info.startup_relaxation is True and info.startup_relaxation_scope == "Complete"
    assert info.oem_authorization_required is True  # from the documents-required row
    assert info.estimated_value == 997100
    assert info.emd_amount == 19950
    assert ("Contract Period", "1 Year(s)") in info.details


def test_spec_tables_and_scope_of_work():
    info = BidDocumentInfo()
    apply_tables(info, [SPEC_GRID, SPEC_GRID_3COL])
    assert len(info.spec_sections) == 2
    assert info.spec_sections[0][0].group == "Core"
    assert info.spec_sections[1][1].group == "Generic"  # carried down from the row above
    assert "Scope of work: Software Enhancements , Technical Modifications" in info.scope_of_work
    assert "Solution Scope: Recruitment Management Software" in info.scope_of_work


def test_dates_from_text_layout(sample):
    # In this layout the opening value sits between "Bid Opening" and "Date/Time".
    assert sample.bid_opening == datetime(2026, 10, 15, 13, 0)
    assert sample.published_at == datetime(2026, 9, 23)
    assert sample.offer_validity_days == 180
    assert sample.atc_text is None or isinstance(sample.atc_text, str)


def test_classify_links_uses_labels_from_text():
    text = (
        "/Additional Qualification/Data Required\n"
        "Scope of Work:1791886354.pdf\n"
        "3 / 6\n"
        "Payment Terms:1791886359.pdf\n"
        "SOR - 1791886677.xlsx\n"
    )
    base = "https://bidplus.gem.gov.in/resources/upload_nas/OctQ426/bidding"
    links = [
        f"{base}/biddoc/bid-1/1791886359.pdf",
        f"{base}/biddoc/bid-1/1791886354.pdf",
        f"{base}/excel/bid-1/1791886677.xlsx",
        "https://admin.gem.gov.in/apis/v1/gtc/pdfByDate/?date=20261014",
        "https://fulfilment.gem.gov.in/contract/slafds?fileDownloadPath=SLA/X/SOW_ab12.pdf",
        "mailto:someone@gov.in",
    ]
    atts = classify_links(links, text)
    by_label = {a.label: a.kind for a in atts}
    assert by_label["Scope of Work"] == "sow"
    assert by_label["Payment Terms"] == "buyer_doc"
    assert by_label["SOR"] == "boq"
    assert by_label["Scope of work (SOW)"] == "sow"
    assert by_label["GeM General Terms & Conditions"] == "gtc"
    assert atts[0].kind == "sow"  # SOW documents listed first
    assert all(not a.url.startswith("mailto") for a in atts)


def test_state_from_pin():
    assert state_from_pin("110001") == "Delhi"
    assert state_from_pin("492012") == "Chhattisgarh"
    assert state_from_pin("500032") == "Telangana"
    assert state_from_pin("560001") == "Karnataka"
    assert state_from_pin("834001") == "Jharkhand"
    assert state_from_pin("226001") == "Uttar Pradesh"
    assert state_from_pin("012345") is None
