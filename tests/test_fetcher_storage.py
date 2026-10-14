"""Fetcher parsing/politeness and storage change detection (no network)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from tender_tracker.config import SearchConfig
from tender_tracker.fetcher import BlockedError, GemClient, parse_listing_doc
from tender_tracker.storage import Bid, init_db, make_engine, session_factory, upsert_listing

# Shape of a real /all-bids-data doc (Oct 2026), trimmed.
DOC = {
    "id": "9794149",
    "b_id": [9794149],
    "b_bid_number": ["GEM/2026/B/7950933"],
    "b_category_name": ["Multilayered PCB,Hardware Casing/ Housing,Software Interface,"],
    "b_total_quantity": [4],
    "b_status": [1],
    "b_bid_type": [1],
    "b_eval_type": [0],
    "final_start_date_sort": ["2026-09-23T12:13:02Z"],
    "final_end_date_sort": ["2026-10-14T13:00:00Z"],
    "bd_category_name": ["Multilayered PCB,Hardware Casing/ Housing,Software Interface,Installation"],
    "b_is_custom_item": [0],
    "bbt_title": ["Software Intelligence System"],
    "ba_official_details_minName": ["Ministry of Defence"],
    "ba_official_details_deptName": ["Department of Military Affairs"],
}


def test_parse_listing_doc():
    bid = parse_listing_doc(DOC)
    assert bid.b_id == 9794149
    assert bid.bid_number == "GEM/2026/B/7950933"
    assert bid.title == "Multilayered PCB,Hardware Casing/ Housing,Software Interface (Software Intelligence System)"
    assert bid.end_date == datetime(2026, 10, 14, 13, 0)  # the "Z" is really IST
    assert bid.bid_type == "Bid"
    assert bid.doc_url == "https://bidplus.gem.gov.in/showbidDocument/9794149"
    assert not bid.cancelled


def test_parse_listing_doc_ra():
    bid = parse_listing_doc({**DOC, "b_bid_type": [2]})
    assert bid.bid_type == "RA"
    assert "/showradocumentPdf/" in bid.doc_url


class FakeResponse:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self.headers = {}
        self._payload = payload
        self.text = ""
        self.content = b""

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.cookies = []
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_client_stops_after_repeated_blocks():
    cfg = SearchConfig(max_retries=5, max_consecutive_blocks=3)
    session = FakeSession([FakeResponse(429)] * 5)
    sleeps = []
    client = GemClient(cfg, session=session, sleep=sleeps.append)
    with pytest.raises(BlockedError):
        client.bid_other_details(1)
    assert session.calls == 3
    assert all(s >= 2 for s in sleeps)  # politeness delay / backoff between attempts


def test_client_retries_server_errors_then_succeeds():
    cfg = SearchConfig(max_retries=3)
    ok = FakeResponse(200, {"response": {"corrigendum": True}})
    session = FakeSession([FakeResponse(503), ok])
    client = GemClient(cfg, session=session, sleep=lambda s: None)
    resp = client._request("GET", "https://example.invalid")
    assert resp is ok and session.calls == 2


def test_search_with_no_results_is_not_an_error():
    # GeM answers an empty search with {"code": 404, "message": "No data found"}
    session = FakeSession([FakeResponse(404, {"status": 0, "code": 404, "message": "No data found"})])
    client = GemClient(SearchConfig(), session=session, sleep=lambda s: None)
    client._token = "token"
    assert client.search_page("nothing matches this", 1) == ([], 0)
    assert session.calls == 1


def test_upsert_detects_deadline_extension():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    Session = session_factory(engine)
    listing = parse_listing_doc(DOC)
    with Session() as s:
        _, is_new, changes = upsert_listing(s, listing)
        s.commit()
        assert is_new and changes == []

        _, is_new, changes = upsert_listing(s, parse_listing_doc(DOC))
        assert not is_new and changes == []

        extended = parse_listing_doc({**DOC, "final_end_date_sort": ["2026-10-21T13:00:00Z"]})
        bid, is_new, changes = upsert_listing(s, extended)
        s.commit()
        assert not is_new
        assert changes == ["Deadline extended: 14-10-2026 13:00 -> 21-10-2026 13:00"]
        assert bid.changed_at is not None
        assert s.scalar(select(Bid).where(Bid.bid_number == listing.bid_number)).end_date == extended.end_date

        cancelled = parse_listing_doc({**DOC, "final_end_date_sort": ["2026-10-21T13:00:00Z"], "b_status": [3]})
        _, _, changes = upsert_listing(s, cancelled)
        assert changes == ["Bid cancelled"]
