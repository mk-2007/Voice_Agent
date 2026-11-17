"""Input guards for the voice agent's tools.

Two problems seen on real calls:
  * The model guessed doctor IDs ("DOC002", "dr_michael_chen", "DR_CHEN") because it
    was never given the real IDs. resolve_doctor() maps any reasonable spelling to the
    real doctor, and refuses to guess when it is ambiguous.
  * A booking was saved with a phone number that was not a valid length.
    validate_phone() rejects it so the agent asks the caller again.
"""

import re
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.database.models import Doctor

_TITLES = {"dr", "doctor"}


def _alnum(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _tokens(value: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (value or "").lower()) if t]


def resolve_doctor(db: Session, raw: str) -> Optional[Doctor]:
    """Return the one active doctor that `raw` clearly refers to, else None.

    Accepts: the ID in any spelling ("DOC-002", "doc002"), the full name with or
    without a title ("Dr. Michael Chen", "dr_michael_chen"), a unique part of the name
    ("Chen"), or a unique specialization ("dermatologist").
    A misheard name like "Aymer Khan" matches nothing, so the caller is asked.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    doctors = db.query(Doctor).filter(Doctor.active_status.is_(True)).all()

    key = _alnum(raw)
    for d in doctors:                                   # 1) ID, any spelling
        if _alnum(d.doctor_id) == key:
            return d

    query_tokens = [t for t in _tokens(raw) if t not in _TITLES]
    if not query_tokens:
        return None
    query_name = "".join(query_tokens)

    for d in doctors:                                   # 2) full name, title optional
        name = "".join(t for t in _tokens(d.doctor_name) if t not in _TITLES)
        if name == query_name:
            return d

    hits = [d for d in doctors                          # 3) every word is in exactly one name
            if all(t in set(_tokens(d.doctor_name)) for t in query_tokens)]
    if len(hits) == 1:
        return hits[0]

    if len(query_name) >= 4:                            # 4) a unique specialization
        by_spec = [d for d in doctors if query_name in _alnum(d.specialization)]
        if len(by_spec) == 1:
            return by_spec[0]
    return None


def list_doctor_names(db: Session) -> List[str]:
    rows = db.query(Doctor).filter(Doctor.active_status.is_(True)).order_by(Doctor.doctor_id).all()
    return [d.doctor_name for d in rows]


def validate_phone(raw: str) -> Tuple[bool, str]:
    """A phone number must have 10 to 15 digits (e.g. 03001234567 or +92 300 1234567)."""
    digits = re.sub(r"\D", "", raw or "")
    if 10 <= len(digits) <= 15:
        return True, ""
    return False, (
        f"The phone number given has {len(digits)} digits, which is not a valid number. "
        "Do not book yet. Ask the caller to say the full number again slowly, "
        "then read it back to them to confirm."
    )


def validate_name(raw: str) -> Tuple[bool, str]:
    name = (raw or "").strip()
    if len(name) >= 2 and any(ch.isalpha() for ch in name):
        return True, ""
    return False, ("The patient name is missing or unclear. Do not book yet. "
                   "Ask the caller for their full name.")