'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { Plus, Zap, Activity, Clock, Edit2, ChevronRight, Trash2, GitBranch, Loader2, AlertTriangle, Power } from 'lucide-react'
import Link from 'next/link'
import {
  api, ApiError,
  type Project as BackendProject,
  type Playbook as BackendPlaybook,
  type ClusterNamespace,
} from '@/lib/api'
import { reactFlowToSpec } from '@/lib/adapters'

// Task 11.8d — projects are now bound to a Kubernetes namespace and gated
// by a playbooks_enabled boolean (see DECISION-028). The old status enum
// (active/paused/draft) and its tab filter are gone.

interface ProjectView {
  id: string
  name: string
  namespace: string
  description: string
  playbooksEnabled: boolean
  createdAt: string
  lastRun: string | null
  workflows: BackendPlaybook[]
}

function toProjectView(p: BackendProject, playbooks: BackendPlaybook[] = []): ProjectView {
  return {
    id: p.id,
    name: p.name,
    namespace: p.namespace,
    description: p.description ?? '',
    playbooksEnabled: p.playbooks_enabled,
    createdAt: p.created_at.slice(0, 10),
    lastRun: null,
    workflows: playbooks,
  }
}

function NewProjectModal({
  onClose, onCreate, busy, claimedNamespaces,
}: {
  onClose: () => void
  onCreate: (name: string, namespace: string, desc: string) => void
  busy: boolean
  claimedNamespaces: Set<string>
}) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [namespace, setNamespace] = useState('')
  const [namespaces, setNamespaces] = useState<ClusterNamespace[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.clusters.listNamespaces()
      .then(list => { if (!cancelled) setNamespaces(list) })
      .catch(err => {
        if (!cancelled) {
          const detail = err instanceof ApiError ? err.detail : 'Failed to load namespaces'
          setLoadError(detail)
          setNamespaces([])
        }
      })
    return () => { cancelled = true }
  }, [])

  const availableNamespaces = namespaces?.filter(n => !claimedNamespaces.has(n.name)) ?? []
  const allClaimed = namespaces !== null && availableNamespaces.length === 0 && namespaces.length > 0

  // Default the display name to the selected namespace if the user hasn't typed anything.
  const effectiveName = name.trim() || namespace
  const canCreate = !!effectiveName && !!namespace && !busy

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-md flex items-center justify-center z-50">
      <div className="bg-[#1A1F2E] border border-[#2A3248] rounded-2xl p-6 w-full max-w-md shadow-2xl">
        <div className="flex items-center gap-3 mb-5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#E8935A] to-[#6B7FE8] flex items-center justify-center">
            <Plus size={16} className="text-white" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-[#F4F5F8]">New Project</h2>
            <p className="text-xs text-[#6B7588]">Bind a Kubernetes namespace to an AutoMend project</p>
          </div>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-[#6B7588] mb-1.5 uppercase tracking-wider">Namespace</label>
            {namespaces === null ? (
              <div className="flex items-center gap-2 text-xs text-[#6B7588]">
                <Loader2 size={12} className="animate-spin" /> Loading namespaces from cluster…
              </div>
            ) : loadError ? (
              <p className="text-xs text-[#E85A6B]">{loadError}</p>
            ) : allClaimed ? (
              <div className="text-xs text-[#6B7588] bg-[#0D0F1A] border border-dashed border-[#2A3248] rounded-xl p-3 leading-relaxed">
                Every namespace already has a project.
                <br />
                Create a new namespace first: <code className="text-[#E8935A]">kubectl create namespace &lt;name&gt;</code>
              </div>
            ) : (
              <select
                value={namespace}
                onChange={e => setNamespace(e.target.value)}
                className="w-full bg-[#0D0F1A] border border-[#2A3248] rounded-xl px-3 py-2.5 text-sm text-[#F4F5F8] focus:outline-none focus:border-[#E8935A] transition-colors"
              >
                <option value="" disabled>Select a namespace…</option>
                {namespaces.map(n => (
                  <option
                    key={n.name}
                    value={n.name}
                    disabled={claimedNamespaces.has(n.name)}
                  >
                    {n.name}{claimedNamespaces.has(n.name) ? ' (already owned)' : ''}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-[#6B7588] mb-1.5 uppercase tracking-wider">Display Name <span className="text-[#3a4a6b] normal-case">(optional — defaults to namespace)</span></label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder={namespace || 'e.g. Fraud Detection API'}
              className="w-full bg-[#0D0F1A] border border-[#2A3248] rounded-xl px-3 py-2.5 text-sm text-[#F4F5F8] placeholder-[#2A3248] focus:outline-none focus:border-[#E8935A] transition-colors"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-[#6B7588] mb-1.5 uppercase tracking-wider">Description</label>
            <textarea
              value={desc}
              onChange={e => setDesc(e.target.value)}
              placeholder="What model does this project monitor?"
              rows={3}
              className="w-full bg-[#0D0F1A] border border-[#2A3248] rounded-xl px-3 py-2.5 text-sm text-[#F4F5F8] placeholder-[#2A3248] focus:outline-none focus:border-[#E8935A] transition-colors resize-none"
            />
          </div>
        </div>

        <div className="flex gap-3 mt-6">
          <button onClick={onClose} disabled={busy} className="flex-1 px-4 py-2.5 text-sm text-[#6B7588] border border-[#2A3248] rounded-xl hover:border-[#E8935A] hover:text-[#F4F5F8] transition-colors disabled:opacity-40">
            Cancel
          </button>
          <button
            onClick={() => canCreate && onCreate(effectiveName, namespace, desc)}
            disabled={!canCreate}
            className="flex-1 px-4 py-2.5 text-sm font-medium bg-gradient-to-r from-[#E8935A] to-[#F4B97A] text-white rounded-xl hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity flex items-center justify-center gap-2"
          >
            {busy && <Loader2 size={14} className="animate-spin" />}
            Create Project
          </button>
        </div>
      </div>
    </div>
  )
}

function WorkflowsPopover({ project, onClose, onWorkflowCreated }: {
  project: ProjectView
  onClose: () => void
  onWorkflowCreated: (projectId: string, playbook: BackendPlaybook) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [onClose])

  const addWorkflow = async () => {
    setBusy(true)
    setError(null)
    try {
      const playbook = await api.playbooks.create(
        `Untitled Workflow ${new Date().toLocaleString()}`,
        undefined,
        undefined,
        project.id,
      )
      // Seed an empty initial version so the workflow page has something to load.
      const emptySpec = reactFlowToSpec(playbook.name, '', [], [], { namespace: project.namespace })
      await api.playbooks.saveVersion(playbook.id, emptySpec as unknown as Record<string, unknown>)
      onWorkflowCreated(project.id, playbook)
      window.location.href = `/workflow/${playbook.id}`
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : 'Failed to create workflow'
      setError(detail)
      setBusy(false)
    }
  }

  return (
    <div ref={ref} className="absolute top-full left-0 mt-1 w-64 bg-[#1A1F2E] border border-[#2A3248] rounded-xl shadow-2xl z-50 overflow-hidden">
      <div className="px-4 py-3 border-b border-[#2A3248] bg-gradient-to-r from-[#E8935A]/5 to-transparent">
        <p className="text-xs font-semibold text-[#F4F5F8]">{project.name}</p>
        <p className="text-xs text-[#6B7588] mt-0.5">Workflows · namespace <code className="text-[#E8935A]">{project.namespace}</code></p>
      </div>

      {project.workflows.length === 0 ? (
        <div className="px-4 py-6 text-center">
          <GitBranch size={20} className="text-[#2A3248] mx-auto mb-2" />
          <p className="text-xs text-[#6B7588]">No workflows yet</p>
        </div>
      ) : (
        <div className="p-2">
          {project.workflows.map(w => (
            <button
              key={w.id}
              onClick={() => { window.location.href = `/workflow/${w.id}` }}
              className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg hover:bg-[#2A3248] transition-colors group text-left"
            >
              <div>
                <p className="text-xs font-medium text-[#F4F5F8] group-hover:text-[#E8935A] transition-colors">{w.name}</p>
                <p className="text-xs text-[#6B7588] mt-0.5">{w.description ?? ''}</p>
              </div>
              <Edit2 size={12} className="text-[#2A3248] group-hover:text-[#E8935A] shrink-0 ml-2 transition-colors" />
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="px-4 py-2 text-xs text-[#E85A6B] border-t border-[#2A3248]">{error}</div>
      )}

      <div className="px-2 pb-2">
        <button
          onClick={addWorkflow}
          disabled={busy}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-dashed border-[#2A3248] text-xs text-[#6B7588] hover:border-[#E8935A]/50 hover:text-[#E8935A] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
          Add New Workflow
        </button>
      </div>
    </div>
  )
}

function ProjectCard({ project, onDelete, onRename, onToggleEnabled, onWorkflowCreated }: {
  project: ProjectView
  onDelete: () => void
  onRename: (id: string, name: string) => void
  onToggleEnabled: (id: string, next: boolean) => void
  onWorkflowCreated: (projectId: string, playbook: BackendPlaybook) => void
}) {
  const [showWorkflows, setShowWorkflows] = useState(false)
  const [editingName, setEditingName] = useState<string | null>(null)

  const enabledStyle = project.playbooksEnabled
    ? { dot: 'bg-[#4ADE80]', badge: 'bg-[#4ADE80]/10 text-[#4ADE80] border-[#4ADE80]/20', label: 'Enabled',  border: 'card-active' }
    : { dot: 'bg-[#E8935A]', badge: 'bg-[#E8935A]/10 text-[#E8935A] border-[#E8935A]/20', label: 'Disabled', border: 'card-paused' }

  return (
    <div className={`relative group bg-[#1A1F2E] ${enabledStyle.border} border border-[#2A3248] rounded-xl p-5 hover:border-[#E8935A]/30 transition-all duration-200 hover:shadow-lg hover:shadow-[#E8935A]/5`}>
      <div className="absolute inset-0 bg-gradient-to-br from-white/[0.015] to-transparent pointer-events-none rounded-xl" />

      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <div className={`w-2 h-2 rounded-full ${enabledStyle.dot} mt-0.5 shrink-0`} />
          {editingName !== null ? (
            <input
              autoFocus
              value={editingName}
              onChange={e => setEditingName(e.target.value)}
              onBlur={() => { onRename(project.id, editingName); setEditingName(null) }}
              onKeyDown={e => {
                if (e.key === 'Enter') { onRename(project.id, editingName); setEditingName(null) }
                if (e.key === 'Escape') setEditingName(null)
              }}
              className="text-sm font-semibold bg-transparent border-b border-[#E8935A] text-[#F4F5F8] focus:outline-none w-36"
            />
          ) : (
            <h3 className="font-semibold text-[#F4F5F8] text-sm truncate">{project.name}</h3>
          )}
          <button
            onClick={() => setEditingName(project.name)}
            className="text-[#2A3248] hover:text-[#E8935A] transition-colors shrink-0"
          >
            <Edit2 size={11} />
          </button>
        </div>

        <button
          onClick={() => onToggleEnabled(project.id, !project.playbooksEnabled)}
          title={project.playbooksEnabled
            ? 'Playbooks enabled — click to disable remediation for this namespace'
            : 'Playbooks disabled — click to re-enable remediation'}
          className={`text-xs px-2 py-0.5 rounded-full border ${enabledStyle.badge} flex items-center gap-1 shrink-0 hover:opacity-90 transition-opacity`}
        >
          <Power size={9} />
          {enabledStyle.label}
        </button>
      </div>

      <div className="flex items-center gap-2 mb-3 text-xs">
        <span className="text-[#6B7588]">ns</span>
        <code className="text-[#E8935A] bg-[#E8935A]/5 border border-[#E8935A]/10 px-1.5 py-0.5 rounded">{project.namespace}</code>
      </div>

      <p className="text-xs text-[#6B7588] mb-4 leading-relaxed line-clamp-2">{project.description || <span className="italic opacity-60">No description</span>}</p>

      <div className="flex items-center gap-4 text-xs mb-4">
        <span className="flex items-center gap-1.5 bg-[#E8935A]/5 border border-[#E8935A]/10 px-2 py-1 rounded-lg">
          <GitBranch size={10} className="text-[#E8935A]" />
          <span className="text-[#E8935A]">{project.workflows?.length || 0}</span>
          <span className="text-[#6B7588]">workflow{(project.workflows?.length || 0) !== 1 ? 's' : ''}</span>
        </span>
        <span className="flex items-center gap-1.5 text-[#6B7588]">
          <Clock size={11} />
          {project.lastRun ? project.lastRun : 'Never run'}
        </span>
        <span className="flex items-center gap-1.5 text-[#6B7588]">
          <Activity size={11} />
          {project.createdAt}
        </span>
      </div>

      <div className="flex gap-2 pt-3 border-t border-[#2A3248]">
        <button
          onClick={() => setShowWorkflows(!showWorkflows)}
          className={`flex items-center gap-1.5 text-xs font-medium transition-all px-3 py-1.5 rounded-lg ${
            showWorkflows
              ? 'bg-[#E8935A]/10 text-[#E8935A] border border-[#E8935A]/20'
              : 'text-[#6B7588] hover:text-[#E8935A] hover:bg-[#E8935A]/5'
          }`}
        >
          <ChevronRight size={12} className={`transition-transform ${showWorkflows ? 'rotate-90' : ''}`} />
          View Workflows
        </button>
        <button onClick={onDelete} className="flex items-center gap-1.5 text-xs text-[#6B7588] hover:text-[#E85A6B] transition-colors px-2 py-1 rounded hover:bg-red-500/5 ml-auto">
          <Trash2 size={12} />
        </button>
      </div>

      {showWorkflows && (
        <WorkflowsPopover
          project={project}
          onClose={() => setShowWorkflows(false)}
          onWorkflowCreated={onWorkflowCreated}
        />
      )}
    </div>
  )
}

export default function HomePage() {
  const [projects, setProjects] = useState<ProjectView[]>([])
  const [showModal, setShowModal] = useState(false)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | 'enabled' | 'disabled'>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const list = await api.projects.list()
      const details = await Promise.all(list.map(p => api.projects.get(p.id).catch(() => null)))
      const views: ProjectView[] = list.map((p, i) => {
        const d = details[i]
        return toProjectView(p, d?.playbooks ?? [])
      })
      setProjects(views)
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : 'Failed to load projects'
      setError(detail)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  const handleCreate = async (name: string, namespace: string, desc: string) => {
    setCreating(true)
    try {
      const created = await api.projects.create(name, namespace, desc || undefined)
      setProjects(prev => [toProjectView(created), ...prev])
      setShowModal(false)
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : 'Failed to create project'
      setError(detail)
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (id: string) => {
    const snapshot = projects
    setProjects(prev => prev.filter(p => p.id !== id))
    try {
      await api.projects.delete(id)
    } catch (err) {
      setProjects(snapshot)
      const detail = err instanceof ApiError ? err.detail : 'Failed to delete project'
      setError(detail)
    }
  }

  const handleRename = async (id: string, name: string) => {
    if (!name.trim()) return
    const snapshot = projects
    setProjects(prev => prev.map(p => p.id === id ? { ...p, name } : p))
    try {
      await api.projects.update(id, { name })
    } catch (err) {
      setProjects(snapshot)
      const detail = err instanceof ApiError ? err.detail : 'Failed to rename project'
      setError(detail)
    }
  }

  const handleToggleEnabled = async (id: string, next: boolean) => {
    const snapshot = projects
    setProjects(prev => prev.map(p => p.id === id ? { ...p, playbooksEnabled: next } : p))
    try {
      await api.projects.update(id, { playbooks_enabled: next })
    } catch (err) {
      setProjects(snapshot)
      const detail = err instanceof ApiError ? err.detail : 'Failed to update project'
      setError(detail)
    }
  }

  const handleWorkflowCreated = (projectId: string, playbook: BackendPlaybook) => {
    setProjects(prev => prev.map(p =>
      p.id === projectId ? { ...p, workflows: [playbook, ...p.workflows] } : p
    ))
  }

  const filtered = projects.filter(p => {
    const matchSearch = p.name.toLowerCase().includes(search.toLowerCase())
      || p.namespace.toLowerCase().includes(search.toLowerCase())
    const matchFilter =
      filter === 'all'
        || (filter === 'enabled' && p.playbooksEnabled)
        || (filter === 'disabled' && !p.playbooksEnabled)
    return matchSearch && matchFilter
  })

  const counts = {
    all:      projects.length,
    enabled:  projects.filter(p => p.playbooksEnabled).length,
    disabled: projects.filter(p => !p.playbooksEnabled).length,
  }

  const claimedNamespaces = new Set(projects.map(p => p.namespace))

  return (
    <div className="min-h-screen bg-[#0D0F1A] grid-bg">
      <header className="border-b border-[#2A3248] px-6 py-3 flex items-center justify-between sticky top-0 z-10 glass">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-[#E8935A] to-[#6B7FE8] flex items-center justify-center float">
            <Zap size={14} className="text-white" />
          </div>
          <span className="font-bold text-[#F4F5F8] tracking-tight">Auto<span className="gradient-text">Mend</span></span>
          <span className="text-[#6B7588] text-xs font-mono bg-[#1A1F2E] px-2 py-0.5 rounded-full border border-[#2A3248]">v1.0</span>
          <nav className="ml-4 flex items-center gap-1">
            <span className="text-xs px-3 py-1.5 rounded-lg bg-[#1A1F2E] border border-[#2A3248] text-[#F4F5F8] font-medium">Projects</span>
            <Link href="/incidents" className="text-xs px-3 py-1.5 rounded-lg text-[#6B7588] hover:text-[#F4F5F8] hover:bg-[#1A1F2E] transition-colors flex items-center gap-1.5">
              <AlertTriangle size={12} /> Incidents
            </Link>
          </nav>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-[#E8935A] to-[#F4B97A] text-white text-sm font-medium rounded-xl hover:opacity-90 transition-opacity shadow-lg shadow-[#E8935A]/20"
        >
          <Plus size={15} /> New Project
        </button>
      </header>

      <div className="hero-gradient px-6 pt-10 pb-6 max-w-6xl mx-auto text-center">
        <h1 className="text-3xl font-bold text-[#F4F5F8] mb-3">
          From Alert to Action <span className="gradient-text">in Seconds</span>
        </h1>
        <p className="text-sm text-[#6B7588] max-w-lg mx-auto">
          AutoMend automatically detects anomalies in your ML models and triggers the<br />
          right remediation workflow — no manual intervention needed.
        </p>
        <div className="flex gap-6 mt-5 justify-center">
          {[
            { label: 'Enabled Projects',  value: counts.enabled },
            { label: 'Total Workflows',   value: projects.reduce((acc, p) => acc + (p.workflows?.length || 0), 0) },
            { label: 'Disabled Projects', value: counts.disabled },
          ].map(stat => (
            <div key={stat.label} className="flex items-center gap-2">
              <span className="text-xl font-bold text-[#F4F5F8]">{stat.value}</span>
              <span className="text-xs text-[#6B7588]">{stat.label}</span>
            </div>
          ))}
        </div>
      </div>

      <main className="max-w-6xl mx-auto px-6 pb-12">
        <div className="flex items-center gap-3 mb-6 flex-wrap">
          <div className="flex gap-1 bg-[#1A1F2E] border border-[#2A3248] rounded-xl p-1">
            {(['all', 'enabled', 'disabled'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1.5 text-xs rounded-lg capitalize transition-all ${
                  filter === f
                    ? 'bg-gradient-to-r from-[#E8935A]/20 to-[#F4B97A]/20 text-[#F4F5F8] border border-[#E8935A]/20'
                    : 'text-[#6B7588] hover:text-[#F4F5F8]'
                }`}
              >
                {f} <span className="opacity-60 ml-1">{counts[f]}</span>
              </button>
            ))}
          </div>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search by name or namespace…"
            className="flex-1 max-w-xs bg-[#1A1F2E] border border-[#2A3248] rounded-xl px-3 py-2 text-sm text-[#F4F5F8] placeholder-[#2A3248] focus:outline-none focus:border-[#E8935A] transition-colors"
          />
          <span className="text-xs text-[#6B7588] ml-auto">{filtered.length} project{filtered.length !== 1 ? 's' : ''}</span>
        </div>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-xl border border-[#E85A6B]/30 bg-[#E85A6B]/5 text-xs text-[#E85A6B] flex items-center justify-between">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="ml-4 text-[#E85A6B]/70 hover:text-[#E85A6B]">×</button>
          </div>
        )}

        {loading ? (
          <div className="text-center py-20">
            <Loader2 size={24} className="animate-spin text-[#6B7588] mx-auto mb-4" />
            <p className="text-sm text-[#6B7588]">Loading projects…</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <div className="w-16 h-16 rounded-2xl border border-dashed border-[#2A3248] flex items-center justify-center mx-auto mb-4">
              <Zap size={24} className="text-[#2A3248]" />
            </div>
            <p className="text-sm font-medium text-[#6B7588]">{projects.length === 0 ? 'No projects yet' : 'No projects match your filters'}</p>
            <p className="text-xs mt-1 text-[#2A3248]">{projects.length === 0 ? 'Create your first project to get started' : 'Try adjusting your search or filters'}</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map(p => (
              <ProjectCard
                key={p.id}
                project={p}
                onDelete={() => handleDelete(p.id)}
                onRename={handleRename}
                onToggleEnabled={handleToggleEnabled}
                onWorkflowCreated={handleWorkflowCreated}
              />
            ))}
            <button
              onClick={() => setShowModal(true)}
              className="border border-dashed border-[#2A3248] rounded-xl p-5 flex flex-col items-center justify-center gap-3 text-[#6B7588] hover:border-[#E8935A]/50 hover:text-[#E8935A] transition-all min-h-[180px] group"
            >
              <div className="w-12 h-12 rounded-2xl border border-dashed border-current flex items-center justify-center group-hover:scale-110 group-hover:bg-[#E8935A]/10 transition-all">
                <Plus size={20} />
              </div>
              <div className="text-center">
                <p className="text-sm font-medium">New Project</p>
                <p className="text-xs opacity-60 mt-0.5">Bind a namespace to monitor</p>
              </div>
            </button>
          </div>
        )}
      </main>

      {showModal && (
        <NewProjectModal
          onClose={() => setShowModal(false)}
          onCreate={handleCreate}
          busy={creating}
          claimedNamespaces={claimedNamespaces}
        />
      )}
    </div>
  )
}
