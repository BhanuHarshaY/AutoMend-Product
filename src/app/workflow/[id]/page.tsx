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
  BackgroundVariant,
  ReactFlowInstance,
} from 'reactflow'
import 'reactflow/dist/style.css'

import {
  ArrowLeft, Zap, Settings, Send, ChevronDown,
  ChevronRight, Save, Play,
} from 'lucide-react'
import { NODE_TYPES_CONFIG, SAMPLE_PROJECTS } from '@/lib/data'
import { WorkflowNode } from '@/components/WorkflowNode'
import { NodeConfigPanel } from '@/components/NodeConfigPanel'

const nodeTypes = { custom: WorkflowNode }
const INITIAL_NODES: Node[] = []

const CHAT_SUGGESTIONS = [
  'If fraud model latency exceeds 200ms, scale up replicas and alert the team',
  'When drift score > 0.5, trigger retraining and notify on Slack',
  'If GPU utilization drops below 20%, scale down and wait for approval',
]

export default function WorkflowPage({ params }: { params: { id: string } }) {
  const [project, setProject] = useState({
    id: params.id, name: '', description: '', status: 'draft'
  })

  const [nodes, setNodes, onNodesChange] = useNodesState(INITIAL_NODES)
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null)
  const [chatInput, setChatInput] = useState('')
  const [chatMessages, setChatMessages] = useState<{ role: 'user' | 'ai'; text: string }[]>([
    { role: 'ai', text: "Hi! Describe the remediation workflow you want to create and I'll build it for you." }
  ])
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [activeSection, setActiveSection] = useState<string>('integrations')
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saved'>('idle')
  const dropRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // Load project info
    const savedProjects = localStorage.getItem('automend-projects')
    const projects = savedProjects ? JSON.parse(savedProjects) : SAMPLE_PROJECTS
    const found = projects.find((p: { id: string }) => p.id === params.id)
    if (found) setProject(found)

    // Load saved workflow
    const savedWorkflow = localStorage.getItem(`workflow-${params.id}`)
    if (savedWorkflow) {
      const data = JSON.parse(savedWorkflow)
      if (data.nodes) setNodes(data.nodes)
      if (data.edges) setEdges(data.edges)
    }
  }, [params.id])

  const handleSave = () => {
    // Save workflow nodes and edges
    localStorage.setItem(`workflow-${params.id}`, JSON.stringify({ nodes, edges }))

    // Update project name in projects list
    const savedProjects = localStorage.getItem('automend-projects')
    const projects = savedProjects ? JSON.parse(savedProjects) : SAMPLE_PROJECTS
    const updated = projects.map((p: { id: string }) =>
      p.id === params.id ? { ...p, name: project.name } : p
    )
    localStorage.setItem('automend-projects', JSON.stringify(updated))

    setSaveStatus('saved')
    setTimeout(() => setSaveStatus('idle'), 2000)
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

  const handleChat = () => {
    if (!chatInput.trim()) return
    const userMsg = chatInput.trim()
    setChatMessages(prev => [...prev, { role: 'user', text: userMsg }])
    setChatInput('')
    setTimeout(() => {
      setChatMessages(prev => [...prev, {
        role: 'ai',
        text: `Got it! Generating a workflow for: "${userMsg}". Backend integration coming soon.`
      }])
    }, 800)
  }

  const categories = [...new Set(NODE_TYPES_CONFIG.map(n => n.category))]

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
          <span className="text-sm font-semibold text-white">{project.name}</span>
          <span className="text-xs text-[#3a4a6b] font-mono">/ workflow</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleSave}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs border rounded-lg transition-colors ${
              saveStatus === 'saved'
                ? 'border-[#2ec4b6] text-[#2ec4b6]'
                : 'text-[#7b8db0] border-[#1e2d4a] hover:border-[#2e3d5a] hover:text-white'
            }`}
          >
            <Save size={12} />
            {saveStatus === 'saved' ? 'Saved!' : 'Save'}
          </button>
          <button className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-[#2ec4b6]/20 text-[#2ec4b6] border border-[#2ec4b6]/30 rounded-lg cursor-not-allowed opacity-60">
            <Play size={12} /> Deploy (backend pending)
          </button>
        </div>
      </header>

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
                <p className="text-xs font-semibold text-[#7b8db0] uppercase tracking-wider mb-2">Project</p>
                <input
                  value={project.name}
                  onChange={e => setProject(prev => ({ ...prev, name: e.target.value }))}
                  placeholder="Project name"
                  className="w-full bg-[#0a0e1a] border border-[#1e2d4a] rounded-md px-2 py-1.5 text-xs text-white placeholder-[#3a4a6b] focus:outline-none focus:border-[#2ec4b6] transition-colors"
                />
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
          {nodes.length === 0 && (
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
            <NodeConfigPanel node={selectedNode} onClose={() => setSelectedNode(null)} onUpdateConfig={updateNodeConfig} />
          )}
          <div className={`flex flex-col ${selectedNode ? 'h-1/2' : 'flex-1'} border-t border-[#1e2d4a]`}>
            <div className="px-4 py-2.5 border-b border-[#1e2d4a] flex items-center gap-2">
              <div className="w-1.5 h-1.5 rounded-full bg-[#2ec4b6] animate-pulse" />
              <span className="text-xs font-semibold text-white">Generative Architect</span>
              <span className="text-xs text-[#3a4a6b] ml-auto font-mono">placeholder</span>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-3">
              {chatMessages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[85%] rounded-xl px-3 py-2 text-xs leading-relaxed ${msg.role === 'user' ? 'bg-[#2ec4b6]/20 text-[#2ec4b6] border border-[#2ec4b6]/20' : 'bg-[#1e2d4a] text-[#c0cce0]'}`}>
                    {msg.text}
                  </div>
                </div>
              ))}
              {chatMessages.length === 1 && (
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
                  className="flex-1 bg-[#0a0e1a] border border-[#1e2d4a] rounded-lg px-3 py-2 text-xs text-white placeholder-[#3a4a6b] focus:outline-none focus:border-[#2ec4b6] transition-colors resize-none"
                />
                <button onClick={handleChat} disabled={!chatInput.trim()} className="px-3 bg-[#2ec4b6] text-[#0a0e1a] rounded-lg hover:bg-[#25a99d] disabled:opacity-40 disabled:cursor-not-allowed transition-colors self-end py-2">
                  <Send size={13} />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}