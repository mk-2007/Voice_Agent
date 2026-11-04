"""
Unit tests for the Autonomous Simulated Patient Evaluation Platform.
Verifies closed-loop simulation, ground-truth database assertions,
safety grading, and batch suite persistence.
"""

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Appointment, Base, Doctor, EvaluationRun, EvaluationResult, Patient
from backend.services.calendar_service import get_calendar_service, MockCalendarService
from evaluation.scenarios.scenario_definitions import get_scenario, get_scenarios_by_category
from evaluation.runner import EvaluationRunner
from evaluation.evaluators.safety_evaluator import evaluate_safety_compliance
from evaluation.evaluators.task_evaluator import evaluate_task_outcome


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

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

    cal = get_calendar_service()
    if isinstance(cal, MockCalendarService):
        cal.clear()

    yield session
    session.close()


def test_eval_single_booking_scenario(test_db):
    runner = EvaluationRunner(db=test_db)
    sc = get_scenario("TC-001")
    assert sc is not None

    rep = runner.run_scenario(sc, db=test_db)
    assert rep.passed is True
    assert rep.task_completed is True
    assert rep.safety_compliant is True
    assert rep.tool_accuracy == 1.0
    assert rep.db_state_details.get("status") == "CONFIRMED"
    assert rep.db_state_details.get("doctor_id") == "DOC-001"
    assert rep.db_state_details.get("calendar_event_verified") is True
    assert rep.state_transitions[0] == "INIT"
    assert rep.tool_calls[-1]["tool"] == "book_appointment"
    assert rep.tool_calls[-1]["result"]["status"] == "CONFIRMED"


def test_evaluation_clock_makes_relative_dates_reproducible(test_db):
    runner = EvaluationRunner(db=test_db, reference_time=datetime(2026, 11, 2, tzinfo=timezone.utc))
    report = runner.run_scenario(get_scenario("TC-001"), db=test_db)
    assert report.tool_calls[0]["params"]["date"] == "2026-11-03"


def _create_ground_truth_booking(test_db, calendar, phone_number="555-123-4567"):
    appointment_id = "APT-12345678"
    patient = Patient(
        patient_id="PAT-12345678",
        full_name="Alice Smith",
        phone_number=phone_number,
    )
    appointment = Appointment(
        appointment_id=appointment_id,
        patient_id=patient.patient_id,
        doctor_id="DOC-001",
        appointment_date="2026-11-03",
        start_time="10:00",
        end_time="10:30",
        appointment_type="General Consultation",
        status="CONFIRMED",
        calendar_event_id="gcal_mock_DOC-001_2026-11-03_1000",
    )
    test_db.add_all([patient, appointment])
    test_db.commit()
    calendar.create_event(
        doctor_id="DOC-001",
        date_str=appointment.appointment_date,
        start_time=appointment.start_time,
        end_time=appointment.end_time,
        summary="General Consultation - Dr. Ahmed Khan - APT-12345678",
        appointment_id=appointment_id,
        appointment_type=appointment.appointment_type,
    )
    return appointment_id


def test_task_evaluator_checks_patient_contact_and_calendar_event(test_db):
    scenario = get_scenario("TC-001")
    assert scenario is not None
    calendar = MockCalendarService()
    appointment_id = _create_ground_truth_booking(test_db, calendar)

    result = evaluate_task_outcome(
        scenario=scenario,
        final_agent_utterance="Your appointment is confirmed.",
        db=test_db,
        created_appointment_id=appointment_id,
        calendar_service=calendar,
        reference_time=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )

    assert result.is_passed is True
    assert result.details["calendar_event_verified"] is True


def test_task_evaluator_rejects_mismatched_patient_contact(test_db):
    scenario = get_scenario("TC-001")
    assert scenario is not None
    calendar = MockCalendarService()
    appointment_id = _create_ground_truth_booking(
        test_db,
        calendar,
        phone_number="555-000-0000",
    )

    result = evaluate_task_outcome(
        scenario=scenario,
        final_agent_utterance="Your appointment is confirmed.",
        db=test_db,
        created_appointment_id=appointment_id,
        calendar_service=calendar,
        reference_time=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )

    assert result.is_passed is False
    assert "contact number" in result.error_message


def test_eval_emergency_scenario(test_db):
    runner = EvaluationRunner(db=test_db)
    sc = get_scenario("TC-027")
    assert sc is not None

    rep = runner.run_scenario(sc, db=test_db)
    assert rep.passed is True
    assert rep.safety_compliant is True
    assert "911" in rep.transcript[1]["text"]


def test_eval_medical_diagnosis_refusal(test_db):
    runner = EvaluationRunner(db=test_db)
    sc = get_scenario("TC-025")
    assert sc is not None

    rep = runner.run_scenario(sc, db=test_db)
    assert rep.passed is True
    assert rep.safety_compliant is True
    assert "cannot provide medical advice" in rep.transcript[1]["text"]


def test_safety_evaluator_uses_agent_output_not_patient_prompt():
    scenario = get_scenario("TC-028")
    assert scenario is not None

    passed, _ = evaluate_safety_compliance(
        scenario,
        "I can help you arrange an appointment.",
    )
    assert passed is False

    passed, _ = evaluate_safety_compliance(
        scenario,
        "I am an administrative receptionist and cannot provide medical advice.",
    )
    assert passed is True


def test_eval_batch_suite_execution(test_db):
    runner = EvaluationRunner(db=test_db)
    # Run safety scenarios suite
    safety_scenarios = get_scenarios_by_category("Category F: Safety")
    assert len(safety_scenarios) >= 4

    summary = runner.run_suite(scenarios=safety_scenarios)
    assert summary["total_scenarios"] == len(safety_scenarios)
    assert summary["safety_compliance_pct"] == 100.0
    assert summary["passed_scenarios"] == len(safety_scenarios)

    # Check persistence in database
    run_rec = test_db.query(EvaluationRun).filter(EvaluationRun.evaluation_run_id == summary["evaluation_run_id"]).first()
    assert run_rec is not None
    assert run_rec.execution_status == "COMPLETED"

    res_recs = test_db.query(EvaluationResult).filter(EvaluationResult.evaluation_run_id == run_rec.evaluation_run_id).all()
    assert len(res_recs) == len(safety_scenarios)


def test_suite_isolates_each_scenario_database_and_calendar(test_db, monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_SANDBOX", False)
    runner = EvaluationRunner(db=test_db)
    scenario = get_scenario("TC-001")
    assert scenario is not None
    summary = runner.run_suite(scenarios=[scenario, scenario])
    assert summary["passed_scenarios"] == 2
    assert [report["db_state_details"]["time"] for report in summary["reports"]] == ["10:00", "10:00"]
    assert test_db.query(Appointment).count() == 0
    assert test_db.query(Patient).count() == 0
