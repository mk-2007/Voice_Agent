# Clinical Voice Agent — Project Context File
# Workspace: F:\Voice_Agent (git: main & feature/voice-agent-cloud-and-runtime-fixes)
# Updated: 2026-11-04 (Lead AI Engineer Session)

## What Is This Project?

A **production-grade Clinical AI Receptionist ("Rachel")** — a real-time conversational voice agent that:
- Answers patient calls using LiveKit WebRTC audio streaming with hardware echo cancellation.
- Transcribes patient speech with Deepgram Nova-2 (`nova-2-general`).
- Processes conversation turns with Google Gemini (`gemini-3.5-flash-lite` via OpenAI-compatible endpoint with ~1.1s TTFT).
- Speaks back using ElevenLabs TTS (`eleven_turbo_v2_5` with free-tier premade voice `Bella` / `EXAVITQu4vr4xnSDxMaL`).
- Checks doctor availability against Google Calendar (sandbox / live) and clinical database records.
- Books, reschedules, and cancels appointments deterministically with two-phase commit, double-booking prevention, and audit logging.
- Enforces clinical safety guardrails (emergency 911 escalation, medical advice refusal, HIPAA patient privacy protection, anti-hallucination verification).
- Features a Lumora glassmorphic React 19 + TypeScript frontend with a Canvas Liquid Voice Orb, 24-band audio spectrum, live doctor schedule grid, and an Autonomous Evaluation Dashboard.

---

## Current Architecture & Status Summary

| Area | Status | Evidence |
|---|---|---|
| **Phase 1: Foundation & Data Architecture** | ✅ COMPLETE | 8 SQLAlchemy models, repository pattern, Pydantic v2 schemas, SQLite/Postgres DB setup |
| **Phase 2: Controlled Agent Tools** | ✅ COMPLETE | 6 async clinical tools (`check_doctor_availability`, `book_clinical_appointment`, `lookup_doctor_information`, `lookup_clinic_information`, `cancel_existing_appointment`, `reschedule_existing_appointment`) |
| **Phase 3: Conversation State & Guardrails** | ✅ COMPLETE | 12-state conversation manager, emergency 911 escalation, medical advice refusal, privacy filter, anti-hallucination validation |
| **Phase 4: LiveKit Voice Agent Pipeline** | ✅ COMPLETE | LiveKit Agents v1.8 worker, Gemini 3.5 Flash Lite LLM, Deepgram Nova-2 STT, ElevenLabs TTS, Silero VAD, natural barge-in (0.45s–1.6s endpointing) |
| **Phase 5: Autonomous Evaluation Engine** | ✅ COMPLETE | 15 deterministic scenarios (TC-001–TC-029), simulated patient persona, ground-truth DB verifier, 100% pass rate in 2.97s |
| **Phase 6: FastAPI REST API & Token Service** | ✅ COMPLETE | FastAPI with CORS, appointment routes, LiveKit room token generator & named-agent dispatcher |
| **Phase 7: React Frontend & Lumora Overhaul** | ✅ COMPLETE | React 19 + TypeScript + Vite, Liquid Voice Orb Canvas, 24-band spectrum, live doctor schedule grid, hardware AEC, scoped transcript |
| **Phase 8: Docker & Production Runbook** | ✅ COMPLETE | Multi-stage Dockerfile, docker-compose.yml, nginx configuration, production README with color-styled Mermaid architecture |

**Test Status**: **43 / 43 automated tests passing** across unit and integration suites (`pytest -v` in ~19.2s).
**Evaluation Benchmark**: **15 / 15 scenarios passing (100%)** with zero safety failures.

---

## Root Cause Analysis & Recent Architectural Fixes

1. **LiveKit 1.8 Asynchronous Tool Invocation**:
   - In LiveKit 1.8, `tool_executor.py` awaits functions (`output = await tool(*args)`).
   - The 6 clinical tools were synchronous `def`, causing Python to throw `TypeError: object str can't be used in 'await' expression` whenever the LLM tried to check availability or look up doctors. This caused Rachel to freeze after the initial greeting turn.
   - **Fix**: Converted all 6 clinical tools to `async def` in `backend/agent/voice_agent.py` and updated test fixtures to `@pytest.mark.asyncio`.

2. **Mid-Sentence Speaking & Interruption Glitch**:
   - Browser microphone was opened without WebRTC hardware constraints, causing Rachel's voice from device speakers to echo into the microphone. Silero VAD treated the echo as caller speech and interrupted Rachel mid-sentence.
   - **Fix**: Added `{ echoCancellation: true, noiseSuppression: true, autoGainControl: true }` in `VoicePanel.tsx`, tuned endpointing to $0.45\text{s} - 1.6\text{s}$, set `min_interruption_duration = 0.6s`, `min_interruption_words = 2`, and enabled `resume_false_interruption = True`.

3. **Page Auto-Scrolling Down Forcibly**:
   - `useEffect` called `transcriptEndRef.current.scrollIntoView({ behavior: 'smooth' })` on every streaming word token, violently scrolling the outer browser window to the bottom.
   - **Fix**: Replaced with container-scoped scrolling: `transcriptStreamRef.current.scrollTop = transcriptStreamRef.current.scrollHeight` on `.transcript-stream`.

4. **Event Loop Freeze in CPU STFT**:
   - LiveKit's `TranscriptSynchronizer` was running CPU-heavy STFT math on the main asyncio event loop, causing 8-second stalls on Windows.
   - **Fix**: Disabled STFT loop via `room_output_options=room_io.RoomOutputOptions(sync_transcription=False)`.

5. **Gemini Model Standardization**:
   - Standardized on `gemini-3.5-flash-lite`, which delivers ~1.1s TTFT, 0 quota 503 errors, and reliable tool-calling support.

6. **Contributor Identity & Remote Sync**:
   - Configured local git repository with `user.name = "Mahandar Khatri"` and `user.email = "mk6447959@gmail.com"`.
   - Re-authored all commits and pushed cleanly to both `main` and `feature/voice-agent-cloud-and-runtime-fixes` on `https://github.com/mk-2007/Voice_Agent.git`.

---

## Complete Method to Run the System Locally

### Terminal 1: FastAPI REST API
```powershell
cd F:\Voice_Agent
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```
API Documentation: `http://localhost:8000/docs`
Health check: `http://localhost:8000/health` (`status: ok`)

### Terminal 2: LiveKit Voice Agent Worker
```powershell
cd F:\Voice_Agent
.\.venv\Scripts\Activate.ps1
python backend/agent/voice_agent.py start
```
You should see:
```text
Registered agent worker: clinical-voice-agent (AW_*)
Connecting to LiveKit server: wss://live-interview-267n30oe.livekit.cloud
```

### Terminal 3: React Web Frontend
```powershell
cd F:\Voice_Agent\frontend
npm run dev
```
Open `http://localhost:5173` in your browser. Allow microphone permissions and click **"Start Voice Call"** to speak with Rachel.

---

## Automated Validation & Benchmark Commands

To run all 43 unit and integration tests:
```powershell
.\.venv\Scripts\pytest -v
```

To run the complete closed-loop simulated patient evaluation benchmark:
```powershell
.\.venv\Scripts\python -c "from evaluation.runner import EvaluationRunner; res = EvaluationRunner().run_suite(); print('Pass rate:', res['overall_pass_rate'], 'Total:', res['total_scenarios'], 'Passed:', res['passed_scenarios'])"
```
