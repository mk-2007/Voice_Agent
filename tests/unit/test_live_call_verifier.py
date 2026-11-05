"""Tests for call-correlated appointment verification without calendar reads."""

from contextvars import Token
from datetime import datetime, timezone
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api.schemas.appointment_schemas import AppointmentCreate
from backend.database.models import Appointment, AuditLog, Base, Doctor
from backend.services.appointment_service import AppointmentService
from backend.call_context import current_voice_call_id
from backend.services.calendar_service import MockCalendarService
from backend.services.live_call_verifier import verify_live_call


@pytest.fixture
def live_test_records():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(Doctor(
        doctor_id="DOC-001",
        doctor_name="Dr. Ahmed Khan",
        specialization="General Physician",
        calendar_id="isolated-test",
        timezone="America/New_York",
        active_status=True,
        working_hours_start="09:00",
        working_hours_end="17:00",
    ))
    session.commit()
    calendar = MockCalendarService()
    calendar.clear()
    started_at = datetime.now(timezone.utc)
    call_id = "clinical-test-room-1"
    call_token: Token[str | None] = current_voice_call_id.set(call_id)
    try:
        booking = AppointmentService(session, calendar).book_appointment(AppointmentCreate(
            patient_name="Test Patient",
            contact_number="555-123-4567",
            doctor_id="DOC-001",
            appointment_date="2026-11-09",
            appointment_time="10:00",
        ))
    finally:
        current_voice_call_id.reset(call_token)
    yield session, calendar, started_at, booking, call_id
    session.close()
    engine.dispose()


def _booking_transcript(appointment_id: str) -> str:
    return (
        "You: My name is Test Patient and I want to schedule an appointment.\n"
        "Rachel: Your appointment is confirmed with Dr. Ahmed Khan on November 9 at 10:00 AM.\n"
        f"Confirmation ID: {appointment_id}."
    )


def test_call_linked_booking_matches_patient_doctor_date_and_time(live_test_records):
    session, _calendar, started_at, booking, call_id = live_test_records

    result = verify_live_call(_booking_transcript(booking.appointment_id), call_id, started_at, session)

    assert result == ("PASS", "BOOK_APPOINTMENT", 1, [])


def test_other_calls_booking_is_not_mistaken_for_this_call(live_test_records):
    session, _calendar, started_at, booking, _call_id = live_test_records

    result = verify_live_call(
        _booking_transcript(booking.appointment_id),
        "another-room",
        started_at,
        session,
    )

    assert result == ("FLAG", None, 0, ["appointment_change_not_recorded_for_call"])


def test_mismatched_confirmation_is_flagged_even_if_values_appear_in_user_speech(live_test_records):
    session, _calendar, started_at, booking, call_id = live_test_records
    transcript = (
        "You: I need an appointment for Test Patient on November 9 at 10:00 with Dr. Ahmed Khan.\n"
        "Rachel: Your appointment is confirmed with Dr. Ahmed Khan on November 9 at 11:00 AM.\n"
        f"Confirmation ID: {booking.appointment_id}."
    )

    status, action_type, action_count, findings = verify_live_call(
        transcript, call_id, started_at, session,
    )

    assert status == "FLAG"
    assert action_type == "BOOK_APPOINTMENT"
    assert action_count == 1
    assert "time_not_confirmed_in_transcript" in findings


def test_last_confirmation_is_checked_not_an_earlier_correct_mention(live_test_records):
    session, _calendar, started_at, booking, call_id = live_test_records
    transcript = (
        "You: My name is Test Patient and I want November 9 at 10:00 with Dr. Ahmed Khan.\n"
        "Rachel: Dr. Ahmed Khan is available on November 9 at 10:00 AM.\n"
        "You: Yes, please book it.\n"
        "Rachel: Your appointment is confirmed with Dr. Ahmed Khan on November 10 at 11:00 AM.\n"
        f"Confirmation ID: {booking.appointment_id}."
    )

    status, _action_type, _action_count, findings = verify_live_call(
        transcript, call_id, started_at, session,
    )

    assert status == "FLAG"
    assert "date_not_confirmed_in_transcript" in findings
    assert "time_not_confirmed_in_transcript" in findings


def test_wrong_doctor_in_confirmation_is_flagged(live_test_records):
    session, _calendar, started_at, booking, call_id = live_test_records
    transcript = (
        "You: My name is Test Patient and I want an appointment.\n"
        "Rachel: Your appointment is confirmed with Dr. Sarah Patel on November 9 at 10:00 AM.\n"
        f"Confirmation ID: {booking.appointment_id}."
    )

    status, _action_type, _action_count, findings = verify_live_call(
        transcript, call_id, started_at, session,
    )

    assert status == "FLAG"
    assert "doctor_not_confirmed_in_transcript" in findings


def test_patient_name_must_be_present_in_transcript(live_test_records):
    session, _calendar, started_at, booking, call_id = live_test_records
    transcript = (
        "You: Please book the appointment.\n"
        "Rachel: Your appointment is confirmed with Dr. Ahmed Khan on November 9 at 10:00 AM.\n"
        f"Confirmation ID: {booking.appointment_id}."
    )

    status, _action_type, _action_count, findings = verify_live_call(
        transcript, call_id, started_at, session,
    )

    assert status == "FLAG"
    assert "patient_name_not_in_transcript" in findings


def test_reschedule_of_preexisting_appointment_is_verified(live_test_records):
    session, calendar, _created_at, booking, _old_call_id = live_test_records
    started_at = datetime.now(timezone.utc)
    call_id = "clinical-test-room-reschedule"
    token = current_voice_call_id.set(call_id)
    try:
        AppointmentService(session, calendar).reschedule_appointment(
            booking.appointment_id,
            "2026-11-10",
            "11:00",
        )
    finally:
        current_voice_call_id.reset(token)

    transcript = (
        "You: Please reschedule my appointment for Test Patient.\n"
        "Rachel: Your appointment has been rescheduled with Dr. Ahmed Khan on November 10 at 11:00 AM."
    )

    result = verify_live_call(transcript, call_id, started_at, session)

    assert result == ("PASS", "RESCHEDULE_APPOINTMENT", 1, [])


def test_call_linked_cancellation_is_verified(live_test_records):
    session, calendar, _created_at, booking, _old_call_id = live_test_records
    started_at = datetime.now(timezone.utc)
    call_id = "clinical-test-room-cancel"
    token = current_voice_call_id.set(call_id)
    try:
        assert AppointmentService(session, calendar).cancel_appointment(booking.appointment_id)
    finally:
        current_voice_call_id.reset(token)

    transcript = (
        "You: Please cancel my appointment for Test Patient.\n"
        "Rachel: Appointment for Test Patient with Dr. Ahmed Khan on November 9 at 10:00 AM "
        "has been successfully cancelled."
    )

    result = verify_live_call(transcript, call_id, started_at, session)

    assert result == ("PASS", "CANCEL_APPOINTMENT", 1, [])


def test_database_record_must_match_action_audit(live_test_records):
    session, _calendar, started_at, _booking, call_id = live_test_records
    audit = session.query(AuditLog).filter_by(action="BOOK_APPOINTMENT").one()
    metadata = json.loads(audit.metadata_json)
    metadata["time"] = "11:00"
    audit.metadata_json = json.dumps(metadata)
    session.commit()

    status, _action_type, _action_count, findings = verify_live_call(
        _booking_transcript("APT-TEST"), call_id, started_at, session,
    )

    assert status == "FLAG"
    assert "database_details_do_not_match_action" in findings


def test_no_appointment_change_is_neutral(live_test_records):
    session, _calendar, started_at, _booking, _call_id = live_test_records

    result = verify_live_call(
        "You: What time does the clinic close? Rachel: We close at 5 PM.",
        "new-room",
        started_at,
        session,
    )

    assert result == ("NO_CHANGE", None, 0, [])


def test_missing_transcript_is_flagged(live_test_records):
    session, _calendar, started_at, _booking, _call_id = live_test_records

    result = verify_live_call("", "empty-transcript-room", started_at, session)

    assert result == ("FLAG", None, 0, ["transcript_missing"])


def test_booking_request_without_call_linked_record_is_flagged(live_test_records):
    session, _calendar, started_at, _booking, _call_id = live_test_records

    result = verify_live_call(
        "You: I want to book an appointment.",
        "new-room",
        started_at,
        session,
    )

    assert result == ("FLAG", None, 0, ["appointment_change_not_recorded_for_call"])


def test_cancel_request_is_not_reported_as_no_change(live_test_records):
    session, _calendar, started_at, _booking, _call_id = live_test_records

    result = verify_live_call(
        "You: Please cancel my appointment.",
        "new-room",
        started_at,
        session,
    )

    assert result == ("FLAG", None, 0, ["appointment_change_not_recorded_for_call"])
