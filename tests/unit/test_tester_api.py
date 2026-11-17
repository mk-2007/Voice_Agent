import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import tester_api
from backend.database.models import Appointment, AuditLog, Base, Doctor, Patient
from backend.services import call_auditor
from backend.services.calendar_service import MockCalendarService
from config.settings import settings

CALL = "clinical-room-1"
NOW = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
STARTED = datetime(2026, 11, 7, 14, 55, tzinfo=ZoneInfo("Asia/Karachi"))
TRANSCRIPT = (
    "Rachel: Thank you for calling. How can I help?\n"
    "Caller: I need an appointment with Dr. Ahmed Khan on November 10 at 3 PM.\n"
    "Rachel: Sure. May I have your name?\n"
    "Caller: Ali Raza.\n"
    "Caller: My number is 0300 1234567.\n"
    "Rachel: To confirm: Dr. Ahmed Khan on November 10 at 3 PM for Ali Raza. Shall I book it?\n"
    "Caller: Yes please.\n"
    "Rachel: Your appointment with Dr. Ahmed Khan on November 10 at 3 PM is confirmed. "
    "Your confirmation ID is APT-A1B2C3."
)
CLAIMS = {"name": "Ali Raza", "phone": "03001234567", "doctor": "Dr. Ahmed Khan",
          "date": "2026-11-10", "time": "15:00", "confirmed": True}


@pytest.fixture(autouse=True)
def pkt(monkeypatch):
    monkeypatch.setattr(settings, "CLINIC_TIMEZONE", "Asia/Karachi")


@pytest.fixture
def world(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(tester_api, "SessionLocal", Session)

    cal = MockCalendarService()
    with Session() as db:
        db.add(Doctor(doctor_id="DOC-001", doctor_name="Dr. Ahmed Khan", specialization="General Physician",
                      calendar_id="x", timezone="Asia/Karachi", working_hours_start="09:00",
                      working_hours_end="17:00"))
        db.add(Patient(patient_id="P1", full_name="Ali Raza", phone_number="03001234567"))
        event_id = cal.create_event("DOC-001", "2026-11-10", "15:00", "15:30", "Visit", "APT-A1B2C3")
        db.add(Appointment(appointment_id="APT-A1B2C3", patient_id="P1", doctor_id="DOC-001",
                           appointment_date="2026-11-10", start_time="15:00", end_time="15:30",
                           status="CONFIRMED", calendar_event_id=event_id))
        db.add(AuditLog(actor="agent", action="BOOK_APPOINTMENT", resource_type="APPOINTMENT",
                        resource_id="APT-A1B2C3", timestamp=datetime(2026, 11, 7, 10, 0),
                        metadata_json=json.dumps({"call_id": CALL, "date": "2026-11-10", "time": "15:00",
                                                  "patient_id": "P1", "doctor_id": "DOC-001"})))
        db.commit()
        call_auditor.run_and_store_audit(db, CALL, "live", STARTED, TRANSCRIPT, calendar=cal,
                                         extractor=lambda t, d: CLAIMS, now=NOW)

    # the API's re-verify should use the same fake calendar / reader / clock as the test
    real = call_auditor.audit_live_call
    monkeypatch.setattr(tester_api, "audit_live_call",
                        lambda *a, **k: real(*a, calendar=cal, extractor=lambda t, d: CLAIMS, now=NOW, **k))

    app = FastAPI()
    app.include_router(tester_api.router)
    return TestClient(app), Session, cal, event_id


def test_list_has_stats_and_a_readable_headline(world):
    client, *_ = world
    body = client.get("/api/tester/calls").json()
    assert body["stats"] == {"total": 1, "pass": 1, "flag": 0, "no_change": 0, "error": 0, "pass_rate": 100}
    call = body["calls"][0]
    assert call["verdict"] == "PASS" and call["failed"] == 0 and call["passed"] == call["total"] >= 20
    assert call["headline"] == "Booked Dr. Ahmed Khan for Ali Raza, Tue 10 Nov 2026 at 3:00 PM"
    assert call["started_at"].endswith("Z") and call["has_transcript"] is True


def test_detail_has_checks_bookings_claims_and_transcript_turns(world):
    client, *_ = world
    d = client.get(f"/api/tester/calls/{CALL}").json()
    assert d["bookings"][0]["patient_name"] == "Ali Raza"
    assert d["claims"]["phone"] == "03001234567"
    assert {c["group"] for c in d["checks"]} >= {"caller_vs_booking", "rachel", "booking_record", "calendar"}
    assert d["transcript"][0] == {"speaker": "rachel", "text": "Thank you for calling. How can I help?"}
    assert d["transcript"][-1]["speaker"] == "rachel" and "APT-A1B2C3" in d["transcript"][-1]["text"]


def test_unknown_call_is_404(world):
    client, *_ = world
    assert client.get("/api/tester/calls/nope").status_code == 404
    assert client.post("/api/tester/calls/nope/reverify").status_code == 404


def test_reverify_picks_up_a_problem_that_appeared_since(world):
    client, Session, cal, event_id = world
    cal.delete_event("DOC-001", event_id)          # someone deleted the calendar event
    d = client.post(f"/api/tester/calls/{CALL}/reverify").json()
    assert d["verdict"] == "FLAG" and d["has_critical"] is True and d["reverified_at"]
    failed = [c["code"] for c in d["checks"] if c["status"] == "fail"]
    assert "calendar_event_missing" in failed
    assert client.get("/api/tester/calls").json()["stats"]["flag"] == 1


def test_reverify_without_stored_transcript_is_a_clear_409(world, monkeypatch):
    client, Session, *_ = world
    from backend.database.audit_details import CallAuditDetail
    with Session() as db:
        row = db.get(CallAuditDetail, CALL)
        row.transcript_text = None
        db.commit()
    r = client.post(f"/api/tester/calls/{CALL}/reverify")
    assert r.status_code == 409 and "not stored" in r.json()["detail"]


def test_page_is_served_and_html_in_data_stays_plain_text(world):
    client, Session, *_ = world
    page = client.get("/tester")
    assert page.status_code == 200 and "Call Tester" in page.text
    assert page.headers["cache-control"] == "no-store"
    from backend.database.audit_details import CallAuditDetail
    with Session() as db:
        row = db.get(CallAuditDetail, CALL)
        row.transcript_text = "Caller: <img src=x onerror=alert(1)>"
        db.commit()
    turn = client.get(f"/api/tester/calls/{CALL}").json()["transcript"][0]
    assert turn == {"speaker": "caller", "text": "<img src=x onerror=alert(1)>"}   # the page escapes it