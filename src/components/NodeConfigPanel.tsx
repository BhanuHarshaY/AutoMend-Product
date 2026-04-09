'use client'
import { X, Settings } from 'lucide-react'

interface NodeConfigPanelProps {
  node: { id: string; data: { label: string; type: string; color: string; config?: Record<string, string> } } | null
  onClose: () => void
  onUpdateConfig: (nodeId: string, config: Record<string, string>) => void
}

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

export function NodeConfigPanel({ node, onClose, onUpdateConfig }: NodeConfigPanelProps) {
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
        {fields.length === 0 ? (
          <p className="text-xs text-[#3a4a6b] italic">No configuration needed</p>
        ) : (
          fields.map(field => (
            <div key={field.label}>
              <label className="block text-xs font-medium text-[#7b8db0] mb-1.5 uppercase tracking-wider">{field.label}</label>
              <input
                defaultValue={config[field.label] || ''}
                placeholder={field.placeholder}
                onChange={e => onUpdateConfig(node.id, { ...config, [field.label]: e.target.value })}
                className="w-full bg-[#0a0e1a] border border-[#1e2d4a] rounded-lg px-3 py-2 text-xs text-white placeholder-[#3a4a6b] focus:outline-none focus:border-[#2ec4b6] transition-colors font-mono"
              />
            </div>
          ))
        )}
      </div>
      <div className="px-4 py-3 border-t border-[#1e2d4a]">
        <p className="text-xs text-[#3a4a6b] font-mono">id: {node.id}</p>
      </div>
    </div>
  )
}