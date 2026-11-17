"""Checks the Google Calendar link and that times line up with Pakistan time.

Run from the repo root:

    python tools/check_calendar.py            # read-only: tests the connection
    python tools/check_calendar.py --write    # also creates, reads back and deletes
                                              # one short TEST event on your calendar

Prints no secrets, only whether each credential is present.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.calendar_service import GoogleCalendarService  # noqa: E402
from config.settings import settings  # noqa: E402

DOCTOR = "DOC-001"


def have(value):
    return "set" if value else "MISSING"


def main(write=False):
    tz = pytz.timezone(settings.CLINIC_TIMEZONE)
    now = datetime.now(tz)
    print(f"\nClinic timezone : {settings.CLINIC_TIMEZONE}")
    print(f"Now in that zone: {now:%Y-%m-%d %H:%M:%S %Z (UTC%z)}")
    if settings.CLINIC_TIMEZONE != "Asia/Karachi":
        print("  !! This is not Asia/Karachi. Set CLINIC_TIMEZONE=Asia/Karachi in .env")
    print(f"Sandbox calendar: {settings.GOOGLE_CALENDAR_SANDBOX}"
          + ("   <-- the app is using the MOCK calendar, not Google. Set "
             "GOOGLE_CALENDAR_SANDBOX=false" if settings.GOOGLE_CALENDAR_SANDBOX else ""))
    print(f"Client ID {have(settings.GOOGLE_CLIENT_ID)}, client secret {have(settings.GOOGLE_CLIENT_SECRET)}, "
          f"refresh token {have(settings.GOOGLE_REFRESH_TOKEN)}, calendar id '{settings.GOOGLE_CALENDAR_ID}'")

    if settings.GOOGLE_REFRESH_TOKEN.startswith("ya29."):
        print("   -> GOOGLE_REFRESH_TOKEN is an access token, not a refresh token. "
              "Run tools/get_google_refresh_token.py and confirm it prints "
              "'New refresh token saved'.")
        return False

    svc = GoogleCalendarService()
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    print("\n1) Connection test (read-only)")
    try:
        for day in (today, tomorrow):
            busy = svc.get_busy_intervals(DOCTOR, day)
            shown = ", ".join(f"{b['start']}-{b['end']}" for b in busy) or "nothing booked"
            print(f"   {day}: {shown}")
        print("   OK: Google Calendar answered.")
    except Exception as exc:
        text = str(exc)
        print(f"   FAILED: {type(exc).__name__}: {text[:300]}")
        if "invalid_grant" in text:
            print("   -> The refresh token is expired or revoked. If your Google OAuth app is in "
                  "'Testing' mode, tokens last only about 7 days. Create a new refresh token "
                  "(or publish the app to 'In production').")
        elif "not configured" in text or "incomplete" in text:
            print("   -> A credential is missing or the Google libraries failed to load.")
        return False

    if not write:
        print("\nRead-only check passed. Add --write to test creating an event and its time.")
        return True

    print("\n2) Write test (creates one 15-minute TEST event, reads it back, deletes it)")
    date = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    start, end = "12:00", "12:15"
    event_id = None
    try:
        event_id = svc.create_event(DOCTOR, date, start, end,
                                    "TEST - delete me (voice agent check)",
                                    appointment_id="TEST", appointment_type="Test")
        event = svc.get_event(DOCTOR, event_id)
        if event is None:
            print("   FAILED: event was created but could not be read back.")
            return False
        got = (event["date"], event["start"], event["end"])
        want = (date, start, end)
        print(f"   sent {want}  read back {got}")
        if got == want:
            print(f"   OK: the event sits at {start} {settings.CLINIC_TIMEZONE} on your calendar.")
        else:
            print("   !! TIME MISMATCH: times are not aligned. Tell me this output.")
            return False
        return True
    except Exception as exc:
        print(f"   FAILED: {type(exc).__name__}: {str(exc)[:300]}")
        return False
    finally:
        if event_id:
            deleted = svc.delete_event(DOCTOR, event_id)
            print("   test event deleted." if deleted else
                  f"   COULD NOT DELETE the test event (id {event_id}). Delete it by hand in Google Calendar.")


if __name__ == "__main__":
    sys.exit(0 if main(write="--write" in sys.argv) else 1)