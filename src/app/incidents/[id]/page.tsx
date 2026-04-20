'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  ArrowLeft, CheckCircle2, Clock, Loader2, Zap,
  Activity, AlertCircle, PlayCircle, Wrench, MessageSquare, Flag,
  type LucideIcon,
} from 'lucide-react'

import {
  api, ApiError, connectIncidentEvents,
  type Incident, type IncidentEvent, type WorkflowExecution,
} from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

const SEVERITY_STYLES: Record<Incident['severity'], { text: string; badge: string; dot: string; label: string }> = {
  critical: { text: 'text-[#E85A6B]', badge: 'bg-[#E85A6B]/10 text-[#E85A6B] border-[#E85A6B]/20', dot: 'bg-[#E85A6B]', label: 'Critical' },
  high:     { text: 'text-[#E8935A]', badge: 'bg-[#E8935A]/10 text-[#E8935A] border-[#E8935A]/20', dot: 'bg-[#E8935A]', label: 'High' },
  medium:   { text: 'text-[#F4B97A]', badge: 'bg-[#F4B97A]/10 text-[#F4B97A] border-[#F4B97A]/20', dot: 'bg-[#F4B97A]', label: 'Medium' },
  low:      { text: 'text-[#6B7FE8]', badge: 'bg-[#6B7FE8]/10 text-[#6B7FE8] border-[#6B7FE8]/20', dot: 'bg-[#6B7FE8]', label: 'Low' },
  info:     { text: 'text-[#6B7588]', badge: 'bg-[#6B7588]/10 text-[#6B7588] border-[#6B7588]/20', dot: 'bg-[#6B7588]', label: 'Info' },
}

const STATUS_STYLES: Record<Incident['status'], { label: string; className: string }> = {
  open:         { label: 'Open',          className: 'text-[#E85A6B] bg-[#E85A6B]/10 border-[#E85A6B]/20' },
  acknowledged: { label: 'Acknowledged',  className: 'text-[#E8935A] bg-[#E8935A]/10 border-[#E8935A]/20' },
  in_progress:  { label: 'In Progress',   className: 'text-[#6B7FE8] bg-[#6B7FE8]/10 border-[#6B7FE8]/20' },
  resolved:     { label: 'Resolved',      className: 'text-[#4ADE80] bg-[#4ADE80]/10 border-[#4ADE80]/20' },
  closed:       { label: 'Closed',        className: 'text-[#6B7588] bg-[#6B7588]/10 border-[#6B7588]/20' },
  suppressed:   { label: 'Suppressed',    className: 'text-[#6B7588] bg-[#6B7588]/10 border-[#6B7588]/20' },
}

const EVENT_ICON: Record<string, LucideIcon> = {
  created: Flag,
  signal_added: Activity,
  workflow_started: PlayCircle,
  step_completed: Wrench,
  step_failed: AlertCircle,
  acknowledged: MessageSquare,
  resolved: CheckCircle2,
  note_added: MessageSquare,
}

function formatTs(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function canMutate(role: string | undefined): boolean {
  return role === 'admin' || role === 'operator'
}

export default function IncidentDetailPage({ params }: { params: { id: string } }) {
  const router = useRouter()
  const { user } = useAuth()
  const [incident, setIncident] = useState<Incident | null>(null)
  const [events, setEvents] = useState<IncidentEvent[]>([])
  const [workflow, setWorkflow] = useState<WorkflowExecution | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'ack' | 'resolve' | null>(null)
  const cleanupRef = useRef<(() => void) | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [inc, evs] = await Promise.all([
        api.incidents.get(params.id),
        api.incidents.events(params.id),
      ])
      setIncident(inc)
      setEvents(evs)
      if (inc.temporal_workflow_id) {
        try {
          const wf = await api.workflows.get(inc.temporal_workflow_id)
          setWorkflow(wf)
        } catch {
          // Temporal may be unreachable — don't fail the whole page.
        }
      }
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : 'Failed to load incident'
      setError(detail)
    } finally {
      setLoading(false)
    }
  }, [params.id])

  useEffect(() => { load() }, [load])

  // Live updates: any event for this incident refreshes both the row and the timeline.
  useEffect(() => {
    let cancelled = false
    try {
      const cleanup = connectIncidentEvents(async (event) => {
        if (cancelled) return
        if (event.event_type === 'ready' || event.event_type === 'heartbeat') return
        const incidentId = event.data?.incident_id as string | undefined
        if (incidentId !== params.id) return
        try {
          const [inc, evs] = await Promise.all([
            api.incidents.get(params.id),
            api.incidents.events(params.id),
          ])
          if (!cancelled) {
            setIncident(inc)
            setEvents(evs)
          }
        } catch {
          // Silent; next user-initiated load will reconcile.
        }
      }, 'all')
      cleanupRef.current = cleanup
    } catch {
      // No token — AuthGuard will redirect.
    }
    return () => {
      cancelled = true
      cleanupRef.current?.()
      cleanupRef.current = null
    }
  }, [params.id])

  const handleAcknowledge = async () => {
    setBusy('ack')
    try {
      const inc = await api.incidents.acknowledge(params.id)
      setIncident(inc)
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : 'Acknowledge failed'
      setError(detail)
    } finally {
      setBusy(null)
    }
  }

  const handleResolve = async () => {
    setBusy('resolve')
    try {
      const inc = await api.incidents.resolve(params.id)
      setIncident(inc)
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : 'Resolve failed'
      setError(detail)
    } finally {
      setBusy(null)
    }
  }

  const sev = incident ? SEVERITY_STYLES[incident.severity] : null
  const stat = incident ? STATUS_STYLES[incident.status] : null
  const showActions = canMutate(user?.role)

  return (
    <div className="min-h-screen bg-[#0D0F1A] grid-bg">
      <header className="border-b border-[#2A3248] px-6 py-3 flex items-center justify-between sticky top-0 z-10 glass">
        <div className="flex items-center gap-3 min-w-0">
          <button onClick={() => router.push('/incidents')} className="text-[#7b8db0] hover:text-white transition-colors p-1 rounded hover:bg-[#1e2d4a]">
            <ArrowLeft size={16} />
          </button>
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-[#E8935A] to-[#6B7FE8] flex items-center justify-center">
            <Zap size={14} className="text-white" />
          </div>
          <span className="font-bold text-[#F4F5F8] tracking-tight">Auto<span className="gradient-text">Mend</span></span>
          <span className="text-[#3a4a6b] font-mono text-xs">/</span>
          <span className="text-sm font-medium text-[#7b8db0]">Incidents</span>
          {incident && (
            <>
              <span className="text-[#3a4a6b] font-mono text-xs">/</span>
              <span className="text-sm font-semibold text-white truncate">{incident.incident_type}</span>
            </>
          )}
        </div>
        {incident && showActions && incident.status !== 'resolved' && incident.status !== 'closed' && (
          <div className="flex items-center gap-2">
            {incident.status === 'open' && (
              <button
                onClick={handleAcknowledge}
                disabled={busy !== null}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-[#E8935A]/40 text-[#E8935A] rounded-lg hover:bg-[#E8935A]/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {busy === 'ack' ? <Loader2 size={12} className="animate-spin" /> : <MessageSquare size={12} />}
                Acknowledge
              </button>
            )}
            <button
              onClick={handleResolve}
              disabled={busy !== null}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-[#4ADE80]/20 border border-[#4ADE80]/40 text-[#4ADE80] rounded-lg hover:bg-[#4ADE80]/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {busy === 'resolve' ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
              Resolve
            </button>
          </div>
        )}
      </header>

      {error && (
        <div className="px-4 py-2 text-xs text-[#E85A6B] border-b border-[#E85A6B]/20 bg-[#E85A6B]/5">
          {error}
        </div>
      )}

      <main className="max-w-5xl mx-auto px-6 py-8">
        {loading || !incident ? (
          <div className="text-center py-20">
            <Loader2 size={24} className="animate-spin text-[#6B7588] mx-auto mb-4" />
            <p className="text-sm text-[#6B7588]">Loading incident…</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Main column */}
            <div className="lg:col-span-2 space-y-6">
              {/* Summary card */}
              <div className="bg-[#1A1F2E] border border-[#2A3248] rounded-xl p-5">
                <div className="flex items-start gap-3 mb-4">
                  <div className={`w-2 h-2 rounded-full ${sev!.dot} mt-2 shrink-0`} />
                  <div className="flex-1">
                    <h1 className="text-lg font-bold text-[#F4F5F8]">{incident.incident_type}</h1>
                    <p className="text-xs text-[#6B7588] font-mono mt-0.5">{incident.incident_key}</p>
                  </div>
                  <div className="flex flex-col items-end gap-2 shrink-0">
                    <span className={`text-xs px-2 py-0.5 rounded-full border ${sev!.badge}`}>{sev!.label}</span>
                    <span className={`text-xs px-2 py-0.5 rounded-full border capitalize ${stat!.className}`}>{stat!.label}</span>
                  </div>
                </div>

                <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs">
                  <div>
                    <dt className="text-[#6B7588] uppercase tracking-wider mb-0.5">Created</dt>
                    <dd className="text-[#F4F5F8]">{formatTs(incident.created_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-[#6B7588] uppercase tracking-wider mb-0.5">Updated</dt>
                    <dd className="text-[#F4F5F8]">{formatTs(incident.updated_at)}</dd>
                  </div>
                  {incident.resolved_at && (
                    <div>
                      <dt className="text-[#6B7588] uppercase tracking-wider mb-0.5">Resolved</dt>
                      <dd className="text-[#4ADE80]">{formatTs(incident.resolved_at)}</dd>
                    </div>
                  )}
                  <div>
                    <dt className="text-[#6B7588] uppercase tracking-wider mb-0.5">Sources</dt>
                    <dd className="text-[#F4F5F8] font-mono">{incident.sources.join(', ') || '—'}</dd>
                  </div>
                </dl>
              </div>

              {/* Entity */}
              <div className="bg-[#1A1F2E] border border-[#2A3248] rounded-xl p-5">
                <h2 className="text-sm font-semibold text-[#F4F5F8] mb-3">Entity</h2>
                {Object.keys(incident.entity).length === 0 ? (
                  <p className="text-xs text-[#6B7588]">No entity info</p>
                ) : (
                  <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                    {Object.entries(incident.entity).map(([k, v]) => (
                      <div key={k}>
                        <dt className="text-[#6B7588] uppercase tracking-wider mb-0.5">{k}</dt>
                        <dd className="text-[#F4F5F8] font-mono truncate">{String(v)}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </div>

              {/* Evidence */}
              <div className="bg-[#1A1F2E] border border-[#2A3248] rounded-xl p-5">
                <h2 className="text-sm font-semibold text-[#F4F5F8] mb-3">Evidence</h2>
                {Object.keys(incident.evidence).length === 0 ? (
                  <p className="text-xs text-[#6B7588]">No evidence recorded</p>
                ) : (
                  <pre className="text-xs text-[#c0cce0] bg-[#0D0F1A] border border-[#2A3248] rounded-lg p-3 overflow-x-auto font-mono leading-relaxed">
{JSON.stringify(incident.evidence, null, 2)}
                  </pre>
                )}
              </div>
            </div>

            {/* Side column */}
            <div className="space-y-6">
              {/* Workflow panel */}
              <div className="bg-[#1A1F2E] border border-[#2A3248] rounded-xl p-5">
                <h2 className="text-sm font-semibold text-[#F4F5F8] mb-3">Remediation Workflow</h2>
                {!incident.temporal_workflow_id ? (
                  <p className="text-xs text-[#6B7588]">No workflow started for this incident.</p>
                ) : (
                  <dl className="text-xs space-y-2">
                    <div>
                      <dt className="text-[#6B7588] uppercase tracking-wider mb-0.5">Workflow ID</dt>
                      <dd className="text-[#F4F5F8] font-mono break-all">{incident.temporal_workflow_id}</dd>
                    </div>
                    {incident.temporal_run_id && (
                      <div>
                        <dt className="text-[#6B7588] uppercase tracking-wider mb-0.5">Run ID</dt>
                        <dd className="text-[#F4F5F8] font-mono break-all">{incident.temporal_run_id}</dd>
                      </div>
                    )}
                    {workflow ? (
                      <>
                        <div>
                          <dt className="text-[#6B7588] uppercase tracking-wider mb-0.5">Status</dt>
                          <dd className="text-[#F4F5F8]">{workflow.status}</dd>
                        </div>
                        {workflow.start_time && (
                          <div>
                            <dt className="text-[#6B7588] uppercase tracking-wider mb-0.5">Started</dt>
                            <dd className="text-[#F4F5F8]">{formatTs(workflow.start_time)}</dd>
                          </div>
                        )}
                        {workflow.close_time && (
                          <div>
                            <dt className="text-[#6B7588] uppercase tracking-wider mb-0.5">Closed</dt>
                            <dd className="text-[#F4F5F8]">{formatTs(workflow.close_time)}</dd>
                          </div>
                        )}
                      </>
                    ) : (
                      <p className="text-xs text-[#6B7588] pt-1">Temporal status unavailable.</p>
                    )}
                  </dl>
                )}
              </div>

              {/* Event timeline */}
              <div className="bg-[#1A1F2E] border border-[#2A3248] rounded-xl p-5">
                <h2 className="text-sm font-semibold text-[#F4F5F8] mb-4">Timeline</h2>
                {events.length === 0 ? (
                  <p className="text-xs text-[#6B7588]">No events yet</p>
                ) : (
                  <ol className="space-y-3">
                    {events.map((ev, i) => {
                      const Icon = EVENT_ICON[ev.event_type] ?? Activity
                      const isLast = i === events.length - 1
                      return (
                        <li key={ev.id} className="flex gap-3 relative">
                          {!isLast && <span className="absolute left-[11px] top-6 bottom-[-12px] w-px bg-[#2A3248]" />}
                          <div className="w-6 h-6 rounded-full bg-[#0D0F1A] border border-[#2A3248] flex items-center justify-center shrink-0 z-10">
                            <Icon size={12} className="text-[#E8935A]" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-xs font-medium text-[#F4F5F8] capitalize">{ev.event_type.replace(/_/g, ' ')}</p>
                            <p className="text-xs text-[#6B7588] flex items-center gap-1.5 mt-0.5">
                              <Clock size={10} /> {formatTs(ev.created_at)}
                              {ev.actor && <span className="font-mono">· {ev.actor}</span>}
                            </p>
                            {Object.keys(ev.payload ?? {}).length > 0 && (
                              <pre className="mt-1.5 text-[11px] text-[#c0cce0] bg-[#0D0F1A] border border-[#2A3248] rounded px-2 py-1 overflow-x-auto font-mono">
{JSON.stringify(ev.payload, null, 0)}
                              </pre>
                            )}
                          </div>
                        </li>
                      )
                    })}
                  </ol>
                )}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
