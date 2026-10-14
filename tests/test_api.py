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
ai: {{enabled: false}}
storage: {{database_url: "sqlite:///{(tmp_path / 't.db').as_posix()}", data_dir: "{tmp_path.as_posix()}"}}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("TT_CONFIG", str(cfg))
    monkeypatch.delenv("APP_USERNAME", raising=False)
    import tender_tracker.api as api_module

    api = importlib.reload(api_module)
    seed(api)
    client = TestClient(api.app)
    r = client.post("/api/auth/register", json={"name": "Admin", "email": "admin@acme.in", "password": "Secret123"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"  # first account = admin, signed in
    return client


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


def test_login_registration_and_roles(client):
    import tender_tracker.api as api
    from fastapi.testclient import TestClient

    anon = TestClient(api.app)
    assert anon.get("/api/bids").status_code == 401
    assert anon.get("/api/health").status_code == 200
    status = anon.get("/api/auth/status").json()
    assert status["user"] is None and not status["needs_setup"] and status["registration"] == "approval"

    # weak passwords and duplicate emails are refused
    assert anon.post("/api/auth/register", json={"name": "B", "email": "bob@acme.in", "password": "short"}).status_code == 400
    assert anon.post("/api/auth/register", json={"name": "A", "email": "ADMIN@acme.in", "password": "Another123"}).status_code == 400

    # later sign-ups wait for approval and can't log in yet
    r = anon.post("/api/auth/register", json={"name": "Bob", "email": "bob@acme.in", "password": "Bobpass123"})
    assert r.json() == {"user": None, "pending": True}
    assert "approve" in anon.post("/api/auth/login", json={"email": "bob@acme.in", "password": "Bobpass123"}).json()["detail"]

    users = client.get("/api/users").json()["items"]
    bob = next(u for u in users if u["email"] == "bob@acme.in")
    assert bob["status"] == "pending"
    client.patch(f"/api/users/{bob['id']}", json={"status": "active"})

    assert anon.post("/api/auth/login", json={"email": "bob@acme.in", "password": "wrong-pass1"}).status_code == 400
    r = anon.post("/api/auth/login", json={"email": "Bob@Acme.in", "password": "Bobpass123"})
    assert r.status_code == 200 and anon.get("/api/bids").status_code == 200
    # members can work with tenders but not change settings or users
    assert anon.put("/api/settings", json={"values": {}}).status_code == 403
    assert anon.get("/api/users").status_code == 403

    # disabling a user ends their session immediately
    client.patch(f"/api/users/{bob['id']}", json={"status": "disabled"})
    assert anon.get("/api/bids").status_code == 401

    # admins can't lock themselves out
    me = client.get("/api/auth/status").json()["user"]
    assert client.patch(f"/api/users/{me['id']}", json={"status": "disabled"}).status_code == 400

    # cross-site writes are blocked; logout ends the session
    assert client.post("/api/runs", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    client.post("/api/auth/logout")
    assert client.get("/api/bids").status_code == 401


def test_password_hashing_and_change(client):
    from tender_tracker.auth import hash_password, verify_password

    h = hash_password("Secret123")
    assert h.startswith("scrypt$") and "Secret123" not in h
    assert verify_password("Secret123", h) and not verify_password("secret123", h)
    assert client.post("/api/auth/password", json={"current_password": "nope", "new_password": "Newpass123"}).status_code == 400
    assert client.post("/api/auth/password", json={"current_password": "Secret123", "new_password": "Newpass123"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "admin@acme.in", "password": "Newpass123"}).status_code == 200


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


def test_library_upload_and_tender_workspace(client):
    import tender_tracker.api as api

    r = client.post("/api/library", data={"kind": "capability", "category": "gst", "title": "GST certificate", "valid_until": "2031-01-01"},
                    files={"file": ("gst.txt", b"GSTIN 27AAPFU0939F1ZV registration", "text/plain")})  # fmt: skip
    assert r.status_code == 201 and r.json()["category_label"] == "GST registration certificate"
    doc_id = r.json()["id"]
    assert client.get(f"/api/library/{doc_id}/download").content.startswith(b"GSTIN")
    bad = client.post("/api/library", data={"kind": "capability", "category": "nonsense"}, files={"file": ("x.txt", b"x", "text/plain")})
    assert bad.status_code == 400

    tender = (
        "REQUEST FOR PROPOSAL - Development of Citizen Grievance Portal\n" + "Scope of work: build and maintain a web portal. " * 30
        + "\nThe bidder must submit GST registration certificate and PAN card.\n"
    )
    r = client.post("/api/analyses/upload", data={"title": "Grievance portal RFP"}, files={"file": ("rfp.txt", tender.encode(), "text/plain")})
    assert r.status_code == 202
    aid = r.json()["id"]
    api._analysis_threads[aid].join(timeout=30)
    detail = client.get(f"/api/analyses/{aid}").json()
    assert detail["status"] == "done", detail.get("error")
    assert detail["result"]["stages"]["analyse"]["status"] == "skipped"  # no API key in tests
    assert detail["result"]["sources"][0]["name"] == "rfp.txt"
    for fmt in ("docx", "pptx", "xlsx"):
        resp = client.get(f"/api/analyses/{aid}/export/{fmt}")
        assert resp.status_code == 200 and resp.content[:2] == b"PK"
    assert client.delete(f"/api/library/{doc_id}").json() == {"deleted": doc_id}
    assert client.delete(f"/api/analyses/{aid}").json() == {"deleted": aid}
