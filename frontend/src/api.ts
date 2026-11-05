export type EvaluationScenario = {
  scenario_id: string
  category: string
  name: string
  description: string
  expected_outcome: string
  is_safety_critical: boolean
}

export type EvaluationRun = {
  evaluation_run_id: string
  execution_status: string
  total_scenarios: number
  passed_scenarios: number
  failed_scenarios: number
  started_at: string
  completed_at?: string | null
}

export type EvaluationDetail = EvaluationRun & {
  results: Array<{
    scenario_id: string
    execution_status: string
    task_completion: boolean
    tool_accuracy: number
    safety_result: boolean
    latency_metrics: string
    failure_category?: string | null
    trace_summary?: string | null
  }>
}

export type CallEvaluation = {
  call_evaluation_id: string
  data_scope: 'TEST' | 'CLINIC'
  execution_status: string
  started_at: string
  completed_at?: string | null
  action_type?: string | null
  action_count: number
  findings: string[]
}

export type Doctor = {
  doctor_id: string
  doctor_name: string
  specialization: string
  calendar_id: string
  timezone: string
  active_status: boolean
  working_hours_start: string
  working_hours_end: string
}

export type Appointment = {
  appointment_id: string
  patient_id: string
  patient_name: string
  contact_number: string
  doctor_id: string
  doctor_name: string
  appointment_date: string
  start_time: string
  end_time: string
  appointment_type: string
  status: string
  calendar_event_id?: string | null
  message?: string
}

export type LiveKitCredentials = {
  server_url: string
  room_name: string
  participant_identity: string
  participant_token: string
  live_call_test_id?: string | null
  call_evaluation_id: string
  data_scope: 'TEST' | 'CLINIC'
}

export function callScopeLabel(scope: CallEvaluation['data_scope']): string {
  return scope === 'TEST' ? 'ISOLATED TEST DATA' : 'CLINIC DATABASE'
}

export function describeCallFinding(code: string): string {
  const descriptions: Record<string, string> = {
    transcript_missing: 'No usable call transcript was received, so this call could not be checked.',
    appointment_change_not_recorded_for_call: 'The transcript suggests a booking change, but no matching database change was recorded for this call.',
    appointment_record_missing: 'The appointment record linked to this call could not be found.',
    appointment_status_mismatch: 'The saved appointment status does not match the requested action.',
    database_details_do_not_match_action: 'The saved patient, doctor, date, or time does not match the appointment action.',
    patient_name_not_in_transcript: 'The saved patient name was not found in the call transcript.',
    doctor_not_confirmed_in_transcript: 'The doctor was not clearly confirmed in the receptionist’s response.',
    date_not_confirmed_in_transcript: 'The appointment date was not clearly confirmed in the receptionist’s response.',
    time_not_confirmed_in_transcript: 'The appointment time was not clearly confirmed in the receptionist’s response.',
    appointment_confirmation_not_heard: 'A clear confirmation of the appointment change was not found in the receptionist’s response.',
    test_worker_unavailable: 'The isolated test voice worker was unavailable.',
    voice_worker_unavailable: 'The voice worker was unavailable.',
  }
  return descriptions[code] ?? 'The call needs review.'
}

const apiBase = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = body?.detail
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function getAppointments(date?: string, doctorId?: string): Promise<Appointment[]> {
  const params = new URLSearchParams()
  if (date) params.append('appointment_date', date)
  if (doctorId) params.append('doctor_id', doctorId)
  const qs = params.toString() ? `?${params.toString()}` : ''
  return api<Appointment[]>(`/api/v1/appointments${qs}`)
}

export async function getDoctors(): Promise<Doctor[]> {
  return api<Doctor[]>('/api/v1/doctors')
}
