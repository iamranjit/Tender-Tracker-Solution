"""API tests against a temporary SQLite database (no network)."""

import importlib
from datetime import timedelta

import pytest

from tender_tracker.attachments import extract_document_text
from tender_tracker.config import now_ist
from tender_tracker.fetcher import parse_listing_doc
from tender_tracker.pdf_parser import Attachment, BidDocumentInfo, SpecRow

from .test_fetcher_storage import DOC


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
company: {{name: Test Co, is_startup: true, is_mse: true, turnover_lakh: 40, experience_years: 2}}
search: {{terms: [software]}}
filters: {{include_keywords: {{software: 3}}}}
storage: {{database_url: "sqlite:///{(tmp_path / 't.db').as_posix()}", data_dir: "{tmp_path.as_posix()}"}}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("TT_CONFIG", str(cfg))
    monkeypatch.delenv("APP_USERNAME", raising=False)
    import tender_tracker.api as api_module

    api = importlib.reload(api_module)
    seed(api)
    return TestClient(api.app)


def seed(api) -> None:
    from tender_tracker.storage import Score, save_document, upsert_listing

    now = now_ist()
    end = (now + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with next(api.get_session()) as session:
        bid, _, _ = upsert_listing(session, parse_listing_doc({**DOC, "final_end_date_sort": [end]}), now)
        info = BidDocumentInfo(
            bid_number=bid.bid_number,
            bid_opening=now + timedelta(days=11),
            min_turnover_lakh=200,
            startup_relaxation=True,
            startup_relaxation_scope="Complete",
            spec_sections=[[SpecRow("Core", "Scope of work", "Software Enhancements")]],
            attachments=[Attachment("https://mkp.gem.gov.in/x/boq.csv", "BoQ line items (CSV)", "boq")],
        )
        save_document(session, bid, info, None, now)
        session.add(Score(bid=bid, total=82, eligibility_status="Eligible via startup/MSE exemption",
                          shortlisted=True, matched_keywords=["software"]))  # fmt: skip
        bid.shortlisted_at = now
        session.commit()


def test_list_and_detail(client):
    listing = client.get("/api/bids").json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["bid_number"] == "GEM/2026/B/7950933"
    assert item["opening_date"] and item["has_sow"]

    detail = client.get(f"/api/bids/{item['id']}").json()
    assert detail["sow"]["scope_rows"] == [{"group": "Core", "name": "Scope of work", "value": "Software Enhancements"}]
    assert detail["sow"]["attachments"][0]["status"] == "not_fetched"
    checklist = {r["criterion"]: r["result"] for r in detail["eligibility"]["checklist"]}
    assert checklist["Average annual turnover"] == "exempt"

    assert client.get("/api/bids", params={"q": "no such tender"}).json()["total"] == 0
    assert client.get("/api/bids/999").status_code == 404


def test_feedback_roundtrip(client):
    bid_id = client.get("/api/bids").json()["items"][0]["id"]
    r = client.put(f"/api/bids/{bid_id}/feedback", json={"feedback": "interested", "note": "call buyer"})
    assert r.json()["feedback"] == "interested"
    assert client.get("/api/bids", params={"feedback": "interested"}).json()["total"] == 1
    assert client.put(f"/api/bids/{bid_id}/feedback", json={"feedback": "maybe"}).status_code == 400


def test_config_is_validated_before_saving(client):
    original = client.get("/api/config").json()["text"]
    assert client.put("/api/config", json={"text": "nonsense_section: 1"}).status_code == 400
    assert client.get("/api/config").json()["text"] == original


def test_basic_auth(client, monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "admin")
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert client.get("/api/bids").status_code == 401
    assert client.get("/api/bids", auth=("admin", "s3cret")).status_code == 200
    assert client.get("/api/health").status_code == 200


def test_extract_csv_and_html_text():
    csv_bytes = b"Item Number,Item Title,Item Description\n1,Language Learning Portal,Web portal with AI chatbot\n"
    assert "Language Learning Portal | Web portal with AI chatbot" in extract_document_text(csv_bytes, "text/csv", "x.csv")
    html = b"<html><nav>Login Sign Up</nav><h2>Special Terms and Condition</h2><p>1. Uptime 99%</p><script>x()</script></html>"
    text = extract_document_text(html, "text/html", "https://bidplus.gem.gov.in/bidding/bid/bidsla/1")
    assert text.startswith("Special Terms") and "Uptime 99%" in text and "x()" not in text
    assert extract_document_text(b"\x00\x01", "application/octet-stream", "x.bin") is None


def test_progress_is_persisted_and_stale_runs_are_interrupted(client):
    import tender_tracker.api as api
    from tender_tracker.progress import RunProgress
    from tender_tracker.storage import RunLog

    with next(api.get_session()) as session:
        live = RunLog(status="running", started_at=now_ist())
        dead = RunLog(status="running", started_at=now_ist() - timedelta(hours=1))
        session.add_all([live, dead])
        session.commit()
        live_id, dead_id = live.id, dead.id

    progress = RunProgress(api.state.Session, live_id, interval=0)
    progress.phase("search", total=10)
    progress.step(done=5, message="page 5", bids_found=42)
    progress.phase("documents", total=4)
    progress.step(done=2)

    runs = {r["id"]: r for r in client.get("/api/runs").json()["items"]}
    p = runs[live_id]["progress"]
    assert runs[live_id]["status"] == "running"
    assert p["phase"] == "documents" and p["counters"]["bids_found"] == 42
    assert p["percent"] == pytest.approx((0.55 + 0.02 + 0.25 * 0.5) * 100)
    assert any("Reading bid documents" in e for e in p["events"])
    assert runs[dead_id]["status"] == "interrupted"  # no heartbeat for an hour


def test_settings_update_keeps_comments_and_only_changes_edits(client, tmp_path):
    import tender_tracker.api as api

    path = api.CONFIG_PATH
    path.write_text(
        path.read_text(encoding="utf-8").replace("company:", "# my comment\ncompany:")
        + "notifications:\n  channels: [email]   # inline comment\n",
        encoding="utf-8",
    )
    before = path.read_text(encoding="utf-8")
    values = client.get("/api/settings").json()["values"]
    values.pop("storage")

    # Saving the form unchanged must not touch the file at all.
    assert client.put("/api/settings", json={"values": values}).status_code == 200
    assert path.read_text(encoding="utf-8") == before

    values["company"]["turnover_lakh"] = 55
    values["filters"]["include_keywords"] = {"software": 3, "portal": 2}
    values["notifications"]["channels"] = ["email", "telegram"]
    r = client.put("/api/settings", json={"values": values})
    assert r.status_code == 200
    after = path.read_text(encoding="utf-8")
    assert "# my comment" in after and "# inline comment" in after
    assert "turnover_lakh: 55" in after and "portal: 2" in after
    assert "user_agent" not in after  # defaults are not written out
    assert r.json()["values"]["notifications"]["channels"] == ["email", "telegram"]

    bad = dict(values, filters={**values["filters"], "oem_authorization_policy": "sometimes"})
    assert client.put("/api/settings", json={"values": bad}).status_code == 400
    assert path.read_text(encoding="utf-8") == after  # invalid input never reaches the file
    assert client.put("/api/settings", json={"values": {"nonsense": {}}}).status_code == 400


def test_profile_change_rescores_stored_tenders(client):
    item = client.get("/api/bids").json()["items"][0]
    assert item["eligibility_status"] == "Eligible via startup/MSE exemption"  # seeded score

    # The seeded bid needs Rs 200 L turnover; startup relaxation is what makes us eligible.
    values = client.get("/api/settings").json()["values"]
    values.pop("storage")
    values["company"]["is_startup"] = False
    r = client.put("/api/settings", json={"values": values}).json()
    assert r["rescore"]["rescored"] == 1
    assert client.get("/api/meta").json()["company"]["is_startup"] is False

    after = client.get("/api/bids", params={"view": "all"}).json()["items"][0]
    assert after["eligibility_status"] == "Not eligible"
