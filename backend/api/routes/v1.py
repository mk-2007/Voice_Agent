"""HTTP API for clinic operations, evaluation, and LiveKit access tokens."""

from datetime import date, datetime, timedelta, timezone
import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from livekit import api as livekit_api
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config.settings import settings
from backend.database.connection import get_db
from backend.database.models import (
    Appointment, Doctor, EvaluationResult, EvaluationRun, LiveCallEvaluation, Patient,
)
from backend.database.repositories import DoctorRepository, PatientRepository
from backend.services.appointment_service import AppointmentService
from backend.services.live_call_verifier import (
    database_urls_match, live_test_database, verify_live_call,
)
from backend.api.schemas.appointment_schemas import (
    AppointmentCreate, AppointmentResponse,
    AvailabilityRequest, AvailabilityResponse, DoctorResponse, PatientCreate,
    PatientResponse,
)
from evaluation.runner import EvaluationRunner
from evaluation.scenarios.scenario_definitions import SCENARIO_LIBRARY

router = APIRouter()


class LiveKitTokenResponse(BaseModel):
    server_url: str
    room_name: str
    participant_identity: str
    participant_token: str
    live_call_test_id: Optional[str] = None
    call_evaluation_id: str
    data_scope: str


class LiveKitTokenRequest(BaseModel):
    live_call_test_id: Optional[str] = None


class LiveCallTestVerifyRequest(BaseModel):
    transcript: str = Field(max_length=50000)


class CallEvaluationResponse(BaseModel):
    call_evaluation_id: str
    data_scope: str
    execution_status: str
    started_at: Any
    completed_at: Any = None
    action_type: Optional[str] = None
    action_count: int
    findings: List[str]


class EvaluationRunRequest(BaseModel):
    scenario_ids: Optional[List[str]] = None


class RescheduleRequest(BaseModel):
    new_date: date
    new_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class EvaluationRunSummary(BaseModel):
    evaluation_run_id: str
    execution_status: str
    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    started_at: Any
    completed_at: Any = None


def _doctor_response(doctor: Doctor) -> DoctorResponse:
    return DoctorResponse(
        doctor_id=doctor.doctor_id, doctor_name=doctor.doctor_name,
        specialization=doctor.specialization, calendar_id=doctor.calendar_id,
        timezone=doctor.timezone, active_status=doctor.active_status,
        working_hours_start=doctor.working_hours_start,
        working_hours_end=doctor.working_hours_end,
    )


def _appointment_service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, ConnectionError):
        return HTTPException(status_code=503, detail="Scheduling service is unavailable")
    return HTTPException(status_code=500, detail="Appointment operation failed")


def _validate_live_test_configuration() -> None:
    if not settings.LIVE_TEST_DATABASE_URL:
        raise HTTPException(status_code=503, detail="Configure LIVE_TEST_DATABASE_URL before running isolated tests")
    if database_urls_match(settings.LIVE_TEST_DATABASE_URL, settings.DATABASE_URL):
        raise HTTPException(
            status_code=503,
            detail="Live call testing requires a separate LIVE_TEST_DATABASE_URL",
        )
    if settings.LIVE_TEST_AGENT_NAME == settings.LIVEKIT_AGENT_NAME:
        raise HTTPException(
            status_code=503,
            detail="The live test worker must use a separate LIVE_TEST_AGENT_NAME",
        )


@router.get("/doctors", response_model=List[DoctorResponse])
def list_doctors(db: Session = Depends(get_db)):
    return [_doctor_response(d) for d in DoctorRepository(db).list_active()]


@router.get("/patients", response_model=List[PatientResponse])
def list_patients(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return (
        db.query(Patient)
        .order_by(Patient.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.post("/patients", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    existing = PatientRepository(db).get_by_phone(payload.phone_number.strip())
    if existing:
        raise HTTPException(status_code=409, detail="A patient with this phone number already exists")
    try:
        patient = PatientRepository(db).create(payload.full_name, payload.phone_number)
        return patient
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Patient could not be created")


@router.post("/appointments/check", response_model=AvailabilityResponse)
def check_appointment(payload: AvailabilityRequest, db: Session = Depends(get_db)):
    try:
        return AppointmentService(db).check_availability(
            payload.doctor_id, payload.appointment_date, payload.preferred_time
        )
    except Exception as exc:
        raise _appointment_service_error(exc)


@router.get("/appointments", response_model=List[AppointmentResponse])
def list_appointments(
    appointment_date: Optional[date] = None,
    doctor_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Appointment)
    if appointment_date:
        query = query.filter_by(appointment_date=appointment_date.isoformat())
    if doctor_id:
        query = query.filter_by(doctor_id=doctor_id)
    records = query.order_by(Appointment.appointment_date.asc(), Appointment.start_time.asc()).offset(offset).limit(limit).all()
    service = AppointmentService(db)
    return [service.get_appointment(a.appointment_id) for a in records]


@router.post("/appointments", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
@router.post(
    "/appointments/book",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def book_appointment(payload: AppointmentCreate, db: Session = Depends(get_db)):
    try:
        return AppointmentService(db).book_appointment(payload)
    except Exception as exc:
        raise _appointment_service_error(exc)


@router.post("/appointments/{appointment_id}/reschedule", response_model=AppointmentResponse)
def reschedule_appointment(appointment_id: str, payload: RescheduleRequest, db: Session = Depends(get_db)):
    try:
        return AppointmentService(db).reschedule_appointment(
            appointment_id, payload.new_date.isoformat(), payload.new_time
        )
    except Exception as exc:
        raise _appointment_service_error(exc)


@router.get("/appointments/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(appointment_id: str, db: Session = Depends(get_db)):
    result = AppointmentService(db).get_appointment(appointment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return result


@router.delete("/appointments/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_appointment(appointment_id: str, reason: str = Query("Patient requested cancellation", max_length=500), db: Session = Depends(get_db)):
    try:
        if not AppointmentService(db).cancel_appointment(appointment_id, reason):
            raise HTTPException(status_code=404, detail="Appointment not found or already cancelled")
    except HTTPException:
        raise
    except Exception as exc:
        raise _appointment_service_error(exc)


@router.post("/livekit/token", response_model=LiveKitTokenResponse)
async def create_livekit_token(
    payload: LiveKitTokenRequest = LiveKitTokenRequest(),
    db: Session = Depends(get_db),
):
    if not (settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET):
        raise HTTPException(status_code=503, detail="LiveKit is not configured")
    identity = f"caller-{uuid4().hex}"
    evaluation = None
    agent_name = settings.LIVEKIT_AGENT_NAME
    if payload.live_call_test_id:
        _validate_live_test_configuration()
        evaluation = db.query(LiveCallEvaluation).filter_by(
            call_evaluation_id=payload.live_call_test_id
        ).first()
        if (
            evaluation is None
            or evaluation.data_scope != "TEST"
            or evaluation.execution_status != "IN_PROGRESS"
        ):
            raise HTTPException(status_code=409, detail="Live call test is not active")
        if evaluation.call_room_name is not None:
            raise HTTPException(status_code=409, detail="This isolated test session already has a call")
        agent_name = settings.LIVE_TEST_AGENT_NAME
    else:
        evaluation = LiveCallEvaluation(data_scope="CLINIC")
        db.add(evaluation)
        db.commit()
        db.refresh(evaluation)
    # Use a fresh server-generated room for every anonymous token to prevent
    # a caller from joining a room selected or shared by another caller.
    room_name = f"clinical-{uuid4().hex}"
    evaluation.call_room_name = room_name
    db.commit()
    livekit_client = livekit_api.LiveKitAPI(
        settings.LIVEKIT_URL,
        settings.LIVEKIT_API_KEY,
        settings.LIVEKIT_API_SECRET,
    )
    try:
        await livekit_client.agent_dispatch.create_dispatch(
            livekit_api.CreateAgentDispatchRequest(
                agent_name=agent_name,
                room=room_name,
            )
        )
    except Exception as exc:
        evaluation.execution_status = "FLAG"
        evaluation.completed_at = datetime.now(timezone.utc)
        evaluation.findings_json = json.dumps([
            "test_worker_unavailable" if evaluation.data_scope == "TEST" else "voice_worker_unavailable"
        ])
        db.commit()
        raise HTTPException(status_code=503, detail="Could not start the voice receptionist") from exc
    finally:
        await livekit_client.aclose()

    token = (
        livekit_api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("Clinic caller")
        .with_ttl(timedelta(minutes=30))
        .with_grants(livekit_api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        ))
        .to_jwt()
    )
    return LiveKitTokenResponse(
        server_url=settings.LIVEKIT_URL, room_name=room_name,
        participant_identity=identity, participant_token=token,
        live_call_test_id=evaluation.call_evaluation_id if evaluation.data_scope == "TEST" else None,
        call_evaluation_id=evaluation.call_evaluation_id,
        data_scope=evaluation.data_scope,
    )


@router.get("/evaluations/scenarios")
def list_scenarios():
    return [scenario.model_dump() for scenario in SCENARIO_LIBRARY]


@router.post("/evaluations/run")
def run_evaluation(payload: EvaluationRunRequest, db: Session = Depends(get_db)):
    scenarios = SCENARIO_LIBRARY
    if payload.scenario_ids is not None:
        by_id = {scenario.scenario_id: scenario for scenario in SCENARIO_LIBRARY}
        unknown = sorted(set(payload.scenario_ids) - by_id.keys())
        if unknown:
            raise HTTPException(status_code=422, detail={"unknown_scenario_ids": unknown})
        scenarios = [by_id[scenario_id] for scenario_id in payload.scenario_ids]
    try:
        return EvaluationRunner(db=db).run_suite(scenarios=scenarios)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Evaluation run failed")


def _call_evaluation_response(evaluation: LiveCallEvaluation) -> CallEvaluationResponse:
    return CallEvaluationResponse(
        call_evaluation_id=evaluation.call_evaluation_id,
        data_scope=evaluation.data_scope,
        execution_status=evaluation.execution_status,
        started_at=evaluation.started_at,
        completed_at=evaluation.completed_at,
        action_type=evaluation.action_type,
        action_count=evaluation.action_count,
        findings=json.loads(evaluation.findings_json or "[]"),
    )


@router.post("/evaluations/live-calls", response_model=CallEvaluationResponse)
def start_live_call_test(db: Session = Depends(get_db)):
    _validate_live_test_configuration()
    evaluation = LiveCallEvaluation(data_scope="TEST")
    db.add(evaluation)
    db.commit()
    db.refresh(evaluation)
    return _call_evaluation_response(evaluation)


@router.post(
    "/evaluations/live-calls/{live_call_test_id}/verify",
    response_model=CallEvaluationResponse,
)
def verify_live_call_test(
    live_call_test_id: str,
    payload: LiveCallTestVerifyRequest,
    db: Session = Depends(get_db),
):
    evaluation = db.query(LiveCallEvaluation).filter_by(
        call_evaluation_id=live_call_test_id
    ).first()
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Live call test not found")
    if evaluation.execution_status != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="Live call test has already been completed")
    if not evaluation.call_room_name:
        raise HTTPException(status_code=409, detail="No voice call has been connected to this evaluation")

    try:
        if evaluation.data_scope == "TEST":
            _validate_live_test_configuration()
            with live_test_database() as test_db:
                status_value, action_type, action_count, findings = verify_live_call(
                    payload.transcript, evaluation.call_room_name, evaluation.started_at, test_db,
                )
        else:
            status_value, action_type, action_count, findings = verify_live_call(
                payload.transcript, evaluation.call_room_name, evaluation.started_at, db,
            )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not verify the appointment action for this call") from exc

    evaluation.execution_status = status_value
    evaluation.completed_at = datetime.now(timezone.utc)
    evaluation.action_type = action_type
    evaluation.action_count = action_count
    evaluation.findings_json = json.dumps(findings)
    db.commit()
    db.refresh(evaluation)
    return _call_evaluation_response(evaluation)


@router.get("/evaluations/live-calls", response_model=List[CallEvaluationResponse])
def list_live_call_tests(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    evaluations = (
        db.query(LiveCallEvaluation)
        .order_by(LiveCallEvaluation.started_at.desc())
        .limit(limit)
        .all()
    )
    return [_call_evaluation_response(evaluation) for evaluation in evaluations]


@router.get("/evaluations/runs", response_model=List[EvaluationRunSummary])
def list_evaluation_runs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return db.query(EvaluationRun).order_by(EvaluationRun.started_at.desc()).offset(offset).limit(limit).all()


@router.get("/evaluations/runs/{run_id}")
def get_evaluation_run(run_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    run = db.query(EvaluationRun).filter_by(evaluation_run_id=run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    results = db.query(EvaluationResult).filter_by(evaluation_run_id=run_id).all()
    return {
        "evaluation_run_id": run.evaluation_run_id,
        "execution_status": run.execution_status,
        "total_scenarios": run.total_scenarios,
        "passed_scenarios": run.passed_scenarios,
        "failed_scenarios": run.failed_scenarios,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "results": [
            {"scenario_id": r.scenario_id, "execution_status": r.execution_status,
             "task_completion": r.task_completion, "tool_accuracy": r.tool_accuracy,
             "safety_result": r.safety_result, "latency_metrics": r.latency_metrics,
             "failure_category": r.failure_category, "trace_summary": r.trace_summary}
            for r in results
        ],
    }
