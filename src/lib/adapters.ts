/**
 * Frontend ReactFlow ⇄ Backend Playbook DSL adapters.
 *
 * The frontend builder produces a graph of ReactFlow nodes + edges. The
 * backend stores workflows as a `workflow_spec` JSON (§19 PlaybookSpec DSL)
 * on a `PlaybookVersion`. These two pure functions translate between them
 * so the builder can save to the backend and reload an existing version.
 *
 * Mapping:
 *   trigger node   → spec.trigger  (not a step)
 *   scale          → step { type: "action", tool: "scale_deployment" }
 *   rollback       → step { type: "action", tool: "rollback_release" }
 *   retrain        → step { type: "action", tool: "retrain_pipeline" }
 *   alert          → step { type: "notification", tool: "slack_notification" }
 *   wait           → step { type: "delay", duration }
 *   condition      → step { type: "condition", condition, branches }
 *   approval       → step { type: "approval", approval_channel, approval_timeout }
 */

import type { Edge, Node } from 'reactflow'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type FrontendNodeType =
  | 'trigger' | 'scale' | 'rollback' | 'retrain'
  | 'alert' | 'wait' | 'condition' | 'approval'

export interface FrontendNodeData {
  label: string
  type: FrontendNodeType
  color: string
  description?: string
  config?: Record<string, string>
}

export type WorkflowNode = Node<FrontendNodeData>

export interface PlaybookStep {
  id: string
  name: string
  type: 'action' | 'approval' | 'condition' | 'delay' | 'notification'
  tool?: string
  input?: Record<string, unknown>
  on_success?: string
  on_failure?: string
  condition?: string
  branches?: { true?: string; false?: string }
  duration?: string
  approval_channel?: string
  approval_message?: string
  approval_timeout?: string
}

export interface PlaybookSpec {
  name: string
  description?: string
  version: string
  trigger: {
    incident_types: string[]
    severity_filter?: string[]
    entity_filter?: Record<string, unknown>
  }
  steps: PlaybookStep[]
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TOOL_BY_TYPE: Record<FrontendNodeType, string | null> = {
  trigger: null,
  scale: 'scale_deployment',
  rollback: 'rollback_release',
  retrain: 'retrain_pipeline',
  alert: 'slack_notification',
  wait: null,
  condition: null,
  approval: null,
}

const STEP_TYPE_BY_NODE: Record<FrontendNodeType, PlaybookStep['type'] | null> = {
  trigger: null,
  scale: 'action',
  rollback: 'action',
  retrain: 'action',
  alert: 'notification',
  wait: 'delay',
  condition: 'condition',
  approval: 'approval',
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'step'
}

function cfg(node: WorkflowNode, key: string): string | undefined {
  return node.data.config?.[key]?.trim() || undefined
}

// Per-node-type mapping from the frontend's config field labels to the backend
// tool's `input_schema` keys. Without this, `slug(label)` produces generic
// names like "service" / "replicas" / "message" that don't match what the
// Temporal activities actually read (e.g. scale_deployment_activity expects
// "deployment_name", not "service"). Task 11.8 will replace this hand-maintained
// table with a dynamic lookup against `/api/tools`; for now it unblocks the
// manual test.
const INPUT_KEY_MAP: Record<FrontendNodeType, Record<string, string>> = {
  trigger:   {},  // trigger has no step.input — Metric drives spec.trigger
  scale: {
    Service:   'deployment_name',
    Replicas:  'replicas',
    Direction: 'direction',        // not yet read by scale_deployment_activity;
                                    // kept as a hint for a future scale-up/down branch
  },
  rollback: {
    Service: 'deployment_name',
    Version: 'target_revision',
  },
  retrain: {
    Pipeline: 'pipeline_name',
    Dataset:  'dataset_uri',
  },
  alert: {
    Channel: 'channel',
    Message: 'message',
  },
  wait:      {},  // duration lands on step.duration, not step.input
  condition: {},  // condition/branches at step-level, not step.input
  approval: {
    Approver: 'approval_channel',
    Timeout:  'approval_timeout',
  },
}

// Implicit fields added when not specified in the config UI. Task 11.8d
// removed the Scale/Rollback `namespace: 'default'` hardcodes: the namespace
// now comes from the project context (passed to reactFlowToSpec via the
// options arg) and is only applied to scale/rollback steps when the caller
// supplies it.
const IMPLICIT_DEFAULTS: Record<FrontendNodeType, Record<string, string>> = {
  trigger:   {},
  scale:     {},
  rollback:  {},
  retrain:   {},
  alert:     {},
  wait:      {},
  condition: {},
  approval:  {},
}

// Only these node types receive a per-project namespace fallback. Others
// either don't need one (alert, wait, condition, approval, retrain) or
// carry their own (trigger — namespace lives in entity_filter not input).
const NAMESPACE_SCOPED_TYPES: ReadonlySet<FrontendNodeType> = new Set([
  'scale',
  'rollback',
])

function buildStepInput(
  node: WorkflowNode,
  opts: { namespace?: string } = {},
): Record<string, unknown> | undefined {
  const c = node.data.config
  if (!c) return undefined
  const type = node.data.type
  const keyMap = INPUT_KEY_MAP[type] ?? {}
  const entries = Object.entries(c).filter(([, v]) => v != null && v !== '')
  const mapped: Record<string, unknown> = { ...IMPLICIT_DEFAULTS[type] }
  for (const [k, v] of entries) {
    const backendKey = keyMap[k] ?? slug(k)
    mapped[backendKey] = v
  }
  // Project-scoped namespace fallback for scale/rollback. Only fills in when
  // the user hasn't already specified one via the config panel and the
  // caller gave us a namespace to use.
  if (opts.namespace && NAMESPACE_SCOPED_TYPES.has(type) && !mapped.namespace) {
    mapped.namespace = opts.namespace
  }
  if (Object.keys(mapped).length === 0) return undefined
  // Coerce numeric-looking strings to numbers for fields the backend expects
  // as integers (e.g. replicas). Simple heuristic — doesn't break for other
  // fields because they're already strings.
  for (const [k, v] of Object.entries(mapped)) {
    if (typeof v === 'string' && /^\d+$/.test(v)) {
      mapped[k] = Number(v)
    }
  }
  return mapped
}

// ---------------------------------------------------------------------------
// reactFlowToSpec — serialize builder state to backend DSL
// ---------------------------------------------------------------------------

export interface ReactFlowToSpecOptions {
  /**
   * Kubernetes namespace to apply to scale/rollback steps when the user
   * didn't override it in the node config. Typically the namespace owned
   * by the project containing this workflow. Task 11.8d — replaces the
   * former hardcoded 'default'.
   */
  namespace?: string
}

export function reactFlowToSpec(
  name: string,
  description: string,
  nodes: WorkflowNode[],
  edges: Edge[],
  opts: ReactFlowToSpecOptions = {},
): PlaybookSpec {
  const trigger = nodes.find(n => n.data.type === 'trigger')
  const metric = trigger ? cfg(trigger, 'Metric') : undefined
  const incidentType = metric ? `incident.${slug(metric)}` : 'incident.generic'

  // Assign deterministic step ids based on node id so round-tripping is stable.
  const stepIdOf = (nodeId: string) => `step_${nodeId}`

  const steps: PlaybookStep[] = []
  const edgesBySource = new Map<string, Edge[]>()
  for (const e of edges) {
    if (!edgesBySource.has(e.source)) edgesBySource.set(e.source, [])
    edgesBySource.get(e.source)!.push(e)
  }

  for (const node of nodes) {
    const stepType = STEP_TYPE_BY_NODE[node.data.type]
    if (stepType === null) continue  // trigger node has no step

    const outgoing = edgesBySource.get(node.id) ?? []
    const step: PlaybookStep = {
      id: stepIdOf(node.id),
      name: node.data.label,
      type: stepType,
    }

    const tool = TOOL_BY_TYPE[node.data.type]
    if (tool) step.tool = tool

    const input = buildStepInput(node, { namespace: opts.namespace })
    if (input) step.input = input

    if (node.data.type === 'wait') {
      step.duration = cfg(node, 'Duration') ?? '1m'
    } else if (node.data.type === 'condition') {
      const metricKey = cfg(node, 'Metric') ?? 'value'
      const op = cfg(node, 'Operator') ?? '>'
      const value = cfg(node, 'Value') ?? '0'
      step.condition = `${metricKey} ${op} ${value}`
      // Conditions use sourceHandle to distinguish true/false branches if present.
      const trueEdge = outgoing.find(e => e.sourceHandle === 'true') ?? outgoing[0]
      const falseEdge = outgoing.find(e => e.sourceHandle === 'false') ?? outgoing[1]
      step.branches = {
        true: trueEdge ? stepIdOf(trueEdge.target) : undefined,
        false: falseEdge ? stepIdOf(falseEdge.target) : undefined,
      }
    } else if (node.data.type === 'approval') {
      const channel = cfg(node, 'Approver')
      const timeout = cfg(node, 'Timeout')
      if (channel) step.approval_channel = channel
      if (timeout) step.approval_timeout = timeout
      step.approval_message = `Approve: ${node.data.label}`
    }

    if (node.data.type !== 'condition' && outgoing.length > 0) {
      step.on_success = stepIdOf(outgoing[0].target)
    }

    steps.push(step)
  }

  return {
    name,
    description: description || undefined,
    version: '1.0.0',
    trigger: { incident_types: [incidentType] },
    steps,
  }
}

// ---------------------------------------------------------------------------
// specToReactFlow — deserialize backend DSL to builder state
// ---------------------------------------------------------------------------

// Reverse-lookup: backend step → frontend node type.
const TYPE_BY_TOOL: Record<string, FrontendNodeType> = {
  scale_deployment: 'scale',
  rollback_release: 'rollback',
  retrain_pipeline: 'retrain',
  slack_notification: 'alert',
}

function nodeTypeFor(step: PlaybookStep): FrontendNodeType {
  if (step.type === 'delay') return 'wait'
  if (step.type === 'condition') return 'condition'
  if (step.type === 'approval') return 'approval'
  if (step.type === 'notification') return 'alert'
  if (step.tool && TYPE_BY_TOOL[step.tool]) return TYPE_BY_TOOL[step.tool]
  return 'scale'  // default for unknown action tools
}

// Palette colors (match NODE_TYPES_CONFIG in data.ts).
const COLOR_BY_TYPE: Record<FrontendNodeType, string> = {
  trigger: '#e63946',
  scale: '#3a86ff',
  rollback: '#ffbe0b',
  retrain: '#2ec4b6',
  alert: '#8338ec',
  wait: '#7b8db0',
  condition: '#ff6b35',
  approval: '#06d6a0',
}

const LABEL_BY_TYPE: Record<FrontendNodeType, string> = {
  trigger: 'Trigger',
  scale: 'Scale Deployment',
  rollback: 'Rollback',
  retrain: 'Retrain',
  alert: 'Send Alert',
  wait: 'Wait',
  condition: 'If / Else',
  approval: 'Human Approval',
}

function stepInputToConfig(
  input: Record<string, unknown> | undefined,
  nodeType: FrontendNodeType,
  fieldLabels: string[],
): Record<string, string> {
  if (!input) return {}
  const keyMap = INPUT_KEY_MAP[nodeType] ?? {}
  const result: Record<string, string> = {}
  for (const label of fieldLabels) {
    // Prefer the per-type backend key (e.g. Service → deployment_name), fall
    // back to the slugged label for types without a map entry (alert.Channel
    // slugs to "channel" which IS the backend key).
    const backendKey = keyMap[label] ?? slug(label)
    const v = input[backendKey];
    if (v !== undefined && v !== null) result[label] = String(v)
  }
  return result
}

// Labels must match CONFIG_FIELDS in NodeConfigPanel.tsx so the side panel
// renders pre-filled inputs correctly.
const CONFIG_FIELD_LABELS: Record<FrontendNodeType, string[]> = {
  trigger: ['Metric', 'Threshold', 'Window'],
  scale: ['Service', 'Replicas', 'Direction'],
  rollback: ['Service', 'Version'],
  retrain: ['Pipeline', 'Dataset'],
  alert: ['Channel', 'Message'],
  wait: ['Duration'],
  condition: ['Metric', 'Operator', 'Value'],
  approval: ['Approver', 'Timeout'],
}

export function specToReactFlow(
  spec: PlaybookSpec,
): { nodes: WorkflowNode[]; edges: Edge[] } {
  const nodes: WorkflowNode[] = []
  const edges: Edge[] = []

  const colX = 250
  const rowY = 120
  let row = 0

  // 1. Trigger node (synthesized from spec.trigger).
  const triggerId = 'trigger'
  const incidentType = spec.trigger.incident_types[0] ?? 'incident.generic'
  const triggerMetric = incidentType.replace(/^incident\./, '').replace(/_/g, ' ')
  nodes.push({
    id: triggerId,
    type: 'custom',    // must match nodeTypes key in workflow/[id]/page.tsx
    position: { x: 100, y: 100 },
    data: {
      label: LABEL_BY_TYPE.trigger,
      type: 'trigger',
      color: COLOR_BY_TYPE.trigger,
      config: { Metric: triggerMetric },
    },
  })

  // 2. Steps — each becomes a node.
  // Map backend step id → frontend node id (they're the same for round-trip stability).
  const nodeIdByStepId = new Map<string, string>()

  for (const step of spec.steps) {
    const nodeId = step.id
    nodeIdByStepId.set(step.id, nodeId)
    const nodeType = nodeTypeFor(step)

    let config: Record<string, string> = stepInputToConfig(
      step.input,
      nodeType,
      CONFIG_FIELD_LABELS[nodeType],
    )
    if (nodeType === 'wait' && step.duration) {
      config = { ...config, Duration: step.duration }
    } else if (nodeType === 'condition' && step.condition) {
      const match = step.condition.match(/^(\S+)\s+(\S+)\s+(.+)$/)
      if (match) {
        config = { ...config, Metric: match[1], Operator: match[2], Value: match[3] }
      }
    } else if (nodeType === 'approval') {
      if (step.approval_channel) config = { ...config, Approver: step.approval_channel }
      if (step.approval_timeout) config = { ...config, Timeout: step.approval_timeout }
    }

    row += 1
    nodes.push({
      id: nodeId,
      type: 'custom',    // must match nodeTypes key in workflow/[id]/page.tsx
      position: { x: 100 + colX * row, y: 100 + (row % 2 === 0 ? rowY : 0) },
      data: {
        label: step.name || LABEL_BY_TYPE[nodeType],
        type: nodeType,
        color: COLOR_BY_TYPE[nodeType],
        config: Object.keys(config).length ? config : undefined,
      },
    })
  }

  // 3. Edges.
  // First-step inference: if a step has no explicit incoming ref in the spec,
  // the first step in spec.steps order is the entry point from the trigger.
  const referenced = new Set<string>()
  for (const s of spec.steps) {
    if (s.on_success) referenced.add(s.on_success)
    if (s.on_failure) referenced.add(s.on_failure)
    if (s.branches?.true) referenced.add(s.branches.true)
    if (s.branches?.false) referenced.add(s.branches.false)
  }
  const entrySteps = spec.steps.filter(s => !referenced.has(s.id))
  for (const entry of entrySteps) {
    edges.push({
      id: `${triggerId}-${entry.id}`,
      source: triggerId,
      target: entry.id,
    })
  }

  for (const step of spec.steps) {
    if (step.on_success && nodeIdByStepId.has(step.on_success)) {
      edges.push({
        id: `${step.id}-success-${step.on_success}`,
        source: step.id,
        target: step.on_success,
      })
    }
    if (step.branches?.true && nodeIdByStepId.has(step.branches.true)) {
      edges.push({
        id: `${step.id}-true-${step.branches.true}`,
        source: step.id,
        sourceHandle: 'true',
        target: step.branches.true,
        label: 'true',
      })
    }
    if (step.branches?.false && nodeIdByStepId.has(step.branches.false)) {
      edges.push({
        id: `${step.id}-false-${step.branches.false}`,
        source: step.id,
        sourceHandle: 'false',
        target: step.branches.false,
        label: 'false',
      })
    }
  }

  return { nodes, edges }
}
