'use client'
import { useState, useCallback, useRef, useEffect } from 'react'
import ReactFlow, {
  addEdge,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Connection,
  Node,
  Edge,
  BackgroundVariant,
  ReactFlowInstance,
} from 'reactflow'
import 'reactflow/dist/style.css'

import {
  ArrowLeft, Zap, Settings, Send, ChevronDown,
  ChevronRight, Save, Play, Loader2,
} from 'lucide-react'
import { NODE_TYPES_CONFIG } from '@/lib/data'
import { WorkflowNode } from '@/components/WorkflowNode'
import { NodeConfigPanel } from '@/components/NodeConfigPanel'
import { api, ApiError } from '@/lib/api'
import { reactFlowToSpec, specToReactFlow, type PlaybookSpec, type WorkflowNode as WNode } from '@/lib/adapters'

const nodeTypes = { custom: WorkflowNode }
const INITIAL_NODES: Node[] = []

const CHAT_SUGGESTIONS = [
  'If fraud model latency exceeds 200ms, scale up replicas and alert the team',
  'When drift score > 0.5, trigger retraining and notify on Slack',
  'If GPU utilization drops below 20%, scale down and wait for approval',
]

type DeployStatus = 'draft' | 'generated' | 'validated' | 'approved' | 'published' | 'archived'

const DEPLOY_CHAIN: DeployStatus[] = ['validated', 'approved', 'published']

function draftKey(playbookId: string): string {
  return `automend-workflow-draft-${playbookId}`
}

export default function WorkflowPage({ params }: { params: { id: string } }) {
  const [projectName, setProjectName] = useState('AutoMend')
  // Task 11.8d — project's Kubernetes namespace. Drives the NodeConfigPanel's
  // deployment dropdown for Scale/Rollback and the adapter's namespace option.
  const [projectNamespace, setProjectNamespace] = useState<string | undefined>(undefined)
  const [workflowName, setWorkflowName] = useState('Untitled Workflow')
  const [playbookId] = useState(params.id)
  const [latestVersion, setLatestVersion] = useState<{ id: string; status: DeployStatus } | null>(null)

  const [nodes, setNodes, onNodesChange] = useNodesState(INITIAL_NODES)
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null)
  const [chatInput, setChatInput] = useState('')
  const [chatBusy, setChatBusy] = useState(false)
  const [chatMessages, setChatMessages] = useState<{ role: 'user' | 'ai'; text: string }[]>([
    { role: 'ai', text: "Hi! Describe the remediation workflow you want to create and I'll build it for you." }
  ])
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [activeSection, setActiveSection] = useState<string>('integrations')
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [deploying, setDeploying] = useState(false)
  const [deployMsg, setDeployMsg] = useState<string | null>(null)
  const dropRef = useRef<HTMLDivElement>(null)

  // Load playbook + latest version on mount.
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setLoadError(null)
      try {
        const playbook = await api.playbooks.get(playbookId)
        if (cancelled) return

        setWorkflowName(playbook.name)

        // Newest version first (API returns versions ordered by version_number desc).
        const latest = playbook.versions[0]
        if (latest) {
          setLatestVersion({ id: latest.id, status: latest.status })
        }

        // Prefer an unsaved local draft (browser crash recovery) if newer than the loaded spec.
        const draftRaw = typeof window !== 'undefined' ? window.localStorage.getItem(draftKey(playbookId)) : null
        if (draftRaw) {
          try {
            const draft = JSON.parse(draftRaw) as { nodes: Node[]; edges: Edge[]; ts: number }
            if (Array.isArray(draft.nodes) && Array.isArray(draft.edges)) {
              setNodes(draft.nodes)
              setEdges(draft.edges as never)
              return
            }
          } catch {
            // Ignore malformed draft.
          }
        }

        if (latest?.workflow_spec) {
          const spec = latest.workflow_spec as unknown as PlaybookSpec
          const { nodes: n, edges: e } = specToReactFlow(spec)
          setNodes(n as unknown as Node[])
          setEdges(e as never)
        }

        // Fetch parent project name for the breadcrumb.
        if (playbook.project_id) {
          try {
            const proj = await api.projects.get(playbook.project_id)
            if (!cancelled) {
              setProjectName(proj.name)
              setProjectNamespace(proj.namespace)
            }
          } catch {
            // Non-fatal — just keep the default breadcrumb.
          }
        }
      } catch (err) {
        if (!cancelled) {
          const detail = err instanceof ApiError ? err.detail : 'Failed to load workflow'
          setLoadError(detail)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [playbookId, setNodes, setEdges])

  // Persist a local draft on every change so a tab crash doesn't lose work.
  useEffect(() => {
    if (loading || typeof window === 'undefined') return
    const payload = JSON.stringify({ nodes, edges, ts: Date.now() })
    window.localStorage.setItem(draftKey(playbookId), payload)
  }, [nodes, edges, loading, playbookId])

  const handleSave = async () => {
    setSaveStatus('saving')
    try {
      const spec = reactFlowToSpec(
        workflowName, '', nodes as unknown as WNode[], edges as never,
        { namespace: projectNamespace },
      )
      const version = await api.playbooks.saveVersion(
        playbookId,
        spec as unknown as Record<string, unknown>,
      )
      setLatestVersion({ id: version.id, status: version.status as DeployStatus })
      setSaveStatus('saved')
      if (typeof window !== 'undefined') window.localStorage.removeItem(draftKey(playbookId))
      setTimeout(() => setSaveStatus('idle'), 2000)
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : 'Save failed'
      setDeployMsg(detail)
      setSaveStatus('error')
      setTimeout(() => setSaveStatus('idle'), 3000)
    }
  }

  const handleDeploy = async () => {
    if (!latestVersion) {
      setDeployMsg('Save the workflow first before deploying.')
      return
    }
    setDeploying(true)
    setDeployMsg(null)
    const startIdx = DEPLOY_CHAIN.indexOf(latestVersion.status)
    const remaining = startIdx === -1 ? DEPLOY_CHAIN : DEPLOY_CHAIN.slice(startIdx + 1)
    try {
      let current = latestVersion
      for (const next of remaining) {
        const resp = await api.playbooks.transitionStatus(playbookId, current.id, next)
        current = { id: current.id, status: resp.new_status as DeployStatus }
      }
      setLatestVersion(current)
      setDeployMsg('Deployed — workflow is now live.')
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : 'Deploy failed'
      setDeployMsg(detail)
    } finally {
      setDeploying(false)
      setTimeout(() => setDeployMsg(null), 4000)
    }
  }

  const onConnect = useCallback((params: Connection) => {
    setEdges(eds => addEdge({ ...params, animated: true, style: { stroke: '#2ec4b6', strokeWidth: 2 } }, eds))
  }, [setEdges])

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedNode(node)
  }, [])

  const onPaneClick = useCallback(() => {
    setSelectedNode(null)
  }, [])

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    if (!rfInstance || !dropRef.current) return

    const type = e.dataTransfer.getData('nodeType')
    const config = NODE_TYPES_CONFIG.find(n => n.type === type)
    if (!config) return

    const bounds = dropRef.current.getBoundingClientRect()
    const position = rfInstance.project({
      x: e.clientX - bounds.left,
      y: e.clientY - bounds.top,
    })

    const newNode: Node = {
      id: `${type}-${Date.now()}`,
      type: 'custom',
      position,
      data: {
        label: config.label,
        type: config.type,
        color: config.color,
        description: config.description,
        config: {},
      },
    }
    setNodes(nds => [...nds, newNode])
  }, [rfInstance, setNodes])

  const updateNodeConfig = useCallback((nodeId: string, config: Record<string, string>) => {
    setNodes(nds => nds.map(n => n.id === nodeId ? { ...n, data: { ...n.data, config } } : n))
    setSelectedNode(prev => prev?.id === nodeId ? { ...prev, data: { ...prev.data, config } } : prev)
  }, [setNodes])

  const handleChat = async () => {
    if (!chatInput.trim() || chatBusy) return
    const userMsg = chatInput.trim()
    setChatMessages(prev => [...prev, { role: 'user', text: userMsg }])
    setChatInput('')
    setChatBusy(true)
    try {
      const resp = await api.design.generateWorkflow(userMsg)
      const spec = resp.workflow_spec as unknown as PlaybookSpec
      const { nodes: n, edges: e } = specToReactFlow(spec)
      setNodes(n as unknown as Node[])
      setEdges(e as never)
      setChatMessages(prev => [...prev, {
        role: 'ai',
        text: `Generated a workflow with ${n.length} nodes. Review and save when ready.` + (resp.warnings?.length ? `\n\nWarnings:\n• ${resp.warnings.join('\n• ')}` : ''),
      }])
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : 'Generation failed'
      setChatMessages(prev => [...prev, { role: 'ai', text: `Sorry — ${detail}` }])
    } finally {
      setChatBusy(false)
    }
  }

  const categories = [...new Set(NODE_TYPES_CONFIG.map(n => n.category))]

  const deployLabel = (() => {
    if (deploying) return 'Deploying…'
    if (!latestVersion) return 'Deploy'
    if (latestVersion.status === 'published') return 'Published'
    if (latestVersion.status === 'archived') return 'Archived'
    return `Deploy (${latestVersion.status} → published)`
  })()

  return (
    <div className="h-screen flex flex-col bg-[#0a0e1a] overflow-hidden">
      <header className="flex items-center justify-between px-4 py-2.5 border-b border-[#1e2d4a] bg-[#0a0e1a] z-10 shrink-0">
        <div className="flex items-center gap-3">
          <button onClick={() => { window.location.href = '/' }} className="text-[#7b8db0] hover:text-white transition-colors p-1 rounded hover:bg-[#1e2d4a]">
            <ArrowLeft size={16} />
          </button>
          <div className="w-5 h-5 rounded bg-gradient-to-br from-[#e63946] to-[#2ec4b6] flex items-center justify-center">
            <Zap size={11} className="text-white" />
          </div>
          <span className="text-sm font-medium text-[#7b8db0]">{projectName}</span>
          <span className="text-xs text-[#3a4a6b] font-mono">/</span>
          <span className="text-sm font-semibold text-white">{workflowName}</span>
          {latestVersion && (
            <span className="text-xs font-mono px-2 py-0.5 rounded-full border border-[#1e2d4a] text-[#7b8db0]">
              {latestVersion.status}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {deployMsg && <span className="text-xs text-[#7b8db0] px-2">{deployMsg}</span>}
          <button
            onClick={handleSave}
            disabled={saveStatus === 'saving' || loading}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs border rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
              saveStatus === 'saved'
                ? 'border-[#2ec4b6] text-[#2ec4b6]'
                : saveStatus === 'error'
                ? 'border-[#E85A6B] text-[#E85A6B]'
                : 'text-[#7b8db0] border-[#1e2d4a] hover:border-[#2e3d5a] hover:text-white'
            }`}
          >
            {saveStatus === 'saving' ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            {saveStatus === 'saving' ? 'Saving…' : saveStatus === 'saved' ? 'Saved!' : saveStatus === 'error' ? 'Error' : 'Save'}
          </button>
          <button
            onClick={handleDeploy}
            disabled={deploying || loading || !latestVersion || latestVersion.status === 'published' || latestVersion.status === 'archived'}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-[#2ec4b6]/20 text-[#2ec4b6] border border-[#2ec4b6]/30 rounded-lg hover:bg-[#2ec4b6]/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {deploying ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
            {deployLabel}
          </button>
        </div>
      </header>

      {loadError && (
        <div className="px-4 py-2 text-xs text-[#E85A6B] border-b border-[#E85A6B]/20 bg-[#E85A6B]/5">
          {loadError}
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">
        <div className={`${leftCollapsed ? 'w-10' : 'w-56'} transition-all duration-200 border-r border-[#1e2d4a] flex flex-col bg-[#0d1117] shrink-0`}>
          {leftCollapsed ? (
            <button onClick={() => setLeftCollapsed(false)} className="flex items-center justify-center h-full text-[#7b8db0] hover:text-white transition-colors">
              <ChevronRight size={16} />
            </button>
          ) : (
            <>
              <button onClick={() => setLeftCollapsed(true)} className="flex items-center justify-end px-3 py-2 text-[#3a4a6b] hover:text-[#7b8db0] transition-colors">
                <ChevronDown size={13} className="rotate-90" />
              </button>

              <div
                className={`px-3 py-2 cursor-pointer ${activeSection === 'trigger' ? 'bg-[#1e2d4a]/50' : ''}`}
                onClick={() => setActiveSection(activeSection === 'trigger' ? '' : 'trigger')}
              >
                <div className="flex items-center gap-2 text-xs font-semibold text-[#e63946] uppercase tracking-wider">
                  <Zap size={11} /> Triggers
                </div>
              </div>
              {activeSection === 'trigger' && (
                <div className="px-3 pb-2">
                  {NODE_TYPES_CONFIG.filter(n => n.type === 'trigger').map(node => (
                    <div key={node.type} draggable onDragStart={e => e.dataTransfer.setData('nodeType', node.type)} className="flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-grab text-xs text-[#c0cce0] hover:bg-[#1e2d4a] hover:text-white transition-colors">
                      <div className="w-2 h-2 rounded-full shrink-0" style={{ background: node.color }} />
                      {node.label}
                    </div>
                  ))}
                </div>
              )}

              <div className="px-3 py-2 border-t border-[#1e2d4a]">
                <p className="text-xs font-semibold text-[#7b8db0] uppercase tracking-wider mb-2">Workflow Name</p>
                <input
                  value={workflowName}
                  onChange={e => setWorkflowName(e.target.value)}
                  placeholder="Workflow name"
                  className="w-full bg-[#0a0e1a] border border-[#1e2d4a] rounded-md px-2 py-1.5 text-xs text-white placeholder-[#3a4a6b] focus:outline-none focus:border-[#2ec4b6] transition-colors"
                />
                <p className="text-xs text-[#3a4a6b] mt-1.5 leading-relaxed">
                  Name is saved into the workflow spec on each Save.
                </p>
              </div>

              <div className="px-3 py-2 border-t border-[#1e2d4a] flex-1 overflow-y-auto">
                <div className="flex items-center justify-between cursor-pointer mb-2" onClick={() => setActiveSection(activeSection === 'integrations' ? '' : 'integrations')}>
                  <p className="text-xs font-semibold text-[#7b8db0] uppercase tracking-wider">Integrations</p>
                  <ChevronDown size={12} className={`text-[#7b8db0] transition-transform ${activeSection === 'integrations' ? '' : '-rotate-90'}`} />
                </div>
                {activeSection === 'integrations' && (
                  <div className="space-y-0.5">
                    {categories.filter(c => c !== 'trigger').map(cat => (
                      <div key={cat}>
                        <p className="text-xs text-[#3a4a6b] uppercase tracking-wider py-1 font-mono">{cat}</p>
                        {NODE_TYPES_CONFIG.filter(n => n.category === cat).map(node => (
                          <div key={node.type} draggable onDragStart={e => e.dataTransfer.setData('nodeType', node.type)} className="flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-grab text-xs text-[#c0cce0] hover:bg-[#1e2d4a] hover:text-white transition-colors">
                            <div className="w-2 h-2 rounded-full shrink-0" style={{ background: node.color }} />
                            {node.label}
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="px-3 py-2.5 border-t border-[#1e2d4a]">
                <button className="flex items-center gap-2 text-xs text-[#7b8db0] hover:text-white transition-colors w-full">
                  <Settings size={12} /> Settings
                </button>
              </div>
            </>
          )}
        </div>

        <div className="flex-1 relative" ref={dropRef} onDragOver={onDragOver} onDrop={onDrop}>
          {loading ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <Loader2 size={24} className="animate-spin text-[#3a4a6b]" />
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={onNodeClick}
              onPaneClick={onPaneClick}
              onInit={setRfInstance}
              nodeTypes={nodeTypes}
              fitView
            >
              <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#1e2d4a" />
              <Controls />
              <MiniMap nodeColor={n => (n.data as { color: string })?.color || '#1e2d4a'} maskColor="rgba(10, 14, 26, 0.8)" />
            </ReactFlow>
          )}
          {!loading && nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <div className="text-center">
                <div className="w-12 h-12 rounded-full border border-dashed border-[#1e2d4a] flex items-center justify-center mx-auto mb-3">
                  <Zap size={20} className="text-[#3a4a6b]" />
                </div>
                <p className="text-sm text-[#3a4a6b]">Drag a Trigger to start your workflow</p>
                <p className="text-xs text-[#2e3d5a] mt-1">or describe it in the chat on the right</p>
              </div>
            </div>
          )}
        </div>

        <div className="w-72 border-l border-[#1e2d4a] flex flex-col bg-[#0d1117] shrink-0">
          {selectedNode && (
            <NodeConfigPanel node={selectedNode} onClose={() => setSelectedNode(null)} onUpdateConfig={updateNodeConfig} projectNamespace={projectNamespace} />
          )}
          <div className={`flex flex-col ${selectedNode ? 'h-1/2' : 'flex-1'} border-t border-[#1e2d4a]`}>
            <div className="px-4 py-2.5 border-b border-[#1e2d4a] flex items-center gap-2">
              <div className="w-1.5 h-1.5 rounded-full bg-[#2ec4b6] animate-pulse" />
              <span className="text-xs font-semibold text-white">Generative Architect</span>
              <span className="text-xs text-[#3a4a6b] ml-auto font-mono">live</span>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-3">
              {chatMessages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[85%] rounded-xl px-3 py-2 text-xs leading-relaxed whitespace-pre-wrap ${msg.role === 'user' ? 'bg-[#2ec4b6]/20 text-[#2ec4b6] border border-[#2ec4b6]/20' : 'bg-[#1e2d4a] text-[#c0cce0]'}`}>
                    {msg.text}
                  </div>
                </div>
              ))}
              {chatBusy && (
                <div className="flex justify-start">
                  <div className="bg-[#1e2d4a] text-[#7b8db0] rounded-xl px-3 py-2 text-xs flex items-center gap-2">
                    <Loader2 size={11} className="animate-spin" /> Generating…
                  </div>
                </div>
              )}
              {chatMessages.length === 1 && !chatBusy && (
                <div className="space-y-1.5">
                  {CHAT_SUGGESTIONS.map((s, i) => (
                    <button key={i} onClick={() => setChatInput(s)} className="w-full text-left text-xs text-[#7b8db0] border border-[#1e2d4a] rounded-lg px-3 py-2 hover:border-[#2ec4b6]/30 hover:text-[#c0cce0] transition-colors">
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="p-3 border-t border-[#1e2d4a]">
              <div className="flex gap-2">
                <textarea
                  value={chatInput}
                  onChange={e => setChatInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleChat() } }}
                  placeholder="Describe your workflow..."
                  rows={2}
                  disabled={chatBusy}
                  className="flex-1 bg-[#0a0e1a] border border-[#1e2d4a] rounded-lg px-3 py-2 text-xs text-white placeholder-[#3a4a6b] focus:outline-none focus:border-[#2ec4b6] transition-colors resize-none disabled:opacity-40"
                />
                <button onClick={handleChat} disabled={!chatInput.trim() || chatBusy} className="px-3 bg-[#2ec4b6] text-[#0a0e1a] rounded-lg hover:bg-[#25a99d] disabled:opacity-40 disabled:cursor-not-allowed transition-colors self-end py-2">
                  {chatBusy ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
