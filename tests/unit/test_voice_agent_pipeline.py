"""
Unit tests for LiveKit Voice Agent pipeline instantiation, tool registration,
and multi-provider audio configuration.
"""

import asyncio
import time

import pytest
from backend.agent import voice_agent
from backend.agent.voice_agent import (
    create_clinical_agent,
    build_llm,
    CLINICAL_TOOLS,
    check_doctor_availability,
    book_clinical_appointment,
    lookup_existing_appointment,
    lookup_doctor_information,
    lookup_clinic_information,
)


def test_agent_instantiation():
    agent = create_clinical_agent()
    assert agent is not None
    assert len(CLINICAL_TOOLS) == 7


def test_agent_instructions_include_clinic_local_date(monkeypatch):
    class FixedDateTime:
        @classmethod
        def now(cls, tz):
            from datetime import datetime
            return datetime(2026, 11, 4, 9, 0, tzinfo=tz)

    monkeypatch.setattr(voice_agent, "datetime", FixedDateTime)
    instructions = voice_agent.build_receptionist_instructions()
    assert "Today's date is 2026-11-04 in the clinic timezone (America/New_York)." in instructions


@pytest.mark.asyncio
async def test_lookup_existing_appointment_returns_verified_date(monkeypatch):
    monkeypatch.setattr(
        voice_agent,
        "get_appointment",
        lambda appointment_id: {
            "found": True,
            "appointment_id": appointment_id,
            "doctor_name": "Dr. Ahmed Khan",
            "appointment_date": "2026-11-11",
            "start_time": "10:00",
            "status": "CONFIRMED",
        },
    )
    result = await lookup_existing_appointment(appointment_id="APT-12345678")
    assert "on 2026-11-11 at 10:00" in result
    assert "Status: CONFIRMED" in result


@pytest.mark.asyncio
async def test_clinical_tools_callable():
    clinic_info = await lookup_clinic_information()
    assert "Serenity Medical Clinic" in clinic_info

    doc_info = await lookup_doctor_information(query="Ahmed")
    assert "Dr. Ahmed Khan" in doc_info


@pytest.mark.asyncio
async def test_unknown_doctor_is_not_silently_substituted(monkeypatch):
    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(voice_agent.asyncio, "to_thread", fake_to_thread)
    result = await lookup_doctor_information(query="Ramad Khan")
    assert "No doctor matches 'Ramad Khan'" in result
    assert "Do not substitute without asking" in result
    assert "Dr. Ahmed Khan" in result


@pytest.mark.asyncio
async def test_booking_tool_returns_confirmation_id_only_on_success(monkeypatch):
    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(voice_agent.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(
        voice_agent,
        "book_appointment",
        lambda **kwargs: {
            "status": "CONFIRMED",
            "appointment_id": "APT-12345678",
            "message": "Appointment successfully confirmed.",
        },
    )
    result = await book_clinical_appointment(
        patient_name="Test Patient",
        contact_number="5551234567",
        doctor_id="DOC-001",
        appointment_date="2026-11-09",
        appointment_time="11:00",
    )
    assert "APT-12345678" in result

    monkeypatch.setattr(
        voice_agent,
        "book_appointment",
        lambda **kwargs: {
            "status": "FAILED",
            "message": "Could not book: doctor not found.",
        },
    )
    result = await book_clinical_appointment(
        patient_name="Test Patient",
        contact_number="5551234567",
        doctor_id="DOC-UNKNOWN",
        appointment_date="2026-11-09",
        appointment_time="11:00",
    )
    assert "Booking was not confirmed" in result
    assert "Confirmation ID" not in result


@pytest.mark.asyncio
async def test_availability_tool_does_not_block_voice_event_loop(monkeypatch):
    def slow_check(**kwargs):
        time.sleep(0.2)
        return {"message": "Availability checked."}

    monkeypatch.setattr(voice_agent, "check_availability", slow_check)
    task = asyncio.create_task(
        check_doctor_availability(
            doctor_id="DOC-001",
            appointment_date="2026-11-09",
            preferred_time="11:00",
        )
    )
    await asyncio.sleep(0.02)
    assert not task.done()
    assert await task == "Availability checked."


@pytest.mark.asyncio
async def test_tool_availability_execution():
    avail = await check_doctor_availability(
        doctor_id="DOC-001",
        appointment_date="2026-11-04",
        preferred_time="10:00",
    )
    assert isinstance(avail, str)
    assert len(avail) > 0


def test_gemini_provider_configuration(monkeypatch):
    captured = {}
    expected = object()
    monkeypatch.setattr(voice_agent.settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(voice_agent.settings, "GEMINI_API_KEY", "local-test-key")
    monkeypatch.setattr(voice_agent.settings, "GEMINI_MODEL", "gemini-test-model")
    monkeypatch.setattr(
        voice_agent.openai,
        "LLM",
        lambda **kwargs: captured.update(kwargs) or expected,
    )

    assert build_llm() is expected
    assert captured["model"] == "gemini-test-model"
    assert captured["api_key"] == "local-test-key"
    assert captured["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert captured["extra_headers"]["x-goog-api-client"] == "clinical-voice-agent/1.0.0"


def test_gemini_provider_requires_key(monkeypatch):
    monkeypatch.setattr(voice_agent.settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(voice_agent.settings, "GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not configured"):
        build_llm()


def test_gemini_model_fallback_normalization(monkeypatch):
    captured = {}
    expected = object()
    monkeypatch.setattr(voice_agent.settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(voice_agent.settings, "GEMINI_API_KEY", "local-test-key")
    monkeypatch.setattr(voice_agent.settings, "GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(
        voice_agent.openai,
        "LLM",
        lambda **kwargs: captured.update(kwargs) or expected,
    )

    assert build_llm() is expected
    assert captured["model"] == "gemini-3.5-flash"
    assert captured["timeout"] == 20.0


def test_groq_provider_requires_key(monkeypatch):
    monkeypatch.setattr(voice_agent.settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(voice_agent.settings, "GROQ_API_KEY", "")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY is not configured"):
        build_llm()


def test_groq_provider_has_bounded_request_timeout(monkeypatch):
    captured = {}
    expected = object()
    monkeypatch.setattr(voice_agent.settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(voice_agent.settings, "GROQ_API_KEY", "local-test-key")
    monkeypatch.setattr(voice_agent.settings, "GROQ_MODEL", "qwen-test-model")
    monkeypatch.setattr(
        voice_agent.openai,
        "LLM",
        lambda **kwargs: captured.update(kwargs) or expected,
    )

    assert build_llm() is expected
    assert captured["timeout"] == 20.0
    assert captured["base_url"] == "https://api.groq.com/openai/v1"


def test_voice_turn_handling_allows_stt_to_finish():
    options = voice_agent.build_turn_handling_options()
    assert options["endpointing"]["min_delay"] == 0.8
    assert options["endpointing"]["max_delay"] == 2.5
    assert options["interruption"]["min_duration"] == 0.6
    assert options["interruption"]["min_words"] == 2


def test_elevenlabs_voice_fallback(monkeypatch):
    captured = {}
    expected = object()
    monkeypatch.setattr(voice_agent.settings, "TTS_PROVIDER", "elevenlabs")
    monkeypatch.setattr(voice_agent.settings, "ELEVENLABS_API_KEY", "sk-test-eleven")
    monkeypatch.setattr(voice_agent.settings, "ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    monkeypatch.setattr(voice_agent.settings, "ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")

    class FakeElevenLabsTTS:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("livekit.plugins.elevenlabs.TTS", FakeElevenLabsTTS)
    tts = voice_agent.build_tts()
    assert isinstance(tts, FakeElevenLabsTTS)
    # Rachel (21m00Tcm4TlvDq8ikWAM) should normalize to free premade voice Bella
    assert captured["voice_id"] == "EXAVITQu4vr4xnSDxMaL"


def test_elevenlabs_fallback_to_openai_without_key(monkeypatch):
    captured = {}
    expected = object()
    monkeypatch.setattr(voice_agent.settings, "TTS_PROVIDER", "elevenlabs")
    monkeypatch.setattr(voice_agent.settings, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(voice_agent.openai, "TTS", lambda **kwargs: captured.update(kwargs) or expected)

    assert voice_agent.build_tts() is expected
    assert "voice" in captured


def test_deepgram_stt_fallback_without_key(monkeypatch):
    captured = {}
    expected = object()
    monkeypatch.setattr(voice_agent.settings, "STT_PROVIDER", "deepgram")
    monkeypatch.setattr(voice_agent.settings, "DEEPGRAM_API_KEY", "")
    monkeypatch.setattr(voice_agent.openai, "STT", lambda **kwargs: captured.update(kwargs) or expected)

    assert voice_agent.build_stt() is expected
    assert "api_key" in captured
