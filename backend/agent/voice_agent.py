"""
LiveKit Voice Agent Worker for Clinical Receptionist.
Configures the cascaded STT -> LLM -> TTS pipeline with real-time barge-in,
multi-provider support (Groq, OpenAI, Deepgram, ElevenLabs), and controlled tools.
Compliant with PRD Section 4, 5, 6, and 7.
"""

import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

# Ensure workspace root is on sys.path regardless of how script was invoked
root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv

# LiveKit's worker CLI reads credentials from process environment variables.
load_dotenv(override=False)

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    EndpointingOptions,
    InterruptionOptions,
    JobContext,
    JobProcess,
    TurnHandlingOptions,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.agents.voice import room_io
from livekit.plugins import openai, silero

try:
    from livekit.plugins import deepgram
except ImportError:
    deepgram = None

try:
    from livekit.plugins import elevenlabs
except ImportError:
    elevenlabs = None

from config.settings import settings
from backend.database.connection import init_db
from backend.services.live_call_verifier import database_urls_match
from backend.call_context import current_voice_call_id
from backend.agent.prompts import RECEPTIONIST_SYSTEM_PROMPT
from backend.tools.clinical_tools import (
    get_clinic_information,
    get_doctor_information,
    check_availability,
    book_appointment,
    get_appointment,
    cancel_appointment,
    reschedule_appointment,
)

logger = logging.getLogger("clinical_voice_agent")


# =====================================================================
# Provider Factories (Free-Tier Friendly & Pluggable)
# =====================================================================

def build_llm():
    """
    Constructs LLM provider.
    Defaults to Groq (ultra-low ~150ms TTFT, generous free tier) or OpenAI.
    """
    if settings.LLM_PROVIDER == "groq":
        if not settings.GROQ_API_KEY:
            raise RuntimeError("LLM_PROVIDER is groq but GROQ_API_KEY is not configured")
        logger.info(f"Using Groq LLM: {settings.GROQ_MODEL}")
        return openai.LLM(
            model=settings.GROQ_MODEL,
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
            temperature=0.2,
            timeout=20.0,
        )
    if settings.LLM_PROVIDER == "gemini":
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("LLM_PROVIDER is gemini but GEMINI_API_KEY is not configured")
        model = settings.GEMINI_MODEL
        if model == "gemini-3.8-flash":
            logger.warning("Gemini 3.8 Flash is currently returning overloaded responses; using tested gemini-3.5-flash-lite")
            model = "gemini-3.5-flash-lite"
        logger.info(f"Using Gemini LLM: {model}")
        return openai.LLM(
            model=model,
            api_key=settings.GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            temperature=0.2,
            timeout=20.0,
            extra_headers={"x-goog-api-client": "clinical-voice-agent/1.0.0"},
        )
    logger.info(f"Using OpenAI LLM: {settings.OPENAI_MODEL}")
    return openai.LLM(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY or "sk-dummy-key-for-test",
        temperature=0.2,
    )


def build_stt():
    """Constructs Speech-to-Text provider (Deepgram or OpenAI)."""
    if settings.STT_PROVIDER == "deepgram" and settings.DEEPGRAM_API_KEY and deepgram is not None:
        try:
            logger.info("Using Deepgram STT (nova-2-general)")
            return deepgram.STT(
                api_key=settings.DEEPGRAM_API_KEY,
                model="nova-2-general",
                language="en-US",
            )
        except Exception as e:
            logger.warning(f"Failed to load Deepgram plugin: {e}, falling back to OpenAI STT")
    api_key = settings.OPENAI_API_KEY or "sk-dummy-test-key-for-local-dev"
    return openai.STT(api_key=api_key)


def build_tts():
    """Constructs Text-to-Speech provider (ElevenLabs or OpenAI)."""
    if settings.TTS_PROVIDER == "elevenlabs" and settings.ELEVENLABS_API_KEY and elevenlabs is not None:
        try:
            voice_id = settings.ELEVENLABS_VOICE_ID
            # Rachel (21m00Tcm4TlvDq8ikWAM) requires a paid tier; EXAVITQu4vr4xnSDxMaL (Bella) is free premade
            if voice_id == "21m00Tcm4TlvDq8ikWAM":
                voice_id = "EXAVITQu4vr4xnSDxMaL"
            logger.info(f"Using ElevenLabs TTS (Voice: {voice_id})")
            return elevenlabs.TTS(
                api_key=settings.ELEVENLABS_API_KEY,
                voice_id=voice_id,
                model=settings.ELEVENLABS_MODEL_ID,
                auto_mode=True,
            )
        except Exception as e:
            logger.warning(f"Failed to load ElevenLabs plugin: {e}, falling back to OpenAI TTS")
    api_key = settings.OPENAI_API_KEY or "sk-dummy-test-key-for-local-dev"
    return openai.TTS(api_key=api_key, voice="alloy")


def build_vad():
    """Loads Silero Voice Activity Detector for barge-in and turn detection."""
    try:
        return silero.VAD.load(
            min_speech_duration=0.08,
            min_silence_duration=0.35,
            activation_threshold=0.55,
        )
    except Exception as e:
        logger.warning(f"Failed to load Silero VAD: {e}")
        return None


def build_turn_handling_options() -> TurnHandlingOptions:
    return {
        "endpointing": EndpointingOptions(
            mode="fixed",
            min_delay=0.45,
            max_delay=1.6,
        ),
        "interruption": InterruptionOptions(
            enabled=True,
            mode="vad",
            min_duration=0.6,
            min_words=2,
            resume_false_interruption=True,
        ),
    }


def log_voice_turn_latency(event: Any) -> None:
    """Log per-turn pipeline timings without logging transcripts or patient data."""
    item = getattr(event, "item", None)
    if item is None:
        return

    metrics = getattr(item, "metrics", {})
    stage_names = (
        ("end_of_turn_delay", "end_of_turn"),
        ("transcription_delay", "transcription"),
        ("on_user_turn_completed_delay", "turn_callback"),
        ("e2e_latency", "end_to_end"),
        ("llm_node_ttft", "llm_first_token"),
        ("llm_node_ttfs", "llm_first_sentence"),
        ("tts_node_ttfb", "tts_first_audio"),
        ("playback_latency", "audio_playback"),
    )
    stage_ms = {
        label: round(metrics[key] * 1000, 1)
        for key, label in stage_names
        if isinstance(metrics, dict) and isinstance(metrics.get(key), (int, float))
    }
    if stage_ms:
        logger.info("Voice turn latency role=%s stage_ms=%s", item.role, stage_ms)


# =====================================================================
# LiveKit Function Tool Registrations
# =====================================================================

@function_tool
async def check_doctor_availability(
    doctor_id: str,
    appointment_date: str,
    preferred_time: Optional[str] = None,
) -> str:
    """
    Check if a doctor is available on a specific date (YYYY-MM-DD) and optional time (HH:MM).
    Always call this tool before telling a patient that a slot is free.
    """
    res = await asyncio.to_thread(
        check_availability,
        doctor_id=doctor_id,
        appointment_date=appointment_date,
        preferred_time=preferred_time,
    )
    return res.get("message", "Checked availability.")


@function_tool
async def book_clinical_appointment(
    patient_name: str,
    contact_number: str,
    doctor_id: str,
    appointment_date: str,
    appointment_time: str,
    appointment_type: str = "General Consultation",
) -> str:
    """
    Confirm and book an appointment after the patient has explicitly agreed.
    Requires patient name, contact number, doctor ID/name, date (YYYY-MM-DD), and time (HH:MM).
    """
    res = await asyncio.to_thread(
        book_appointment,
        patient_name=patient_name,
        contact_number=contact_number,
        doctor_id=doctor_id,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        appointment_type=appointment_type,
    )
    message = res.get("message", "Appointment booking failed.")
    if res.get("status") != "CONFIRMED":
        return f"Booking was not confirmed. {message}"
    return f"{message} Confirmation ID: {res['appointment_id']}."


@function_tool
async def lookup_doctor_information(query: Optional[str] = None) -> str:
    """
    Retrieve doctors at the clinic and their medical specialties.
    Query can be a doctor's name or medical field (e.g. 'Dermatologist').
    """
    res = await asyncio.to_thread(get_doctor_information, query=query)
    docs = res.get("doctors", [])
    if not docs:
        if query:
            all_doctors = await asyncio.to_thread(get_doctor_information)
            names = ", ".join(d["name"] for d in all_doctors.get("doctors", []))
            return f"No doctor matches '{query}'. Do not substitute without asking. Clinic doctors include: {names}."
        return "No doctors are currently listed."
    doc_lines = [f"{d['name']} ({d['specialization']}) - Hours: {d['working_hours']}" for d in docs]
    return "Our clinic doctors are: " + "; ".join(doc_lines)


@function_tool
async def lookup_clinic_information() -> str:
    """
    Get general clinic administrative information including hours, address, and phone number.
    """
    info = await asyncio.to_thread(get_clinic_information)
    return (
        f"{info['clinic_name']} is located at {info['address']}. "
        f"Phone: {info['phone']}. Hours: {info['operating_hours']}."
    )


@function_tool
async def cancel_existing_appointment(appointment_id: str) -> str:
    """
    Cancel an appointment given its confirmation ID (e.g. APT-XXXXXX).
    """
    res = await asyncio.to_thread(cancel_appointment, appointment_id=appointment_id)
    return res.get("message", "Cancellation processed.")


@function_tool
async def lookup_existing_appointment(appointment_id: str) -> str:
    """Look up an appointment by its reference ID and return its date, time, doctor, and status."""
    res = await asyncio.to_thread(get_appointment, appointment_id)
    if not res.get("found"):
        return res.get("message", f"Appointment '{appointment_id}' not found.")
    return (
        f"Appointment {res['appointment_id']} is with {res['doctor_name']} "
        f"on {res['appointment_date']} at {res['start_time']}. Status: {res['status']}."
    )


@function_tool
async def reschedule_existing_appointment(
    appointment_id: str,
    new_date: str,
    new_time: str,
) -> str:
    """
    Reschedule an existing appointment to a new date (YYYY-MM-DD) and time (HH:MM).
    """
    res = await asyncio.to_thread(
        reschedule_appointment,
        appointment_id=appointment_id,
        new_date=new_date,
        new_time=new_time,
    )
    return res.get("message", "Rescheduling processed.")


CLINICAL_TOOLS = [
    check_doctor_availability,
    book_clinical_appointment,
    lookup_doctor_information,
    lookup_clinic_information,
    lookup_existing_appointment,
    cancel_existing_appointment,
    reschedule_existing_appointment,
]


# =====================================================================
# Clinical Agent Class & Factory
# =====================================================================

class ReceptionistAgent(Agent):
    """
    LiveKit Agent for Serenity Medical Clinic receptionist.
    Handles clinical triage, FAQ answering, doctor scheduling, and natural barge-in.
    """

    async def on_enter(self) -> None:
        """
        Executed automatically when the agent session enters the active activity.
        Speaks the friendly opening clinic greeting to the caller.
        """
        greeting = (
            f"Hello! Thank you for calling {settings.CLINIC_NAME}. "
            "I'm Rachel, your AI receptionist. How can I help you today?"
        )
        logger.info(f"Rachel speaking opening greeting to caller: {greeting}")
        self.session.say(greeting)


def build_receptionist_instructions() -> str:
    """Build the system prompt with the current date in the clinic's timezone."""
    return RECEPTIONIST_SYSTEM_PROMPT.format(
        clinic_name=settings.CLINIC_NAME,
        clinic_phone=settings.CLINIC_PHONE,
        clinic_address=settings.CLINIC_ADDRESS,
        clinic_opening_time=settings.CLINIC_OPENING_TIME,
        clinic_closing_time=settings.CLINIC_CLOSING_TIME,
        clinic_timezone=settings.CLINIC_TIMEZONE,
        clinic_today=datetime.now(ZoneInfo(settings.CLINIC_TIMEZONE)).date().isoformat(),
    )


def create_clinical_agent() -> Agent:
    """Factory to create a configured LiveKit Agent with tools and prompts."""
    instructions = build_receptionist_instructions()

    llm = build_llm()
    stt = build_stt()
    tts = build_tts()
    vad = build_vad()

    return ReceptionistAgent(
        instructions=instructions,
        tools=CLINICAL_TOOLS,
        llm=llm,
        stt=stt,
        tts=tts,
        vad=vad,
        turn_handling=build_turn_handling_options(),
        allow_interruptions=True,
    )


# =====================================================================
# LiveKit Worker Lifecycle
# =====================================================================

def prewarm(proc: JobProcess):
    """Preloads Silero VAD ONNX model to avoid event loop stalls when a call connects."""
    try:
        proc.userdata["vad"] = silero.VAD.load(
            min_speech_duration=0.1,
            min_silence_duration=0.35,
            activation_threshold=0.55,
        )
        logger.info("Silero VAD successfully prewarmed")
    except Exception as e:
        logger.warning(f"Silero VAD prewarm skipped: {e}")


async def entrypoint(ctx: JobContext):
    """LiveKit agent worker lifecycle entry point."""
    if settings.LIVEKIT_AGENT_NAME == settings.LIVE_TEST_AGENT_NAME:
        if (
            not settings.GOOGLE_CALENDAR_SANDBOX
            or not database_urls_match(settings.DATABASE_URL, settings.LIVE_TEST_DATABASE_URL)
        ):
            raise RuntimeError("Live test worker must use its isolated database and mock calendar")
    init_db()
    logger.info(f"Connecting to room: {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    instructions = build_receptionist_instructions()
    llm, stt, tts = await asyncio.gather(
        asyncio.to_thread(build_llm),
        asyncio.to_thread(build_stt),
        asyncio.to_thread(build_tts),
    )

    vad = None
    if hasattr(ctx, "proc") and hasattr(ctx.proc, "userdata"):
        vad = ctx.proc.userdata.get("vad")
    if vad is None:
        vad = await asyncio.to_thread(build_vad)

    agent = ReceptionistAgent(
        instructions=instructions,
        tools=CLINICAL_TOOLS,
        llm=llm,
        stt=stt,
        tts=tts,
        vad=vad,
        turn_handling=build_turn_handling_options(),
        allow_interruptions=True,
    )
    session = AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        vad=vad,
        tools=CLINICAL_TOOLS,
        turn_handling=build_turn_handling_options(),
        allow_interruptions=True,
    )
    session.on("conversation_item_added", log_voice_turn_latency)

    # Start the agent session in the room immediately.
    # sync_transcription=False bypasses TranscriptSynchronizer's CPU STFT loop,
    # completely eliminating audio stutter and event loop freezes on Windows.
    call_context_token = current_voice_call_id.set(ctx.room.name)
    try:
        await session.start(
            agent,
            room=ctx.room,
            room_output_options=room_io.RoomOutputOptions(sync_transcription=False),
        )
        logger.info(f"Agent session successfully started in room: {ctx.room.name} (STFT bypassed)")

        # Keep worker alive for call duration and cleanly exit when call ends
        disconnect_future = asyncio.get_running_loop().create_future()

        def _mark_done(*_):
            if not disconnect_future.done():
                disconnect_future.set_result(None)

        ctx.room.on("disconnected", _mark_done)
        ctx.room.on("participant_disconnected", _mark_done)
        session.on("close", _mark_done)

        try:
            await disconnect_future
            logger.info(f"Room {ctx.room.name} call ended, closing agent session.")
        finally:
            await session.aclose()
    finally:
        current_voice_call_id.reset(call_context_token)


def main():
    """Runs the LiveKit worker CLI."""
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name=settings.LIVEKIT_AGENT_NAME,
            load_threshold=10.0,
        )
    )


if __name__ == "__main__":
    main()
