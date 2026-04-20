/**
 * Tests for the ReactFlow ⇄ PlaybookSpec adapter.
 *
 * Run with:   node --test --experimental-strip-types src/lib/adapters.test.ts
 * (Node 22.7+ supports stripping TypeScript types natively.)
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import { reactFlowToSpec, specToReactFlow, type PlaybookSpec, type WorkflowNode } from './adapters.ts'
import type { Edge } from 'reactflow'

const triggerNode: WorkflowNode = {
  id: 'trigger',
  type: 'workflow',
  position: { x: 0, y: 0 },
  data: {
    label: 'Trigger',
    type: 'trigger',
    color: '#e63946',
    config: { Metric: 'latency_p95', Threshold: '500ms', Window: '5min' },
  },
}

const scaleNode: WorkflowNode = {
  id: 'scale-1',
  type: 'workflow',
  position: { x: 200, y: 0 },
  data: {
    label: 'Scale Up',
    type: 'scale',
    color: '#3a86ff',
    config: { Service: 'fraud-model-v2', Replicas: '5', Direction: 'up' },
  },
}

const alertNode: WorkflowNode = {
  id: 'alert-1',
  type: 'workflow',
  position: { x: 400, y: 0 },
  data: {
    label: 'Notify SRE',
    type: 'alert',
    color: '#8338ec',
    config: { Channel: '#mlops-alerts', Message: 'Scaled up' },
  },
}

test('reactFlowToSpec: empty graph produces a valid spec shell', () => {
  const spec = reactFlowToSpec('My WF', 'desc', [], [])
  assert.equal(spec.name, 'My WF')
  assert.equal(spec.description, 'desc')
  assert.equal(spec.version, '1.0.0')
  assert.deepEqual(spec.trigger.incident_types, ['incident.generic'])
  assert.deepEqual(spec.steps, [])
})

test('reactFlowToSpec: trigger metric becomes incident_type', () => {
  const spec = reactFlowToSpec('T', '', [triggerNode], [])
  assert.equal(spec.trigger.incident_types[0], 'incident.latency_p95')
  assert.equal(spec.steps.length, 0, 'trigger must not become a step')
})

test('reactFlowToSpec: linear scale → alert produces two connected steps', () => {
  const edges: Edge[] = [
    { id: 'e1', source: 'trigger', target: 'scale-1' },
    { id: 'e2', source: 'scale-1', target: 'alert-1' },
  ]
  // No namespace option → scale step has no `namespace` unless the user put one
  // in the Scale node's config directly (Task 11.8d removed the hardcoded default).
  const spec = reactFlowToSpec('Scale then alert', '', [triggerNode, scaleNode, alertNode], edges)
  assert.equal(spec.steps.length, 2)

  const scaleStep = spec.steps[0]
  assert.equal(scaleStep.id, 'step_scale-1')
  assert.equal(scaleStep.type, 'action')
  assert.equal(scaleStep.tool, 'scale_deployment')
  assert.equal(scaleStep.on_success, 'step_alert-1')
  // Config labels are mapped to the backend tool's input_schema keys
  // (Service → deployment_name etc.) and numeric strings are coerced to numbers.
  assert.deepEqual(scaleStep.input, {
    deployment_name: 'fraud-model-v2',
    replicas: 5,
    direction: 'up',
  })

  const alertStep = spec.steps[1]
  assert.equal(alertStep.type, 'notification')
  assert.equal(alertStep.tool, 'slack_notification')
  assert.equal(alertStep.on_success, undefined, 'leaf step has no on_success')
})

test('reactFlowToSpec: namespace option lands on scale/rollback steps', () => {
  // Task 11.8d — when the workflow builder knows its project's namespace,
  // it passes it via the options bag and the adapter fills in scale/rollback
  // step inputs that didn't explicitly specify one.
  const edges: Edge[] = [
    { id: 'e1', source: 'trigger', target: 'scale-1' },
  ]
  const spec = reactFlowToSpec(
    'With ns', '', [triggerNode, scaleNode], edges, { namespace: 'ml' },
  )
  assert.equal(spec.steps[0].input?.namespace, 'ml',
    'scale step should inherit the project namespace when user left Namespace blank')

  // Other node types are NOT namespace-scoped — the option shouldn't leak
  // into e.g. alert steps.
  const alertOnly: WorkflowNode[] = [triggerNode, alertNode]
  const alertEdges: Edge[] = [{ id: 'e', source: 'trigger', target: 'alert-1' }]
  const spec2 = reactFlowToSpec('a', '', alertOnly, alertEdges, { namespace: 'ml' })
  assert.equal(spec2.steps[0].input?.namespace, undefined,
    'alert steps should not get a namespace from the project scope')
})

test('reactFlowToSpec: explicit Namespace in scale config beats the project fallback', () => {
  // If the operator DID fill in a Namespace field on the node (via the
  // UI picker in Task 11.8d), that wins over the project-level default.
  const scaleWithNs: WorkflowNode = {
    ...scaleNode,
    data: { ...scaleNode.data, config: { ...scaleNode.data.config!, Namespace: 'payments' } },
  }
  const edges: Edge[] = [{ id: 'e', source: 'trigger', target: 'scale-1' }]
  const spec = reactFlowToSpec(
    'override', '', [triggerNode, scaleWithNs], edges, { namespace: 'ml' },
  )
  // Config Namespace maps to 'namespace' via slug() (no entry in INPUT_KEY_MAP
  // for Namespace specifically; fallback to lower-case of the label).
  assert.equal(spec.steps[0].input?.namespace, 'payments')
})

test('reactFlowToSpec: wait node uses duration field, not input', () => {
  const wait: WorkflowNode = {
    id: 'w1', type: 'workflow', position: { x: 0, y: 0 },
    data: { label: 'Wait', type: 'wait', color: '#7b8db0', config: { Duration: '10min' } },
  }
  const spec = reactFlowToSpec('w', '', [wait], [])
  assert.equal(spec.steps[0].type, 'delay')
  assert.equal(spec.steps[0].duration, '10min')
})

test('reactFlowToSpec: condition emits true/false branches', () => {
  const cond: WorkflowNode = {
    id: 'c1', type: 'workflow', position: { x: 0, y: 0 },
    data: {
      label: 'If err > 5%', type: 'condition', color: '#ff6b35',
      config: { Metric: 'error_rate', Operator: '>', Value: '0.05' },
    },
  }
  const edges: Edge[] = [
    { id: 'e1', source: 'c1', sourceHandle: 'true', target: 'scale-1' },
    { id: 'e2', source: 'c1', sourceHandle: 'false', target: 'alert-1' },
  ]
  const spec = reactFlowToSpec('c', '', [cond, scaleNode, alertNode], edges)
  const condStep = spec.steps.find(s => s.id === 'step_c1')
  assert.ok(condStep)
  assert.equal(condStep.type, 'condition')
  assert.equal(condStep.condition, 'error_rate > 0.05')
  assert.equal(condStep.branches?.true, 'step_scale-1')
  assert.equal(condStep.branches?.false, 'step_alert-1')
  assert.equal(condStep.on_success, undefined, 'condition uses branches, not on_success')
})

test('reactFlowToSpec: approval node maps channel + timeout', () => {
  const appr: WorkflowNode = {
    id: 'a1', type: 'workflow', position: { x: 0, y: 0 },
    data: {
      label: 'Need sign-off', type: 'approval', color: '#06d6a0',
      config: { Approver: '#ml-leads', Timeout: '30min' },
    },
  }
  const spec = reactFlowToSpec('a', '', [appr], [])
  const step = spec.steps[0]
  assert.equal(step.type, 'approval')
  assert.equal(step.approval_channel, '#ml-leads')
  assert.equal(step.approval_timeout, '30min')
})

test('specToReactFlow: synthesizes trigger + one step', () => {
  const spec: PlaybookSpec = {
    name: 'x', version: '1.0.0',
    trigger: { incident_types: ['incident.gpu_oom'] },
    steps: [
      {
        id: 'step_scale',
        name: 'Scale up',
        type: 'action',
        tool: 'scale_deployment',
        input: { deployment_name: 'inference', replicas: '3', namespace: 'default' },
      },
    ],
  }
  const { nodes, edges } = specToReactFlow(spec)
  assert.equal(nodes.length, 2)

  const trigger = nodes.find(n => n.data.type === 'trigger')
  assert.ok(trigger)
  assert.equal(trigger.data.config?.Metric, 'gpu oom')

  const scale = nodes.find(n => n.data.type === 'scale')
  assert.ok(scale)
  assert.equal(scale.data.label, 'Scale up')
  assert.equal(scale.data.config?.Service, 'inference')
  assert.equal(scale.data.config?.Replicas, '3')

  // A trigger→entry-step edge must exist.
  assert.equal(edges.length, 1)
  assert.equal(edges[0].source, 'trigger')
  assert.equal(edges[0].target, 'step_scale')
})

test('specToReactFlow: condition branches produce labeled edges', () => {
  const spec: PlaybookSpec = {
    name: 'x', version: '1.0.0',
    trigger: { incident_types: ['incident.foo'] },
    steps: [
      {
        id: 'c',
        name: 'If',
        type: 'condition',
        condition: 'error_rate > 0.05',
        branches: { true: 't', false: 'f' },
      },
      { id: 't', name: 'Scale', type: 'action', tool: 'scale_deployment' },
      { id: 'f', name: 'Alert', type: 'notification', tool: 'slack_notification' },
    ],
  }
  const { edges } = specToReactFlow(spec)
  const trueEdge = edges.find(e => e.sourceHandle === 'true')
  const falseEdge = edges.find(e => e.sourceHandle === 'false')
  assert.ok(trueEdge)
  assert.ok(falseEdge)
  assert.equal(trueEdge.target, 't')
  assert.equal(falseEdge.target, 'f')
})

test('round-trip: reactFlowToSpec → specToReactFlow preserves shape', () => {
  const edges: Edge[] = [
    { id: 'e1', source: 'trigger', target: 'scale-1' },
    { id: 'e2', source: 'scale-1', target: 'alert-1' },
  ]
  const spec = reactFlowToSpec('rt', '', [triggerNode, scaleNode, alertNode], edges)
  const { nodes, edges: backEdges } = specToReactFlow(spec)

  // Trigger + 2 steps
  assert.equal(nodes.length, 3)
  // Trigger → entry + scale→alert
  assert.equal(backEdges.length, 2)

  // Node types preserved
  const types = nodes.map(n => n.data.type).sort()
  assert.deepEqual(types, ['alert', 'scale', 'trigger'])

  // Scale config survived the round trip (canonicalized to lowercase keys
  // in input, then reconstituted to pascal-ish labels).
  const scale = nodes.find(n => n.data.type === 'scale')
  assert.equal(scale?.data.config?.Service, 'fraud-model-v2')
  assert.equal(scale?.data.config?.Replicas, '5')
})
