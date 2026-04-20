'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowLeft, CheckCircle2, Clock, Loader2,
  Zap, Activity, ChevronRight, AlertTriangle, FolderGit2,
} from 'lucide-react'

import {
  api, ApiError, connectIncidentEvents,
  type Incident,
} from '@/lib/api'

type IncidentStatus = Incident['status']
type Severity = Incident['severity']
type FilterValue = 'all' | IncidentStatus

const SEVERITY_STYLES: Record<Severity, { dot: string; text: string; badge: string; label: string }> = {
  critical: { dot: 'bg-[#E85A6B]',      text: 'text-[#E85A6B]',      badge: 'bg-[#E85A6B]/10 text-[#E85A6B] border-[#E85A6B]/20',      label: 'Critical' },
  high:     { dot: 'bg-[#E8935A]',      text: 'text-[#E8935A]',      badge: 'bg-[#E8935A]/10 text-[#E8935A] border-[#E8935A]/20',      label: 'High' },
  medium:   { dot: 'bg-[#F4B97A]',      text: 'text-[#F4B97A]',      badge: 'bg-[#F4B97A]/10 text-[#F4B97A] border-[#F4B97A]/20',      label: 'Medium' },
  low:      { dot: 'bg-[#6B7FE8]',      text: 'text-[#6B7FE8]',      badge: 'bg-[#6B7FE8]/10 text-[#6B7FE8] border-[#6B7FE8]/20',      label: 'Low' },
  info:     { dot: 'bg-[#6B7588]',      text: 'text-[#6B7588]',      badge: 'bg-[#6B7588]/10 text-[#6B7588] border-[#6B7588]/20',      label: 'Info' },
}

const STATUS_STYLES: Record<IncidentStatus, { label: string; className: string }> = {
  open:         { label: 'Open',          className: 'text-[#E85A6B] bg-[#E85A6B]/10 border-[#E85A6B]/20' },
  acknowledged: { label: 'Acknowledged',  className: 'text-[#E8935A] bg-[#E8935A]/10 border-[#E8935A]/20' },
  in_progress:  { label: 'In Progress',   className: 'text-[#6B7FE8] bg-[#6B7FE8]/10 border-[#6B7FE8]/20' },
  resolved:     { label: 'Resolved',      className: 'text-[#4ADE80] bg-[#4ADE80]/10 border-[#4ADE80]/20' },
  closed:       { label: 'Closed',        className: 'text-[#6B7588] bg-[#6B7588]/10 border-[#6B7588]/20' },
  suppressed:   { label: 'Suppressed',    className: 'text-[#6B7588] bg-[#6B7588]/10 border-[#6B7588]/20' },
}

function formatRelative(iso: string): string {
  const diff = Date.now() - Date.parse(iso)
  if (Number.isNaN(diff)) return iso
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.floor(hr / 24)
  return `${day}d ago`
}

function entitySummary(entity: Record<string, string>): string {
  const parts: string[] = []
  if (entity.namespace) parts.push(entity.namespace)
  if (entity.workload) parts.push(entity.workload)
  else if (entity.pod) parts.push(entity.pod)
  else if (entity.service) parts.push(entity.service)
  return parts.length ? parts.join(' / ') : Object.values(entity).slice(0, 2).join(' / ') || '—'
}

export default function IncidentsPage() {
  const router = useRouter()
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [stats, setStats] = useState<{ by_status: Record<string, number>; by_severity: Record<string, number> } | null>(null)
  const [filter, setFilter] = useState<FilterValue>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [liveConnected, setLiveConnected] = useState(false)
  const cleanupRef = useRef<(() => void) | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [list, s] = await Promise.all([
        api.incidents.list({ limit: 100 }),
        api.incidents.stats(),
      ])
      setIncidents(list)
      setStats(s)
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : 'Failed to load incidents'
      setError(detail)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // WebSocket for live updates. On any event, re-fetch the affected incident so
  // status/severity/updated_at stay authoritative. New incidents are prepended.
  useEffect(() => {
    let cancelled = false
    try {
      const cleanup = connectIncidentEvents(async (event) => {
        if (cancelled) return
        setLiveConnected(true)
        if (event.event_type === 'ready' || event.event_type === 'heartbeat') return

        const incidentId = event.data?.incident_id as string | undefined
        if (!incidentId) return

        if (event.event_type === 'incident_created') {
          try {
            const inc = await api.incidents.get(incidentId)
            if (!cancelled) {
              setIncidents(prev => (prev.some(i => i.id === inc.id) ? prev : [inc, ...prev]))
            }
          } catch {
            // Silent — list will be consistent on next refresh.
          }
          return
        }

        // Any other event type touching an incident we already have: refresh its row.
        try {
          const inc = await api.incidents.get(incidentId)
          if (!cancelled) {
            setIncidents(prev => prev.map(i => i.id === inc.id ? inc : i))
          }
        } catch {
          // Ignore — incident might have been deleted; list will reconcile on next load.
        }
      }, 'all')
      cleanupRef.current = cleanup
    } catch {
      // No token yet; AuthGuard should redirect.
    }
    return () => {
      cancelled = true
      cleanupRef.current?.()
      cleanupRef.current = null
    }
  }, [])

  const filtered = useMemo(() => {
    if (filter === 'all') return incidents
    return incidents.filter(i => i.status === filter)
  }, [incidents, filter])

  const counts = useMemo(() => {
    const c: Record<FilterValue, number> = {
      all: incidents.length,
      open: 0, acknowledged: 0, in_progress: 0, resolved: 0, closed: 0, suppressed: 0,
    }
    for (const i of incidents) c[i.status] = (c[i.status] ?? 0) + 1
    return c
  }, [incidents])

  const totalsBySeverity = stats?.by_severity ?? {}

  return (
    <div className="min-h-screen bg-[#0D0F1A] grid-bg">
      <header className="border-b border-[#2A3248] px-6 py-3 flex items-center justify-between sticky top-0 z-10 glass">
        <div className="flex items-center gap-3">
          <button onClick={() => router.push('/')} className="text-[#7b8db0] hover:text-white transition-colors p-1 rounded hover:bg-[#1e2d4a]">
            <ArrowLeft size={16} />
          </button>
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-[#E8935A] to-[#6B7FE8] flex items-center justify-center">
            <Zap size={14} className="text-white" />
          </div>
          <span className="font-bold text-[#F4F5F8] tracking-tight">Auto<span className="gradient-text">Mend</span></span>
          <nav className="ml-4 flex items-center gap-1">
            <Link href="/" className="text-xs px-3 py-1.5 rounded-lg text-[#6B7588] hover:text-[#F4F5F8] hover:bg-[#1A1F2E] transition-colors flex items-center gap-1.5">
              <FolderGit2 size={12} /> Projects
            </Link>
            <span className="text-xs px-3 py-1.5 rounded-lg bg-[#1A1F2E] border border-[#2A3248] text-[#F4F5F8] font-medium flex items-center gap-1.5">
              <AlertTriangle size={12} /> Incidents
            </span>
          </nav>
        </div>
        <div className="flex items-center gap-2">
          <div className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded-full border ${liveConnected ? 'border-[#4ADE80]/30 text-[#4ADE80]' : 'border-[#2A3248] text-[#6B7588]'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${liveConnected ? 'bg-[#4ADE80] animate-pulse' : 'bg-[#6B7588]'}`} />
            {liveConnected ? 'Live' : 'Connecting…'}
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8">
        {/* Stats cards */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
          {(['critical', 'high', 'medium', 'low', 'info'] as Severity[]).map(sev => {
            const style = SEVERITY_STYLES[sev]
            const n = totalsBySeverity[sev] ?? 0
            return (
              <div key={sev} className="bg-[#1A1F2E] border border-[#2A3248] rounded-xl px-4 py-3 flex items-center justify-between">
                <div>
                  <p className="text-xs text-[#6B7588] uppercase tracking-wider">{style.label}</p>
                  <p className={`text-xl font-bold ${style.text}`}>{n}</p>
                </div>
                <div className={`w-2 h-2 rounded-full ${style.dot}`} />
              </div>
            )
          })}
        </div>

        {/* Filter tabs */}
        <div className="flex items-center gap-3 mb-6 flex-wrap">
          <div className="flex gap-1 bg-[#1A1F2E] border border-[#2A3248] rounded-xl p-1">
            {(['all', 'open', 'acknowledged', 'in_progress', 'resolved'] as FilterValue[]).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1.5 text-xs rounded-lg capitalize transition-all ${
                  filter === f
                    ? 'bg-gradient-to-r from-[#E8935A]/20 to-[#F4B97A]/20 text-[#F4F5F8] border border-[#E8935A]/20'
                    : 'text-[#6B7588] hover:text-[#F4F5F8]'
                }`}
              >
                {f.replace('_', ' ')} <span className="opacity-60 ml-1">{counts[f]}</span>
              </button>
            ))}
          </div>
          <span className="text-xs text-[#6B7588] ml-auto">{filtered.length} incident{filtered.length !== 1 ? 's' : ''}</span>
        </div>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-xl border border-[#E85A6B]/30 bg-[#E85A6B]/5 text-xs text-[#E85A6B]">
            {error}
          </div>
        )}

        {loading ? (
          <div className="text-center py-20">
            <Loader2 size={24} className="animate-spin text-[#6B7588] mx-auto mb-4" />
            <p className="text-sm text-[#6B7588]">Loading incidents…</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <div className="w-16 h-16 rounded-2xl border border-dashed border-[#2A3248] flex items-center justify-center mx-auto mb-4">
              <CheckCircle2 size={24} className="text-[#2A3248]" />
            </div>
            <p className="text-sm font-medium text-[#6B7588]">
              {incidents.length === 0 ? 'No incidents yet' : 'No incidents match this filter'}
            </p>
            <p className="text-xs mt-1 text-[#2A3248]">
              {incidents.length === 0 ? 'When your ML services raise alerts, they will appear here' : 'Try another status'}
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map(inc => {
              const sev = SEVERITY_STYLES[inc.severity]
              const stat = STATUS_STYLES[inc.status]
              return (
                <button
                  key={inc.id}
                  onClick={() => router.push(`/incidents/${inc.id}`)}
                  className="w-full bg-[#1A1F2E] border border-[#2A3248] rounded-xl px-4 py-3 hover:border-[#E8935A]/30 transition-all text-left flex items-center gap-4 group"
                >
                  <div className={`w-2 h-2 rounded-full ${sev.dot} shrink-0`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-sm text-[#F4F5F8] truncate">{inc.incident_type}</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full border ${sev.badge}`}>{sev.label}</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full border capitalize ${stat.className}`}>{stat.label}</span>
                    </div>
                    <p className="text-xs text-[#6B7588] mt-1 font-mono truncate">{entitySummary(inc.entity)}</p>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-[#6B7588] shrink-0">
                    <span className="flex items-center gap-1">
                      <Activity size={11} />
                      {inc.sources.length} source{inc.sources.length !== 1 ? 's' : ''}
                    </span>
                    <span className="flex items-center gap-1">
                      <Clock size={11} />
                      {formatRelative(inc.created_at)}
                    </span>
                    <ChevronRight size={14} className="text-[#2A3248] group-hover:text-[#E8935A] transition-colors" />
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </main>
    </div>
  )
}
