"""Per-call audit details: every check the tester ran, with its evidence.

This is a NEW table, so it needs no change to models.py and no migration:
ensure_table() creates it the first time it is needed (idempotent and cheap).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.models import Base


class CallAuditDetail(Base):
    __tablename__ = "call_audit_details"

    call_room_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)        # naive UTC
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # naive UTC
    reverified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    verdict: Mapped[str] = mapped_column(String(24), default="PASS")          # PASS | FLAG | NO_CHANGE | ERROR
    action_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    checks_json: Mapped[str] = mapped_column(Text, default="[]")
    claims_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bookings_json: Mapped[str] = mapped_column(Text, default="[]")
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    transcript_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # only if enabled
    error_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


_ready_engines = set()


def ensure_table(bind) -> None:
    """Create the table if it does not exist yet. Safe to call on every request."""
    key = id(bind)
    if key in _ready_engines:
        return
    Base.metadata.create_all(bind=bind, tables=[CallAuditDetail.__table__], checkfirst=True)
    _ready_engines.add(key)