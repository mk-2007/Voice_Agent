"""Tester API + page: open any audited call and see exactly what was verified.

Add these two lines to the end of backend/main.py:

    from backend.tester_api import router as tester_router
    app.include_router(tester_router)

Then open  http://localhost:8000/tester

Note: this page shows names and phone numbers from calls. It has no login, so keep it
on localhost (or add authentication) before hosting it anywhere public.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import func

from backend.database.audit_details import CallAuditDetail, ensure_table
from backend.database.connection import SessionLocal
from backend.database.models import LiveCallEvaluation
from backend.services.call_auditor import (
    _fmt_date, _fmt_time, audit_live_call, persist_audit, store_details,
)

router = APIRouter()
PAGE = Path(__file__).with_name("tester_page.html")

_VERBS = {"BOOK_APPOINTMENT": "Booked", "RESCHEDULE_APPOINTMENT": "Rescheduled",
          "CANCEL_APPOINTMENT": "Cancelled"}


def _iso(value: Optional[datetime]) -> Optional[str]:
    """Database timestamps are naive UTC; send them with an explicit Z."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _loads(text: Optional[str], default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except json.JSONDecodeError:
        return default


def _turns(transcript: Optional[str]) -> List[Dict[str, str]]:
    """Split 'Rachel: ...' / 'Caller: ...' lines into turns; unlabeled lines continue the last turn."""
    turns: List[Dict[str, str]] = []
    for line in (transcript or "").splitlines():
        label, sep, content = line.partition(":")
        speaker = label.strip().lower()
        if sep and speaker in ("rachel", "caller"):
            turns.append({"speaker": speaker, "text": content.strip()})
        elif turns and line.strip():
            turns[-1]["text"] += " " + line.strip()
    return turns


def _summary(row: CallAuditDetail) -> Dict[str, Any]:
    checks = _loads(row.checks_json, [])
    bookings = _loads(row.bookings_json, [])
    failed = [c for c in checks if c.get("status") == "fail"]
    passed = sum(1 for c in checks if c.get("status") == "pass")
    skipped = sum(1 for c in checks if c.get("status") == "skip")

    if row.verdict == "ERROR":
        headline = "The tester crashed on this call"
    elif bookings:
        b = bookings[0]
        verb = _VERBS.get(b.get("action"), "Changed")
        headline = (f"{verb} {b.get('doctor')} for {b.get('patient_name')}, "
                    f"{_fmt_date(b.get('date'))} at {_fmt_time(b.get('start_time'))}")
        if len(bookings) > 1:
            headline += f" (+{len(bookings) - 1} more)"
    elif failed:
        headline = failed[0].get("label", "Needs review")
    else:
        headline = "No appointment requested"

    return {
        "room": row.call_room_name,
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "reverified_at": _iso(row.reverified_at),
        "verdict": row.verdict,
        "action_type": row.action_type,
        "headline": headline,
        "passed": passed,
        "failed": len(failed),
        "skipped": skipped,
        "total": len(checks),
        "has_critical": any(c.get("severity") == "critical" for c in failed),
        "has_transcript": bool(row.transcript_text),
    }


def _detail(row: CallAuditDetail) -> Dict[str, Any]:
    out = _summary(row)
    out.update({
        "checks": _loads(row.checks_json, []),
        "claims": _loads(row.claims_json, None),
        "bookings": _loads(row.bookings_json, []),
        "findings": _loads(row.findings_json, []),
        "transcript": _turns(row.transcript_text),
        "error_text": row.error_text,
    })
    return out


@router.get("/tester", response_class=HTMLResponse)
def tester_page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@router.get("/api/tester/calls")
def list_calls(limit: int = Query(60, ge=1, le=200)) -> Dict[str, Any]:
    with SessionLocal() as db:
        ensure_table(db.get_bind())
        rows = (db.query(CallAuditDetail)
                .order_by(CallAuditDetail.started_at.desc()).limit(limit).all())
        counts = dict(db.query(CallAuditDetail.verdict, func.count())
                      .group_by(CallAuditDetail.verdict).all())
        calls = [_summary(r) for r in rows]
    passed, flagged = counts.get("PASS", 0), counts.get("FLAG", 0)
    errors = counts.get("ERROR", 0)
    judged = passed + flagged + errors
    return {
        "stats": {
            "total": sum(counts.values()),
            "pass": passed,
            "flag": flagged,
            "no_change": counts.get("NO_CHANGE", 0),
            "error": errors,
            "pass_rate": round(100 * passed / judged) if judged else None,
        },
        "calls": calls,
    }


@router.get("/api/tester/calls/{room}")
def call_detail(room: str) -> Dict[str, Any]:
    with SessionLocal() as db:
        ensure_table(db.get_bind())
        row = db.get(CallAuditDetail, room)
        if row is None:
            raise HTTPException(status_code=404, detail="No audit stored for that call.")
        return _detail(row)


@router.post("/api/tester/calls/{room}/reverify")
def reverify(room: str) -> Dict[str, Any]:
    """Run the whole audit again on the stored transcript, against the database and
    calendar as they are NOW. Useful after fixing data, or to show the tester live."""
    with SessionLocal() as db:
        ensure_table(db.get_bind())
        row = db.get(CallAuditDetail, room)
        if row is None:
            raise HTTPException(status_code=404, detail="No audit stored for that call.")
        if not row.transcript_text:
            raise HTTPException(
                status_code=409,
                detail="This call's transcript was not stored (STORE_CALL_TRANSCRIPTS is off), "
                       "so it cannot be re-verified.")
        scope_row = db.query(LiveCallEvaluation).filter_by(call_room_name=room).first()
        scope = scope_row.data_scope if scope_row else "live"
        try:
            result = audit_live_call(row.transcript_text, room, row.started_at, db)
        except Exception as exc:  # report, do not 500 with a stack trace
            raise HTTPException(status_code=500,
                                detail=f"Re-verify failed: {type(exc).__name__}") from exc
        persist_audit(db, room, scope, row.started_at, result)
        updated = store_details(db, room, row.started_at, result, row.transcript_text, reverified=True)
        return _detail(updated)