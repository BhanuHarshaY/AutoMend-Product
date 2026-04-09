'use client'
import { Handle, Position, NodeProps } from 'reactflow'

interface NodeData {
  label: string
  type: string
  color: string
  description?: string
  config?: Record<string, string>
}

export function WorkflowNode({ data, selected }: NodeProps<NodeData>) {
  const icons: Record<string, string> = {
    trigger:   '⚡',
    scale:     '⬆',
    rollback:  '↩',
    retrain:   '🔄',
    alert:     '🔔',
    wait:      '⏱',
    condition: '◇',
    approval:  '👤',
  }

  return (
    <div className="relative min-w-[160px]" style={{ filter: selected ? `drop-shadow(0 0 8px ${data.color}60)` : 'none' }}>
      {data.type !== 'trigger' && <Handle type="target" position={Position.Left} />}
      <div className="rounded-xl overflow-hidden" style={{ background: '#111827', border: `1.5px solid ${selected ? data.color : '#1e2d4a'}` }}>
        <div className="h-1" style={{ background: data.color }} />
        <div className="px-4 py-3">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-base leading-none">{icons[data.type] || '●'}</span>
            <span className="text-sm font-semibold text-white">{data.label}</span>
          </div>
          {data.description && <p className="text-xs text-[#7b8db0] leading-relaxed">{data.description}</p>}
        </div>
        {data.config && Object.keys(data.config).length > 0 && (
          <div className="px-4 pb-3 space-y-1">
            {Object.entries(data.config).slice(0, 2).map(([k, v]) => (
              <div key={k} className="flex items-center gap-2 text-xs">
                <span className="text-[#3a4a6b] font-mono">{k}:</span>
                <span className="text-[#2ec4b6] font-mono truncate max-w-[80px]">{v}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}