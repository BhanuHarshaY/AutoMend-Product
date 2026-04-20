/**
 * Typed API client for the AutoMend backend.
 *
 * During development, /api/* is proxied to the backend via next.config.js
 * rewrites. In production, set NEXT_PUBLIC_API_BASE_URL to the deployed
 * backend URL.
 *
 * Usage:
 *   import { api } from '@/lib/api'
 *   const token = await api.auth.login('user@x.com', 'pw')
 *   const playbooks = await api.playbooks.list()
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || ''

// ---------------------------------------------------------------------------
// Token management — localStorage-backed
// ---------------------------------------------------------------------------

const TOKEN_KEY = 'automend-access-token'
const REFRESH_KEY = 'automend-refresh-token'

export function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null
  return window.localStorage.getItem(TOKEN_KEY)
}

export function setTokens(access: string, refresh?: string): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(TOKEN_KEY, access)
  if (refresh) window.localStorage.setItem(REFRESH_KEY, refresh)
}

export function clearTokens(): void {
  if (typeof window === 'undefined') return
  window.localStorage.removeItem(TOKEN_KEY)
  window.localStorage.removeItem(REFRESH_KEY)
}

// ---------------------------------------------------------------------------
// Low-level fetch wrapper
// ---------------------------------------------------------------------------

class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`)
    this.status = status
    this.detail = detail
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getAccessToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch {
      // ignore
    }
    throw new ApiError(res.status, detail)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Response types — match backend Pydantic schemas
// ---------------------------------------------------------------------------

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface UserResponse {
  id: string
  email: string
  display_name: string | null
  role: string
  is_active: boolean
  created_at: string
}

export interface Tool {
  id: string
  name: string
  display_name: string
  description: string
  category: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  side_effect_level: 'read' | 'write' | 'destructive'
  required_approvals: number
  environments_allowed: string[]
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface Playbook {
  id: string
  project_id: string | null
  name: string
  description: string | null
  owner_team: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

// Task 11.8c — projects are bound to a Kubernetes namespace + gated by
// `playbooks_enabled` (replaces the former `status` enum). The UI rework
// in Task 11.8d consumes the new shape; `src/app/page.tsx` still
// references the old fields and will be refactored there.
export interface Project {
  id: string
  name: string
  namespace: string
  description: string | null
  playbooks_enabled: boolean
  owner_team: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface ProjectDetail extends Project {
  playbooks: Playbook[]
}

export interface PlaybookVersion {
  id: string
  playbook_id: string
  version_number: number
  status: 'draft' | 'generated' | 'validated' | 'approved' | 'published' | 'archived'
  workflow_spec: Record<string, unknown>
  trigger_bindings: Record<string, unknown> | null
  spec_checksum: string
  change_notes: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface PlaybookDetail extends Playbook {
  versions: PlaybookVersion[]
}

export interface Incident {
  id: string
  incident_key: string
  incident_type: string
  status: 'open' | 'acknowledged' | 'in_progress' | 'resolved' | 'closed' | 'suppressed'
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info'
  entity: Record<string, string>
  sources: string[]
  evidence: Record<string, unknown>
  playbook_version_id: string | null
  temporal_workflow_id: string | null
  temporal_run_id: string | null
  resolved_at: string | null
  created_at: string
  updated_at: string
}

export interface IncidentEvent {
  id: string
  incident_id: string
  event_type: string
  payload: Record<string, unknown>
  actor: string | null
  created_at: string
}

export interface WorkflowExecution {
  workflow_id: string
  run_id: string
  workflow_type: string
  status: string
  start_time: string | null
  close_time: string | null
}

export interface ValidationResult {
  valid: boolean
  errors: string[]
  warnings: string[]
}

export interface ClusterNamespace {
  name: string
  labels: Record<string, string>
  created_at: string | null
}

export interface ClusterResource {
  name: string
  namespace: string
  replicas: number | null
  labels: Record<string, string>
  created_at: string | null
}

export type ClusterResourceKind = 'deployment' | 'statefulset' | 'daemonset' | 'pod'

// ---------------------------------------------------------------------------
// API methods — grouped by resource
// ---------------------------------------------------------------------------

export const api = {
  auth: {
    async login(email: string, password: string): Promise<TokenResponse> {
      const resp = await request<TokenResponse>('POST', '/api/auth/login', { email, password })
      setTokens(resp.access_token, resp.refresh_token)
      return resp
    },
    async me(): Promise<UserResponse> {
      return request<UserResponse>('GET', '/api/auth/me')
    },
    async register(email: string, password: string, role = 'viewer', displayName?: string): Promise<UserResponse> {
      return request<UserResponse>('POST', '/api/auth/register', {
        email, password, role, display_name: displayName,
      })
    },
    logout(): void {
      clearTokens()
    },
  },

  tools: {
    list: (category?: string) =>
      request<Tool[]>('GET', `/api/tools${category ? `?category=${encodeURIComponent(category)}` : ''}`),
    get: (id: string) => request<Tool>('GET', `/api/tools/${id}`),
    create: (tool: Partial<Tool>) => request<Tool>('POST', '/api/tools', tool),
    update: (id: string, patch: Partial<Tool>) => request<Tool>('PUT', `/api/tools/${id}`, patch),
    deactivate: (id: string) => request<void>('DELETE', `/api/tools/${id}`),
  },

  projects: {
    list: (enabled?: boolean) =>
      request<Project[]>(
        'GET',
        `/api/projects${enabled === undefined ? '' : `?enabled=${enabled}`}`,
      ),
    get: (id: string) => request<ProjectDetail>('GET', `/api/projects/${id}`),
    create: (name: string, namespace: string, description?: string, ownerTeam?: string) =>
      request<Project>('POST', '/api/projects', {
        name, namespace, description, owner_team: ownerTeam,
      }),
    update: (
      id: string,
      patch: {
        name?: string
        description?: string
        owner_team?: string
        playbooks_enabled?: boolean
      },
    ) =>
      request<Project>('PATCH', `/api/projects/${id}`, patch),
    delete: (id: string) => request<void>('DELETE', `/api/projects/${id}`),
  },

  playbooks: {
    list: () => request<Playbook[]>('GET', '/api/playbooks'),
    get: (id: string) => request<PlaybookDetail>('GET', `/api/playbooks/${id}`),
    create: (name: string, description?: string, ownerTeam?: string, projectId?: string) =>
      request<Playbook>('POST', '/api/playbooks', {
        name, description, owner_team: ownerTeam, project_id: projectId,
      }),
    saveVersion: (playbookId: string, workflowSpec: Record<string, unknown>, changeNotes?: string) =>
      request<PlaybookVersion>('POST', `/api/playbooks/${playbookId}/versions`, {
        workflow_spec: workflowSpec, change_notes: changeNotes,
      }),
    getVersion: (playbookId: string, versionId: string) =>
      request<PlaybookVersion>('GET', `/api/playbooks/${playbookId}/versions/${versionId}`),
    transitionStatus: (playbookId: string, versionId: string, newStatus: string) =>
      request<{ version_id: string; new_status: string }>(
        'PATCH',
        `/api/playbooks/${playbookId}/versions/${versionId}/status`,
        { new_status: newStatus },
      ),
    delete: (id: string) => request<void>('DELETE', `/api/playbooks/${id}`),
  },

  incidents: {
    list: (filters?: { status?: string; severity?: string; incident_type?: string; limit?: number }) => {
      const params = new URLSearchParams()
      if (filters?.status) params.set('status', filters.status)
      if (filters?.severity) params.set('severity', filters.severity)
      if (filters?.incident_type) params.set('incident_type', filters.incident_type)
      if (filters?.limit) params.set('limit', String(filters.limit))
      const qs = params.toString()
      return request<Incident[]>('GET', `/api/incidents${qs ? `?${qs}` : ''}`)
    },
    get: (id: string) => request<Incident>('GET', `/api/incidents/${id}`),
    stats: () => request<{ by_status: Record<string, number>; by_severity: Record<string, number> }>(
      'GET', '/api/incidents/stats',
    ),
    acknowledge: (id: string) => request<Incident>('POST', `/api/incidents/${id}/acknowledge`),
    resolve: (id: string) => request<Incident>('POST', `/api/incidents/${id}/resolve`),
    update: (id: string, patch: { status?: string; severity?: string }) =>
      request<Incident>('PATCH', `/api/incidents/${id}`, patch),
    events: (id: string) => request<IncidentEvent[]>('GET', `/api/incidents/${id}/events`),
  },

  workflows: {
    list: () => request<WorkflowExecution[]>('GET', '/api/workflows'),
    // Workflow IDs can contain slashes (they embed the entity_key). The backend
    // route uses the `:path` converter to capture everything including slashes;
    // we still `encodeURIComponent` here so intermediate proxies (cert-manager
    // challenge stubs, some WAFs) don't get confused. Both layers cooperate.
    get: (workflowId: string) =>
      request<WorkflowExecution>('GET', `/api/workflows/${encodeURIComponent(workflowId)}`),
    signal: (workflowId: string, signalName: string, payload: Record<string, unknown> = {}) =>
      request<{ message: string }>('POST', `/api/workflows/${encodeURIComponent(workflowId)}/signal`, {
        signal_name: signalName, payload,
      }),
    cancel: (workflowId: string) =>
      request<{ message: string }>('POST', `/api/workflows/${encodeURIComponent(workflowId)}/cancel`),
  },

  clusters: {
    // Task 11.8b — cluster discovery. Single cluster "default" for now; path
    // shape already supports a future multi-cluster story without breaking.
    listNamespaces: (includeSystem = false, cluster = 'default') =>
      request<ClusterNamespace[]>(
        'GET',
        `/api/clusters/${encodeURIComponent(cluster)}/namespaces${
          includeSystem ? '?include_system=true' : ''
        }`,
      ),
    listResources: (
      namespace: string,
      kind: ClusterResourceKind = 'deployment',
      cluster = 'default',
    ) =>
      request<ClusterResource[]>(
        'GET',
        `/api/clusters/${encodeURIComponent(cluster)}/namespaces/${encodeURIComponent(
          namespace,
        )}/resources?kind=${encodeURIComponent(kind)}`,
      ),
  },

  design: {
    ragSearch: (query: string, searchTypes: string[] = ['tools', 'playbooks'], limit = 10) =>
      request<{ tools: unknown[]; playbooks: unknown[] }>('POST', '/api/design/rag_search', {
        query, search_types: searchTypes, limit,
      }),
    generateWorkflow: (intent: string, targetIncidentTypes?: string[]) =>
      request<{
        workflow_spec: Record<string, unknown>
        warnings: string[]
        suggested_name: string | null
        suggested_description: string | null
      }>('POST', '/api/design/generate_workflow', {
        intent, target_incident_types: targetIncidentTypes,
      }),
    validateWorkflow: (workflowSpec: Record<string, unknown>) =>
      request<ValidationResult>('POST', '/api/design/validate_workflow', {
        workflow_spec: workflowSpec,
      }),
  },
}

// ---------------------------------------------------------------------------
// WebSocket for real-time events
// ---------------------------------------------------------------------------

/**
 * Connect to the real-time incidents WebSocket.
 * Returns a cleanup function that closes the socket.
 *
 * Example:
 *   const cleanup = connectIncidentEvents((event) => {
 *     if (event.event_type === 'incident_created') updateState(event.data)
 *   })
 *   // later: cleanup()
 */
export function connectIncidentEvents(
  onEvent: (event: { event_type: string; data?: Record<string, unknown> }) => void,
  channel: 'all' | 'incidents' | 'workflows' = 'all',
): () => void {
  const token = getAccessToken()
  if (!token) throw new Error('No auth token — call api.auth.login first')

  const wsBase = API_BASE.replace(/^http/, 'ws') || (typeof window !== 'undefined'
    ? window.location.origin.replace(/^http/, 'ws') : '')
  const ws = new WebSocket(`${wsBase}/api/ws/incidents?token=${encodeURIComponent(token)}&channel=${channel}`)

  ws.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data))
    } catch {
      // ignore malformed
    }
  }

  return () => ws.close()
}

export { ApiError }
