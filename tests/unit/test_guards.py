import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agent.guards import (
    list_doctor_names, resolve_doctor, validate_name, validate_phone,
)
from backend.database.models import Base, Doctor


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for did, name, spec in (
        ("DOC-001", "Dr. Ahmed Khan", "General Physician"),
        ("DOC-002", "Dr. Sarah Patel", "Dermatologist"),
        ("DOC-003", "Dr. Michael Chen", "Cardiologist"),
    ):
        session.add(Doctor(doctor_id=did, doctor_name=name, specialization=spec, calendar_id="x"))
    session.add(Doctor(doctor_id="DOC-004", doctor_name="Dr. Old Retired", specialization="GP",
                       calendar_id="x", active_status=False))
    session.commit()
    return session


@pytest.mark.parametrize("raw,expected", [
    ("DOC-003", "DOC-003"),
    ("DOC002", "DOC-002"),               # the real call: no hyphen
    ("doc 001", "DOC-001"),
    ("dr_michael_chen", "DOC-003"),      # the real call
    ("DR_CHEN", "DOC-003"),              # the real call
    ("Dr. Ahmed Khan", "DOC-001"),
    ("ahmed khan", "DOC-001"),
    ("Doctor Sarah Patel", "DOC-002"),
    ("Chen", "DOC-003"),
    ("dermatologist", "DOC-002"),
    ("cardiologist", "DOC-003"),
])
def test_resolves_reasonable_spellings(db, raw, expected):
    assert resolve_doctor(db, raw).doctor_id == expected


@pytest.mark.parametrize("raw", [
    "Aymer Khan",        # misheard name: must NOT silently become Ahmed Khan
    "Dr. Nobody",
    "", "   ", "dr", "doctor",
    "DOC-999",
    "Dr. Old Retired",   # inactive doctor
])
def test_does_not_guess(db, raw):
    assert resolve_doctor(db, raw) is None


def test_unique_specialization_word(db):
    assert resolve_doctor(db, "physician").doctor_id == "DOC-001"


def test_ambiguous_part_of_name_is_not_guessed(db):
    db.add(Doctor(doctor_id="DOC-005", doctor_name="Dr. Bilal Khan", specialization="ENT", calendar_id="x"))
    db.commit()
    assert resolve_doctor(db, "Khan") is None
    assert resolve_doctor(db, "Ahmed Khan").doctor_id == "DOC-001"


def test_list_names_only_active(db):
    assert list_doctor_names(db) == ["Dr. Ahmed Khan", "Dr. Sarah Patel", "Dr. Michael Chen"]


@pytest.mark.parametrize("phone", [
    "03001234567", "0300 1234567", "+92 300 1234567", "+923001234567", "(555) 234-5678", "0300-123-4567",
])
def test_valid_phones(phone):
    assert validate_phone(phone) == (True, "")


@pytest.mark.parametrize("phone,digits", [
    ("123456", 6), ("0300123", 7), ("", 0), ("no number", 0), ("1234567890123456", 16),
])
def test_invalid_phones(phone, digits):
    ok, message = validate_phone(phone)
    assert not ok and f"{digits} digits" in message and "Do not book yet" in message


def test_names():
    assert validate_name("Ali Raza") == (True, "")
    assert validate_name("Ma") == (True, "")
    for bad in ("", " ", "A", "12345"):
        assert validate_name(bad)[0] is False