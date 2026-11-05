from contextvars import ContextVar
from typing import Optional


current_voice_call_id: ContextVar[Optional[str]] = ContextVar(
    "current_voice_call_id",
    default=None,
)
