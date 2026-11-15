"""Show the latest call audits and recent appointments, in Pakistan time.

Run from the repo root, right after a call:

    python tools/check_calls.py        # last 5 calls and last 5 appointments
    python tools/check_calls.py 10     # last 10

It reads whatever database DATABASE_URL points to (your .env), so it shows
what the API and dashboard see. Run it against the same database the agent writes to.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database.connection import SessionLocal  # noqa: E402
from backend.database.models import Appointment, LiveCallEvaluation  # noqa: E402
from backend.services.call_auditor import severity  # noqa: E402
from config.settings import settings  # noqa: E402

TZ = ZoneInfo(settings.CLINIC_TIMEZONE)

EXPLAIN = {
    "appointment_change_not_recorded_for_call": "Caller wanted an appointment, but nothing was recorded for this call.",
    "agent_spoke_reference_without_booking": "Rachel gave a reference ID but no booking was recorded.",
    "calendar_event_id_missing": "Booking saved, but no calendar event was linked to it.",
    "calendar_event_missing": "Booking saved, but its calendar event cannot be found.",
    "calendar_details_mismatch": "Calendar event time differs from the booking.",
    "calendar_event_still_exists": "A cancelled booking still has a calendar event.",
    "calendar_unreachable": "Could not reach the calendar to check.",
    "slot_double_booked": "Another confirmed appointment overlaps this slot.",
    "outside_working_hours": "Appointment is outside the doctor's working hours.",
    "appointment_in_past": "Appointment time is in the past.",
    "phone_not_plausible": "Stored phone number does not look valid.",
    "reference_id_not_spoken": "Rachel never read the reference ID aloud.",
    "caller_did_not_confirm": "Caller did not clearly say yes before booking.",
    "name_not_provided_by_caller": "Caller never gave a name.",
    "patient_name_differs_from_caller": "Stored name differs from what the caller said.",
    "phone_not_provided_by_caller": "Caller never gave a phone number.",
    "phone_differs_from_caller": "Stored phone differs from what the caller said.",
    "doctor_differs_from_caller": "Booked doctor differs from the one the caller asked for.",
    "date_not_provided_by_caller": "Caller never gave a date.",
    "date_differs_from_caller": "Booked date differs from what the caller said.",
    "time_not_provided_by_caller": "Caller never gave a time.",
    "time_differs_from_caller": "Booked time differs from what the caller said.",
    "caller_details_unverified": "Could not read the caller's side (the Gemini check failed); review by ear.",
    "transcript_missing": "No transcript was captured for this call.",
    "appointment_record_missing": "An audited booking is missing from the database.",
    "database_details_do_not_match_action": "Stored booking differs from what the agent recorded.",
}


def pkt(value):
    """Database timestamps are stored as naive UTC."""
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def main(limit):
    with SessionLocal() as db:
        calls = (db.query(LiveCallEvaluation)
                 .order_by(LiveCallEvaluation.started_at.desc()).limit(limit).all())
        appts = (db.query(Appointment)
                 .order_by(Appointment.created_at.desc()).limit(limit).all())

        print(f"\nLATEST CALL AUDITS  (times in {settings.CLINIC_TIMEZONE})")
        print("-" * 72)
        if not calls:
            print("none yet - no call has finished and been audited in this database")
        for c in calls:
            what = c.action_type or "no appointment change"
            print(f"{pkt(c.started_at)}  {c.execution_status:<10} {what}  ({c.action_count})")
            print(f"    room {c.call_room_name}")
            for code in json.loads(c.findings_json or "[]"):
                tag = "CRITICAL" if severity(code) == "critical" else "review"
                print(f"    [{tag}] {EXPLAIN.get(code, code)}")

        print(f"\nRECENT APPOINTMENTS")
        print("-" * 72)
        if not appts:
            print("none")
        for a in appts:
            who = a.patient.full_name if a.patient else "?"
            phone = a.patient.phone_number if a.patient else "?"
            doc = a.doctor.doctor_name if a.doctor else a.doctor_id
            cal = "yes" if a.calendar_event_id else "NO"
            print(f"{a.appointment_id}  {a.appointment_date} {a.start_time}-{a.end_time}  "
                  f"{doc}  {who} ({phone})  {a.status}  calendar event: {cal}")
        print()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)