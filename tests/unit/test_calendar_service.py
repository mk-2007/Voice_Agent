import pytest

from backend.services.calendar_service import GoogleCalendarService


class FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class FakeEvents:
    def __init__(self, events):
        self.events = events
        self.inserted_body = None

    def list(self, **_kwargs):
        return FakeRequest({"items": self.events})

    def insert(self, **kwargs):
        self.inserted_body = kwargs["body"]
        return FakeRequest({"id": "google-event-1"})


class FakeGoogleService:
    def __init__(self, events):
        self._events = FakeEvents(events)

    def events(self):
        return self._events


def _timed_event(event_id, start, end, doctor_id=None):
    private = {"doctor_id": doctor_id} if doctor_id else {}
    return {
        "id": event_id,
        "start": {"dateTime": f"2026-11-03T{start}:00-05:00"},
        "end": {"dateTime": f"2026-11-03T{end}:00-05:00"},
        "extendedProperties": {"private": private},
    }


def test_shared_calendar_busy_intervals_are_filtered_by_doctor(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "clinic-calendar")
    monkeypatch.setattr(settings, "CLINIC_TIMEZONE", "America/New_York")
    service = GoogleCalendarService.__new__(GoogleCalendarService)
    service.calendar_id = "clinic-calendar"
    service._service = FakeGoogleService([
        _timed_event("doctor-1", "09:00", "09:30", "DOC-001"),
        _timed_event("doctor-2", "09:30", "10:00", "DOC-002"),
        _timed_event("untagged", "10:00", "10:30"),
    ])

    assert service.get_busy_intervals("DOC-001", "2026-11-03") == [
        {"start": "09:00", "end": "09:30"},
        {"start": "10:00", "end": "10:30"},
    ]


def test_live_event_contains_reference_without_patient_details(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "clinic-calendar")
    monkeypatch.setattr(settings, "CLINIC_TIMEZONE", "America/New_York")
    fake_google = FakeGoogleService([])
    service = GoogleCalendarService.__new__(GoogleCalendarService)
    service.calendar_id = "clinic-calendar"
    service._service = fake_google

    event_id = service.create_event(
        doctor_id="DOC-001",
        date_str="2026-11-03",
        start_time="10:00",
        end_time="10:30",
        summary="General Consultation - Dr. Ahmed Khan - APT-12345678",
        appointment_id="APT-12345678",
        appointment_type="General Consultation",
    )

    assert event_id == "google-event-1"
    body = fake_google._events.inserted_body
    assert body["summary"] == "General Consultation - Dr. Ahmed Khan - APT-12345678"
    assert body["extendedProperties"]["private"] == {
        "doctor_id": "DOC-001",
        "appointment_id": "APT-12345678",
        "appointment_type": "General Consultation",
    }
    assert "Alice Smith" not in str(body)
    assert "555-123-4567" not in str(body)


def test_unconfigured_google_calendar_does_not_report_free_availability():
    service = GoogleCalendarService.__new__(GoogleCalendarService)
    service._service = None

    with pytest.raises(ConnectionError, match="Google Calendar is not configured"):
        service.get_busy_intervals("DOC-001", "2026-11-03")
