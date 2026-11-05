"""
Configuration settings for the Clinical Voice Agent.
Loads environment variables using pydantic-settings with sensible defaults.
Supports multi-provider LLM (Groq, OpenAI, Gemini), STT (Deepgram, OpenAI),
TTS (ElevenLabs, Edge-TTS), and storage (PostgreSQL, SQLite).
"""

from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application settings
    APP_ENV: Literal["development", "testing", "staging", "production"] = "development"
    APP_PORT: int = 8000
    APP_HOST: str = "0.0.0.0"
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "supersecret-dev-key-change-in-production"

    # Database settings
    # Defaults to local SQLite for instant zero-dependency testing, or PostgreSQL URL
    DATABASE_URL: str = Field(
        default="sqlite:///./clinical_agent.db",
        description="SQLAlchemy database connection string",
    )

    # LiveKit real-time communication credentials
    LIVEKIT_URL: str = Field(default="", description="LiveKit server WebSocket URL")
    LIVEKIT_API_KEY: str = Field(default="", description="LiveKit API Key")
    LIVEKIT_API_SECRET: str = Field(default="", description="LiveKit API Secret")
    LIVEKIT_AGENT_NAME: str = Field(default="clinical-voice-agent", description="Registered LiveKit agent name for room dispatch")
    LIVE_TEST_AGENT_NAME: str = "clinical-voice-agent-test"

    # LLM Provider selection
    LLM_PROVIDER: Literal["groq", "openai", "gemini"] = "gemini"
    
    # Groq (Free tier, ultra-low TTFT ~150ms)
    GROQ_API_KEY: str = Field(default="", description="Groq Cloud API Key")
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    
    # OpenAI
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API Key")
    OPENAI_MODEL: str = "gpt-4o-mini"
    
    # Google Gemini
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API Key")
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"

    # Speech-to-Text (STT) Provider
    STT_PROVIDER: Literal["deepgram", "openai", "groq"] = "deepgram"
    DEEPGRAM_API_KEY: str = Field(default="", description="Deepgram API Key")

    # Text-to-Speech (TTS) Provider
    TTS_PROVIDER: Literal["elevenlabs", "openai", "edge"] = "elevenlabs"
    ELEVENLABS_API_KEY: str = Field(default="", description="ElevenLabs API Key")
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel voice
    ELEVENLABS_MODEL_ID: str = "eleven_turbo_v2_5"

    # Google Calendar integration
    GOOGLE_CALENDAR_SANDBOX: bool = Field(
        default=True,
        description="Whether to use the deterministic sandbox calendar service",
    )
    GOOGLE_CLIENT_ID: str = Field(default="")
    GOOGLE_CLIENT_SECRET: str = Field(default="")
    GOOGLE_REFRESH_TOKEN: str = Field(default="")
    GOOGLE_CALENDAR_ID: str = Field(default="primary")
    LIVE_TEST_DATABASE_URL: str = Field(
        default="sqlite:///./clinical_live_test.db",
        description="Isolated database URL used only by the live call tester",
    )

    # Clinic operational defaults
    CLINIC_NAME: str = "Serenity Medical Clinic"
    CLINIC_PHONE: str = "+1 (555) 234-5678"
    CLINIC_ADDRESS: str = "123 Healthcare Boulevard, Suite 400"
    CLINIC_TIMEZONE: str = "America/New_York"
    CLINIC_OPENING_TIME: str = "09:00"
    CLINIC_CLOSING_TIME: str = "17:00"
    DEFAULT_APPOINTMENT_DURATION_MINUTES: int = 30
    BUFFER_TIME_MINUTES: int = 10


settings = Settings()
