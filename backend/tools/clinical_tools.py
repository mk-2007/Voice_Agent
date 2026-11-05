"""
Controlled Agent Tools for the Clinical Voice Agent.
Implements the 8 mandatory tools defined in PRD Section 7.3.
Each tool provides typed signatures, docstrings for LLM reasoning,
and robust error handling so errors are reported verbally to the caller
rather than crashing the conversation.
"""

from datetime import datetime
import json
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from config.settings import settings
from backend.database.connection import SessionLocal
from backend.database.repositories import DoctorRepository
from backend.services.appointment_service import AppointmentService
from backend.api.schemas.appointment_schemas import AppointmentCreate

logger = logging.getLogger(__name__)


def get_clinic_information() -> Dict[str, Any]:
    """
    Returns verified administrative information about the clinic,
    including address, phone number, operating hours, and emergency guidance.
    """
    return {
        "clinic_name": settings.CLINIC_NAME,
        "address": settings.CLINIC_ADDRESS,
        "phone": settings.CLINIC_PHONE,
        "operating_hours": f"Monday to Friday, {settings.CLINIC_OPENING_TIME} to {settings.CLINIC_CLOSING_TIME} ({settings.CLINIC_TIMEZONE})",
        "appointment_duration_minutes": settings.DEFAULT_APPOINTMENT_DURATION_MINUTES,
        "emergency_guidance": "In case of severe symptoms or a life-threatening medical emergency, call 911 immediately or go to the nearest emergency room.",
    }


def get_doctor_information(
    query: Optional[str] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Retrieves information on clinic doctors and their specializations.
    
    Args:
        query: Optional doctor name (e.g. 'Ahmed') or medical specialty (e.g. 'Dermatologist').
        db: Optional database session.
    """
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True

    try:
        doctor_repo = DoctorRepository(db)
        doctors = doctor_repo.list_active()
        
        if query:
            clean_q = query.lower().replace("dr.", "").replace("dr", "").strip()
            matched = [
                d for d in doctors
                if clean_q in d.doctor_name.lower() or clean_q in d.specialization.lower()
            ]
            doctors = matched

        return {
            "doctors": [
                {
                    "doctor_id": d.doctor_id,
                    "name": d.doctor_name,
                    "specialization": d.specialization,
                    "working_hours": f"{d.working_hours_start} - {d.working_hours_end}",
                    "active": d.active_status,
                }
                for d in doctors
            ]
        }
    finally:
        if own_session:
            db.close()


def check_availability(
    doctor_id: str,
    appointment_date: str,
    preferred_time: Optional[str] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Checks real-time availability for a specific doctor on a given date and optional time.
    
    Args:
        doctor_id: Doctor ID (e.g. 'DOC-001') or doctor name (e.g. 'Dr. Ahmed Khan').
        appointment_date: Date in YYYY-MM-DD format.
        preferred_time: Desired time in HH:MM (24-hour) format (e.g. '11:00').
        db: Optional database session.
    """
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True

    try:
        service = AppointmentService(db)
        resp = service.check_availability(
            doctor_id=doctor_id,
            appointment_date=appointment_date,
            preferred_time=preferred_time,
        )
        return resp.model_dump()
    except Exception as e:
        logger.error(f"check_availability error: {e}")
        return {
            "doctor_id": doctor_id,
            "doctor_name": doctor_id,
            "appointment_date": appointment_date,
            "requested_time_available": False,
            "requested_time": preferred_time,
            "available_slots": [],
            "alternative_slots": [],
            "error": str(e),
            "message": f"Unable to check availability: {str(e)}",
        }
    finally:
        if own_session:
            db.close()


def get_alternative_slots(
    doctor_id: str,
    appointment_date: str,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Returns available alternative appointment times for a doctor on a specific date.
    
    Args:
        doctor_id: Doctor ID or name.
        appointment_date: Date in YYYY-MM-DD format.
        db: Optional database session.
    """
    return check_availability(
        doctor_id=doctor_id,
        appointment_date=appointment_date,
        preferred_time=None,
        db=db,
    )


def book_appointment(
    patient_name: str,
    contact_number: str,
    doctor_id: str,
    appointment_date: str,
    appointment_time: str,
    appointment_type: str = "General Consultation",
    idempotency_key: Optional[str] = None,
    notes: Optional[str] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Books a verified appointment. Checks availability, prevents double-booking,
    creates the calendar event, and commits the database record.
    
    Args:
        patient_name: Full name of the patient.
        contact_number: Patient's phone number.
        doctor_id: Doctor ID or name.
        appointment_date: Date in YYYY-MM-DD format.
        appointment_time: Slot time in HH:MM (24-hour) format (e.g. '14:30').
        appointment_type: Type of visit (e.g. 'General Consultation', 'Skin Check').
        idempotency_key: Unique identifier to avoid duplicate bookings on network retries.
        notes: Optional medical symptoms or reasons provided by caller.
        db: Optional database session.
    """
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True

    try:
        service = AppointmentService(db)
        req = AppointmentCreate(
            patient_name=patient_name,
            contact_number=contact_number,
            doctor_id=doctor_id,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            appointment_type=appointment_type,
            idempotency_key=idempotency_key,
            notes=notes,
        )
        resp = service.book_appointment(req)
        return resp.model_dump()
    except Exception as e:
        logger.error(f"book_appointment error: {e}")
        return {
            "appointment_id": "",
            "patient_name": patient_name,
            "status": "FAILED",
            "error": str(e),
            "message": f"Could not book appointment: {str(e)}",
        }
    finally:
        if own_session:
            db.close()


def get_appointment(
    appointment_id: str,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Retrieves details of an existing appointment by its appointment ID.
    
    Args:
        appointment_id: The appointment reference ID (e.g. 'APT-XXXXXX').
        db: Optional database session.
    """
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True

    try:
        service = AppointmentService(db)
        resp = service.get_appointment(appointment_id)
        if not resp:
            return {"appointment_id": appointment_id, "found": False, "message": f"Appointment '{appointment_id}' not found."}
        data = resp.model_dump()
        data["found"] = True
        return data
    finally:
        if own_session:
            db.close()


def cancel_appointment(
    appointment_id: str,
    reason: Optional[str] = "Patient requested cancellation",
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Cancels an existing appointment in both the clinic database and calendar.
    
    Args:
        appointment_id: The appointment reference ID.
        reason: Optional explanation for cancellation.
        db: Optional database session.
    """
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True

    try:
        existing = get_appointment(appointment_id, db=db)
        service = AppointmentService(db)
        success = service.cancel_appointment(appointment_id=appointment_id, reason=reason or "")
        if success:
            if existing.get("found"):
                return {
                    "appointment_id": appointment_id,
                    "cancelled": True,
                    "message": (
                        f"Appointment for {existing['patient_name']} with {existing['doctor_name']} "
                        f"on {existing['appointment_date']} at {existing['start_time']} "
                        "has been successfully cancelled."
                    ),
                }
            return {
                "appointment_id": appointment_id,
                "cancelled": True,
                "message": f"Appointment {appointment_id} has been successfully cancelled.",
            }
        else:
            return {
                "appointment_id": appointment_id,
                "cancelled": False,
                "message": f"Appointment {appointment_id} could not be cancelled. Please verify the appointment ID.",
            }
    finally:
        if own_session:
            db.close()


def reschedule_appointment(
    appointment_id: str,
    new_date: str,
    new_time: str,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Reschedules an existing appointment to a new date and time.
    
    Args:
        appointment_id: The appointment reference ID.
        new_date: New appointment date in YYYY-MM-DD format.
        new_time: New appointment start time in HH:MM format.
        db: Optional database session.
    """
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True

    try:
        service = AppointmentService(db)
        resp = service.reschedule_appointment(
            appointment_id=appointment_id,
            new_date=new_date,
            new_time=new_time,
        )
        return resp.model_dump()
    except Exception as e:
        logger.error(f"reschedule_appointment error: {e}")
        return {
            "appointment_id": appointment_id,
            "status": "FAILED",
            "error": str(e),
            "message": f"Could not reschedule appointment: {str(e)}",
        }
    finally:
        if own_session:
            db.close()
