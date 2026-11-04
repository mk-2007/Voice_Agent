import pytest

from backend.services.calendar_service import MockCalendarService, use_calendar_service
from config.settings import settings


@pytest.fixture(autouse=True)
def isolate_calendar_from_external_services(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_SANDBOX", True)
    with use_calendar_service(MockCalendarService()):
        yield
