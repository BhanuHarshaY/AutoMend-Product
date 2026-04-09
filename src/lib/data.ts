export type ProjectStatus = 'active' | 'paused' | 'draft'

export interface Project {
  id: string
  name: string
  description: string
  status: ProjectStatus
  createdAt: string
  triggerCount: number
  lastRun: string | null
}

export const SAMPLE_PROJECTS: Project[] = [
  {
    id: '1',
    name: 'Fraud Detection Monitor',
    description: 'Monitors fraud model latency and triggers rollback on degradation',
    status: 'active',
    createdAt: '2026-01-15',
    triggerCount: 3,
    lastRun: '2026-04-05 14:32',
  },
  {
    id: '2',
    name: 'GPU Utilization Guard',
    description: 'Scales down GPU resources when utilization drops below 20%',
    status: 'active',
    createdAt: '2026-02-01',
    triggerCount: 2,
    lastRun: '2026-04-06 09:10',
  },
  {
    id: '3',
    name: 'Data Drift Retrainer',
    description: 'Triggers retraining pipeline when drift score exceeds threshold',
    status: 'paused',
    createdAt: '2026-02-20',
    triggerCount: 1,
    lastRun: '2026-03-28 11:45',
  },
  {
    id: '4',
    name: 'Recommendation Model HA',
    description: 'High availability setup for recommendation service',
    status: 'draft',
    createdAt: '2026-03-10',
    triggerCount: 0,
    lastRun: null,
  },
]

export const NODE_TYPES_CONFIG = [
  { type: 'trigger',   label: 'Trigger',          color: '#e63946', category: 'trigger',    description: 'Starts the workflow on a metric event' },
  { type: 'scale',     label: 'Scale Deployment', color: '#3a86ff', category: 'action',     description: 'Scale replicas up or down' },
  { type: 'rollback',  label: 'Rollback',         color: '#ffbe0b', category: 'action',     description: 'Roll back to previous model version' },
  { type: 'retrain',   label: 'Retrain',          color: '#2ec4b6', category: 'action',     description: 'Trigger retraining pipeline' },
  { type: 'alert',     label: 'Send Alert',       color: '#8338ec', category: 'action',     description: 'Send Slack or email notification' },
  { type: 'wait',      label: 'Wait',             color: '#7b8db0', category: 'logic',      description: 'Wait for specified duration' },
  { type: 'condition', label: 'If / Else',        color: '#ff6b35', category: 'logic',      description: 'Branch based on condition' },
  { type: 'approval',  label: 'Human Approval',   color: '#06d6a0', category: 'governance', description: 'Pause for human approval via Slack' },
]