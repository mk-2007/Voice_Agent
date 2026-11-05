"""Integration tests for the FastAPI v1 contract using an isolated SQLite DB."""

import json
from contextlib import contextmanager
import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from backend.database.connection import get_db
from backend.database.models import Base, Doctor, LiveCallEvaluation
from backend.main import app
from backend.services.calendar_service import get_calendar_service, MockCalendarService
from config.settings import settings


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with TestingSession() as session:
        session.add(Doctor(
            doctor_id="DOC-001", doctor_name="Dr. Ahmed Khan",
            specialization="General Physician", calendar_id="primary",
            timezone="America/New_York", active_status=True,
            working_hours_start="09:00", working_hours_end="17:00",
        ))
        session.commit()
    calendar = get_calendar_service()
    if isinstance(calendar, MockCalendarService):
        calendar.clear()

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


def test_health_openapi_and_doctors(client):
    assert client.get("/health").json() == {"status": "ok"}
    schema = client.get("/openapi.json").json()
    assert "/api/v1/appointments/check" in schema["paths"]
    doctors = client.get("/api/v1/doctors")
    assert doctors.status_code == 200
    assert doctors.json()[0]["doctor_id"] == "DOC-001"


def test_appointment_lifecycle_and_validation(client):
    availability = client.post("/api/v1/appointments/check", json={
        "doctor_id": "DOC-001", "appointment_date": "2026-11-04", "preferred_time": "10:00",
    })
    assert availability.status_code == 200
    assert availability.json()["requested_time_available"] is True

    booking = client.post("/api/v1/appointments/book", json={
        "patient_name": "Taylor Patient", "contact_number": "555-123-4567",
        "doctor_id": "DOC-001", "appointment_date": "2026-11-04",
        "appointment_time": "10:00", "idempotency_key": "api-test-booking-1",
    })
    assert booking.status_code == 201, booking.text
    appointment_id = booking.json()["appointment_id"]
    assert client.get(f"/api/v1/appointments/{appointment_id}").json()["status"] == "CONFIRMED"
    dashboard_records = client.get(
        "/api/v1/appointments",
        params={"appointment_date": "2026-11-04"},
    ).json()
    assert any(record["appointment_id"] == appointment_id for record in dashboard_records)

    rescheduled = client.post(f"/api/v1/appointments/{appointment_id}/reschedule", json={
        "new_date": "2026-11-05", "new_time": "11:00",
    })
    assert rescheduled.status_code == 200, rescheduled.text
    assert rescheduled.json()["start_time"] == "11:00"
    assert any(
        record["appointment_id"] == appointment_id
        for record in client.get(
            "/api/v1/appointments",
            params={"appointment_date": "2026-11-05"},
        ).json()
    )

    cancelled = client.delete(f"/api/v1/appointments/{appointment_id}")
    assert cancelled.status_code == 204
    assert client.get(f"/api/v1/appointments/{appointment_id}").json()["status"] == "CANCELLED"

    bad_payload = client.post("/api/v1/appointments/book", json={"doctor_id": "DOC-001"})
    assert bad_payload.status_code == 422
    invalid_date = client.post("/api/v1/appointments/check", json={
        "doctor_id": "DOC-001", "appointment_date": "2026-02-30", "preferred_time": "10:00",
    })
    assert invalid_date.status_code == 422
    invalid_time = client.post("/api/v1/appointments/book", json={
        "patient_name": "Taylor Patient", "contact_number": "555-123-4567",
        "doctor_id": "DOC-001", "appointment_date": "2026-11-04",
        "appointment_time": "25:00",
    })
    assert invalid_time.status_code == 422
    assert client.get("/api/v1/appointments/unknown").status_code == 404


def test_patient_routes(client):
    created = client.post("/api/v1/patients", json={
        "full_name": "Morgan Example", "phone_number": "+1 555 222 3333",
    })
    assert created.status_code == 201
    assert created.json()["full_name"] == "Morgan Example"
    assert client.post("/api/v1/patients", json={
        "full_name": "Morgan Example", "phone_number": "+1 555 222 3333",
    }).status_code == 409
    assert len(client.get("/api/v1/patients").json()) == 1


def test_evaluation_routes(client):
    scenarios = client.get("/api/v1/evaluations/scenarios")
    assert scenarios.status_code == 200
    assert len(scenarios.json()) == 15
    assert client.post("/api/v1/evaluations/run", json={"scenario_ids": ["bad-id"]}).status_code == 422

    run = client.post("/api/v1/evaluations/run", json={"scenario_ids": ["TC-027"]})
    assert run.status_code == 200, run.text
    run_id = run.json()["evaluation_run_id"]
    assert run.json()["total_scenarios"] == 1
    assert client.get("/api/v1/evaluations/runs").json()[0]["evaluation_run_id"] == run_id
    details = client.get(f"/api/v1/evaluations/runs/{run_id}")
    assert details.status_code == 200
    assert details.json()["results"][0]["scenario_id"] == "TC-027"
    trace = json.loads(details.json()["results"][0]["trace_summary"])
    assert trace["transcript"][0]["speaker"] == "patient"
    assert client.get("/api/v1/evaluations/runs/missing").status_code == 404


def test_live_call_tester_requires_a_separate_database(client, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///./clinical_agent.db")
    monkeypatch.setattr(settings, "LIVE_TEST_DATABASE_URL", "sqlite:///./clinical_agent.db")
    monkeypatch.setattr(settings, "LIVEKIT_AGENT_NAME", "clinic-worker")
    monkeypatch.setattr(settings, "LIVE_TEST_AGENT_NAME", "clinic-test-worker")

    response = client.post("/api/v1/evaluations/live-calls")

    assert response.status_code == 503
    assert "separate LIVE_TEST_DATABASE_URL" in response.json()["detail"]


def test_live_call_verification_saves_only_minimal_result(client, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///./clinical_agent.db")
    monkeypatch.setattr(settings, "LIVE_TEST_DATABASE_URL", "sqlite:///./clinical_live_test.db")
    monkeypatch.setattr(settings, "LIVEKIT_AGENT_NAME", "clinic-worker")
    monkeypatch.setattr(settings, "LIVE_TEST_AGENT_NAME", "clinic-test-worker")

    started = client.post("/api/v1/evaluations/live-calls")
    assert started.status_code == 200, started.text
    test_id = started.json()["call_evaluation_id"]

    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)
    evaluation = db.query(LiveCallEvaluation).filter_by(
        call_evaluation_id=test_id
    ).one()
    evaluation.call_room_name = "isolated-test-room"
    db.commit()
    db.close()
    db_generator.close()

    @contextmanager
    def use_test_database():
        db_generator = app.dependency_overrides[get_db]()
        db = None
        try:
            db = next(db_generator)
            yield db
        finally:
            if db is not None:
                db.close()
            db_generator.close()

    monkeypatch.setattr("backend.api.routes.v1.live_test_database", use_test_database)
    response = client.post(
        f"/api/v1/evaluations/live-calls/{test_id}/verify",
        json={"transcript": "You: What time does the clinic close? Rachel: We close at 5 PM."},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["data_scope"] == "TEST"
    assert result["execution_status"] == "NO_CHANGE"
    assert result["findings"] == []
    assert result["action_count"] == 0
    assert "clinic close" not in response.text.lower()
    assert client.get("/api/v1/evaluations/live-calls").json()[0] == result
    assert client.post(
        f"/api/v1/evaluations/live-calls/{test_id}/verify",
        json={"transcript": "retry"},
    ).status_code == 409


def test_regular_call_check_does_not_require_test_or_calendar_configuration(client, monkeypatch):
    monkeypatch.setattr(settings, "LIVE_TEST_DATABASE_URL", settings.DATABASE_URL)
    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)
    evaluation = LiveCallEvaluation(
        call_room_name="clinic-call-room",
        data_scope="CLINIC",
    )
    db.add(evaluation)
    db.commit()
    evaluation_id = evaluation.call_evaluation_id
    db.close()
    db_generator.close()

    response = client.post(
        f"/api/v1/evaluations/live-calls/{evaluation_id}/verify",
        json={"transcript": "You: What time does the clinic close? Rachel: We close at 5 PM."},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data_scope"] == "CLINIC"
    assert response.json()["execution_status"] == "NO_CHANGE"


def test_livekit_token_requires_configuration_and_uses_fresh_room(client, monkeypatch):
    monkeypatch.setattr(settings, "LIVEKIT_URL", "")
    assert client.post("/api/v1/livekit/token", json={}).status_code == 503
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "test-secret-that-is-at-least-thirty-two-bytes")

    class FakeDispatch:
        def __init__(self):
            self.create_dispatch = AsyncMock()

    class FakeLiveKitAPI:
        def __init__(self, *_args):
            self.agent_dispatch = FakeDispatch()
            self.aclose = AsyncMock()

    dispatch_clients = []

    def create_livekit_client(*args):
        instance = FakeLiveKitAPI(*args)
        dispatch_clients.append(instance)
        return instance

    monkeypatch.setattr("backend.api.routes.v1.livekit_api.LiveKitAPI", create_livekit_client)
    first = client.post("/api/v1/livekit/token", json={})
    second = client.post("/api/v1/livekit/token")
    assert first.status_code == 200
    assert first.json()["room_name"] != second.json()["room_name"]
    assert "test-secret-that-is-at-least-thirty-two-bytes" not in first.text
    assert len(dispatch_clients) == 2
    for client_instance in dispatch_clients:
        client_instance.agent_dispatch.create_dispatch.assert_awaited_once()
        client_instance.aclose.assert_awaited_once()

    failed_client = FakeLiveKitAPI()
    failed_client.agent_dispatch.create_dispatch.side_effect = RuntimeError("upstream detail")
    monkeypatch.setattr("backend.api.routes.v1.livekit_api.LiveKitAPI", lambda *_args: failed_client)
    failed = client.post("/api/v1/livekit/token", json={})
    assert failed.status_code == 503
    assert "upstream detail" not in failed.text
    failed_client.aclose.assert_awaited_once()


def test_live_call_token_dispatches_to_isolated_worker(client, monkeypatch):
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "test-secret-that-is-at-least-thirty-two-bytes")
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///./clinical_agent.db")
    monkeypatch.setattr(settings, "LIVE_TEST_DATABASE_URL", "sqlite:///./clinical_live_test.db")
    monkeypatch.setattr(settings, "LIVEKIT_AGENT_NAME", "clinic-worker")
    monkeypatch.setattr(settings, "LIVE_TEST_AGENT_NAME", "clinic-test-worker")
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_SANDBOX", False)

    class FakeDispatch:
        def __init__(self):
            self.create_dispatch = AsyncMock()

    class FakeLiveKitAPI:
        def __init__(self, *_args):
            self.agent_dispatch = FakeDispatch()
            self.aclose = AsyncMock()

    instances = []

    def create_client(*args):
        instance = FakeLiveKitAPI(*args)
        instances.append(instance)
        return instance

    monkeypatch.setattr("backend.api.routes.v1.livekit_api.LiveKitAPI", create_client)
    test_run = client.post("/api/v1/evaluations/live-calls")
    assert test_run.status_code == 200, test_run.text

    token = client.post("/api/v1/livekit/token", json={
        "live_call_test_id": test_run.json()["call_evaluation_id"],
    })

    assert token.status_code == 200, token.text
    assert token.json()["live_call_test_id"] == test_run.json()["call_evaluation_id"]
    assert token.json()["data_scope"] == "TEST"
    assert token.json()["call_evaluation_id"] == test_run.json()["call_evaluation_id"]
    dispatch_request = instances[0].agent_dispatch.create_dispatch.call_args.args[0]
    assert dispatch_request.agent_name == "clinic-test-worker"
