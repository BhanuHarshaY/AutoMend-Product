---

## Screens

### Screen 1 — Project Dashboard (`/`)
Shows all ML projects as cards. Each project can have multiple workflows. Users can:
- Create, rename, delete projects
- Change project status (Active / Paused / Draft)
- View workflows per project via popover
- Add new workflows per project

### Screen 2 — Workflow Builder (`/workflow/[id]`)
Visual drag-and-drop workflow builder for a specific workflow. Users can:
- Drag nodes from the left sidebar onto the canvas
- Connect nodes by dragging from handle to handle
- Click a node to configure it in the right panel
- Delete nodes via the × button or Delete key
- Type in the chat interface to describe the workflow
- Save the workflow (stored in localStorage)

---

## Data Storage (Current — Frontend Only)

All data is currently stored in **localStorage**. This is temporary and needs to be replaced with backend API calls.

| Key | Value | Description |
|-----|-------|-------------|
| `automend-projects` | `Project[]` JSON | All projects with their workflows |
| `workflow-{id}` | `{ nodes, edges }` JSON | Saved ReactFlow state per workflow |

---

## Type Definitions

```typescript
// src/lib/data.ts

interface Workflow {
  id: string
  name: string
  description: string
  createdAt: string
}

interface Project {
  id: string
  name: string
  description: string
  status: 'active' | 'paused' | 'draft'
  createdAt: string
  lastRun: string | null
  workflows: Workflow[]
}
```

---

## Backend Integration Points

Below are all the places where the frontend expects backend API calls. Each section shows the current frontend-only implementation and what the backend integration should replace it with.

---

### 1. Load Projects on Home Page

**File:** `src/app/page.tsx`  
**Current:** Reads from `localStorage.getItem('automend-projects')`  
**Replace with:** `GET /api/projects`

```typescript
// Current
useEffect(() => {
  const saved = localStorage.getItem('automend-projects')
  if (saved) setProjects(JSON.parse(saved))
}, [])

// Replace with
useEffect(() => {
  fetch('/api/projects')
    .then(res => res.json())
    .then(data => setProjects(data))
}, [])
```

---

### 2. Create Project

**File:** `src/app/page.tsx` → `handleCreate()`  
**Current:** Saves to localStorage  
**Replace with:** `POST /api/projects`

```typescript
// Current
localStorage.setItem('automend-projects', JSON.stringify(updated))

// Replace with
await fetch('/api/projects', {
  method: 'POST',
  body: JSON.stringify({ name, description, status: 'draft' })
})
```

---

### 3. Delete Project

**File:** `src/app/page.tsx` → `handleDelete()`  
**Current:** Filters from localStorage  
**Replace with:** `DELETE /api/projects/:id`

---

### 4. Rename Project

**File:** `src/app/page.tsx` → `handleRename()`  
**Current:** Updates localStorage  
**Replace with:** `PATCH /api/projects/:id` with `{ name }`

---

### 5. Change Project Status

**File:** `src/app/page.tsx` → `handleStatusChange()`  
**Current:** Updates localStorage  
**Replace with:** `PATCH /api/projects/:id` with `{ status }`

---

### 6. Add New Workflow to Project

**File:** `src/app/page.tsx` → `WorkflowsPopover` → Add New Workflow button  
**Current:** Adds to localStorage and navigates  
**Replace with:** `POST /api/projects/:projectId/workflows`

```typescript
// Current
const newWorkflowId = `${project.id}-${Date.now()}`
// ... saves to localStorage
window.location.href = `/workflow/${newWorkflowId}`

// Replace with
const res = await fetch(`/api/projects/${project.id}/workflows`, {
  method: 'POST',
  body: JSON.stringify({ name: 'New Workflow' })
})
const { id } = await res.json()
window.location.href = `/workflow/${id}`
```

---

### 7. Load Workflow (nodes + edges)

**File:** `src/app/workflow/[id]/page.tsx` → `useEffect`  
**Current:** Reads from `localStorage.getItem('workflow-{id}')`  
**Replace with:** `GET /api/workflows/:id`

```typescript
// Current
const saved = localStorage.getItem(`workflow-${params.id}`)
if (saved) {
  const data = JSON.parse(saved)
  if (data.nodes) setNodes(data.nodes)
  if (data.edges) setEdges(data.edges)
}

// Replace with
const res = await fetch(`/api/workflows/${params.id}`)
const data = await res.json()
if (data.nodes) setNodes(data.nodes)
if (data.edges) setEdges(data.edges)
```

---

### 8. Save Workflow (nodes + edges)

**File:** `src/app/workflow/[id]/page.tsx` → `handleSave()`  
**Current:** Saves to localStorage  
**Replace with:** `PUT /api/workflows/:id`

```typescript
// Current
localStorage.setItem(`workflow-${params.id}`, JSON.stringify({ nodes, edges }))

// Replace with
await fetch(`/api/workflows/${params.id}`, {
  method: 'PUT',
  body: JSON.stringify({ nodes, edges, name: workflowName })
})
```

---

### 9. Deploy Workflow

**File:** `src/app/workflow/[id]/page.tsx` → Deploy button  
**Current:** Button is disabled with "backend pending" label  
**Replace with:** `POST /api/workflows/:id/deploy`

```typescript
// The Deploy button is already in the UI at the top right
// Just wire up the onClick:
onClick={async () => {
  await fetch(`/api/workflows/${params.id}/deploy`, { method: 'POST' })
  // Show success toast
}}
```

---

### 10. Chat / Generative Architect

**File:** `src/app/workflow/[id]/page.tsx` → `handleChat()`  
**Current:** Returns a hardcoded placeholder response  
**Replace with:** `POST /api/workflows/:id/generate`

```typescript
// Current
setTimeout(() => {
  setChatMessages(prev => [...prev, {
    role: 'ai',
    text: `Got it! Generating a workflow for: "${userMsg}". Backend integration coming soon.`
  }])
}, 800)

// Replace with
const res = await fetch(`/api/workflows/${params.id}/generate`, {
  method: 'POST',
  body: JSON.stringify({ prompt: userMsg })
})
const { nodes, edges, message } = await res.json()
setNodes(nodes)       // Populate the canvas with generated workflow
setEdges(edges)
setChatMessages(prev => [...prev, { role: 'ai', text: message }])
```

The expected response format from the backend:
```json
{
  "message": "Here's your workflow — I've added 3 nodes.",
  "nodes": [...],   // ReactFlow Node[] array
  "edges": [...]    // ReactFlow Edge[] array
}
```

---

### 11. Load Project Name in Workflow Builder

**File:** `src/app/workflow/[id]/page.tsx` → `useEffect`  
**Current:** Looks up project from localStorage  
**Replace with:** Include project name in the `GET /api/workflows/:id` response

---

## Node Types

The following node types are available in the workflow builder:

| Type | Label | Category | Description |
|------|-------|----------|-------------|
| `trigger` | Trigger | trigger | Starts workflow on metric event |
| `scale` | Scale Deployment | action | Scale replicas up or down |
| `rollback` | Rollback | action | Roll back to previous model version |
| `retrain` | Retrain | action | Trigger retraining pipeline |
| `alert` | Send Alert | action | Send Slack or email notification |
| `wait` | Wait | logic | Wait for specified duration |
| `condition` | If / Else | logic | Branch based on condition |
| `approval` | Human Approval | governance | Pause for human approval |

Each node has configurable fields defined in `src/components/NodeConfigPanel.tsx`.

---

## Running Locally

```bash
git clone https://github.com/sanjanamenon0/automend-ui-.git
cd automend-ui-
npm install
npm run dev
```

Open `http://localhost:3000`

To reset all data: open browser console and run `localStorage.clear()` then refresh.

---

## Tech Stack

| Tool | Purpose |
|------|---------|
| Next.js 14 | React framework with App Router |
| TypeScript | Type safety |
| Tailwind CSS | Styling |
| ReactFlow | Workflow canvas / node editor |
| lucide-react | Icons |
| localStorage | Temporary data persistence (replace with backend) |
