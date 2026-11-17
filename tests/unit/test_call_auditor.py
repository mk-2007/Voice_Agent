import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import (
    Appointment, AuditLog, Base, Doctor, LiveCallEvaluation, Patient,
)
from backend.services.calendar_service import MockCalendarService
from backend.database.audit_details import CallAuditDetail
from backend.services.call_auditor import (
    audit_live_call, names_match, persist_audit, phone_matches, record_audit_error,
    run_and_store_audit, severity,
)
from backend.services.live_call_verifier import verify_live_call
from config.settings import settings

CALL = "room-abc"
NOW = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)          # 15:00 in Karachi
STARTED = datetime(2026, 11, 7, 14, 55, tzinfo=ZoneInfo("Asia/Karachi"))  # aware, non-UTC

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

GOOD_CLAIMS = {"name": "Ali Raza", "phone": "03001234567", "doctor": "Dr. Ahmed Khan",
               "date": "2026-11-10", "time": "15:00", "confirmed": True}


@pytest.fixture(autouse=True)
def pkt(monkeypatch):
    monkeypatch.setattr(settings, "CLINIC_TIMEZONE", "Asia/Karachi")


@pytest.fixture
def env():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Doctor(doctor_id="DOC-001", doctor_name="Dr. Ahmed Khan",
                  specialization="General Physician", calendar_id="x",
                  timezone="Asia/Karachi", working_hours_start="09:00",
                  working_hours_end="17:00"))
    db.add(Patient(patient_id="P1", full_name="Ali Raza", phone_number="03001234567"))
    db.commit()
    cal = MockCalendarService()
    return db, cal


def make_booking(db, cal, start="15:00", end="15:30", date="2026-11-10",
                 appt_id="APT-A1B2C3", with_event=True, action="BOOK_APPOINTMENT"):
    event_id = cal.create_event("DOC-001", date, start, end, "Visit", appt_id) if with_event else None
    db.add(Appointment(appointment_id=appt_id, patient_id="P1", doctor_id="DOC-001",
                       appointment_date=date, start_time=start, end_time=end,
                       status="CONFIRMED", calendar_event_id=event_id))
    db.add(AuditLog(actor="agent", action=action, resource_type="APPOINTMENT",
                    resource_id=appt_id, timestamp=datetime(2026, 11, 7, 10, 0),
                    metadata_json=json.dumps({"call_id": CALL, "date": date, "time": start,
                                              "patient_id": "P1", "doctor_id": "DOC-001"})))
    db.commit()
    return event_id


def run(db, cal, claims=GOOD_CLAIMS, transcript=TRANSCRIPT, extractor=None):
    ex = extractor or (lambda t, today: claims)
    return audit_live_call(transcript, CALL, STARTED, db, calendar=cal, extractor=ex, now=NOW)


def test_clean_call_passes(env):
    db, cal = env
    make_booking(db, cal)
    r = run(db, cal)
    assert r.verdict == "PASS", r.findings
    assert r.action_count == 1


def test_agent_booked_wrong_time_is_caught(env):
    """Agent says 4 PM consistently and DB holds 4 PM, but the caller asked for 3 PM.
    The old verifier only compares agent text to DB, so it passes this."""
    db, cal = env
    make_booking(db, cal, start="16:00", end="16:30")
    wrong = TRANSCRIPT.replace("3 PM is confirmed", "4 PM is confirmed")
    assert verify_live_call(wrong, CALL, datetime(2026, 11, 7, 9, 0), db)[0] in ("PASS", "FLAG")
    r = run(db, cal, transcript=wrong)
    assert "time_differs_from_caller" in r.findings
    assert r.verdict == "FLAG"


def test_phone_mismatch(env):
    db, cal = env
    make_booking(db, cal)
    r = run(db, cal, claims={**GOOD_CLAIMS, "phone": "03009999999"})
    assert "phone_differs_from_caller" in r.findings


def test_calendar_event_missing(env):
    db, cal = env
    make_booking(db, cal, with_event=False)
    assert "calendar_event_id_missing" in run(db, cal).findings


def test_calendar_event_deleted(env):
    db, cal = env
    eid = make_booking(db, cal)
    cal.delete_event("DOC-001", eid)
    assert "calendar_event_missing" in run(db, cal).findings


def test_calendar_time_mismatch(env):
    db, cal = env
    eid = make_booking(db, cal)
    for e in cal._events["DOC-001"]:
        if e["event_id"] == eid:
            e["start"], e["end"] = "16:00", "16:30"
    assert "calendar_details_mismatch" in run(db, cal).findings


def test_double_booking_detected(env):
    db, cal = env
    db.add(Patient(patient_id="P2", full_name="Sara", phone_number="03111111111"))
    db.add(Appointment(appointment_id="APT-OTHER", patient_id="P2", doctor_id="DOC-001",
                       appointment_date="2026-11-10", start_time="15:00", end_time="15:30",
                       status="CONFIRMED", calendar_event_id="e"))
    db.commit()
    make_booking(db, cal)
    assert "slot_double_booked" in run(db, cal).findings


def test_outside_hours_and_past(env):
    db, cal = env
    make_booking(db, cal, start="18:00", end="18:30", date="2026-11-01")
    f = run(db, cal).findings
    assert "outside_working_hours" in f and "appointment_in_past" in f


def test_reference_id_not_spoken(env):
    db, cal = env
    make_booking(db, cal)
    silent = TRANSCRIPT.replace("Your confirmation ID is APT-A1B2C3.", "See you then.")
    assert "reference_id_not_spoken" in run(db, cal, transcript=silent).findings


def test_caller_never_said_yes(env):
    db, cal = env
    make_booking(db, cal)
    r = run(db, cal, claims={**GOOD_CLAIMS, "confirmed": False})
    assert "caller_did_not_confirm" in r.findings


def test_hallucinated_booking_is_critical(env):
    db, cal = env
    t = ("Caller: Can I book tomorrow?\n"
         "Rachel: Done! Your confirmation ID is APT-FAKE99.")
    r = run(db, cal, transcript=t)
    assert r.verdict == "FLAG"
    assert "agent_spoke_reference_without_booking" in r.critical


def test_faq_call_is_no_change_and_never_calls_llm(env):
    db, cal = env

    def boom(*a):
        raise AssertionError("LLM must not be called when nothing was booked")

    t = "Caller: What are your opening hours?\nRachel: We open at 9 and close at 5."
    r = run(db, cal, transcript=t, extractor=boom)
    assert r.verdict == "NO_CHANGE" and r.findings == []


def test_extractor_failure_flags_instead_of_crashing(env):
    db, cal = env
    make_booking(db, cal)

    def down(*a):
        raise TimeoutError("quota")

    r = run(db, cal, extractor=down)
    assert r.verdict == "FLAG" and "caller_details_unverified" in r.findings


def test_karachi_start_time_regression(env):
    """verify_live_call strips tzinfo without converting; with a Karachi-aware start it
    looks 5h in the future and wrongly reports 'not recorded'. The auditor must not."""
    db, cal = env
    make_booking(db, cal)
    old = verify_live_call(TRANSCRIPT, CALL, STARTED, db)
    assert "appointment_change_not_recorded_for_call" in old[3]
    assert run(db, cal).verdict == "PASS"


def test_persist_is_idempotent_and_stores_codes_only(env):
    db, cal = env
    make_booking(db, cal)
    r = run(db, cal, claims={**GOOD_CLAIMS, "phone": "03009999999"})
    persist_audit(db, CALL, "live", STARTED, r)
    persist_audit(db, CALL, "live", STARTED, r)
    rows = db.query(LiveCallEvaluation).all()
    assert len(rows) == 1 and rows[0].execution_status == "FLAG"
    assert "phone_differs_from_caller" in json.loads(rows[0].findings_json)
    assert "Ali" not in rows[0].findings_json  # no transcript content leaks


def test_identical_short_numbers_are_not_reported_as_a_mismatch(env):
    """The caller said 12345 and 12345 was stored: that is 'invalid length', not 'differs'."""
    db, cal = env
    make_booking(db, cal)
    patient = db.query(Patient).filter_by(patient_id="P1").one()
    patient.phone_number = "12345"
    db.commit()
    r = run(db, cal, claims={**GOOD_CLAIMS, "phone": "12345"})
    assert "phone_not_plausible" in r.findings and "phone_differs_from_caller" not in r.findings
    assert by_key(r, "phone").status == "pass" and by_key(r, "phone_valid").status == "fail"


def test_helpers():
    assert phone_matches("+92 300 1234567", "03001234567")
    assert not phone_matches("03001234567", "03001234568")
    assert names_match("Ali Raza", "Raza Ali")
    assert names_match("Mahender Khatri", "Mahandar Khatri")
    assert not names_match("Ali Raza", "Sara Malik")
    assert severity("slot_double_booked") == "critical"
    assert severity("name_not_provided_by_caller") == "review"


# ---------------------------------------------------------------------------
# Check-by-check output (what the Tester page shows)
# ---------------------------------------------------------------------------

def by_key(result, key, appointment=None):
    return next(c for c in result.checks if c.key == key and
                (appointment is None or c.appointment_id in (None, appointment)))


def test_clean_call_lists_every_check_with_evidence(env):
    db, cal = env
    make_booking(db, cal)
    r = run(db, cal)
    assert r.verdict == "PASS"
    assert len(r.checks) >= 20
    assert all(c.status == "pass" for c in r.checks), [(c.key, c.status) for c in r.checks if c.status != "pass"]
    name = by_key(r, "name")
    assert (name.expected, name.actual) == ("Ali Raza", "Ali Raza")
    time_check = by_key(r, "time")
    assert (time_check.expected, time_check.actual) == ("3:00 PM", "3:00 PM")
    assert r.bookings[0]["patient_name"] == "Ali Raza" and r.bookings[0]["doctor"] == "Dr. Ahmed Khan"
    groups = {c.group for c in r.checks}
    assert groups == {"call", "caller_vs_booking", "rachel", "booking_record", "calendar"}


def test_wrong_time_shows_both_sides_and_is_critical(env):
    db, cal = env
    make_booking(db, cal, start="16:00", end="16:30")
    wrong = TRANSCRIPT.replace("3 PM is confirmed", "4 PM is confirmed")
    r = run(db, cal, transcript=wrong)
    c = by_key(r, "time")
    assert c.status == "fail" and c.code == "time_differs_from_caller" and c.severity == "critical"
    assert (c.expected, c.actual) == ("3:00 PM", "4:00 PM")


def test_calendar_mismatch_check_shows_calendar_time(env):
    db, cal = env
    eid = make_booking(db, cal)
    for e in cal._events["DOC-001"]:
        if e["event_id"] == eid:
            e["start"], e["end"] = "16:00", "16:30"
    r = run(db, cal)
    c = by_key(r, "time_match")
    assert c.status == "fail" and "4:00 PM" in c.actual and "3:00 PM" in c.expected


def test_unreadable_caller_side_is_skipped_not_passed(env):
    db, cal = env
    make_booking(db, cal)

    def down(*a):
        raise TimeoutError("quota")

    r = run(db, cal, extractor=down)
    assert by_key(r, "caller_readable").status == "fail"
    for key in ("name", "phone", "doctor", "date", "time", "consent"):
        assert by_key(r, key).status == "skip", key


def test_caller_who_named_no_doctor_is_skipped(env):
    db, cal = env
    make_booking(db, cal)
    r = run(db, cal, claims={**GOOD_CLAIMS, "doctor": None})
    assert by_key(r, "doctor").status == "skip"
    assert r.verdict == "PASS"


def test_no_booking_call_checks(env):
    db, cal = env
    t = "Caller: What are your opening hours?\nRachel: We open at 9 and close at 5."
    r = run(db, cal, transcript=t)
    assert r.verdict == "NO_CHANGE"
    assert [c.status for c in r.checks] == ["pass", "pass", "pass"]

    t2 = "Caller: Can I book tomorrow?\nRachel: Done! Your confirmation ID is APT-FAKE99."
    r2 = run(db, cal, transcript=t2)
    assert by_key(r2, "no_false_booking").status == "fail"


def test_cancelled_booking_checks(env):
    db, cal = env
    eid = make_booking(db, cal, action="CANCEL_APPOINTMENT")
    appt = db.query(Appointment).filter_by(appointment_id="APT-A1B2C3").one()
    appt.status = "CANCELLED"
    db.commit()
    cal.delete_event("DOC-001", eid)
    t = ("Caller: Please cancel my appointment with Dr. Ahmed Khan, I am Ali Raza.\n"
         "Rachel: Your appointment with Dr. Ahmed Khan on November 10 at 3 PM is cancelled.")
    r = run(db, cal, transcript=t)
    assert r.verdict == "PASS", r.findings
    assert [c.status for c in r.checks if c.appointment_id] == ["pass", "pass", "pass"]


def test_run_and_store_saves_checks_transcript_and_summary(env):
    db, cal = env
    make_booking(db, cal)
    r = run_and_store_audit(db, CALL, "live", STARTED, TRANSCRIPT, calendar=cal,
                            extractor=lambda t, d: GOOD_CLAIMS, now=NOW)
    assert r.verdict == "PASS"
    row = db.get(CallAuditDetail, CALL)
    assert row.verdict == "PASS" and row.transcript_text == TRANSCRIPT
    stored = json.loads(row.checks_json)
    assert len(stored) == len(r.checks) and stored[0]["status"] in ("pass", "fail", "skip")
    assert json.loads(row.bookings_json)[0]["appointment_id"] == "APT-A1B2C3"
    assert db.query(LiveCallEvaluation).count() == 1
    # storing again for the same room updates, never duplicates
    run_and_store_audit(db, CALL, "live", STARTED, TRANSCRIPT, calendar=cal,
                        extractor=lambda t, d: GOOD_CLAIMS, now=NOW)
    assert db.query(CallAuditDetail).count() == 1 and db.query(LiveCallEvaluation).count() == 1


def test_transcript_not_stored_when_disabled(env, monkeypatch):
    db, cal = env
    make_booking(db, cal)
    monkeypatch.setenv("STORE_CALL_TRANSCRIPTS", "false")
    run_and_store_audit(db, CALL, "live", STARTED, TRANSCRIPT, calendar=cal,
                        extractor=lambda t, d: GOOD_CLAIMS, now=NOW)
    assert db.get(CallAuditDetail, CALL).transcript_text is None


def test_tester_crash_leaves_a_visible_error_row(env):
    db, cal = env
    record_audit_error(db, "room-boom", "live", STARTED, "RuntimeError: db down", transcript=None)
    row = db.get(CallAuditDetail, "room-boom")
    assert row.verdict == "ERROR" and "db down" in row.error_text
    assert json.loads(row.findings_json) == ["audit_failed"]
    assert db.query(LiveCallEvaluation).filter_by(call_room_name="room-boom").one().execution_status == "ERROR"