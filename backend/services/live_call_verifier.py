"""Verify call-linked appointment actions without retaining the transcript."""

from contextlib import contextmanager
from datetime import datetime
import json
import logging
from pathlib import Path
import re
from typing import Dict, Iterator, List, Optional, Tuple

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from backend.database.models import Appointment, AuditLog, Base, Doctor
from config.settings import settings

logger = logging.getLogger(__name__)

APPOINTMENT_ACTIONS = (
    "BOOK_APPOINTMENT",
    "RESCHEDULE_APPOINTMENT",
    "CANCEL_APPOINTMENT",
)


def database_urls_match(first: str, second: str) -> bool:
    first_url = make_url(first)
    second_url = make_url(second)
    if first_url.get_backend_name() == second_url.get_backend_name() == "sqlite":
        first_path = first_url.database
        second_path = second_url.database
        if first_path in (None, "", ":memory:") or second_path in (None, "", ":memory:"):
            return first_path == second_path
        return Path(first_path).resolve() == Path(second_path).resolve()
    return first_url == second_url


def _normalized(value: str) -> str:
    value = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", value.lower())
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _contains_phrase(text: str, value: str) -> bool:
    normalized_text = f" {_normalized(text)} "
    normalized_value = _normalized(value)
    return bool(normalized_value) and f" {normalized_value} " in normalized_text


def _contains_date(text: str, date_str: str) -> bool:
    appointment_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    forms = (
        date_str,
        appointment_date.strftime("%m/%d/%Y"),
        appointment_date.strftime("%m/%d"),
        f"{appointment_date.strftime('%B')} {appointment_date.day}",
        f"{appointment_date.strftime('%b')} {appointment_date.day}",
    )
    return any(_contains_phrase(text, form) for form in forms)


def _contains_time(text: str, time_str: str) -> bool:
    appointment_time = datetime.strptime(time_str, "%H:%M").time()
    hour_12 = appointment_time.hour % 12 or 12
    meridiem = "am" if appointment_time.hour < 12 else "pm"
    forms = [
        time_str,
        f"{hour_12}:{appointment_time.minute:02d} {meridiem}",
        f"{hour_12} {meridiem}" if appointment_time.minute == 0 else "",
    ]
    return any(form and _contains_phrase(text, form) for form in forms)


def _speaker_blocks(transcript: str) -> List[Tuple[str, str]]:
    blocks: List[Tuple[str, str]] = []
    for line in transcript.splitlines():
        label, separator, content = line.partition(":")
        if not separator:
            continue
        speaker = label.strip()
        if blocks and blocks[-1][0].lower() == speaker.lower():
            blocks[-1] = (speaker, f"{blocks[-1][1]} {content.strip()}")
        else:
            blocks.append((speaker, content.strip()))
    return blocks


def _confirmation_text(transcript: str, action: str) -> str:
    if action == "BOOK_APPOINTMENT":
        pattern = r"\b(confirm(?:ed)?|booked|scheduled)\b"
    elif action == "RESCHEDULE_APPOINTMENT":
        pattern = r"\b(reschedul(?:e|ed|ing)|moved|changed)\b"
    else:
        pattern = r"\b(cancel(?:led|ed|ing)?|cancellation)\b"
    confirmations = [
        text for speaker, text in _speaker_blocks(transcript)
        if speaker.lower() == "rachel" and re.search(pattern, text, re.IGNORECASE)
    ]
    return confirmations[-1] if confirmations else ""


def _has_appointment_intent(transcript: str) -> bool:
    return bool(re.search(
        r"\b(book(?:ed|ing)?|schedule(?:d|s|ing)?|reserve(?:d|s|ation)?|reschedul(?:e|ed|ing))\b"
        r"|\bcancel(?:led|ed|ing)?\b"
        r"|\b(?:need|want|make|get)\s+(?:an?\s+)?appointment\b"
        r"|\b(?:move|change|update)\s+(?:my|the|an?\s+)?appointment\b",
        transcript,
        re.IGNORECASE,
    ))


def _action_details_match(
    appointment: Appointment,
    action: AuditLog,
    metadata: Dict[str, str],
    transcript: str,
) -> List[str]:
    findings: List[str] = []
    if action.action == "RESCHEDULE_APPOINTMENT":
        expected_date = metadata.get("new_date")
        expected_time = metadata.get("new_time")
    else:
        expected_date = metadata.get("date")
        expected_time = metadata.get("time")
    expected_patient_id = metadata.get("patient_id")
    expected_doctor_id = metadata.get("doctor_id")

    expected_status = "CANCELLED" if action.action == "CANCEL_APPOINTMENT" else "CONFIRMED"
    if appointment.status != expected_status:
        findings.append("appointment_status_mismatch")
    if (
        not expected_date
        or not expected_time
        or not expected_patient_id
        or not expected_doctor_id
        or appointment.appointment_date != expected_date
        or appointment.start_time != expected_time
        or appointment.patient_id != expected_patient_id
        or appointment.doctor_id != expected_doctor_id
    ):
        findings.append("database_details_do_not_match_action")

    patient_name = appointment.patient.full_name if appointment.patient else ""
    if not patient_name or not _contains_phrase(transcript, patient_name):
        findings.append("patient_name_not_in_transcript")

    agent_text = _confirmation_text(transcript, action.action)
    doctor_name = appointment.doctor.doctor_name if appointment.doctor else ""
    doctor_name_without_title = re.sub(r"^(?:dr|doctor)\.?\s*", "", doctor_name, flags=re.IGNORECASE)
    if not doctor_name or not (
        _contains_phrase(agent_text, doctor_name)
        or _contains_phrase(agent_text, doctor_name_without_title)
    ):
        findings.append("doctor_not_confirmed_in_transcript")
    if not expected_date or not _contains_date(agent_text, expected_date):
        findings.append("date_not_confirmed_in_transcript")
    if not expected_time or not _contains_time(agent_text, expected_time):
        findings.append("time_not_confirmed_in_transcript")

    if action.action == "BOOK_APPOINTMENT":
        confirmation = re.search(r"\b(confirm(?:ed)?|booked|scheduled)\b", agent_text, re.IGNORECASE)
    elif action.action == "RESCHEDULE_APPOINTMENT":
        confirmation = re.search(r"\b(reschedul(?:e|ed|ing)|moved|changed)\b", agent_text, re.IGNORECASE)
    else:
        confirmation = re.search(r"\b(cancel(?:led|ed|ing)?|cancellation)\b", agent_text, re.IGNORECASE)
    if not confirmation:
        findings.append("appointment_confirmation_not_heard")
    return findings


def verify_live_call(
    transcript: str,
    call_id: str,
    started_at: datetime,
    db: Session,
) -> Tuple[str, Optional[str], int, List[str]]:
    """Verify only appointment actions audited for this call; never persist transcript data."""
    if not transcript.strip():
        return "FLAG", None, 0, ["transcript_missing"]

    start = started_at.replace(tzinfo=None) if started_at.tzinfo else started_at
    action_logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.timestamp >= start,
            AuditLog.resource_type == "APPOINTMENT",
            AuditLog.action.in_(APPOINTMENT_ACTIONS),
        )
        .order_by(AuditLog.timestamp.asc())
        .all()
    )

    matching_actions: List[Tuple[AuditLog, Dict[str, str]]] = []
    for action in action_logs:
        try:
            metadata = json.loads(action.metadata_json or "{}")
        except json.JSONDecodeError:
            logger.warning("Skipping malformed appointment audit metadata")
            continue
        if metadata.get("call_id") == call_id:
            matching_actions.append((action, metadata))

    if not matching_actions:
        if _has_appointment_intent(transcript):
            return "FLAG", None, 0, ["appointment_change_not_recorded_for_call"]
        return "NO_CHANGE", None, 0, []

    latest_by_appointment: Dict[str, Tuple[AuditLog, Dict[str, str]]] = {}
    for action, metadata in matching_actions:
        latest_by_appointment[action.resource_id] = (action, metadata)

    findings: List[str] = []
    action_types = []
    for appointment_id, (action, metadata) in latest_by_appointment.items():
        action_types.append(action.action)
        appointment = db.query(Appointment).filter_by(appointment_id=appointment_id).first()
        if appointment is None:
            findings.append("appointment_record_missing")
            continue
        findings.extend(_action_details_match(appointment, action, metadata, transcript))

    unique_findings = list(dict.fromkeys(findings))
    action_type = " + ".join(dict.fromkeys(action_types))
    return (
        "PASS" if not unique_findings else "FLAG",
        action_type,
        len(latest_by_appointment),
        unique_findings,
    )


@contextmanager
def live_test_database() -> Iterator[Session]:
    """Open the isolated test database; its worker uses the mock calendar."""
    if not settings.LIVE_TEST_DATABASE_URL:
        raise RuntimeError("LIVE_TEST_DATABASE_URL is not configured")
    if database_urls_match(settings.LIVE_TEST_DATABASE_URL, settings.DATABASE_URL):
        raise RuntimeError("Live call testing requires a separate LIVE_TEST_DATABASE_URL")

    connect_args = (
        {"check_same_thread": False}
        if make_url(settings.LIVE_TEST_DATABASE_URL).get_backend_name() == "sqlite"
        else {}
    )
    engine = create_engine(
        settings.LIVE_TEST_DATABASE_URL,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as db:
            if db.query(Doctor).count() == 0:
                for doctor in (
                    Doctor(
                        doctor_id="DOC-001", doctor_name="Dr. Ahmed Khan",
                        specialization="General Physician", calendar_id="isolated-test",
                        timezone=settings.CLINIC_TIMEZONE, active_status=True,
                        working_hours_start="09:00", working_hours_end="17:00",
                    ),
                    Doctor(
                        doctor_id="DOC-002", doctor_name="Dr. Sarah Patel",
                        specialization="Dermatologist", calendar_id="isolated-test",
                        timezone=settings.CLINIC_TIMEZONE, active_status=True,
                        working_hours_start="10:00", working_hours_end="16:00",
                    ),
                    Doctor(
                        doctor_id="DOC-003", doctor_name="Dr. Michael Chen",
                        specialization="Cardiologist", calendar_id="isolated-test",
                        timezone=settings.CLINIC_TIMEZONE, active_status=True,
                        working_hours_start="09:00", working_hours_end="15:00",
                    ),
                ):
                    db.add(doctor)
                db.commit()
            yield db
    finally:
        engine.dispose()
