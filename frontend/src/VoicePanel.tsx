import { useCallback, useEffect, useRef, useState } from 'react'
import { Room, RoomEvent, Track, type TranscriptionSegment } from 'livekit-client'
import { api, type LiveKitCredentials } from './api'
import LiquidVoiceOrb, { type OrbState } from './LiquidVoiceOrb'
import AudioSpectrum from './AudioSpectrum'
import PromptChips from './PromptChips'

export type TranscriptLine = { id: string; speaker: string; text: string; final: boolean; timestamp: string }

export interface VoicePanelProps {
  onOpenCalendar?: () => void
}

export default function VoicePanel({ onOpenCalendar }: VoicePanelProps) {
  const roomRef = useRef<Room | null>(null)
  const audioElements = useRef(new Map<string, HTMLAudioElement>())
  const timerIntervalRef = useRef<number | null>(null)
  const transcriptStreamRef = useRef<HTMLDivElement | null>(null)
  const transcriptEndRef = useRef<HTMLDivElement | null>(null)

  const [connecting, setConnecting] = useState(false)
  const [connected, setConnected] = useState(false)
  const [userSpeaking, setUserSpeaking] = useState(false)
  const [agentSpeaking, setAgentSpeaking] = useState(false)
  const [micMuted, setMicMuted] = useState(false)
  const [callDuration, setCallDuration] = useState(0)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const [lines, setLines] = useState<TranscriptLine[]>([])
  const [callEndedAlert, setCallEndedAlert] = useState(false)

  // Auto-scroll transcript container to bottom without scrolling outer window
  useEffect(() => {
    if (transcriptStreamRef.current) {
      transcriptStreamRef.current.scrollTop = transcriptStreamRef.current.scrollHeight
    }
  }, [lines])

  const copyTranscript = async () => {
    if (lines.length === 0) return
    const text = lines.map(l => `[${l.timestamp}] ${l.speaker}: ${l.text}`).join('\n')
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const clearTranscript = () => {
    setLines([])
  }

  // Calculate current Orb State
  const orbState: OrbState = connecting
    ? 'connecting'
    : !connected
    ? 'idle'
    : userSpeaking
    ? 'listening'
    : agentSpeaking
    ? 'speaking'
    : lines.length > 0 && lines[lines.length - 1].speaker === 'You' && !lines[lines.length - 1].final
    ? 'thinking'
    : 'idle'

  // Format call duration as mm:ss
  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }

  const disconnect = useCallback(async () => {
    if (timerIntervalRef.current) {
      clearInterval(timerIntervalRef.current)
      timerIntervalRef.current = null
    }
    const room = roomRef.current
    roomRef.current = null
    if (room) await room.disconnect()
    audioElements.current.forEach(element => {
      element.pause()
      element.remove()
    })
    audioElements.current.clear()
    setConnected(false)
    setUserSpeaking(false)
    setAgentSpeaking(false)
    setCallDuration(0)
    setMicMuted(false)
    setCallEndedAlert(true)
  }, [])

  useEffect(() => () => {
    void roomRef.current?.disconnect()
    audioElements.current.forEach(element => {
      element.pause()
      element.remove()
    })
    audioElements.current.clear()
    if (timerIntervalRef.current) clearInterval(timerIntervalRef.current)
  }, [])

  // Timer for active call
  useEffect(() => {
    if (connected) {
      setCallDuration(0)
      timerIntervalRef.current = window.setInterval(() => {
        setCallDuration(prev => prev + 1)
      }, 1000)
    } else {
      if (timerIntervalRef.current) clearInterval(timerIntervalRef.current)
    }
    return () => {
      if (timerIntervalRef.current) clearInterval(timerIntervalRef.current)
    }
  }, [connected])

  const toggleMute = async () => {
    if (!roomRef.current) return
    const nextMuted = !micMuted
    await roomRef.current.localParticipant.setMicrophoneEnabled(!nextMuted)
    setMicMuted(nextMuted)
  }

  const connect = async () => {
    setConnecting(true)
    setError('')
    setLines([])
    setCallEndedAlert(false)
    let room: Room | null = null
    try {
      // 1. Ensure user has granted microphone permission first before connecting
      if (typeof navigator !== 'undefined' && navigator.mediaDevices?.getUserMedia) {
        try {
          const testStream = await navigator.mediaDevices.getUserMedia({
            audio: {
              echoCancellation: true,
              noiseSuppression: true,
              autoGainControl: true,
            },
          })
          testStream.getTracks().forEach(t => t.stop())
        } catch (micErr: any) {
          throw new Error(
            micErr?.name === 'NotAllowedError'
              ? 'Microphone access was denied. Please allow microphone permissions in your browser URL bar to talk with Rachel.'
              : 'Could not access audio input device. Please check your microphone connection.'
          )
        }
      }

      // 2. Obtain token and trigger LiveKit agent worker dispatch
      const creds = await api<LiveKitCredentials>('/api/v1/livekit/token', { method: 'POST', body: '{}' })
      room = new Room({ adaptiveStream: true, dynacast: true })
      roomRef.current = room

      const attachRemoteAudio = (track: Track) => {
        const trackId = track.sid ?? track.mediaStreamTrack.id
        if (track.kind !== Track.Kind.Audio || audioElements.current.has(trackId)) return
        const audio = track.attach()
        audio.autoplay = true
        audio.setAttribute('playsinline', 'true')
        document.body.appendChild(audio)
        audioElements.current.set(trackId, audio)
        void audio.play().catch(err => {
          if (err instanceof DOMException && err.name === 'NotAllowedError') {
            setError('The browser blocked Rachel’s audio. End the call, click the page, and reconnect to enable playback.')
          } else {
            console.warn('Could not start Rachel’s audio playback:', err)
          }
        })
      }

      room.on(RoomEvent.Connected, () => setConnected(true))
      room.on(RoomEvent.Disconnected, () => {
        setConnected(false)
        setUserSpeaking(false)
        setAgentSpeaking(false)
        audioElements.current.forEach(element => {
          element.pause()
          element.remove()
        })
        audioElements.current.clear()
        setCallEndedAlert(true)
      })

      room.on(RoomEvent.ActiveSpeakersChanged, speakers => {
        const localActive = speakers.some(s => s.isLocal)
        const remoteActive = speakers.some(s => !s.isLocal)
        setUserSpeaking(localActive)
        setAgentSpeaking(remoteActive)
      })

      room.on(RoomEvent.TranscriptionReceived, (segments: TranscriptionSegment[], participant) => {
        const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        setLines(current => {
          const next = [...current]
          for (const segment of segments) {
            const isUser = participant?.isLocal ?? false
            const item: TranscriptLine = {
              id: segment.id,
              speaker: isUser ? 'You' : 'Rachel',
              text: segment.text,
              final: segment.final,
              timestamp: now,
            }
            const existing = next.findIndex(line => line.id === item.id)
            if (existing >= 0) next[existing] = item
            else next.push(item)
          }
          return next.slice(-40)
        })
      })

      room.on(RoomEvent.TrackSubscribed, (track, _publication, participant) => {
        if (track.kind === Track.Kind.Audio && !participant.isLocal) {
          attachRemoteAudio(track)
        }
      })
      room.on(RoomEvent.TrackUnsubscribed, track => {
        const trackId = track.sid ?? track.mediaStreamTrack.id
        const audio = audioElements.current.get(trackId)
        if (!audio) return
        track.detach(audio)
        audio.pause()
        audio.remove()
        audioElements.current.delete(trackId)
      })

      // 3. Connect WebRTC peer connection
      await room.connect(creds.server_url, creds.participant_token)
      await room.startAudio().catch(err => {
        console.warn('Could not initialize browser audio playback:', err)
        setError('The browser could not initialize Rachel’s audio. End the call, click the page, and reconnect to enable playback.')
      })

      // Attach any remote tracks already published
      for (const p of room.remoteParticipants.values()) {
        if (!p.isLocal) {
          for (const pub of p.audioTrackPublications.values()) {
            if (pub.track && pub.track.kind === Track.Kind.Audio) {
              attachRemoteAudio(pub.track)
            }
          }
        }
      }

      // 4. Publish microphone audio track to the agent with acoustic echo cancellation
      await room.localParticipant.setMicrophoneEnabled(true, {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      })
      setConnected(true)
    } catch (cause) {
      await room?.disconnect().catch(() => undefined)
      roomRef.current = null
      setError(cause instanceof Error ? cause.message : 'Could not connect to the voice receptionist.')
      setConnected(false)
    } finally {
      setConnecting(false)
    }
  }

  return (
    <section className="voice-card lumora-glass-card" aria-labelledby="voice-title">
      {/* Voice Card Header */}
      <div className="voice-heading">
        <div>
          <span className="eyebrow">
            <span className="eyebrow-dot" />
            LUMORA CLINICAL INTELLIGENCE
          </span>
          <h2 id="voice-title">Rachel — Voice Receptionist</h2>
          <p>Real-time conversational medical scheduling & clinic triage</p>
        </div>
        <div className="voice-header-badges">
          <span className={`live-pill ${connected ? 'is-live' : ''}`}>
            <i />
            {connected ? `Connected • ${formatDuration(callDuration)}` : 'Ready to Connect'}
          </span>
        </div>
      </div>

      {/* Lumora Liquid Voice Orb */}
      <div className="orb-stage">
        <LiquidVoiceOrb
          state={orbState}
          size={290}
          onClick={!connected && !connecting ? connect : undefined}
        />
        {/* Real-time Audio Spectrum Monitor */}
        <AudioSpectrum
          active={connected}
          isSpeaking={userSpeaking || agentSpeaking}
          speakerLabel={userSpeaking ? 'You' : agentSpeaking ? 'Rachel' : 'Standby'}
        />
      </div>

      {/* Quick Prompt Voice Suggestion Chips */}
      <PromptChips
        activeCall={connected}
        onSelectPrompt={!connected ? () => void connect() : undefined}
      />

      {/* Kinetic Voice Action Controls */}
      <div className="voice-actions-bar">
        {!connected ? (
          <button
            className="button button-call-start"
            disabled={connecting}
            onClick={() => void connect()}
            id="btn-start-call"
          >
            {connecting ? (
              <>
                <span className="spinner" />
                <span>Connecting to Rachel…</span>
              </>
            ) : (
              <>
                <svg className="btn-icon" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M20.01 15.38c-1.23 0-2.42-.2-3.53-.56a.977.977 0 0 0-1.01.24l-2.2 2.2a15.053 15.053 0 0 1-6.59-6.59l2.2-2.21a.96.96 0 0 0 .25-1.01A11.36 11.36 0 0 1 8.5 3.93c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1 0 9.39 7.61 17 17 17 .55 0 1-.45 1-1v-3.55c0-.55-.45-1-1-1z" />
                </svg>
                <span>Start Voice Call</span>
              </>
            )}
          </button>
        ) : (
          <div className="connected-controls-group">
            {/* Mute/Unmute Mic Button */}
            <button
              className={`button button-mute ${micMuted ? 'is-muted' : ''}`}
              onClick={() => void toggleMute()}
              title={micMuted ? 'Unmute microphone' : 'Mute microphone'}
            >
              <svg className="btn-icon" viewBox="0 0 24 24" fill="currentColor">
                {micMuted ? (
                  <path d="M19 11h-1.7c0 .74-.16 1.43-.43 2.05l1.23 1.23c.56-.98.9-2.09.9-3.28zm-4.02.17c0-.06.02-.11.02-.17V5c0-1.66-1.34-3-3-3S9 3.34 9 5v.18l5.98 5.99zM4.27 3L3 4.27l6.01 6.01V11c0 1.66 1.33 3 2.99 3 .22 0 .44-.03.65-.08l1.66 1.66c-.71.33-1.5.52-2.31.52-2.76 0-5.3-2.1-5.3-5.1H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c.91-.13 1.77-.45 2.54-.9l4.19 4.18 1.27-1.27L4.27 3z" />
                ) : (
                  <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.91-3c-.49 0-.9.36-.98.85C16.52 14.2 14.47 16 12 16s-4.52-1.8-4.93-4.15c-.08-.49-.49-.85-.98-.85-.61 0-1.09.54-1 1.14.49 3 2.89 5.35 5.91 5.78V20c0 .55.45 1 1 1s1-.45 1-1v-2.08c3.02-.43 5.42-2.78 5.91-5.78.1-.6-.39-1.14-1-1.14z" />
                )}
              </svg>
              <span>{micMuted ? 'Unmute' : 'Mute'}</span>
            </button>

            {/* End Call Button */}
            <button
              className="button button-call-stop"
              onClick={() => void disconnect()}
              id="btn-end-call"
            >
              <svg className="btn-icon" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 9c-1.6 0-3.15.25-4.6.72v3.1c0 .39-.23.74-.56.9-.98.49-1.87 1.12-2.66 1.85-.18.18-.43.28-.7.28-.28 0-.53-.11-.71-.29L.29 13.08a.996.996 0 0 1 0-1.41C3.28 8.91 7.42 7.5 12 7.5s8.72 1.41 11.71 4.17c.39.39.39 1.02 0 1.41l-2.48 2.48c-.18.18-.43.29-.71.29-.27 0-.52-.11-.7-.28-.79-.74-1.69-1.36-2.67-1.85-.33-.16-.56-.5-.56-.9v-3.1C15.15 9.25 13.6 9 12 9z" />
              </svg>
              <span>End Call</span>
            </button>
          </div>
        )}
      </div>

      <div className="voice-security-note">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 10.99h7c-.53 4.12-3.28 7.79-7 8.94V12H5V6.3l7-3.11v8.8z"/></svg>
        <span>End-to-End Encrypted WebRTC • HIPAA Compliant Audio Channel</span>
      </div>

      {error && (
        <div className="inline-error" role="alert">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
          <span>{error}</span>
        </div>
      )}

      {callEndedAlert && (
        <div className="call-sync-toast" role="status">
          <div className="toast-content">
            <span className="toast-dot" />
            <span>Call ended: Any scheduled appointment has been verified in the clinic DB and synchronized with Google Calendar.</span>
          </div>
          {onOpenCalendar && (
            <button className="btn-view-cal-toast" onClick={onOpenCalendar}>
              View Live Doctor Schedule ➔
            </button>
          )}
          <button
            className="btn-close-toast"
            onClick={() => setCallEndedAlert(false)}
            aria-label="Dismiss alert"
          >
            ✕
          </button>
        </div>
      )}

      {/* Live Conversation Transcript */}
      <div className="transcript-section">
        <div className="transcript-header">
          <div className="transcript-title-group">
            <span className="transcript-title">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M20 2H4c-1.1 0-1.99.9-1.99 2L2 22l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM6 9h12v2H6V9zm8 5H6v-2h8v2zm4-6H6V6h12v2z"/></svg>
              Live Conversation Transcript
            </span>
            <span className="transcript-count">{lines.length} turns</span>
          </div>

          <div className="transcript-actions">
            {lines.length > 0 && (
              <>
                <button
                  className="transcript-action-btn"
                  onClick={() => void copyTranscript()}
                  title="Copy full transcript"
                >
                  {copied ? '✓ Copied' : '📋 Copy'}
                </button>
                <button
                  className="transcript-action-btn"
                  onClick={clearTranscript}
                  title="Clear transcript"
                >
                  ✕ Clear
                </button>
              </>
            )}
          </div>
        </div>

        <div
          className="transcript-stream"
          ref={transcriptStreamRef}
          aria-live="polite"
          aria-label="Conversation stream"
        >
          {lines.length === 0 ? (
            <div className="transcript-empty-state">
              <div className="empty-sparkle">✦</div>
              <p>Your spoken conversation with Rachel will stream here with real-time word-level synchronization.</p>
              <small>Click "Start Voice Call" above to begin.</small>
            </div>
          ) : (
            <>
              {lines.map(line => (
                <div
                  className={`transcript-bubble ${line.speaker === 'You' ? 'is-user' : 'is-agent'}`}
                  key={line.id}
                >
                  <div className="bubble-header">
                    <div className="speaker-avatar">{line.speaker === 'You' ? '👤' : '👩‍⚕️'}</div>
                    <strong className="speaker-name">{line.speaker === 'You' ? 'You' : 'Rachel (Receptionist)'}</strong>
                    {!line.final && <span className="bubble-streaming-badge">streaming…</span>}
                    <span className="bubble-time">{line.timestamp}</span>
                  </div>
                  <p className="bubble-text">{line.text}</p>
                </div>
              ))}
              <div ref={transcriptEndRef} />
            </>
          )}
        </div>
      </div>
    </section>
  )
}
