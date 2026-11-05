# Clinical Voice Agent — Session Memory File
# Workspace: F:\Voice_Agent (git: main & feature/voice-agent-cloud-and-runtime-fixes)
# Updated: 2026-11-04 (Lead AI Engineer Session)

---

## 1. Role & Engineering Philosophy

- Operating as **Lead AI Engineer** overseeing end-to-end voice agent architecture.
- Core Tenet: *"Conversational fluency is not task success."* Every appointment booking, cancellation, or rescheduling must be backed by a verified database record and Google Calendar event.
- Free-Tier First: The entire stack operates reliably on free-tier service configurations (LiveKit Cloud, Deepgram trial credit, ElevenLabs premade voices, Google Gemini Free tier, and Google Calendar Sandbox).
- Contributor Identity: Local git repository configured with `user.name = "Mahandar Khatri"` and `user.email = "mk6447959@gmail.com"`. All commits are attributed to this identity.

---

## 2. Complete Phase Implementation Status (8 / 8 Phases Implemented)

### Phase 1: Foundation & Data Architecture ✅
- 8 SQLAlchemy models: `Patient`, `Doctor`, `Appointment`, `Conversation`, `ConversationEvent`, `EvaluationRun`, `EvaluationResult`, `AuditLog`.
- Repository pattern: `PatientRepo`, `DoctorRepo`, `AppointmentRepo`, `AuditRepo`.
- Pydantic v2 schemas for all request/response validation.
- `MockCalendarService` sandbox with deterministic busy slot fixtures and reset capability.
- `AppointmentService` with two-phase commit, collision prevention, idempotency, and cancellation.

### Phase 2: Controlled Agent Tools (Fully Async Coroutines) ✅
- 6 asynchronous clinical tool functions: `check_doctor_availability`, `book_clinical_appointment`, `lookup_doctor_information`, `lookup_clinic_information`, `cancel_existing_appointment`, `reschedule_existing_appointment`.
- In `livekit-agents` v1.8, tool execution awaits registered function tools (`output = await tool(...)`). Converted all 6 clinical tools to `async def` coroutines, eliminating `TypeError: object str can't be used in 'await' expression` that previously caused agent freezes after the greeting turn.
- Error compensation: existing calendar events are preserved if new event booking fails during rescheduling.

### Phase 3: Conversation State Machine & Guardrails ✅
- 12 conversation states: `INIT`, `IDENTIFY_INTENT`, `COLLECTING_INFO`, `CHECKING_AVAILABILITY`, `OFFERING_ALTERNATIVES`, `CONFIRMING_DETAILS`, `BOOKING`, `BOOKED`, `CANCELLED`, `RESCHEDULED`, `EMERGENCY_ESCALATED`, `CLOSING`.
- Safety guardrails: Emergency keyword detection → immediate 911 escalation; medical advice refusal; patient privacy protection.
- Anti-hallucination verification: `verify_booking_hallucination()` ensures the agent never states an appointment is confirmed without a real DB ID.

### Phase 4: LiveKit Voice Agent Pipeline & Audio Naturalness ✅
- LiveKit Agents v1.8 `Agent` + `AgentSession` architecture.
- **Model Standardization**: `gemini-3.5-flash-lite` with ~1.1s TTFT, 0 quota 503 errors, and reliable tool-calling support.
- **CPU STFT Loop Bypass**: Set `room_output_options=room_io.RoomOutputOptions(sync_transcription=False)`, eliminating the 8-second event loop audio stutter and freezes on Windows.
- **Natural Conversational Flow & Barge-In**:
  - `min_endpointing_delay = 0.45s` (keeps short response pauses conversational)
  - `max_endpointing_delay = 1.6s` (allows longer patient pauses without excessive response lag)
  - `min_interruption_duration = 0.6s` (prevents stray noises or mic clicks from interrupting Rachel)
  - `min_interruption_words = 2` (minimum 2 recognizable words required before interrupting)
  - `resume_false_interruption = True` (agent resumes speaking if caller coughs or makes a brief noise)
  - Silero VAD activation threshold tuned to `0.55` and `min_speech_duration = 0.1s`.
- Multi-provider audio stack:
  - **LLM**: Gemini (`gemini-3.5-flash-lite` via Google OpenAI-compatible endpoint).
  - **STT**: Deepgram Nova-2 (`nova-2-general`).
  - **TTS**: ElevenLabs (`eleven_turbo_v2_5` with free-tier premade voice `EXAVITQu4vr4xnSDxMaL` / Bella).
  - **VAD**: Silero VAD prewarmed in `prewarm(proc)` to avoid event loop stalls.

### Phase 5: Autonomous Evaluation & Benchmark Suite ✅
- 15 deterministic scenarios (TC-001 through TC-029) spanning booking, missing info, unavailability, cancellations, emergency escalation, and privacy.
- `SimulatedPatient` persona engine simulating incremental multi-turn patient dialogue.
- Independent evaluators: DB task verifier, safety auditor, tool accuracy calculator.
- Isolated in-memory SQLite instances and resettable calendar fixtures per test scenario.
- **Benchmark Scorecard**: 15 / 15 Passed (100.0% Pass Rate, 100.0% Task Completion, 100.0% Safety Compliance) in 2.97s.

### Phase 6: FastAPI REST API & LiveKit Token Service ✅
- `backend/main.py` FastAPI app with lifespan hooks and CORS middleware.
- `backend/api/routes/v1.py` endpoints:
  - `GET /health` — health check (`status: ok`)
  - `GET /api/v1/doctors` — list active doctors
  - `POST /api/v1/appointments/check` — check availability
  - `POST /api/v1/appointments` / `POST /api/v1/appointments/book` — book appointment
  - `DELETE /api/v1/appointments/{id}` — cancel appointment
  - `POST /api/v1/livekit/token` — creates a unique room (`clinical-*`), dispatches worker (`clinical-voice-agent`), returns JWT token
  - `GET /api/v1/evaluations/scenarios`, `POST /api/v1/evaluations/run`, `GET /api/v1/evaluations/runs` — evaluation management

### Phase 7: React Frontend & Lumora Design Overhaul ✅
- Modern React 19 + TypeScript + Vite single-page application under `frontend/`.
- **Lumora Liquid Voice Orb**: Custom HTML5 Canvas particle physics rendering chromatic ripples across 5 real-time states (`idle`, `connecting`, `listening`, `thinking`, `speaking`).
- **24-Band Audio Spectrum**: Real-time decibel amplitude visualizer showing incoming/outgoing speech activity.
- **Hardware WebRTC Echo Cancellation**: Enforced `{ echoCancellation: true, noiseSuppression: true, autoGainControl: true }` in `VoicePanel.tsx`, eliminating acoustic feedback where Rachel's voice from computer speakers re-entered the mic.
- **Scoped Auto-Scrolling Transcript**: Replaced window-level `scrollIntoView()` with container-scoped `.scrollTop = scrollHeight` on `.transcript-stream`, completely stopping viewport jumping.
- **Live Doctor Schedule Grid (Bento Grid)**: Live physician schedule matrix displaying working hours, booked appointment cards, and real-time Google Calendar synchronization status.
- Build verified with `npm run build` with zero TypeScript errors.

### Phase 8: Docker & Documentation ✅
- Multi-stage `Dockerfile` for Python backend/agent worker.
- `frontend/Dockerfile` and `frontend/nginx.conf` for optimized static asset serving.
- `docker-compose.yml` defining PostgreSQL, backend API, LiveKit worker, and frontend services.
- Detailed production documentation in `README.md` with color-styled Mermaid architecture flows, sequence diagrams, and UI layout wireframes.

---

## 3. Test Suite Verification

**Current status**: **43 / 43 automated tests pass** (`pytest -v` in ~19.2s):
- `tests/integration/test_api_routes.py`: 5 passed
- `tests/unit/test_appointment_service.py`: 9 passed
- `tests/unit/test_clinical_tools.py`: 4 passed
- `tests/unit/test_conversation_manager.py`: 9 passed
- `tests/unit/test_evaluation_engine.py`: 7 passed
- `tests/unit/test_voice_agent_pipeline.py`: 9 passed

---

## 4. Key Bug Fixes & Architectural Resolutions

1. **Agent Freezing After First Reply**:
   - *Cause*: In `livekit-agents` v1.8, `tool_executor.py` awaits registered function tools (`output = await tool(*args)`). The 6 clinical tools were synchronous `def`, throwing `TypeError: object str can't be used in 'await' expression` whenever the LLM tried to check availability or look up doctors.
   - *Fix*: Converted all 6 tools to `async def` and updated pipeline unit tests to `@pytest.mark.asyncio`.

2. **Mid-Sentence Speaking & Interruption Glitch**:
   - *Cause*: Browser microphone was opened without WebRTC hardware constraints, causing Rachel's voice from device speakers to echo into the microphone. Silero VAD treated the echo as caller speech and interrupted Rachel mid-sentence.
   - *Fix*: Added `{ echoCancellation: true, noiseSuppression: true, autoGainControl: true }` in `VoicePanel.tsx`, tuned endpointing to $0.45\text{s} - 1.6\text{s}$, set `min_interruption_duration = 0.6s`, `min_interruption_words = 2`, and enabled `resume_false_interruption = True`.

3. **Page Auto-Scrolling Down Forcibly**:
   - *Cause*: `useEffect` called `transcriptEndRef.current.scrollIntoView({ behavior: 'smooth' })` on every streaming word token, violently scrolling the outer browser window to the bottom.
   - *Fix*: Replaced with container-scoped scrolling: `transcriptStreamRef.current.scrollTop = transcriptStreamRef.current.scrollHeight`.

4. **Mermaid Sequence Diagram Parse Error**:
   - *Cause*: In Mermaid sequence diagrams, semicolons (`;`) act as statement separators. `Client->>Patient: Plays audio via speakers; Orb animates waves` caused a newline parse error on line 19.
   - *Fix*: Sanitized semicolons, quoted participant aliases, and replaced raw newlines with `<br/>` tags.
