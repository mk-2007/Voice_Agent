"""
Repository pattern implementations for database access.
Decouples SQLAlchemy queries from business logic.
"""

from datetime import datetime, timezone
import json
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from backend.database.models import (
    Patient,
    Doctor,
    Appointment,
    Conversation,
    ConversationEvent,
    EvaluationRun,
    EvaluationResult,
    AuditLog,
    generate_uuid,
)
from backend.call_context import current_voice_call_id


class PatientRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, patient_id: str) -> Optional[Patient]:
        return self.db.query(Patient).filter(Patient.patient_id == patient_id).first()

    def get_by_phone(self, phone_number: str) -> Optional[Patient]:
        return self.db.query(Patient).filter(Patient.phone_number == phone_number).first()

    def create(self, full_name: str, phone_number: str) -> Patient:
        patient = Patient(
            patient_id=f"PAT-{generate_uuid()[:8].upper()}",
            full_name=full_name.strip(),
            phone_number=phone_number.strip(),
        )
        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)
        return patient

    def get_or_create(self, full_name: str, phone_number: str) -> Patient:
        patient = self.get_by_phone(phone_number)
        if not patient:
            patient = self.create(full_name=full_name, phone_number=phone_number)
        return patient


class DoctorRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, doctor_id: str) -> Optional[Doctor]:
        return self.db.query(Doctor).filter(Doctor.doctor_id == doctor_id).first()

    def list_active(self) -> List[Doctor]:
        return self.db.query(Doctor).filter(Doctor.active_status == True).all()

    def find_by_name(self, name_query: str) -> Optional[Doctor]:
        query_clean = name_query.lower().replace("dr.", "").replace("dr", "").strip()
        doctors = self.list_active()
        for doc in doctors:
            doc_clean = doc.doctor_name.lower().replace("dr.", "").replace("dr", "").strip()
            if query_clean in doc_clean or doc_clean in query_clean:
                return doc
        return None


class AppointmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, appointment_id: str) -> Optional[Appointment]:
        return self.db.query(Appointment).filter(Appointment.appointment_id == appointment_id).first()

    def get_by_idempotency_key(self, idempotency_key: str) -> Optional[Appointment]:
        return self.db.query(Appointment).filter(Appointment.idempotency_key == idempotency_key).first()

    def find_conflict(self, doctor_id: str, appointment_date: str, start_time: str) -> Optional[Appointment]:
        return (
            self.db.query(Appointment)
            .filter(
                Appointment.doctor_id == doctor_id,
                Appointment.appointment_date == appointment_date,
                Appointment.start_time == start_time,
                Appointment.status.in_(["CONFIRMED", "PENDING"]),
            )
            .first()
        )

    def list_for_doctor(self, doctor_id: str, appointment_date: str) -> List[Appointment]:
        return (
            self.db.query(Appointment)
            .filter(
                Appointment.doctor_id == doctor_id,
                Appointment.appointment_date == appointment_date,
                Appointment.status.in_(["CONFIRMED", "PENDING"]),
            )
            .all()
        )

    def create(
        self,
        patient_id: str,
        doctor_id: str,
        appointment_date: str,
        start_time: str,
        end_time: str,
        appointment_type: str = "General Consultation",
        status: str = "CONFIRMED",
        calendar_event_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        notes: Optional[str] = None,
        appointment_id: Optional[str] = None,
    ) -> Appointment:
        appointment = Appointment(
            appointment_id=appointment_id or f"APT-{generate_uuid()[:8].upper()}",
            patient_id=patient_id,
            doctor_id=doctor_id,
            appointment_date=appointment_date,
            start_time=start_time,
            end_time=end_time,
            appointment_type=appointment_type,
            status=status,
            calendar_event_id=calendar_event_id,
            idempotency_key=idempotency_key,
            notes=notes,
        )
        self.db.add(appointment)
        self.db.commit()
        self.db.refresh(appointment)
        return appointment

    def cancel(self, appointment_id: str) -> Optional[Appointment]:
        apt = self.get_by_id(appointment_id)
        if apt:
            apt.status = "CANCELLED"
            self.db.commit()
            self.db.refresh(apt)
        return apt

    def update_status(self, appointment_id: str, status: str, calendar_event_id: Optional[str] = None) -> Optional[Appointment]:
        apt = self.get_by_id(appointment_id)
        if apt:
            apt.status = status
            if calendar_event_id:
                apt.calendar_event_id = calendar_event_id
            self.db.commit()
            self.db.refresh(apt)
        return apt


class AuditRepository:
    def __init__(self, db: Session):
        self.db = db

    def log(
        self,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        result: str = "SUCCESS",
        metadata: Optional[dict] = None,
    ) -> AuditLog:
        audit_metadata = dict(metadata or {})
        call_id = current_voice_call_id.get()
        if call_id and resource_type == "APPOINTMENT" and action in {
            "BOOK_APPOINTMENT",
            "RESCHEDULE_APPOINTMENT",
            "CANCEL_APPOINTMENT",
        }:
            audit_metadata["call_id"] = call_id
        audit = AuditLog(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            metadata_json=json.dumps(audit_metadata),
        )
        self.db.add(audit)
        self.db.commit()
        return audit
