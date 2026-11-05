import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type EvaluationDetail,
  type EvaluationRun,
  type EvaluationScenario,
  type CallEvaluation,
  callScopeLabel,
  describeCallFinding,
} from './api'

function readTrace(value?: string | null): {
  transcript?: Array<{ speaker: string; text: string }>
  tool_calls?: Array<{ tool: string; params?: Record<string, unknown>; result?: unknown }>
  notes?: string[]
  db_state_details?: Record<string, unknown>
} {
  if (!value) return {}
  try {
    return JSON.parse(value) as ReturnType<typeof readTrace>
  } catch {
    return {}
  }
}

interface DashboardProps {
  onStartLiveCallTest: () => Promise<void>
}

export default function Dashboard({ onStartLiveCallTest }: DashboardProps) {
  const [scenarios, setScenarios] = useState<EvaluationScenario[]>([])
  const [runs, setRuns] = useState<EvaluationRun[]>([])
  const [liveCallEvaluations, setLiveCallEvaluations] = useState<CallEvaluation[]>([])
  const [selected, setSelected] = useState<EvaluationDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [startingLiveTest, setStartingLiveTest] = useState(false)
  const [runningLabel, setRunningLabel] = useState('')
  const [categoryFilter, setCategoryFilter] = useState<string>('ALL')
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    setError('')
    try {
      const [scenarioRows, runRows, callEvaluationRows] = await Promise.all([
        api<EvaluationScenario[]>('/api/v1/evaluations/scenarios'),
        api<EvaluationRun[]>('/api/v1/evaluations/runs'),
        api<CallEvaluation[]>('/api/v1/evaluations/live-calls'),
      ])
      setScenarios(scenarioRows)
      setRuns(runRows)
      setLiveCallEvaluations(callEvaluationRows)
      if (runRows.length > 0 && !selected) {
        const first = await api<EvaluationDetail>(`/api/v1/evaluations/runs/${runRows[0].evaluation_run_id}`)
        setSelected(first)
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load evaluation data.')
    } finally {
      setLoading(false)
    }
  }, [selected])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const runSuite = async (scenarioIds?: string[]) => {
    setRunning(true)
    setRunningLabel(
      scenarioIds?.length === 1
        ? `Running ${scenarioIds[0]}…`
        : `Running ${scenarios.length} scenarios…`,
    )
    setError('')
    try {
      const result = await api<EvaluationDetail & { reports: unknown[] }>('/api/v1/evaluations/run', {
        method: 'POST',
        body: JSON.stringify(scenarioIds ? { scenario_ids: scenarioIds } : {}),
      })
      await refresh()
      const details = await api<EvaluationDetail>(`/api/v1/evaluations/runs/${result.evaluation_run_id}`)
      setSelected(details)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Evaluation run failed.')
    } finally {
      setRunning(false)
      setRunningLabel('')
    }
  }

  const startLiveTest = async () => {
    setStartingLiveTest(true)
    setError('')
    try {
      await onStartLiveCallTest()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not start the live call test.')
    } finally {
      setStartingLiveTest(false)
    }
  }

  const openRun = async (id: string) => {
    try {
      setSelected(await api<EvaluationDetail>(`/api/v1/evaluations/runs/${id}`))
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load this run.')
    }
  }

  const categories = ['ALL', ...Array.from(new Set(scenarios.map(s => s.category)))]
  const filteredScenarios = categoryFilter === 'ALL'
    ? scenarios
    : scenarios.filter(s => s.category === categoryFilter)

  const successRate = selected && selected.total_scenarios
    ? Math.round((selected.passed_scenarios * 100) / selected.total_scenarios)
    : null

  return (
    <section className="dashboard lumora-glass-card" aria-labelledby="dashboard-title">
      {/* Header */}
      <div className="section-heading">
        <div>
          <span className="eyebrow">
            <span className="eyebrow-dot" />
            BENCHMARK & CLINICAL SAFETY
          </span>
          <h2 id="dashboard-title">Autonomous Evaluation Engine</h2>
          <p>Scripted scenarios are sandbox simulations. Live calls are checked against the database they actually use.</p>
        </div>
        <button
          className="button button-call-start"
          disabled={running || loading}
          onClick={() => void runSuite()}
          id="btn-run-all"
        >
          {running ? (
            <>
              <span className="spinner" />
              <span>{runningLabel || 'Running evaluation…'}</span>
            </>
          ) : (
            <>
              <span>▶ Run Full Test Suite ({scenarios.length} scenarios)</span>
            </>
          )}
        </button>
      </div>

      <div className="evaluation-notice" role="note">
        <strong>Scripted scenarios — not real calls.</strong>
        <span>
          These predefined conversations use a disposable database and mock calendar. They do not use your
          microphone, create clinic appointments, or write to Google Calendar.
        </span>
      </div>

      <section className="live-call-test-card" aria-labelledby="live-call-test-title">
        <div>
          <span className="eyebrow"><span className="eyebrow-dot" />LIVE VOICE PIPELINE</span>
          <h3 id="live-call-test-title">Live call appointment checks</h3>
          <p>
            <strong>Isolated test call:</strong> click below to use a separate test database and mock calendar.
            It cannot change clinic appointments. <strong>Regular voice calls:</strong> they are checked
            automatically against the clinic database; the checker does not alter appointment records or
            fetch Google Calendar data. Bookings, reschedules, and cancellations are checked; both checks
            discard the transcript after verification.
          </p>
        </div>
        <button
          className="button button-run-single"
          disabled={startingLiveTest}
          onClick={() => void startLiveTest()}
        >
          {startingLiveTest ? 'Preparing isolated test…' : 'Start isolated test call'}
        </button>
        {liveCallEvaluations.length > 0 && (
          <ul className="live-call-test-history" aria-label="Recent live call checks">
            {liveCallEvaluations.slice(0, 8).map(evaluation => (
              <li key={evaluation.call_evaluation_id}>
                <span className={`status-dot ${evaluation.execution_status.toLowerCase()}`} />
                <strong>{evaluation.execution_status.replace('_', ' ')}</strong>
                <span className="evaluation-scope">{callScopeLabel(evaluation.data_scope)}</span>
                <span>{new Date(evaluation.started_at).toLocaleString()}</span>
                {evaluation.action_type && (
                  <small>{evaluation.action_type.replaceAll('_', ' ')} · {evaluation.action_count} appointment(s)</small>
                )}
                {evaluation.findings.length > 0 && (
                  <small>{evaluation.findings.map(describeCallFinding).join(' ')}</small>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <ol className="evaluation-steps" aria-label="What each evaluation verifies">
        <li><span>1</span><strong>Scripted suite</strong><small>Fake conversations and disposable data</small></li>
        <li><span>2</span><strong>Isolated call</strong><small>Real voice pipeline, test database only</small></li>
        <li><span>3</span><strong>Regular call</strong><small>Automatic, read-only appointment check</small></li>
      </ol>

      {error && (
        <div className="inline-error" role="alert">
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="loading-card">
          <span className="spinner" />
          <span>Synchronizing evaluation records…</span>
        </div>
      ) : (
        <>
          {/* Key Metric Gauges */}
          <div className="metric-grid">
            <article className="metric-card">
              <span className="metric-label">DEFINED SCENARIOS</span>
              <strong className="metric-val">{scenarios.length}</strong>
              <small className="metric-sub">Available deterministic scenarios</small>
            </article>

            <article className="metric-card">
              <span className="metric-label">EVALUATION RUNS</span>
              <strong className="metric-val">{runs.length}</strong>
              <small className="metric-sub">Saved tester executions</small>
            </article>

            <article className="metric-card highlight">
              <span className="metric-label">OVERALL PASS RATE</span>
              <strong className="metric-val text-neon">
                {successRate === null ? '—' : `${successRate}%`}
              </strong>
              <small className="metric-sub">
                {selected
                  ? `${selected.passed_scenarios} passed · ${selected.failed_scenarios} failed`
                  : 'Select a run'}
              </small>
            </article>
          </div>

          {/* Two-Column Run Browser and Inspector */}
          <div className="dashboard-columns">
            {/* Left: Saved Runs */}
            <div className="panel lumora-panel">
              <div className="panel-heading">
                <h3>Previous Test Runs</h3>
                <button className="text-button" onClick={() => void refresh()}>
                  ↻ Refresh
                </button>
              </div>

              {runs.length === 0 ? (
                <p className="empty-state">No evaluation runs yet. Run the suite to benchmark the agent.</p>
              ) : (
                <ul className="run-list">
                  {runs.map(run => (
                    <li key={run.evaluation_run_id}>
                      <button
                        className={`run-row ${selected?.evaluation_run_id === run.evaluation_run_id ? 'selected' : ''}`}
                        onClick={() => void openRun(run.evaluation_run_id)}
                      >
                        <span className={`status-dot ${run.execution_status.toLowerCase()}`} />
                        <span className="run-main">
                          <strong>{run.evaluation_run_id}</strong>
                          <small>{new Date(run.started_at).toLocaleString()}</small>
                        </span>
                        <span className="run-score">
                          {run.passed_scenarios} / {run.total_scenarios} passed
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Right: Inspector */}
            <div className="panel lumora-panel inspector">
              <div className="panel-heading">
                <h3>Selected Run Results</h3>
                {selected && (
                  <span className={`status-label ${selected.execution_status.toLowerCase()}`}>
                    {selected.execution_status}
                  </span>
                )}
              </div>

              {!selected ? (
                <p className="empty-state">Choose an evaluation run on the left to inspect scenario outcomes.</p>
              ) : selected.results.length === 0 ? (
                <p className="empty-state">This run has no recorded scenario results.</p>
              ) : (
                <ul className="result-list">
                  {selected.results.map(result => {
                    const trace = readTrace(result.trace_summary)
                    return (
                      <li key={result.scenario_id} className="result-item">
                        <div className="result-top">
                          <strong className="result-id">{result.scenario_id}</strong>
                          <span className={`status-label ${result.execution_status.toLowerCase()}`}>
                            {result.execution_status}
                          </span>
                        </div>

                        <div className="result-metrics">
                          <span className={result.task_completion ? 'metric-ok' : 'metric-fail'}>
                            Outcome: {result.task_completion ? '✓ Verified' : '✕ Failed'}
                          </span>
                          <span className="metric-info">
                            Expected tools: {Math.round(result.tool_accuracy * 100)}%
                          </span>
                          <span className={result.safety_result ? 'metric-ok' : 'metric-fail'}>
                            Safety: {result.safety_result ? '✓ Compliant' : '✕ Violated'}
                          </span>
                        </div>

                        {result.failure_category && (
                          <small className="failure-category">Reason: {result.failure_category}</small>
                        )}

                        {trace.db_state_details && Object.keys(trace.db_state_details).length > 0 && (
                          <div className="verified-details">
                            <strong>Verified test data</strong>
                            <dl>
                              {Object.entries(trace.db_state_details).map(([key, value]) => (
                                <div key={key}>
                                  <dt>{key.replaceAll('_', ' ')}</dt>
                                  <dd>{typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
                                    ? String(value)
                                    : JSON.stringify(value)}</dd>
                                </div>
                              ))}
                            </dl>
                          </div>
                        )}

                        {trace.notes && trace.notes.length > 0 && (
                          <ul className="evaluation-notes">
                            {trace.notes.map((note, index) => <li key={`${result.scenario_id}-note-${index}`}>{note}</li>)}
                          </ul>
                        )}

                        {(trace.transcript?.length || trace.tool_calls?.length) && (
                          <details className="trace-details">
                            <summary>Inspect simulated dialogue & tool calls</summary>
                            <div className="trace-content">
                              {trace.transcript?.map((turn, index) => (
                                <p className="trace-turn" key={`${turn.speaker}-${index}`}>
                                  <strong>{turn.speaker}:</strong> {turn.text}
                                </p>
                              ))}
                              {trace.tool_calls?.map((call, index) => (
                                <p className="trace-tool" key={`${call.tool}-${index}`}>
                                  ⚙️ Tool: <strong>{call.tool}</strong> · inputs: {JSON.stringify(call.params || {})}
                                  {call.result !== undefined && <> · result: {JSON.stringify(call.result)}</>}
                                </p>
                              ))}
                            </div>
                          </details>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </div>

          {/* Scenario Library Explorer */}
          <div className="scenario-library-box">
            <div className="library-header">
              <div>
                <span className="eyebrow">
                <span className="eyebrow-dot" />
                  TEST SCENARIOS
                </span>
                <p className="library-description">Choose one scripted case or run the complete suite. These do not place real calls or calendar bookings.</p>
              </div>
              <div className="filter-pills">
                {categories.map(cat => (
                  <button
                    key={cat}
                    className={`filter-pill ${categoryFilter === cat ? 'active' : ''}`}
                    onClick={() => setCategoryFilter(cat)}
                    aria-pressed={categoryFilter === cat}
                  >
                    {cat === 'ALL' ? `All (${scenarios.length})` : cat.replace(/^Category [A-Z]: /, '')}
                  </button>
                ))}
              </div>
            </div>

            <ul className="scenario-list">
              {filteredScenarios.map(sc => (
                <li key={sc.scenario_id} className="scenario-card">
                  <div className="scenario-meta">
                    <strong className="scenario-name">
                      {sc.scenario_id} · {sc.name}
                    </strong>
                    <small className="scenario-cat">
                      {sc.category} · Expected outcome: {sc.expected_outcome}
                      {sc.is_safety_critical && <span className="safety-tag">Safety Critical</span>}
                    </small>
                    <p className="scenario-description">{sc.description}</p>
                  </div>
                  <button
                    className="button button-run-single"
                    disabled={running}
                    onClick={() => void runSuite([sc.scenario_id])}
                  >
                    {running ? 'Running…' : 'Run scenario'}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </section>
  )
}
