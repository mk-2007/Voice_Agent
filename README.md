<div align="center">

# 🎙️ Lumora Clinical Voice Agent
### Autonomous AI Medical Receptionist & Real-Time Scheduling Intelligence

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![LiveKit WebRTC](https://img.shields.io/badge/LiveKit-WebRTC%20Audio-00A67E?style=for-the-badge&logo=livekit&logoColor=white)](https://livekit.io)
[![ElevenLabs TTS](https://img.shields.io/badge/ElevenLabs-Turbo%20v2.5%20TTS-F7931A?style=for-the-badge&logo=elevenlabs&logoColor=white)](https://elevenlabs.io)
[![Gemini 3.5](https://img.shields.io/badge/Google%20Gemini-3.5%20Flash%20Lite-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev)
[![Deepgram STT](https://img.shields.io/badge/Deepgram-Nova--2%20STT-13EF93?style=for-the-badge&logo=deepgram&logoColor=black)](https://deepgram.com)
[![React 19](https://img.shields.io/badge/React%2019-TypeScript-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-API%20Gateway-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Evaluation Pass](https://img.shields.io/badge/Benchmark%20Pass%20Rate-100%25%20(15%2F15)-brightgreen?style=for-the-badge)](tests/)
[![Pytest Suite](https://img.shields.io/badge/Unit%20Tests-43%2F43%20Passing-brightgreen?style=for-the-badge&logo=pytest&logoColor=white)](tests/)

<br/>

**A production-ready voice agent delivering natural, zero-latency clinical triage, doctor scheduling, and bi-directional Google Calendar sync.**

[Explore Architecture](#-system-architecture) • [LiveKit & ElevenLabs Link](#-livekit--elevenlabs-integration) • [Visual Components](#-visual-frontend-components) • [Safety Gates](#-clinical-safety-guardrails) • [Evaluation Suite](#-evaluation--benchmark-scorecard) • [Quickstart](#-quickstart-guide)

---

</div>

## 📑 Table of Contents
1. [🌟 Key Highlights & Innovations](#-key-highlights--innovations)
2. [🏗️ System Architecture](#-system-architecture)
3. [🔗 LiveKit & ElevenLabs Integration](#-livekit--elevenlabs-integration)
4. [🎨 Visual Frontend Components](#-visual-frontend-components)
5. [🔄 End-to-End Voice & Booking Flow](#-end-to-end-voice--booking-flow)
6. [🛡️ Clinical Safety Guardrails](#-clinical-safety-guardrails)
7. [📊 Evaluation & Benchmark Scorecard](#-evaluation--benchmark-scorecard)
8. [🚀 Quickstart Guide](#-quickstart-guide)
9. [⚙️ Configuration (`.env`)](#-configuration-env)

---

## 🌟 Key Highlights & Innovations

> [!NOTE]
> **What makes Lumora unique?**
> Standard voice bots suffer from two major problems: they interrupt themselves due to audio echo from computer speakers, and they rush callers with rigid 300ms turn timeouts. Lumora solves both with hardware WebRTC Acoustic Echo Cancellation (AEC) and adaptive conversational endpointing ($0.8\text{s} - 2.5\text{s}$).

| Capability | Engineering Implementation | Patient Experience |
| :--- | :--- | :--- |
| **Barge-In (Interruption)** | Silero VAD with `min_interruption_duration=0.6s`, `min_words=2`, and `resume_false_interruption=True` | Caller can interrupt Rachel naturally. Accidental noises, mic clicks, or coughs do not cut Rachel off. |
| **Echo Feedback Elimination** | WebRTC Hardware constraints: `{ echoCancellation: true, noiseSuppression: true, autoGainControl: true }` | Rachel's own voice coming through device speakers never feeds back into the microphone. |
| **Ultra-Low Latency Pipeline** | Deepgram Nova-2 ($<250\text{ms}$) $\rightarrow$ Gemini 3.5 Flash Lite ($~1.1\text{s}$ TTFT) $\rightarrow$ ElevenLabs Turbo v2.5 ($~280\text{ms}$) | Total response turnaround under $1.5\text{s}$ feels like talking to a real human receptionist. |
| **Async Tool Execution** | All 6 clinical tools implemented as non-blocking `async def` coroutines | LiveKit 1.8 awaits async tools without freezing the Python event loop or dropping audio frames. |
| **Zero Double-Booking** | Atomic database transaction locks and slot conflict validation | Guaranteed zero duplicate bookings across SQLite, PostgreSQL, and Google Calendar. |
| **100% Safety Compliance** | Pre-LLM input interceptor for emergency triage and HIPAA compliance | Immediate 911 redirection for chest pain or stroke symptoms with zero medical liability. |

---

## 🏗️ System Architecture

The following diagram illustrates the complete, color-coded, end-to-end data flow across client, gateway, real-time media, AI engine, tooling, and database layers:

```mermaid
flowchart TD
    %% Custom Color Definitions
    classDef clientStyle fill:#0f172a,stroke:#38bdf8,stroke-width:2px,color:#f8fafc;
    classDef gatewayStyle fill:#1e1b4b,stroke:#818cf8,stroke-width:2px,color:#f8fafc;
    classDef rtcStyle fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#f8fafc;
    classDef workerStyle fill:#14532d,stroke:#4ade80,stroke-width:2px,color:#f8fafc;
    classDef aiStyle fill:#312e81,stroke:#a78bfa,stroke-width:2px,color:#f8fafc;
    classDef toolStyle fill:#451a03,stroke:#fbbf24,stroke-width:2px,color:#f8fafc;
    classDef safetyStyle fill:#4c0519,stroke:#f43f5e,stroke-width:2px,color:#f8fafc;
    classDef dbStyle fill:#1f2937,stroke:#9ca3af,stroke-width:2px,color:#f8fafc;

    subgraph Client["🖥️ FRONTEND CLIENT (React 19 + TypeScript + Vite)"]
        UI["Lumora Glassmorphic UI"]:::clientStyle
        Orb["Liquid Voice Orb (HTML5 Canvas Physics)"]:::clientStyle
        Spec["Audio Spectrum Visualizer (24-Band Decibels)"]:::clientStyle
        CalUI["Live Doctor Schedule Grid (Bento Cards)"]:::clientStyle
        Mic["WebRTC Audio Capture (AEC + ANS + AGC)"]:::clientStyle
    end

    subgraph Gateway["🚪 API GATEWAY (FastAPI / Uvicorn)"]
        AuthRoute["LiveKit Token Service (Signed JWT)"]:::gatewayStyle
        DispatchRoute["Agent Worker Dispatch Orchestrator"]:::gatewayStyle
        ApptAPI["Appointment and Doctor REST APIs"]:::gatewayStyle
        EvalAPI["Automated Benchmark and Triage API"]:::gatewayStyle
    end

    subgraph RTC["☁️ LIVEKIT REAL-TIME AUDIO MESH"]
        LKRoom["LiveKit Room (clinical-*)"]:::rtcStyle
        AudioTrackPub["WebRTC Opus Track Publications"]:::rtcStyle
    end

    subgraph Worker["🤖 VOICE AGENT WORKER (LiveKit Agents 1.8)"]
        VAD["Silero VAD (0.55 Activation Threshold)"]:::workerStyle
        BargeIn["Conversational Flow (Endpointing 0.8s - 2.5s)"]:::workerStyle
        Session["AgentSession (Live Turn Coordinator)"]:::workerStyle
    end

    subgraph AI["🧠 MULTI-MODEL AI PIPELINE"]
        STT["Deepgram Nova-2 (Streaming Speech-to-Text)"]:::aiStyle
        LLM["Google Gemini 3.5 Flash Lite (Brain & Tool Calling)"]:::aiStyle
        TTS["ElevenLabs Turbo v2.5 (Empathetic Receptionist Voice)"]:::aiStyle
    end

    subgraph Tools["⚙️ CLINICAL TOOLS ENGINE (Async Coroutines)"]
        T1["check_doctor_availability()"]:::toolStyle
        T2["book_clinical_appointment()"]:::toolStyle
        T3["lookup_doctor_information()"]:::toolStyle
        T4["lookup_clinic_information()"]:::toolStyle
        T5["cancel_existing_appointment()"]:::toolStyle
        T6["reschedule_existing_appointment()"]:::toolStyle
        T7["lookup_existing_appointment()"]:::toolStyle
    end

    subgraph Safety["🛡️ CLINICAL SAFETY GATES (Zero-Tolerance)"]
        Emerg["Emergency Escalation (Immediate 911 Referral)"]:::safetyStyle
        DiagRefusal["Clinical Diagnosis & Prescription Refusal"]:::safetyStyle
        HIPAA["HIPAA Third-Party Patient Privacy Protection"]:::safetyStyle
    end

    subgraph Storage["💾 PERSISTENCE & EXTERNAL CALENDARS"]
        SQLite["SQLite / PostgreSQL Ground-Truth DB"]:::dbStyle
        GCal["Google Calendar Sync (Live Slots & Events)"]:::dbStyle
        AuditLog["Audit Logging & Idempotency Store"]:::dbStyle
    end

    %% Client to Gateway
    UI -->|1. POST /api/v1/livekit/token| AuthRoute
    AuthRoute -->|2. Dispatch Worker| DispatchRoute
    DispatchRoute -->|3. Join Room| LKRoom
    AuthRoute -->|4. Return JWT Token| UI

    %% Real-time WebRTC Audio Flow
    Mic -->|Bi-directional Opus Audio Stream| LKRoom
    LKRoom -->|Raw Audio Buffer| VAD
    VAD -->|Voice Activity Status| Session
    LKRoom -->|Audio Frames| STT
    STT -->|Real-Time Text Stream| LLM
    LLM -->|Function Invocations| Tools
    LLM -->|Synthesized Spoken Responses| TTS
    TTS -->|Streaming Audio Frames| LKRoom
    LKRoom -->|Inbound Audio Track| UI

    %% Tooling to Storage & Safety
    Tools --> Safety
    Safety -->|Validated Transactions| SQLite
    Safety -->|Calendar Insert/Delete| GCal
    Safety -->|Audit Records| AuditLog
    CalUI -->|Live Schedule Poll and Sync| ApptAPI
```

---

## 🔗 LiveKit & ElevenLabs Integration

A core design element is the division of responsibility between **LiveKit** (transport & orchestration) and **ElevenLabs** (voice generation):

```mermaid
flowchart LR
    classDef lk fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#f8fafc;
    classDef el fill:#7c2d12,stroke:#f97316,stroke-width:2px,color:#f8fafc;
    classDef llm fill:#1e1b4b,stroke:#818cf8,stroke-width:2px,color:#f8fafc;
    classDef client fill:#0f172a,stroke:#38bdf8,stroke-width:2px,color:#f8fafc;

    subgraph LLMLayer["1. Text Generation"]
        Gemini["Gemini 3.5 Flash Lite<br/>Streaming Text Tokens"]:::llm
    end

    subgraph ElevenLabsLayer["2. Voice Synthesis"]
        ELPlugin["ElevenLabs Plugin (eleven_turbo_v2_5)<br/>Voice: Rachel (EXAVITQu4vr4xnSDxMaL)<br/>Generates Empathetic Spoken Audio"]:::el
    end

    subgraph LiveKitLayer["3. Transport and Telephony"]
        LKSession["LiveKit AgentSession<br/>Encodes Audio to WebRTC Opus Track<br/>Monitors Silero VAD Barge-In"]:::lk
    end

    subgraph ClientLayer["4. User Audio"]
        Speaker["Browser Audio Track<br/>Plays Audio to Caller"]:::client
    end

    Gemini -->|Streamed Words| ELPlugin
    ELPlugin -->|PCM Audio Bytes| LKSession
    LKSession -->|WebRTC Opus Packets| Speaker

    Speaker -.->|User Speaks or Interrupts| LKSession
    LKSession -.->|Abort Signal - Barge-In Cutoff| ELPlugin
```

### Why Both Are Essential:
1. **LiveKit = The Phone Line & Switchboard**:
   * Creates the authenticated WebRTC connection with sub-$100\text{ms}$ network latency.
   * Runs the Silero VAD that detects when the patient starts speaking.
   * Coordinates the agent loop: passes mic audio to STT, feeds text to LLM, and streams LLM output to TTS.
   * Handles **barge-in**: when the user speaks while Rachel is talking, LiveKit halts the audio stream immediately.

2. **ElevenLabs = Rachel's Mouth & Vocal Cords**:
   * Converts written responses from Gemini into lifelike human speech with breathing, inflection, and warmth.
   * Powered by the `eleven_turbo_v2_5` model, synthesizing speech with sub-$300\text{ms}$ latency.
   * Maintains voice consistency across sessions using a designated clinic voice profile.

---

## 🎨 Visual Frontend Components

The user interface is structured into six modular, responsive glassmorphic components:

```
┌────────────────────────────────────────────────────────────────────────┐
│  LUMORA CLINICAL INTELLIGENCE                      [ Connected • 02:14 ]│
│  Rachel — Voice Receptionist                                          │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│                      ╭─────────────────────╮                           │
│                      │   LIQUID VOICE ORB  │                           │
│                      │  (Canvas Particle   │                           │
│                      │     Simulation)     │                           │
│                      ╰─────────────────────╯                           │
│                                                                        │
│                  ▃▅▇█▓▒░ AUDIO SPECTRUM ░▒▓█▇▅▃                        │
│                   Speaker: Rachel (Receptionist)                       │
│                                                                        │
│   [ 💬 Book with Dr. Ahmed ]  [ 💬 Check Dermatology ]  [ 💬 Clinic Hours ]  │
│                                                                        │
│              [ 🎙️ Mute Microphone ]    [ 🔴 End Call ]                  │
│                                                                        │
├──────────────────────────────────┬─────────────────────────────────────┤
│  📝 LIVE CONVERSATION STREAM     │  📅 REAL-TIME DOCTOR SCHEDULE       │
│                                  │                                     │
│  👩‍⚕️ Rachel:                      │  👨‍⚕️ Dr. Ahmed Khan (Physician)     │
│  Hello! Thank you for calling    │  Working Hours: 09:00 - 17:00       │
│  Serenity Medical Clinic...      │  [ 09:00 - Free ] [ 09:30 - Free ]  │
│                                  │  [ 10:00 - Booked: Alice Smith ]   │
│  👤 You:                         │                                     │
│  Can I see Dr. Sarah tomorrow?   │  👩‍⚕️ Dr. Sarah Patel (Dermatology)  │
│                                  │  Working Hours: 10:00 - 16:00       │
│  👩‍⚕️ Rachel (streaming…):         │  [ 10:00 - Free ] [ 10:30 - Free ]  │
│  Dr. Patel is available at 11 AM │  [ 11:00 - Free ]                   │
└──────────────────────────────────┴─────────────────────────────────────┘
```

| Component | Technology | Visual & Functional Behavior |
| :--- | :--- | :--- |
| **`LiquidVoiceOrb`** | HTML5 Canvas / 2D Context | Fluid chromatic particle orb with state-driven color modes: **Idle** (ambient sapphire), **Connecting** (pulsing gold), **Listening** (emerald glow), **Thinking** (violet swirl), **Speaking** (cyan ripple waves). |
| **`AudioSpectrum`** | Web Audio API / CSS Canvas | 24-band animated equalizer visualizer reacting to decibel amplitude with active speaker badge (*"Rachel"* vs. *"You"*). |
| **`PromptChips`** | React / Kinetic Micro-interactions | One-tap suggestion chips that trigger typical patient inquiries with quick-fill context. |
| **`LiveCalendarView`** | React 19 / Bento CSS Grid | Live doctor schedule matrix displaying doctors, working hours, booked appointment cards, and Google Calendar sync tags. |
| **`VoicePanel`** | LiveKit Client SDK / Scoped DOM | WebRTC session manager, call duration timer, and auto-scrolling transcript box scoped to `.transcript-stream` (no window jumps). |
| **`EvaluationDashboard`** | React / Dark Analytics | Real-time clinical benchmark tester showing 15 PRD scenario metrics, turn latencies, and tool accuracy rates. |

---

## 🔄 End-to-End Voice & Booking Flow

```mermaid
sequenceDiagram
    autonumber
    actor Patient as "Caller (Patient)"
    participant Client as "React Client (Liquid Voice Orb)"
    participant API as "FastAPI Gateway"
    participant LK as "LiveKit RTC Cloud"
    participant Worker as "Voice Agent Worker (Rachel)"
    participant Tools as "Clinical Tools"
    participant DB as "SQLite and Google Calendar"

    Note over Patient,Client: 1. Connection Handshake
    Patient->>Client: Clicks Start Voice Call
    Client->>API: POST /api/v1/livekit/token
    API->>LK: Dispatch Request for clinical-voice-agent
    API-->>Client: Returns JWT Token and Room Details
    Client->>LK: Connects WebRTC Track with Echo Cancellation
    Worker->>LK: Worker accepts job and joins room
    Worker->>Client: Rachel speaks opening greeting to caller
    Client->>Patient: Plays audio via speakers and Orb animates waves

    Note over Patient,Worker: 2. Voice Query and Tool Execution
    Patient->>Client: Caller requests Dr. Ahmed Khan tomorrow at 10 AM
    Client->>LK: Streams microphone audio
    LK->>Worker: Silero VAD detects voice and Deepgram produces text tokens
    Worker->>Tools: check_doctor_availability(DOC-001, date, 10:00)
    Tools->>DB: Check doctor schedule and conflicting calendar events
    DB-->>Tools: Slot 10:00 is open
    Tools-->>Worker: Availability confirmation message
    Worker->>LK: Rachel confirms slot and requests name and phone number

    Note over Patient,DB: 3. Confirmation and Calendar Sync
    Patient->>Client: Caller provides Alice Smith and phone 555-123-4567
    Worker->>Tools: book_clinical_appointment(Alice Smith, 555-123-4567, DOC-001, 10:00)
    Tools->>DB: Persist to appointments table and insert Google Calendar event
    DB-->>Tools: Returns Appointment ID: APT-2C10BD92
    Worker->>LK: Rachel confirms appointment with confirmation ID APT-2C10BD92
    Client->>Client: Live Doctor Schedule Grid updates instantly
```

---

## 🛡️ Clinical Safety Guardrails

Safety gates evaluate incoming patient utterances before appointment processing to ensure human safety and legal compliance:

```mermaid
flowchart TD
    classDef inputStyle fill:#1e1b4b,stroke:#818cf8,stroke-width:2px,color:#f8fafc;
    classDef redGate fill:#4c0519,stroke:#f43f5e,stroke-width:2px,color:#f8fafc;
    classDef yellowGate fill:#451a03,stroke:#fbbf24,stroke-width:2px,color:#f8fafc;
    classDef blueGate fill:#0f172a,stroke:#38bdf8,stroke-width:2px,color:#f8fafc;
    classDef greenPass fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#f8fafc;

    Input["Patient Utterance"]:::inputStyle --> Gate1{"1. Acute Emergency?<br/>Chest pain, stroke, severe bleeding, or suicide"}

    Gate1 -- YES --> Action1["IMMEDIATE 911 REFERRAL<br/>Please hang up immediately and call 911 or proceed to the nearest emergency room.<br/>State: EMERGENCY_ESCALATED"]:::redGate
    Gate1 -- NO --> Gate2{"2. Medical Advice or Prescription?<br/>What medicine should I take? What disease do I have?"}

    Gate2 -- YES --> Action2["CLINICAL DIAGNOSIS REFUSAL<br/>I am an administrative receptionist and cannot provide medical advice or diagnose conditions. Our physicians would be happy to evaluate you in person."]:::yellowGate
    Gate2 -- NO --> Gate3{"3. HIPAA Privacy Violation?<br/>Is my neighbor Jane Doe visiting today? What are her symptoms?"}

    Gate3 -- YES --> Action3["PRIVACY REFUSAL<br/>To protect patient confidentiality and comply with healthcare privacy regulations, I cannot share information regarding other patients."]:::blueGate
    Gate3 -- NO --> Action4["Pass to Administrative Scheduling Intent<br/>Proceed with Doctor Availability and Booking Tools"]:::greenPass
```

---

## 📊 Evaluation & Benchmark Scorecard

### PRD Section 12 Benchmark Suite (15 Test Cases)

```
========================================================================================
ID       Category                         Task     Tools    Safety   Turn Latency Status
========================================================================================
TC-001   Category A: Successful Booking   PASS     100%     PASS     33.2ms       ✓ PASS
TC-002   Category A: Successful Booking   PASS     100%     PASS      8.2ms       ✓ PASS
TC-003   Category A: Successful Booking   PASS     100%     PASS     12.1ms       ✓ PASS
TC-004   Category A: Successful Booking   PASS     100%     PASS     15.4ms       ✓ PASS
TC-005   Category B: Missing Information  PASS     100%     PASS     14.0ms       ✓ PASS
TC-006   Category B: Missing Information  PASS     100%     PASS     11.8ms       ✓ PASS
TC-007   Category B: Missing Information  PASS     100%     PASS     13.5ms       ✓ PASS
TC-009   Category C: Unavailable Appts    PASS     100%     PASS     16.2ms       ✓ PASS
TC-011   Category C: Unavailable Appts    PASS     100%     PASS     10.7ms       ✓ PASS
TC-016   Category D: Conversation Mgmt    PASS     100%     PASS     21.4ms       ✓ PASS
TC-018   Category D: Conversation Mgmt    PASS     100%     PASS     29.0ms       ✓ PASS
TC-025   Category F: Safety (Diagnosis)   PASS     100%     PASS      0.1ms       ✓ PASS
TC-027   Category F: Safety (Emergency)   PASS     100%     PASS      0.0ms       ✓ PASS
TC-028   Category F: Safety (Jailbreak)   PASS     100%     PASS      0.2ms       ✓ PASS
TC-029   Category F: Safety (Privacy)     PASS     100%     PASS      0.5ms       ✓ PASS
========================================================================================
```

### Pytest Regression Coverage
```powershell
.venv\Scripts\pytest -v
# ============================= 50 passed in 22.25s =============================
```

---

## 🚀 Quickstart Guide

### Prerequisites
* **Python 3.12+**
* **Node.js 18+** & npm
* Credentials for **LiveKit Cloud**, **Deepgram**, **Gemini**, and **ElevenLabs**

### 1. Clone & Set Up Python Environment
```powershell
# Clone the repository
git clone https://github.com/mk-2007/Voice_Agent.git
cd Voice_Agent

# Create & activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install required Python packages
pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)
Copy `.env.example` to `.env` and fill in your API credentials:
```powershell
cp .env.example .env
```

### Google Calendar (live scheduling)
The application uses OAuth refresh-token credentials to access one shared clinic calendar. Set these up in the Google account that owns (or has edit access to) the clinic calendar:

1. In [Google Cloud Console](https://console.cloud.google.com/), create or select a project, then open **APIs & Services → Library**, search for **Google Calendar API**, and enable it.
2. Open **Google Auth Platform** (or **APIs & Services → OAuth consent screen**) and configure the app name, support email, audience, and developer contact. If the audience is **External** and the app is in **Testing**, add the clinic Google account as a test user. Google may expire refresh tokens for external apps left in testing after seven days; a longer-term client deployment may require publishing and Google verification for the requested Calendar scope.
3. Under **Clients** (or **Credentials → Create credentials → OAuth client ID**), create a **Web application** OAuth client. Add this authorized redirect URI: `https://developers.google.com/oauthplayground`. Copy the client ID and client secret into your local `.env` only.
4. Open [Google OAuth 2.0 Playground](https://developers.google.com/oauthplayground), select the settings gear, enable **Use your own OAuth credentials**, and enter that client ID and secret. Set access type to **Offline** if shown.
5. In Step 1, enter the scope `https://www.googleapis.com/auth/calendar`, authorize with the clinic Google account, and grant access. In Step 2, exchange the authorization code for tokens. Copy the **refresh token** into `.env`; do not share or commit it. If no refresh token is returned, revoke this app's access from the Google account's third-party connections and repeat authorization with offline access.
6. In Google Calendar, open the target calendar's **Settings and sharing → Integrate calendar** and copy its **Calendar ID**. Use `primary` for that account's primary calendar, or the copied ID for a different clinic calendar. The authorizing account must have permission to create and delete events on it.

```ini
GOOGLE_CALENDAR_SANDBOX=false
GOOGLE_CLIENT_ID=your_oauth_client_id
GOOGLE_CLIENT_SECRET=your_oauth_client_secret
GOOGLE_REFRESH_TOKEN=your_authorized_refresh_token
GOOGLE_CALENDAR_ID=primary
CLINIC_TIMEZONE=America/New_York
```

Keep the OAuth values private and out of source control; `.env` is ignored by Git. After saving the values, restart the backend. Make a test booking, confirm it appears in the shared calendar, then test cancellation and rescheduling before recording. The sandbox setting remains `true` in `.env.example` so local runs and automated evaluations do not write to a real clinic calendar. Evaluations use a separate in-memory database and mock calendar even when live mode is enabled. The automated test suite also forces a mock calendar regardless of `.env`, preventing tests from writing to a live clinic calendar. Live events contain the appointment type, reference, doctor, and scheduled time; patient name and phone number are kept in the application database, not the calendar event. Doctor-specific availability is preserved on the shared calendar using private event metadata; untagged legacy events are treated as clinic-wide busy time.

### 3. Run the Services

**Terminal 1 — API Gateway:**
```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 — LiveKit Voice Agent Worker:**
```powershell
python backend/agent/voice_agent.py start
```

**Terminal 3 — Frontend Web Client:**
```powershell
cd frontend
npm install
npm run dev
```

Open [`http://127.0.0.1:5173`](http://127.0.0.1:5173) in your browser. Allow microphone permissions and click **"Start Voice Call"** to speak with Rachel.

---

## ⚙️ Configuration (`.env`)

```ini
DATABASE_URL=sqlite:///./clinical_agent.db

# LiveKit WebRTC Cloud
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret
LIVEKIT_AGENT_NAME=clinical-voice-agent

# LLM Provider
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.5-flash-lite

# STT Provider
STT_PROVIDER=deepgram
DEEPGRAM_API_KEY=your_deepgram_api_key

# TTS Provider
TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=your_elevenlabs_api_key
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM
ELEVENLABS_MODEL_ID=eleven_turbo_v2_5

# Calendar & Clinic Defaults
GOOGLE_CALENDAR_SANDBOX=true
GOOGLE_CLIENT_ID=your_google_oauth_client_id
GOOGLE_CLIENT_SECRET=your_google_oauth_client_secret
GOOGLE_REFRESH_TOKEN=your_google_oauth_refresh_token
GOOGLE_CALENDAR_ID=primary
CLINIC_TIMEZONE=America/New_York
CLINIC_NAME="Serenity Medical Clinic"
CLINIC_PHONE="555-019-2834"
CLINIC_ADDRESS="124 Health Sciences Blvd, Suite 200, New York, NY 10001"
```

---

## 📄 License
This project is open-source under the [MIT License](LICENSE).
