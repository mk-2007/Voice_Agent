"""
Calendar Service supporting Google Calendar API v3 and Deterministic Mock/Sandbox.
Handles Free/Busy queries, slot calculations, event creation, cancellation, and failure injection.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, time, timedelta, timezone
import logging
from typing import Dict, Iterator, List, Optional
import pytz

from config.settings import settings

logger = logging.getLogger(__name__)


class CalendarEvent(ABC):
    def __init__(
        self,
        event_id: str,
        summary: str,
        start_time: str,
        end_time: str,
        doctor_id: str,
        patient_name: str,
    ):
        self.event_id = event_id
        self.summary = summary
        self.start_time = start_time
        self.end_time = end_time
        self.doctor_id = doctor_id
        self.patient_name = patient_name


class BaseCalendarService(ABC):
    @abstractmethod
    def get_event(self, doctor_id: str, calendar_event_id: str) -> Optional[Dict[str, str]]:
        """Read an event by id, returning None when it does not exist."""
        pass

    @abstractmethod
    def get_busy_intervals(self, doctor_id: str, date_str: str) -> List[Dict[str, str]]:
        """Return list of busy intervals [{'start': '10:00', 'end': '10:30'}] in clinic timezone."""
        pass

    @abstractmethod
    def create_event(
        self,
        doctor_id: str,
        date_str: str,
        start_time: str,
        end_time: str,
        summary: str,
        appointment_id: Optional[str] = None,
        appointment_type: Optional[str] = None,
    ) -> str:
        """Create calendar event and return calendar_event_id."""
        pass

    @abstractmethod
    def delete_event(self, doctor_id: str, calendar_event_id: str) -> bool:
        """Delete/cancel calendar event."""
        pass


class MockCalendarService(BaseCalendarService):
    """
    Deterministic Sandbox Calendar for Free Tier development and automated evaluation.
    Zero external network calls, zero API costs, supports simulating transient API failures.
    """

    def __init__(self):
        self._events: Dict[str, List[Dict[str, str]]] = {}
        self.reset()
        self.simulate_api_failure = False

    def reset(self) -> None:
        """Restore the deterministic evaluation fixture set."""
        # doctor_id -> list of events
        self._events = {
            # Default pre-existing booked slots for realistic testing
            "DOC-001": [
                {"event_id": "mock-evt-1", "date": "2026-11-03", "start": "09:30", "end": "10:00", "summary": "Review"},
                {"event_id": "mock-evt-2", "date": "2026-11-03", "start": "11:00", "end": "11:30", "summary": "Follow-up"},
                {"event_id": "mock-evt-3", "date": "2026-11-03", "start": "14:00", "end": "14:30", "summary": "Consultation"},
            ],
            "DOC-002": [
                {"event_id": "mock-evt-4", "date": "2026-11-03", "start": "10:30", "end": "11:00", "summary": "Skin Check"},
            ],
            "DOC-003": [],
        }
        self.simulate_api_failure = False

    def get_busy_intervals(self, doctor_id: str, date_str: str) -> List[Dict[str, str]]:
        if self.simulate_api_failure:
            raise ConnectionError("Google Calendar API is temporarily unavailable (simulated 503)")
        
        events = self._events.get(doctor_id, [])
        return [
            {"start": evt["start"], "end": evt["end"]}
            for evt in events
            if evt.get("date") == date_str
        ]

    def get_event(self, doctor_id: str, calendar_event_id: str) -> Optional[Dict[str, str]]:
        if self.simulate_api_failure:
            raise ConnectionError("Calendar event lookup failed (simulated 503)")
        return next((dict(evt) for evt in self._events.get(doctor_id, [])
                     if evt.get("event_id") == calendar_event_id), None)

    def create_event(
        self,
        doctor_id: str,
        date_str: str,
        start_time: str,
        end_time: str,
        summary: str,
        appointment_id: Optional[str] = None,
        appointment_type: Optional[str] = None,
    ) -> str:
        if self.simulate_api_failure:
            raise ConnectionError("Google Calendar API event creation failed (simulated 500)")

        event_id = f"gcal_mock_{doctor_id}_{date_str}_{start_time.replace(':', '')}"
        event = {
            "event_id": event_id,
            "date": date_str,
            "start": start_time,
            "end": end_time,
            "summary": summary,
            "appointment_id": appointment_id,
            "appointment_type": appointment_type,
            "doctor_id": doctor_id,
        }
        if doctor_id not in self._events:
            self._events[doctor_id] = []
        self._events[doctor_id].append(event)
        logger.info(f"[MockCalendar] Created event {event_id} for {doctor_id} on {date_str} {start_time}-{end_time}")
        return event_id

    def delete_event(self, doctor_id: str, calendar_event_id: str) -> bool:
        if self.simulate_api_failure:
            raise ConnectionError("Google Calendar API deletion failed (simulated 503)")

        if doctor_id in self._events:
            initial_count = len(self._events[doctor_id])
            self._events[doctor_id] = [e for e in self._events[doctor_id] if e["event_id"] != calendar_event_id]
            return len(self._events[doctor_id]) < initial_count
        return False

    def clear(self):
        """Reset mock data for clean test runs."""
        self._events.clear()
        self.simulate_api_failure = False


class GoogleCalendarService(BaseCalendarService):
    """
    Live Google Calendar API v3 implementation.
    Uses Google OAuth2 Refresh Token credentials.
    """

    def __init__(self, calendar_id: Optional[str] = None):
        self.calendar_id = calendar_id or settings.GOOGLE_CALENDAR_ID
        self._service = None
        self._init_service()

    def _init_service(self):
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            if not (
                settings.GOOGLE_CLIENT_ID
                and settings.GOOGLE_CLIENT_SECRET
                and settings.GOOGLE_REFRESH_TOKEN
            ):
                logger.error("Google Calendar credentials are incomplete.")
                return

            creds = Credentials(
                token=None,
                refresh_token=settings.GOOGLE_REFRESH_TOKEN,
                client_id=settings.GOOGLE_CLIENT_ID,
                client_secret=settings.GOOGLE_CLIENT_SECRET,
                token_uri="https://oauth2.googleapis.com/token",
                scopes=["https://www.googleapis.com/auth/calendar"],
            )
            self._service = build("calendar", "v3", credentials=creds)
        except Exception as e:
            logger.error(f"Failed to initialize Google Calendar client: {e}")
            self._service = None

    def get_busy_intervals(self, doctor_id: str, date_str: str) -> List[Dict[str, str]]:
        if not self._service:
            raise ConnectionError(
                "Google Calendar is not configured. Set the OAuth client ID, client secret, "
                "refresh token, and calendar ID before using live scheduling."
            )

        tz = pytz.timezone(settings.CLINIC_TIMEZONE)
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        day_start = tz.localize(datetime.combine(target_date, time.min))
        day_end = tz.localize(datetime.combine(target_date + timedelta(days=1), time.min))

        busy_intervals = []
        page_token = None
        while True:
            response = self._service.events().list(
                calendarId=self.calendar_id,
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=2500,
                pageToken=page_token,
            ).execute()
            for event in response.get("items", []):
                if event.get("status") == "cancelled":
                    continue
                event_doctor_id = (
                    event.get("extendedProperties", {})
                    .get("private", {})
                    .get("doctor_id")
                )
                # Legacy and manually-created events without a doctor tag remain clinic-wide conflicts.
                if event_doctor_id and event_doctor_id != doctor_id:
                    continue

                start_raw = event.get("start", {})
                end_raw = event.get("end", {})
                if "date" in start_raw:
                    event_start = datetime.combine(
                        datetime.strptime(start_raw["date"], "%Y-%m-%d").date(),
                        time.min,
                    )
                    event_end_date = datetime.strptime(
                        end_raw.get("date", start_raw["date"]), "%Y-%m-%d"
                    ).date()
                    if target_date < event_start.date() or target_date >= event_end_date:
                        continue
                    start_dt = day_start
                    end_dt = day_end
                else:
                    start_dt = datetime.fromisoformat(
                        start_raw["dateTime"].replace("Z", "+00:00")
                    ).astimezone(tz)
                    end_dt = datetime.fromisoformat(
                        end_raw["dateTime"].replace("Z", "+00:00")
                    ).astimezone(tz)
                    start_dt = max(start_dt, day_start)
                    end_dt = min(end_dt, day_end)

                busy_intervals.append({
                    "start": start_dt.strftime("%H:%M"),
                    "end": end_dt.strftime("%H:%M"),
                })

            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return busy_intervals

    def get_event(self, doctor_id: str, calendar_event_id: str) -> Optional[Dict[str, str]]:
        if not self._service:
            raise ConnectionError("Google Calendar API is not configured.")
        from googleapiclient.errors import HttpError
        try:
            event = self._service.events().get(
                calendarId=self.calendar_id, eventId=calendar_event_id
            ).execute()
        except HttpError as exc:
            if getattr(exc, "resp", None) is not None and exc.resp.status == 404:
                return None
            raise

        tz = pytz.timezone(settings.CLINIC_TIMEZONE)
        start_raw = event.get("start", {})
        end_raw = event.get("end", {})
        start_text = start_raw.get("dateTime")
        end_text = end_raw.get("dateTime")
        private_properties = event.get("extendedProperties", {}).get("private", {})
        event_details = {
            "event_id": event.get("id", calendar_event_id),
            "status": event.get("status", ""),
            "appointment_id": private_properties.get("appointment_id", ""),
            "appointment_type": private_properties.get("appointment_type", ""),
            "doctor_id": private_properties.get("doctor_id", ""),
            "summary": event.get("summary", ""),
        }
        if start_text and end_text:
            start_dt = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = tz.localize(start_dt)
            if end_dt.tzinfo is None:
                end_dt = tz.localize(end_dt)
            start_dt, end_dt = start_dt.astimezone(tz), end_dt.astimezone(tz)
            return {
                **event_details,
                "date": start_dt.strftime("%Y-%m-%d"),
                "start": start_dt.strftime("%H:%M"),
                "end": end_dt.strftime("%H:%M"),
            }
        # All-day events cannot be a valid timed appointment; return their date for mismatch detection.
        return {
            **event_details,
            "date": start_raw.get("date", ""),
            "start": "",
            "end": "",
        }

    def create_event(
        self,
        doctor_id: str,
        date_str: str,
        start_time: str,
        end_time: str,
        summary: str,
        appointment_id: Optional[str] = None,
        appointment_type: Optional[str] = None,
    ) -> str:
        if not self._service:
            raise ConnectionError("Google Calendar API is not configured.")

        tz_str = settings.CLINIC_TIMEZONE
        start_iso = f"{date_str}T{start_time}:00"
        end_iso = f"{date_str}T{end_time}:00"

        event_body = {
            "summary": summary,
            "description": (
                f"Appointment reference: {appointment_id or 'unassigned'}\n"
                f"Doctor: {doctor_id}\n"
                f"Appointment type: {appointment_type or summary}"
            ),
            "start": {"dateTime": start_iso, "timeZone": tz_str},
            "end": {"dateTime": end_iso, "timeZone": tz_str},
            "extendedProperties": {
                "private": {
                    "doctor_id": doctor_id,
                    "appointment_id": appointment_id or "",
                    "appointment_type": appointment_type or "",
                }
            },
        }

        created = self._service.events().insert(calendarId=self.calendar_id, body=event_body).execute()
        return created.get("id", "")

    def delete_event(self, doctor_id: str, calendar_event_id: str) -> bool:
        if not self._service:
            return False
        try:
            self._service.events().delete(calendarId=self.calendar_id, eventId=calendar_event_id).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to delete event {calendar_event_id}: {e}")
            return False


# Singleton instances
_mock_calendar_instance = MockCalendarService()
_google_calendar_instance = None
_calendar_service_override: ContextVar[Optional[BaseCalendarService]] = ContextVar(
    "calendar_service_override",
    default=None,
)


@contextmanager
def use_calendar_service(service: BaseCalendarService) -> Iterator[None]:
    token = _calendar_service_override.set(service)
    try:
        yield
    finally:
        _calendar_service_override.reset(token)


def get_calendar_service() -> BaseCalendarService:
    global _google_calendar_instance
    override = _calendar_service_override.get()
    if override is not None:
        return override
    if settings.GOOGLE_CALENDAR_SANDBOX:
        return _mock_calendar_instance
    
    if _google_calendar_instance is None:
        _google_calendar_instance = GoogleCalendarService()
    return _google_calendar_instance
