'use client'
import { X, Settings, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError, type ClusterResource } from '@/lib/api'

interface NodeConfigPanelProps {
  node: { id: string; data: { label: string; type: string; color: string; config?: Record<string, string> } } | null
  onClose: () => void
  onUpdateConfig: (nodeId: string, config: Record<string, string>) => void
  /**
   * Namespace of the parent project. Task 11.8d — when present, Scale and
   * Rollback nodes show a "Namespace: {ns}" label and replace the free-text
   * Service field with a <select> populated from `api.clusters.listResources`.
   * When absent (legacy workflows with no project binding), the old text
   * inputs are kept so users can still edit them.
   */
  projectNamespace?: string
}

// Base text-input fields for each node type. For Scale/Rollback the Service
// field is overridden with a deployment picker when a projectNamespace is
// provided; we still define it here as a fallback for the no-namespace case.
const CONFIG_FIELDS: Record<string, { label: string; placeholder: string }[]> = {
  trigger:   [{ label: 'Metric', placeholder: 'e.g. latency_p95' }, { label: 'Threshold', placeholder: 'e.g. 500ms' }, { label: 'Window', placeholder: 'e.g. 5min' }],
  scale:     [{ label: 'Service', placeholder: 'e.g. fraud-model-v2' }, { label: 'Replicas', placeholder: 'e.g. 3' }, { label: 'Direction', placeholder: 'up / down' }],
  rollback:  [{ label: 'Service', placeholder: 'e.g. fraud-model' }, { label: 'Version', placeholder: 'e.g. v1.2.0' }],
  retrain:   [{ label: 'Pipeline', placeholder: 'e.g. fraud-retrain-v2' }, { label: 'Dataset', placeholder: 'e.g. gs://bucket/data' }],
  alert:     [{ label: 'Channel', placeholder: 'e.g. #mlops-alerts' }, { label: 'Message', placeholder: 'Alert message...' }],
  wait:      [{ label: 'Duration', placeholder: 'e.g. 5min' }],
  condition: [{ label: 'Metric', placeholder: 'e.g. error_rate' }, { label: 'Operator', placeholder: '> / < / ==' }, { label: 'Value', placeholder: 'e.g. 0.05' }],
  approval:  [{ label: 'Approver', placeholder: '@username or channel' }, { label: 'Timeout', placeholder: 'e.g. 30min' }],
}

const USES_DEPLOYMENT_PICKER = new Set(['scale', 'rollback'])

export function NodeConfigPanel({ node, onClose, onUpdateConfig, projectNamespace }: NodeConfigPanelProps) {
  const nodeType = node?.data.type
  const needsDeployments = !!projectNamespace && !!nodeType && USES_DEPLOYMENT_PICKER.has(nodeType)

  const [deployments, setDeployments] = useState<ClusterResource[] | null>(null)
  const [deploymentsError, setDeploymentsError] = useState<string | null>(null)

  useEffect(() => {
    if (!needsDeployments || !projectNamespace) {
      setDeployments(null)
      return
    }
    let cancelled = false
    setDeploymentsError(null)
    api.clusters.listResources(projectNamespace, 'deployment')
      .then(list => { if (!cancelled) setDeployments(list) })
      .catch(err => {
        if (cancelled) return
        const detail = err instanceof ApiError ? err.detail : 'Failed to load deployments'
        setDeploymentsError(detail)
        setDeployments([])
      })
    return () => { cancelled = true }
  }, [needsDeployments, projectNamespace])

  if (!node) return null
  const fields = CONFIG_FIELDS[node.data.type] || []
  const config = node.data.config || {}

  return (
    <div className="w-64 bg-[#111827] border-l border-[#1e2d4a] flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#1e2d4a]">
        <div className="flex items-center gap-2">
          <Settings size={13} style={{ color: node.data.color }} />
          <span className="text-sm font-semibold text-white">{node.data.label}</span>
        </div>
        <button onClick={onClose} className="text-[#7b8db0] hover:text-white transition-colors"><X size={14} /></button>
      </div>
      <div className="h-0.5" style={{ background: node.data.color }} />
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <p className="text-xs text-[#7b8db0]">Configure this node's parameters</p>

        {/* Task 11.8d — show read-only namespace label for scale/rollback so
            operators know which namespace the deployment picker is scoped to. */}
        {needsDeployments && (
          <div className="px-3 py-2 rounded-lg bg-[#0a0e1a] border border-[#1e2d4a]">
            <p className="text-xs text-[#7b8db0] uppercase tracking-wider mb-1">Namespace</p>
            <code className="text-xs text-[#2ec4b6] font-mono">{projectNamespace}</code>
          </div>
        )}

        {fields.length === 0 ? (
          <p className="text-xs text-[#3a4a6b] italic">No configuration needed</p>
        ) : (
          fields.map(field => {
            // Replace the free-text Service input with a deployment dropdown
            // when we have a namespace + the current node type uses it.
            if (needsDeployments && field.label === 'Service') {
              return (
                <div key={field.label}>
                  <label className="block text-xs font-medium text-[#7b8db0] mb-1.5 uppercase tracking-wider">{field.label}</label>
                  {deployments === null ? (
                    <div className="flex items-center gap-2 text-xs text-[#7b8db0]">
                      <Loader2 size={12} className="animate-spin" /> Loading deployments…
                    </div>
                  ) : deploymentsError ? (
                    <p className="text-xs text-[#E85A6B]">{deploymentsError}</p>
                  ) : deployments.length === 0 ? (
                    <p className="text-xs text-[#3a4a6b] italic">
                      No deployments in <code>{projectNamespace}</code>.
                    </p>
                  ) : (
                    <select
                      defaultValue={config[field.label] || ''}
                      onChange={e => onUpdateConfig(node.id, { ...config, [field.label]: e.target.value })}
                      className="w-full bg-[#0a0e1a] border border-[#1e2d4a] rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-[#2ec4b6] transition-colors font-mono"
                    >
                      <option value="" disabled>Select deployment…</option>
                      {deployments.map(d => (
                        <option key={d.name} value={d.name}>
                          {d.name}{d.replicas != null ? ` (${d.replicas} replica${d.replicas === 1 ? '' : 's'})` : ''}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              )
            }
            return (
              <div key={field.label}>
                <label className="block text-xs font-medium text-[#7b8db0] mb-1.5 uppercase tracking-wider">{field.label}</label>
                <input
                  defaultValue={config[field.label] || ''}
                  placeholder={field.placeholder}
                  onChange={e => onUpdateConfig(node.id, { ...config, [field.label]: e.target.value })}
                  className="w-full bg-[#0a0e1a] border border-[#1e2d4a] rounded-lg px-3 py-2 text-xs text-white placeholder-[#3a4a6b] focus:outline-none focus:border-[#2ec4b6] transition-colors font-mono"
                />
              </div>
            )
          })
        )}
      </div>
      <div className="px-4 py-3 border-t border-[#1e2d4a]">
        <p className="text-xs text-[#3a4a6b] font-mono">id: {node.id}</p>
      </div>
    </div>
  )
}
