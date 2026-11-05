"""
Appointment Service implementing deterministic clinical booking logic.
Adheres to PRD Section 7 & Section 8 rules:
- Slot calculations with working hours, duration, and buffers.
- Double-booking prevention across both Calendar and Database.
- Strict idempotency key handling.
- Atomic two-phase booking with reconciliation fallback.
- Audit trail creation.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from config.settings import settings
from backend.database.models import Appointment, Doctor, Patient, generate_uuid
from backend.database.repositories import (
    AppointmentRepository,
    DoctorRepository,
    PatientRepository,
    AuditRepository,
)
from backend.services.calendar_service import get_calendar_service, BaseCalendarService
from backend.api.schemas.appointment_schemas import (
    AvailabilityResponse,
    AppointmentCreate,
    AppointmentResponse,
)

logger = logging.getLogger(__name__)


class AppointmentService:
    def __init__(self, db: Session, calendar_service: Optional[BaseCalendarService] = None):
        self.db = db
        self.doctor_repo = DoctorRepository(db)
        self.patient_repo = PatientRepository(db)
        self.appointment_repo = AppointmentRepository(db)
        self.audit_repo = AuditRepository(db)
        self.calendar_service = calendar_service or get_calendar_service()

    def _parse_time(self, time_str: str) -> datetime:
        return datetime.strptime(time_str, "%H:%M")

    def _format_time(self, dt: datetime) -> str:
        return dt.strftime("%H:%M")

    def _verify_event(
        self,
        doctor_id: str,
        event_id: str,
        date: str,
        start: str,
        end: str,
        appointment_id: str,
        appointment_type: str,
    ) -> None:
        event = self.calendar_service.get_event(doctor_id, event_id)
        if not event or any(event.get(key) != value for key, value in
                            (("date", date), ("start", start), ("end", end),
                             ("appointment_id", appointment_id),
                             ("appointment_type", appointment_type),
                             ("doctor_id", doctor_id))):
            raise ConnectionError("Calendar verification failed: the saved event does not match the appointment.")

    def _verify_deleted(self, doctor_id: str, event_id: str) -> None:
        if self.calendar_service.get_event(doctor_id, event_id) is not None:
            raise ConnectionError("Calendar verification failed: the deleted event is still present.")

    def calculate_slots(self, doctor: Doctor, date_str: str) -> List[str]:
        """
        Generate all valid appointment slots for a doctor on a given date,
        taking into account working hours, duration, calendar busy intervals,
        and database booked appointments.
        """
        duration = timedelta(minutes=settings.DEFAULT_APPOINTMENT_DURATION_MINUTES)
        buffer = timedelta(minutes=settings.BUFFER_TIME_MINUTES)
        
        start_time_dt = self._parse_time(doctor.working_hours_start)
        end_time_dt = self._parse_time(doctor.working_hours_end)

        # 1. Collect busy ranges from Calendar API
        try:
            cal_busy = self.calendar_service.get_busy_intervals(doctor.doctor_id, date_str)
        except Exception as e:
            logger.error(f"Error fetching calendar intervals for {doctor.doctor_id}: {e}")
            raise ConnectionError("Calendar service unavailable")

        # 2. Collect busy ranges from Database
        db_appointments = self.appointment_repo.list_for_doctor(doctor.doctor_id, date_str)
        db_busy = [{"start": apt.start_time, "end": apt.end_time} for apt in db_appointments]

        all_busy = cal_busy + db_busy

        available_slots: List[str] = []
        curr = start_time_dt

        while curr + duration <= end_time_dt:
            curr_str = self._format_time(curr)
            slot_end = curr + duration
            slot_end_str = self._format_time(slot_end)

            # Check collision with busy intervals
            is_collision = False
            for busy in all_busy:
                busy_start = busy["start"]
                busy_end = busy["end"]
                # Collision condition: (slot_start < busy_end) and (slot_end > busy_start)
                if curr_str < busy_end and slot_end_str > busy_start:
                    is_collision = True
                    break

            if not is_collision:
                available_slots.append(curr_str)

            # Step forward by duration
            curr += duration

        return available_slots

    def check_availability(
        self,
        doctor_id: str,
        appointment_date: str,
        preferred_time: Optional[str] = None,
    ) -> AvailabilityResponse:
        doctor = self.doctor_repo.get_by_id(doctor_id)
        if not doctor:
            # Try fuzzy lookup
            doctor = self.doctor_repo.find_by_name(doctor_id)
            if not doctor:
                raise ValueError(f"Doctor '{doctor_id}' not found.")

        # Validate date format YYYY-MM-DD
        try:
            datetime.strptime(appointment_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Invalid date format: '{appointment_date}'. Expected YYYY-MM-DD.")

        available_slots = self.calculate_slots(doctor, appointment_date)

        requested_available = False
        alternatives: List[str] = []

        if preferred_time:
            # Normalize preferred time format
            try:
                norm_time = self._format_time(self._parse_time(preferred_time))
            except ValueError:
                norm_time = preferred_time

            if norm_time in available_slots:
                requested_available = True
            else:
                # Provide nearest alternative slots
                alternatives = available_slots[:3]
                message = f"{doctor.doctor_name} is not available at {preferred_time} on {appointment_date}. Available times include: {', '.join(alternatives)}."
        else:
            alternatives = available_slots[:3]
            message = f"{doctor.doctor_name} has {len(available_slots)} available slots on {appointment_date}."

        if requested_available:
            message = f"{doctor.doctor_name} is available at {preferred_time} on {appointment_date}."

        self.audit_repo.log(
            actor="SYSTEM",
            action="CHECK_AVAILABILITY",
            resource_type="DOCTOR",
            resource_id=doctor.doctor_id,
            result="AVAILABLE" if requested_available else "UNAVAILABLE",
            metadata={"date": appointment_date, "preferred_time": preferred_time},
        )

        return AvailabilityResponse(
            doctor_id=doctor.doctor_id,
            doctor_name=doctor.doctor_name,
            appointment_date=appointment_date,
            requested_time_available=requested_available,
            requested_time=preferred_time,
            available_slots=available_slots,
            alternative_slots=alternatives,
            message=message,
        )

    def book_appointment(self, request: AppointmentCreate) -> AppointmentResponse:
        """
        Executes a deterministic booking operation:
        1. Validates doctor & inputs
        2. Idempotency verification
        3. Double-booking conflict check
        4. Patient get_or_create
        5. Calendar event creation
        6. Database record persistence
        7. Audit logging
        """
        # 1. Validate Doctor
        doctor = self.doctor_repo.get_by_id(request.doctor_id)
        if not doctor:
            doctor = self.doctor_repo.find_by_name(request.doctor_id)
            if not doctor:
                raise ValueError(f"Doctor '{request.doctor_id}' not found.")

        # 2. Check Idempotency Key
        if request.idempotency_key:
            existing = self.appointment_repo.get_by_idempotency_key(request.idempotency_key)
            if existing:
                logger.info(f"Idempotent hit for key {request.idempotency_key}. Returning existing appointment {existing.appointment_id}.")
                return AppointmentResponse(
                    appointment_id=existing.appointment_id,
                    patient_id=existing.patient_id,
                    patient_name=request.patient_name,
                    contact_number=request.contact_number,
                    doctor_id=doctor.doctor_id,
                    doctor_name=doctor.doctor_name,
                    appointment_date=existing.appointment_date,
                    start_time=existing.start_time,
                    end_time=existing.end_time,
                    appointment_type=existing.appointment_type,
                    status=existing.status,
                    calendar_event_id=existing.calendar_event_id,
                    message="Appointment already confirmed (idempotent request).",
                )

        # 3. Check Slot Conflict
        # Normalize start_time
        start_time_dt = self._parse_time(request.appointment_time)
        duration = timedelta(minutes=settings.DEFAULT_APPOINTMENT_DURATION_MINUTES)
        end_time_dt = start_time_dt + duration
        start_time_str = self._format_time(start_time_dt)
        end_time_str = self._format_time(end_time_dt)

        # Check DB Conflict
        conflict = self.appointment_repo.find_conflict(
            doctor_id=doctor.doctor_id,
            appointment_date=request.appointment_date,
            start_time=start_time_str,
        )
        if conflict:
            raise ValueError(f"Slot {start_time_str} on {request.appointment_date} is already booked.")

        # Check Available in fresh slot calculation
        available = self.calculate_slots(doctor, request.appointment_date)
        if start_time_str not in available:
            raise ValueError(f"Slot {start_time_str} on {request.appointment_date} is not available.")

        # 4. Patient record
        patient = self.patient_repo.get_or_create(
            full_name=request.patient_name,
            phone_number=request.contact_number,
        )

        # 5. External Calendar Event Creation
        appointment_id = f"APT-{generate_uuid()[:8].upper()}"
        summary = f"{request.appointment_type} - {doctor.doctor_name} - {appointment_id}"
        calendar_event_id = None
        try:
            calendar_event_id = self.calendar_service.create_event(
                doctor_id=doctor.doctor_id,
                date_str=request.appointment_date,
                start_time=start_time_str,
                end_time=end_time_str,
                summary=summary,
                appointment_id=appointment_id,
                appointment_type=request.appointment_type,
            )
            if not calendar_event_id:
                raise ConnectionError("Calendar did not return an event id.")
            self._verify_event(doctor.doctor_id, calendar_event_id,
                               request.appointment_date, start_time_str, end_time_str,
                               appointment_id, request.appointment_type)
        except Exception as e:
            logger.error(f"Failed to create Google Calendar event: {e}")
            if calendar_event_id:
                try:
                    self.calendar_service.delete_event(doctor.doctor_id, calendar_event_id)
                    self._verify_deleted(doctor.doctor_id, calendar_event_id)
                except Exception:
                    logger.exception("Failed to remove unverified calendar event %s", calendar_event_id)
            raise ConnectionError(f"Scheduling system unavailable: {e}")

        # 6. Database Record Creation
        try:
            appointment = self.appointment_repo.create(
                patient_id=patient.patient_id,
                doctor_id=doctor.doctor_id,
                appointment_date=request.appointment_date,
                start_time=start_time_str,
                end_time=end_time_str,
                appointment_type=request.appointment_type,
                status="CONFIRMED",
                calendar_event_id=calendar_event_id,
                idempotency_key=request.idempotency_key,
                notes=request.notes,
                appointment_id=appointment_id,
            )
        except Exception as db_err:
            logger.error(f"DB insertion failed after calendar event created: {db_err}. Reconciling...")
            # Reconcile: attempt to delete the created calendar event
            if calendar_event_id:
                try:
                    self.calendar_service.delete_event(doctor.doctor_id, calendar_event_id)
                except Exception as del_err:
                    logger.error(f"Failed to reconcile calendar event {calendar_event_id}: {del_err}")
            raise RuntimeError(f"Database error during appointment creation: {db_err}")

        # 7. Audit log
        self.audit_repo.log(
            actor="SYSTEM",
            action="BOOK_APPOINTMENT",
            resource_type="APPOINTMENT",
            resource_id=appointment.appointment_id,
            result="CONFIRMED",
            metadata={
                "patient_id": patient.patient_id,
                "doctor_id": doctor.doctor_id,
                "date": request.appointment_date,
                "time": start_time_str,
                "calendar_event_id": calendar_event_id,
            },
        )

        return AppointmentResponse(
            appointment_id=appointment.appointment_id,
            patient_id=patient.patient_id,
            patient_name=patient.full_name,
            contact_number=patient.phone_number,
            doctor_id=doctor.doctor_id,
            doctor_name=doctor.doctor_name,
            appointment_date=appointment.appointment_date,
            start_time=appointment.start_time,
            end_time=appointment.end_time,
            appointment_type=appointment.appointment_type,
            status=appointment.status,
            calendar_event_id=appointment.calendar_event_id,
            message=f"Appointment successfully confirmed with {doctor.doctor_name} on {appointment.appointment_date} at {appointment.start_time}.",
        )

    def get_appointment(self, appointment_id: str) -> Optional[AppointmentResponse]:
        apt = self.appointment_repo.get_by_id(appointment_id)
        if not apt:
            return None
        doctor = self.doctor_repo.get_by_id(apt.doctor_id)
        patient = self.patient_repo.get_by_id(apt.patient_id)
        return AppointmentResponse(
            appointment_id=apt.appointment_id,
            patient_id=apt.patient_id,
            patient_name=patient.full_name if patient else "Unknown",
            contact_number=patient.phone_number if patient else "Unknown",
            doctor_id=apt.doctor_id,
            doctor_name=doctor.doctor_name if doctor else "Doctor",
            appointment_date=apt.appointment_date,
            start_time=apt.start_time,
            end_time=apt.end_time,
            appointment_type=apt.appointment_type,
            status=apt.status,
            calendar_event_id=apt.calendar_event_id,
            message=f"Appointment {apt.appointment_id} with {doctor.doctor_name if doctor else 'Doctor'} on {apt.appointment_date} at {apt.start_time} (Status: {apt.status}).",
        )

    def cancel_appointment(self, appointment_id: str, reason: str = "") -> bool:
        apt = self.appointment_repo.get_by_id(appointment_id)
        if not apt or apt.status == "CANCELLED":
            return False

        # Delete calendar event if present
        if apt.calendar_event_id:
            try:
                self.calendar_service.delete_event(apt.doctor_id, apt.calendar_event_id)
                self._verify_deleted(apt.doctor_id, apt.calendar_event_id)
            except Exception as e:
                logger.error(f"Calendar cancellation verification failed for {appointment_id}: {e}")
                return False

        self.appointment_repo.cancel(appointment_id)
        self.audit_repo.log(
            actor="SYSTEM",
            action="CANCEL_APPOINTMENT",
            resource_type="APPOINTMENT",
            resource_id=appointment_id,
            result="CANCELLED",
            metadata={
                "reason": reason,
                "patient_id": apt.patient_id,
                "doctor_id": apt.doctor_id,
                "date": apt.appointment_date,
                "time": apt.start_time,
            },
        )
        return True

    def reschedule_appointment(
        self,
        appointment_id: str,
        new_date: str,
        new_time: str,
    ) -> AppointmentResponse:
        apt = self.appointment_repo.get_by_id(appointment_id)
        if not apt:
            raise ValueError(f"Appointment '{appointment_id}' not found.")
        if apt.status == "CANCELLED":
            raise ValueError(f"Appointment '{appointment_id}' is cancelled and cannot be rescheduled.")

        doctor = self.doctor_repo.get_by_id(apt.doctor_id)
        if not doctor:
            raise ValueError(f"Doctor '{apt.doctor_id}' not found.")

        # Normalize new time
        new_start_dt = self._parse_time(new_time)
        duration = timedelta(minutes=settings.DEFAULT_APPOINTMENT_DURATION_MINUTES)
        new_end_dt = new_start_dt + duration
        new_start_str = self._format_time(new_start_dt)
        new_end_str = self._format_time(new_end_dt)

        # Check availability
        available_slots = self.calculate_slots(doctor, new_date)
        if new_start_str not in available_slots:
            raise ValueError(f"Slot {new_start_str} on {new_date} is not available for {doctor.doctor_name}.")

        patient = self.patient_repo.get_by_id(apt.patient_id)
        patient_name = patient.full_name if patient else "Patient"
        phone_number = patient.phone_number if patient else ""

        # Create and verify the replacement before removing the current event.
        old_calendar_event_id = apt.calendar_event_id
        summary = f"{apt.appointment_type} - {doctor.doctor_name} - {apt.appointment_id}"
        new_calendar_event_id = None
        try:
            new_calendar_event_id = self.calendar_service.create_event(
                doctor_id=doctor.doctor_id,
                date_str=new_date,
                start_time=new_start_str,
                end_time=new_end_str,
                summary=summary,
                appointment_id=apt.appointment_id,
                appointment_type=apt.appointment_type,
            )
            if not new_calendar_event_id:
                raise ConnectionError("Calendar did not return an event id.")
            self._verify_event(doctor.doctor_id, new_calendar_event_id,
                               new_date, new_start_str, new_end_str,
                               apt.appointment_id, apt.appointment_type)
        except Exception as e:
            logger.error(f"Failed to create new calendar event during reschedule: {e}")
            if new_calendar_event_id:
                try:
                    self.calendar_service.delete_event(doctor.doctor_id, new_calendar_event_id)
                    self._verify_deleted(doctor.doctor_id, new_calendar_event_id)
                except Exception:
                    logger.exception("Failed to remove unverified replacement event %s", new_calendar_event_id)
            raise ConnectionError(f"Scheduling system unavailable: {e}")

        if old_calendar_event_id:
            try:
                self.calendar_service.delete_event(doctor.doctor_id, old_calendar_event_id)
                self._verify_deleted(doctor.doctor_id, old_calendar_event_id)
            except Exception as e:
                logger.error("Could not verify old calendar event removal during reschedule: %s", e)
                try:
                    self.calendar_service.delete_event(doctor.doctor_id, new_calendar_event_id)
                    self._verify_deleted(doctor.doctor_id, new_calendar_event_id)
                except Exception:
                    logger.exception("Failed to roll back replacement calendar event %s", new_calendar_event_id)
                raise ConnectionError("Reschedule failed because the previous calendar event could not be verified as deleted.") from e

        # Update appointment record
        apt.appointment_date = new_date
        apt.start_time = new_start_str
        apt.end_time = new_end_str
        apt.calendar_event_id = new_calendar_event_id
        apt.status = "CONFIRMED"
        self.db.commit()
        self.db.refresh(apt)

        # Audit log
        self.audit_repo.log(
            actor="SYSTEM",
            action="RESCHEDULE_APPOINTMENT",
            resource_type="APPOINTMENT",
            resource_id=appointment_id,
            result="RESCHEDULED",
            metadata={
                "new_date": new_date,
                "new_time": new_start_str,
                "patient_id": apt.patient_id,
                "doctor_id": doctor.doctor_id,
                "old_calendar_event_id": old_calendar_event_id,
                "new_calendar_event_id": new_calendar_event_id,
            },
        )

        return AppointmentResponse(
            appointment_id=apt.appointment_id,
            patient_id=apt.patient_id,
            patient_name=patient_name,
            contact_number=phone_number,
            doctor_id=doctor.doctor_id,
            doctor_name=doctor.doctor_name,
            appointment_date=apt.appointment_date,
            start_time=apt.start_time,
            end_time=apt.end_time,
            appointment_type=apt.appointment_type,
            status=apt.status,
            calendar_event_id=apt.calendar_event_id,
            message=f"Appointment successfully rescheduled to {new_date} at {new_start_str} with {doctor.doctor_name}.",
        )
