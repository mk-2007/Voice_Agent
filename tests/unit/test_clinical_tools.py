"""
Unit tests for the 8 Clinical Agent Tools.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base, Doctor
from backend.services.calendar_service import get_calendar_service, MockCalendarService
from backend.tools.clinical_tools import (
    get_clinic_information,
    get_doctor_information,
    check_availability,
    get_alternative_slots,
    book_appointment,
    get_appointment,
    cancel_appointment,
    reschedule_appointment,
)


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed test doctor
    doc = Doctor(
        doctor_id="DOC-001",
        doctor_name="Dr. Ahmed Khan",
        specialization="General Physician",
        calendar_id="primary",
        timezone="America/New_York",
        active_status=True,
        working_hours_start="09:00",
        working_hours_end="17:00",
    )
    session.add(doc)
    session.commit()

    # Reset mock calendar
    cal = get_calendar_service()
    if isinstance(cal, MockCalendarService):
        cal.clear()

    yield session
    session.close()


def test_get_clinic_info():
    info = get_clinic_information()
    assert "clinic_name" in info
    assert "emergency_guidance" in info
    assert "911" in info["emergency_guidance"]


def test_get_doctor_info(test_db):
    res = get_doctor_information(query=None, db=test_db)
    assert len(res["doctors"]) >= 1
    assert res["doctors"][0]["name"] == "Dr. Ahmed Khan"

    filtered = get_doctor_information(query="Ahmed", db=test_db)
    assert len(filtered["doctors"]) == 1
    assert filtered["doctors"][0]["doctor_id"] == "DOC-001"

    unmatched = get_doctor_information(query="Ramad Khan", db=test_db)
    assert unmatched["doctors"] == []


def test_check_availability_and_alternatives(test_db):
    res = check_availability(
        doctor_id="DOC-001",
        appointment_date="2026-11-04",
        preferred_time="10:00",
        db=test_db,
    )
    assert res["requested_time_available"] is True
    assert "10:00" in res["available_slots"]

    alt = get_alternative_slots(
        doctor_id="DOC-001",
        appointment_date="2026-11-04",
        db=test_db,
    )
    assert len(alt["available_slots"]) > 0


def test_booking_workflow_lifecycle(test_db):
    # 1. Book appointment
    booked = book_appointment(
        patient_name="David Miller",
        contact_number="555-4321",
        doctor_id="DOC-001",
        appointment_date="2026-11-04",
        appointment_time="10:00",
        appointment_type="Consultation",
        idempotency_key="tool-test-key-1",
        db=test_db,
    )
    assert booked["status"] == "CONFIRMED"
    apt_id = booked["appointment_id"]
    assert apt_id.startswith("APT-")

    # 2. Get appointment
    fetched = get_appointment(appointment_id=apt_id, db=test_db)
    assert fetched["found"] is True
    assert fetched["patient_name"] == "David Miller"
    assert fetched["start_time"] == "10:00"

    # 3. Reschedule appointment
    rescheduled = reschedule_appointment(
        appointment_id=apt_id,
        new_date="2026-11-04",
        new_time="11:00",
        db=test_db,
    )
    assert rescheduled["status"] == "CONFIRMED"
    assert rescheduled["start_time"] == "11:00"

    # 4. Cancel appointment
    cancelled = cancel_appointment(appointment_id=apt_id, db=test_db)
    assert cancelled["cancelled"] is True

    # 5. Verify cancelled status
    check_cancelled = get_appointment(appointment_id=apt_id, db=test_db)
    assert check_cancelled["status"] == "CANCELLED"
