"""
SQLAlchemy Database Models for Clinical Voice Agent.
Compliant with PRD Section 9.2 specifications.
"""

from datetime import datetime, timezone
import json
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Enum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Patient(Base):
    __tablename__ = "patients"

    patient_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    # Relationships
    appointments: Mapped[List["Appointment"]] = relationship("Appointment", back_populates="patient")
    conversations: Mapped[List["Conversation"]] = relationship("Conversation", back_populates="patient")


class Doctor(Base):
    __tablename__ = "doctors"

    doctor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doctor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    specialization: Mapped[str] = mapped_column(String(255), nullable=False)
    calendar_id: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    active_status: Mapped[bool] = mapped_column(Boolean, default=True)
    working_hours_start: Mapped[str] = mapped_column(String(8), default="09:00")
    working_hours_end: Mapped[str] = mapped_column(String(8), default="17:00")

    # Relationships
    appointments: Mapped[List["Appointment"]] = relationship("Appointment", back_populates="doctor")


class Appointment(Base):
    __tablename__ = "appointments"

    appointment_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    patient_id: Mapped[str] = mapped_column(String(64), ForeignKey("patients.patient_id"), nullable=False)
    doctor_id: Mapped[str] = mapped_column(String(64), ForeignKey("doctors.doctor_id"), nullable=False)
    appointment_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    start_time: Mapped[str] = mapped_column(String(8), nullable=False)  # HH:MM
    end_time: Mapped[str] = mapped_column(String(8), nullable=False)  # HH:MM
    appointment_type: Mapped[str] = mapped_column(String(64), default="General Consultation")
    
    # Status: PENDING, CONFIRMED, CANCELLED, FAILED, REQUIRES_RECONCILIATION
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    calendar_event_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    # Relationships
    patient: Mapped["Patient"] = relationship("Patient", back_populates="appointments")
    doctor: Mapped["Doctor"] = relationship("Doctor", back_populates="appointments")

    __table_args__ = (
        Index("idx_doctor_slot", "doctor_id", "appointment_date", "start_time"),
    )


class Conversation(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    patient_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("patients.patient_id"), nullable=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    conversation_status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    transcript_reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    patient: Mapped[Optional["Patient"]] = relationship("Patient", back_populates="conversations")
    events: Mapped[List["ConversationEvent"]] = relationship("ConversationEvent", back_populates="conversation", cascade="all, delete-orphan")


class ConversationEvent(Base):
    __tablename__ = "conversation_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    conversation_id: Mapped[str] = mapped_column(String(64), ForeignKey("conversations.conversation_id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)  # USER_MESSAGE, AGENT_MESSAGE, TOOL_CALL, TOOL_RESULT, STATE_CHANGE
    event_data: Mapped[str] = mapped_column(Text, default="{}")  # JSON string
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="events")


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    evaluation_run_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    agent_version: Mapped[str] = mapped_column(String(32), default="v1.0.0")
    prompt_version: Mapped[str] = mapped_column(String(32), default="v1.0.0")
    model_configuration: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    execution_status: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING, RUNNING, COMPLETED, FAILED
    total_scenarios: Mapped[int] = mapped_column(Integer, default=0)
    passed_scenarios: Mapped[int] = mapped_column(Integer, default=0)
    failed_scenarios: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    results: Mapped[List["EvaluationResult"]] = relationship("EvaluationResult", back_populates="evaluation_run", cascade="all, delete-orphan")


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    evaluation_result_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    evaluation_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("evaluation_runs.evaluation_run_id"), nullable=False)
    scenario_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    execution_status: Mapped[str] = mapped_column(String(32), default="PASSED")  # PASSED, FAILED, ERROR
    task_completion: Mapped[bool] = mapped_column(Boolean, default=False)
    tool_accuracy: Mapped[float] = mapped_column(Float, default=1.0)
    safety_result: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_metrics: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    failure_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    trace_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON or markdown log

    # Relationships
    evaluation_run: Mapped["EvaluationRun"] = relationship("EvaluationRun", back_populates="results")


class LiveCallTestRun(Base):
    __tablename__ = "live_call_test_runs"

    live_call_test_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    execution_status: Mapped[str] = mapped_column(String(16), default="IN_PROGRESS")
    booking_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    calendar_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    findings_json: Mapped[str] = mapped_column(Text, default="[]")


class LiveCallEvaluation(Base):
    __tablename__ = "live_call_evaluations"

    call_evaluation_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    call_room_name: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    data_scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    execution_status: Mapped[str] = mapped_column(String(24), default="IN_PROGRESS")
    action_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action_count: Mapped[int] = mapped_column(Integer, default=0)
    findings_json: Mapped[str] = mapped_column(Text, default="[]")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    result: Mapped[str] = mapped_column(String(32), default="SUCCESS")
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
