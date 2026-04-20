# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 1. Project Overview

**AutoMend** is an AI-powered MLOps incident remediation platform: logs and metrics from Kubernetes clusters are classified by an ML model, correlated into incidents, and remediated by durable workflows that execute approved playbooks.

**Two AI models in the system:**
- **Model 1 (Classifier)** — consumes 5-minute log windows, outputs a structured classification (failure type, confidence, evidence). Runs continuously at runtime.
- **Model 2 (Architect)** — takes a user's natural-language intent and generates a JSON playbook spec. Runs on demand at design time.

**Five architectural planes:**
1. **Design Plane** — users create/edit playbooks in the React UI; AI architect generates specs
2. **Ingestion Plane** — Fluent Bit → OTel Collector → log backend + Redis Streams
3. **Intelligence Plane** — window worker + classifier + correlation worker
4. **Orchestration Plane** — Temporal durable workflows that execute playbook DSL
5. **Control Plane** — FastAPI REST API + React UI

**Status:** Backend is complete (Phases 0–8). Frontend has a working localStorage-based prototype; wiring it to the backend is scheduled as Phase 9 (see `build_plan.md` and DECISION-013).

## 2. Tech Stack

| Layer | Tech | Version |
|---|---|---|
| API framework | FastAPI | 0.110+ |
| Durable workflows | Temporal Python SDK | 1.5+ |
| Primary DB | PostgreSQL + pgvector | 15+ / 0.7+ |
| Cache/streams/locks | Redis | 7+ |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic | 2.0+ / 1.13+ |
| Validation | Pydantic | 2.5+ |
| Auth | PyJWT (HS256) + Passlib/bcrypt | — |
| HTTP client | httpx | 0.27+ |
| Kubernetes | kubernetes_asyncio | 29.0+ |
| Python | CPython | 3.11+ |
| Metrics / alerts | Prometheus + Alertmanager | 2.50+ / 0.27+ |
| Logs | Loki + Grafana | latest |
| Frontend | Next.js 14 (App Router) + React 18 + ReactFlow | 14.2 / 18 / 11 |
| UI styling | Tailwind CSS (dark theme) + lucide-react icons | 3.4 |

## 3. Getting Started

### Prerequisites
- Docker Desktop 24+ (Compose v2)
- `kind`, `kubectl`, `helm` (for Option A — the primary path)
- Python 3.11+ via conda env `mlops_project` (for Option C — host-based inner loop)
- Node.js 18+ (Option C only; Option A ships the frontend as a container)

### Option A — Helm on kind (primary path; matches production shape)

Stands up everything in one `helm install`. No host processes, no `pip install`, no six terminals. See `MANUAL_TESTING.md` for the full walkthrough including the crashing-ML-pod + Fluent Bit wiring.

```bash
# 1. Create a kind cluster
kind create cluster --name automend-demo

# 2. Build + load the 4 app images
docker build -t automend/api:dev             -f infra/dockerfiles/Dockerfile.api             backend
docker build -t automend/worker:dev          -f infra/dockerfiles/Dockerfile.worker          backend
docker build -t automend/temporal-worker:dev -f infra/dockerfiles/Dockerfile.temporal-worker backend
docker build -t automend/frontend:dev        -f infra/dockerfiles/Dockerfile.frontend        .
for i in api worker temporal-worker frontend; do
  kind load docker-image automend/$i:dev --name automend-demo
done

# 3. Install the chart (waits for migrations Job to complete)
kubectl create namespace automend
helm install automend ./infra/helm/automend \
  --namespace automend \
  --values ./infra/helm/automend/values-local.yaml \
  --wait --timeout 5m

# 4. Smoke test + bootstrap admin
helm test automend -n automend
kubectl exec -n automend deployment/automend-api -- \
  env ADMIN_EMAIL=admin@local ADMIN_PASSWORD=admin123 \
  python scripts/bootstrap_admin.py

# 5. Access the UI
kubectl -n automend port-forward svc/automend-frontend 3000:3000
# open http://localhost:3000
```

### Option B — Docker Compose (single-host demo)
```bash
cd infra
docker compose up -d --build     # Build all images + start everything (including frontend)
docker compose ps                # Verify all services healthy
curl http://localhost:8000/health
docker compose logs -f api
docker compose down -v           # Stop + delete data
```

### Option C — Host-based backend (fast inner loop for backend hacking)
Trade off the realistic deployment model for reload-on-save + pdb attachability.

```bash
# 1. Start infra only
cd infra && docker compose -f docker-compose.infra.yml up -d && cd ..

# 2. Install backend deps + run migrations + seed data + bootstrap admin
cd backend
pip install -e ".[dev]"
alembic upgrade head
python scripts/seed_tools.py
python scripts/seed_rules.py
ADMIN_EMAIL=admin@local ADMIN_PASSWORD=admin123 python scripts/bootstrap_admin.py

# 3. Start each process in its own terminal
uvicorn main_api:app --host 0.0.0.0 --port 8000 --reload
python main_window_worker.py
python main_correlation_worker.py
python main_temporal_worker.py
python -m app.services.classifier_server   # port 8001

# 4. Start frontend
cd .. && npm install && npm run dev         # http://localhost:3000
```

Env vars (all backend config via `AUTOMEND_*`) — see `backend/.env.example`. CORS origins via `AUTOMEND_CORS_ORIGINS`. Frontend proxy target via `API_PROXY_TARGET` (defaults to `http://localhost:8000`).

### Option D — GKE Autopilot (demo-grade GCP deploy)

Ship the same chart to GKE in ~20 minutes with in-cluster dev-dep subcharts (not prod-grade). See `DEPLOY_GCP_QUICK.md` at the repo root for the step-by-step runbook: enable APIs → Artifact Registry → GKE Autopilot → push images → `helm install ... -f values-gcp-quick.yaml` → bootstrap admin → port-forward or LoadBalancer. For real production (Cloud SQL + Memorystore + Terraform + External Secrets), see Phase 12 in `build_plan.md`.

## 4. Project Structure

```
automend-ui-/
├── src/                              # Next.js frontend
│   ├── app/
│   │   ├── page.tsx                  # Project dashboard (still uses localStorage)
│   │   ├── workflow/[id]/page.tsx    # ReactFlow workflow builder (still uses localStorage)
│   │   ├── login/page.tsx            # Login form (posts to /api/auth/login)
│   │   ├── layout.tsx                # Root layout (wraps children in AppShell)
│   │   └── globals.css
│   ├── components/
│   │   ├── WorkflowNode.tsx          # Custom ReactFlow node
│   │   ├── NodeConfigPanel.tsx       # Right-panel node config editor
│   │   └── AppShell.tsx              # Client wrapper: AuthProvider + AuthGuard
│   └── lib/
│       ├── data.ts                   # Frontend types + sample data + NODE_TYPES_CONFIG
│       ├── api.ts                    # Typed backend client (NOT yet wired to page.tsx / workflow/[id])
│       └── auth-context.tsx          # React auth context + useAuth hook + AuthGuard
├── backend/                          # Python monorepo (4 process entrypoints)
│   ├── main_api.py                   # FastAPI app + WebSocket endpoint
│   ├── main_window_worker.py         # Redis stream → classifier
│   ├── main_correlation_worker.py    # Classified events → incidents → Temporal
│   ├── main_temporal_worker.py       # Temporal workflow + activity registration
│   ├── app/
│   │   ├── config.py                 # Pydantic Settings
│   │   ├── dependencies.py           # FastAPI DI (db, redis, temporal, auth)
│   │   ├── models/db.py              # 10 SQLAlchemy ORM models
│   │   ├── domain/                   # Pydantic domain models (events, incidents, keys, playbooks, rules, tools)
│   │   ├── stores/
│   │   │   ├── postgres_store.py     # Async CRUD for all tables
│   │   │   └── redis_store.py        # Windows, dedup, locks, streams
│   │   ├── services/                 # Classifier, architect, embedding, vector search, broadcast
│   │   ├── workers/                  # WindowWorker, CorrelationWorker
│   │   ├── temporal/                 # DynamicPlaybookExecutor + 18 activities
│   │   └── api/                      # 8 route modules (auth, tools, playbooks, incidents, rules, webhooks, workflows, design)
│   ├── alembic/versions/001_initial_schema.py
│   ├── scripts/                      # seed_data, seed_tools, seed_rules
│   └── tests/                        # 862 passing tests
├── infra/
│   ├── docker-compose.yml            # Full stack (infra + 5 app services)
│   ├── docker-compose.infra.yml      # Infra only (for host-based dev)
│   ├── dockerfiles/                  # Dockerfile.api, Dockerfile.worker, Dockerfile.temporal-worker, Dockerfile.frontend
│   ├── prometheus/ alertmanager/     # Config files
│   └── tests/                        # Compose file validation tests
├── backend_architecture.md           # Source of truth for backend design (~2000 lines)
├── build_plan.md                     # 34-task build plan (Phases 0–9)
├── PROGRESS.md                       # Task checklist + completion log
├── DECISIONS.md                      # 16 architecture decisions recorded during build
└── README.md                         # Frontend overview + 11 integration points
```

## 5. What's Built

**Backend (complete):**
- 11 Postgres tables with pgvector support (users, tools, **projects**, playbooks, playbook_versions, trigger_rules, incidents, incident_events, classifier_outputs, approval_requests, alert_rules) — `projects` added in Phase 9.2 (DECISION-017) as a parent for playbooks; a Project can own many Playbooks (e.g., one project per ML service, separate playbooks per failure mode). **Task 11.8c** binds each project to a unique Kubernetes `namespace` (DNS-1123 label, UNIQUE constraint) and replaces the display-only `status` enum with a `playbooks_enabled` boolean kill switch consulted by the CorrelationWorker before starting remediation workflows (DECISION-028). Alembic migration `003_projects_namespace_kill_switch.py`.
- Alembic initial migration + `002_projects.py` + 2 idempotent seed scripts (14 tools, 5 alert rules)
- 8 Pydantic domain modules covering the full type system
- `postgres_store.py`: ~30 async CRUD functions; `redis_store.py`: windows, dedup, cooldowns, active incident cache, distributed locks (Lua CAS), Redis Streams
- FastAPI app with 43 HTTP endpoints + WebSocket endpoint + JWT auth + RBAC (admin/operator/editor/viewer) — Task 11.8b adds 2 cluster discovery routes
- **Cluster discovery service (Task 11.8b):** `app/services/k8s_client.py` wraps `kubernetes_asyncio` with an async-lock-guarded 30s in-memory cache; `app/api/routes_clusters.py` surfaces `GET /api/clusters/default/namespaces` + `GET /api/clusters/default/namespaces/{ns}/resources?kind=deployment|statefulset|daemonset|pod`. Powers the workflow-builder's namespace picker + deployment-name dropdowns coming in Task 11.8d. In-cluster SA config first, kubeconfig fallback for dev. System namespaces (`kube-*`/`automend`/`logging`/etc.) filtered by default; `?include_system=true` disables. Errors map to 502 (K8s upstream down) or 422 (unsupported kind). Single "default" cluster for now; path shape supports multi-cluster without breaking clients.
- WindowWorker: consumes `normalized_logs` stream, rolling 5-min windows, calls classifier, emits `classified_events`
- Classifier service: standalone FastAPI, rule-based regex matcher for all 14 taxonomy labels (§10.3)
- CorrelationWorker: dedup + cooldown + severity escalation + Temporal workflow start/signal
- Temporal: `DynamicPlaybookExecutor` workflow (interprets any playbook DSL: action/approval/condition/delay/parallel/notification steps, templates, retries, signals) + 18 activities (4 infra, 8 K8s, 3 notification, 1 Prometheus, 1 Jira, 1 diagnostics)
- Embedding service (OpenAI-compatible) + pgvector search + Architect client (Anthropic Messages API) + design routes (rag_search, generate_workflow, validate_workflow)
- Broadcast service + `WS /api/ws/incidents` for real-time UI updates

**Frontend (Phase 9.3 — dashboard + builder wired to backend):**
- **Project dashboard** (`src/app/page.tsx`) — loads projects via `api.projects.list()` + parallel `api.projects.get(id)` for playbooks. Create/delete/rename/status-change all hit `api.projects.*` with optimistic UI + rollback on error. The WorkflowsPopover's "Add New Workflow" creates a backend playbook scoped to the project (+ seeds an empty version) before navigating.
- **Workflow builder** (`src/app/workflow/[id]/page.tsx`) — params.id is a backend playbook UUID. On mount loads playbook + newest version and runs the spec through `specToReactFlow()`. Save calls `reactFlowToSpec()` + `api.playbooks.saveVersion()`. The chat panel is live: sends the user's intent to `api.design.generateWorkflow()` and populates the canvas with the generated spec. The Deploy button walks the version state machine (current→validated→approved→published) via `api.playbooks.transitionStatus()`. LocalStorage is kept only as a per-playbook draft buffer (`automend-workflow-draft-{id}`) for tab-crash recovery — cleared on successful save.
- **Adapter layer (Phase 9.2 done):** `src/lib/adapters.ts` — `reactFlowToSpec()` serializes the ReactFlow builder state (nodes + edges + trigger) into a backend `PlaybookSpec` (§19 DSL), and `specToReactFlow()` round-trips in reverse. Handles all 8 frontend node types. 9 unit tests via `node:test` + `--experimental-strip-types`, no new npm deps.
- **Typed API client** (`src/lib/api.ts`) — now includes `api.projects.*` (list/get/create/update/delete), extended `api.playbooks.create(…, projectId?)`, and everything the pages need. Used by all pages.
- Next.js proxy in `next.config.js` forwards `/api/*` to backend.
- **Auth flow (Phase 9.1 done):** `src/lib/auth-context.tsx` (AuthProvider + useAuth + AuthGuard), `src/app/login/page.tsx`, `src/components/AppShell.tsx` (client wrapper injected into the root layout). On app mount, reads the JWT from localStorage and calls `/api/auth/me`; silently clears bad tokens. Unauthenticated users are redirected to `/login`.
- **Incidents dashboard (Phase 9.4 done):** `src/app/incidents/page.tsx` lists incidents with severity-colored stat cards, filter tabs (all/open/acknowledged/in_progress/resolved), and live WebSocket updates via `connectIncidentEvents('all')` — new incidents are prepended, existing rows re-fetch on each event, a "Live / Connecting…" pill shows WS state. `src/app/incidents/[id]/page.tsx` shows the incident summary, entity + evidence, a remediation-workflow panel (fetches `api.workflows.get(temporal_workflow_id)` when set), and a vertical event timeline with per-event-type icons and payload JSON. Acknowledge/Resolve header buttons call `api.incidents.acknowledge`/`resolve` and are gated to admin/operator via `useAuth`. The same WebSocket refreshes the detail view in-place.
- **Top nav:** inline Projects ↔ Incidents links in the headers of `/` and `/incidents` (no shared shell component; the workflow builder keeps its own full-screen header).

**Infrastructure:**
- Two Docker Compose files (full stack + infra-only)
- Four Dockerfiles (api, worker, temporal-worker, frontend — the frontend one is a 3-stage `node:20-slim` build with Next.js standalone output, ~321MB, added in Phase 11.1; DECISION-023)
- Full `docker-compose.yml` includes a `frontend` service that depends on `api` and proxies `/api/*` via `API_PROXY_TARGET=http://api:8000` at runtime
- **Helm chart (Phase 11.2–11.6):** `infra/helm/automend/` is the full single-chart deployment unit. Scaffolding (Chart.yaml, values.yaml, values-local.yaml, `_helpers.tpl`, NOTES.txt, .helmignore); 6-component workload topology (Deployments + Services for api/classifier/frontend, outbound-only Deployments for the three workers); config plane (`configmap.yaml` emits 23 non-secret `AUTOMEND_*` keys with hostnames auto-resolving through `automend.postgresHost` / `redisHost` / `temporalServerUrl` / `classifierServiceUrl` helpers; `secret.yaml` emits 8 keys when `secrets.create=true`; `ingress.yaml` single Ingress `/api` → api, `/` → frontend with WebSocket annotations; `serviceaccount.yaml` Workload-Identity-ready); and local-dev deps (`postgres-dev.yaml` with pgvector/pgvector:pg16 + PVC, `redis-dev.yaml` with redis:7-alpine, `temporal-dev.yaml` with temporalio/auto-setup:1.24.2 using SQLite — all three in-house templates rather than external Helm subcharts; DECISION-024); plus `migrations-job.yaml` as a Helm `post-install,post-upgrade` hook that waits for Postgres, runs `alembic upgrade head`, then seeds tools + rules. Values-local produces 21 resources with one `helm install`; Phase 12's `values-gcp.yaml` flips the three dev-dep toggles off and points at managed Cloud SQL / Memorystore / Temporal Cloud via `external.*`. The same chart ships to both targets — only the values file differs. **Test harness (Phase 11.6):** `infra/helm/tests/test_chart.py` runs 71 offline pytest cases that lint + render the chart and assert structural invariants (topology, dev-dep images, the 23+8 `AUTOMEND_*` config keys against `backend/app/config.py`, Ingress routing, security contexts on every Deployment, resources on every container, migration Job hooks, both secret modes, **RBAC rendering in both modes — Task 11.8a**) — skip cleanly if helm isn't on PATH; auto-detect the winget install location on Windows. Plus an in-cluster `helm.sh/hook: test` Pod at `templates/tests/test-health.yaml` that `helm test <release>` uses to smoke-check `/health` on api + classifier + frontend. **RBAC plane (Task 11.8a):** `templates/rbac.yaml` conditionally renders a `ClusterRole` + `ClusterRoleBinding` when `rbac.clusterWide: true` (grants `apps/deployments` + `apps/deployments/scale` + `pods` + `pods/log` cluster-wide plus `get/list/watch` on `namespaces` — the last needed by Task 11.8b's clusters API) and/or one `Role` + `RoleBinding` per entry in `rbac.targetNamespaces` (namespace-scoped versions of the same deployment + pod verbs; omit `namespaces` because Roles can't grant cluster-scoped resources). Both modes are independent toggles, stack-able. Default values.yaml leaves both off so production opts in explicitly; `values-local.yaml` turns on both for the kind-based dev loop.
- Prometheus config + 5 alert rules, Alertmanager routing, Loki, Grafana

**Production GCP infrastructure (Phase 12 — in progress):**
- **Terraform workspace** (Task 12.1 — DECISION-031): `infra/terraform/` scaffolded with `versions.tf` (Terraform >=1.9.0 <2.0.0; google/google-beta ~>6.0, kubernetes ~>2.30, helm ~>2.13), `backend.tf` (GCS partial-config backend; bucket passed at `init` time so one tree serves multiple envs), `variables.tf` (project_id required; region/zone/env/name_prefix with validation), `providers.tf` (google + google-beta wired to vars; kubernetes+helm configured from GKE module outputs), `.terraform-version` (1.9.8), `terraform.tfvars.example`, `README.md` with bucket-bootstrap walkthrough. `.terraform.lock.hcl` checked in for provider-hash reproducibility.
- **GKE module** (Task 12.2 — DECISION-032): `infra/terraform/modules/gke/` creates a regional Standard GKE cluster (`location = region` for 3-zone control-plane HA), VPC-native with pod + service secondary ranges, private nodes + public master gated by `master_authorized_networks`, Workload Identity pool `PROJECT.svc.id.goog`, REGULAR release channel, CALICO network policy, shielded nodes. Single `google_container_node_pool` of e2-standard-4 × `node_count` per zone (default 1 → 3 nodes total), `GKE_METADATA` workload-metadata, auto-repair/upgrade, SURGE upgrade strategy. Dedicated `${prefix}-gke-nodes` SA with 5 minimum roles (logWriter, metricWriter, monitoring.viewer, stackdriver.resourceMetadata.writer, artifactregistry.reader) — replaces the default compute SA. Root module adds: `google_project_service` enabling 9 APIs, `google_compute_network` + subnet with pod (/16) + service (/20) secondary ranges + `private_ip_google_access`, Cloud Router + Cloud NAT for private-node egress (needed so pods can reach Gemini API / Slack webhooks). Live-validated on `automend-demo-2026` project: 21 resources, 3 nodes Ready on v1.35.1-gke.1396002 across us-central1-{b,c,f}.
- **Cloud SQL module** (Task 12.3 — DECISION-033): `infra/terraform/modules/cloud-sql/` creates a private-IP-only Postgres 15 instance (`db-custom-2-7680` default), daily backups + PITR + 7-day WAL retention, query insights on, IAM auth enabled, 5 database flags (pg_stat_statements all, slow-query log >1s, log_connections/disconnections). `google_sql_database` (`automend`, UTF8), `google_sql_user` (`automend` BUILT_IN, password from `random_password` → Secret Manager `${instance}-db-app-password`). Availability type ZONAL for dev / REGIONAL for prod (derived from `env`). Root `main.tf` adds PSA plumbing (`google_compute_global_address` + `google_service_networking_connection` — shared with 12.4 Memorystore) and a pod-level `google_service_account "app"` (`${prefix}-app`) distinct from the node SA; gets `roles/cloudsql.client`, `roles/cloudsql.instanceUser`, plus `roles/secretmanager.secretAccessor` scoped to just the DB password secret. pgvector NOT enabled at Terraform time — deferred to Task 12.6's Helm post-install Job. Live-validated on automend-demo-2026: pgvector 0.8.1 enabled via `kubectl run --rm` + `psql` against the private IP.
- **Memorystore module** (Task 12.4 — DECISION-034): `infra/terraform/modules/memorystore/` creates a STANDARD_HA Redis 7.2 instance (1 GB default, 1 read replica + automatic failover), `auth_enabled = true` (Memorystore auto-generates the auth string — we can't supply our own), `transit_encryption_mode = SERVER_AUTHENTICATION` (server-auth TLS; clients validate Memorystore's CA cert, no client cert needed), `connect_mode = PRIVATE_SERVICE_ACCESS` on the VPC (shares the PSA peering from 12.3). Auth string persisted to Secret Manager as `${instance_name}-auth`; rotation via `terraform apply` writes a new secret version. Module outputs `server_ca_cert` (PEM) for pod TLS trust-store mounting by 12.6. IAM: app SA gets `roles/redis.viewer` + `secretmanager.secretAccessor` scoped to the auth secret. Live-validated: `redis-cli … --tls --insecure PING` → `PONG`.
- **Artifact Registry / helm_release** — pending Tasks 12.5–12.6.

## 6. API Routes

Auth (`/api/auth`):
- `POST /login`, `POST /register` (admin), `GET /me`, `POST /refresh`

Tools (`/api/tools`):
- `GET /`, `GET /{id}`, `POST /` (admin), `PUT /{id}` (admin), `DELETE /{id}` (admin soft-delete)

Clusters (`/api/clusters`) — Task 11.8b:
- `GET /{cluster}/namespaces` (editor+; `?include_system=true` disables system-namespace filter) — only `cluster=default` resolves today; unknown → 404
- `GET /{cluster}/namespaces/{ns}/resources?kind=deployment` (editor+) — supports `deployment|statefulset|daemonset|pod`; 30s in-memory cache on both endpoints; requires the `namespaces` cluster-verb shipped by `rbac.clusterWide: true` in Task 11.8a

Projects (`/api/projects`) — reshaped by Task 11.8c:
- `GET /` (optional `?enabled=true|false` filter on `playbooks_enabled`), `POST /` (editor+; `namespace` required, 409 if namespace already owned by another project), `GET /{id}` (returns project with its playbooks), `PATCH /{id}` (editor+ for name/description/owner_team, operator+ for `playbooks_enabled`; `namespace` is immutable post-create), `DELETE /{id}` (admin — cascades to playbooks, frees the namespace for reuse)

Playbooks (`/api/playbooks`):
- `GET /`, `POST /` (editor+, accepts optional `project_id`), `GET /{id}` (with versions), `GET /{id}/versions/{vid}`, `POST /{id}/versions` (editor+), `PATCH /{id}/versions/{vid}/status` (operator+) — **Task 11.8e:** when a version transitions to `published`, all active `trigger_rules` pointing at sibling versions of the same playbook are auto-repointed to this new version in the same DB transaction (no more manual `UPDATE trigger_rules` after every publish). `DELETE /{id}` (admin)

Incidents (`/api/incidents`):
- `GET /` (filters), `GET /stats`, `GET /{id}`, `PATCH /{id}` (operator+), `POST /{id}/acknowledge` (operator+), `POST /{id}/resolve` (operator+), `GET /{id}/events`, `GET /{id}/workflow`

Rules (`/api/rules`):
- `GET /`, `POST /` (editor+), `PUT /{id}` (editor+), `DELETE /{id}` (admin), `GET /trigger-rules`

Webhooks (`/api/webhooks`):
- `POST /alertmanager`, `POST /ingest/otlp` — no auth, external signal ingestion

Workflows (`/api/workflows`):
- `GET /`, `GET /{workflow_id}`, `POST /{workflow_id}/signal` (operator+), `POST /{workflow_id}/cancel` (operator+)

Design (`/api/design`):
- `POST /rag_search`, `POST /generate_workflow` (editor+), `POST /validate_workflow`

WebSocket:
- `WS /api/ws/incidents?token=<jwt>&channel=all|incidents|workflows`

Health:
- `GET /health` — no auth

## 7. Testing

**Current baseline:** 770 pytest passing, 1 skipped, mypy + ruff both clean. 9 frontend adapter tests passing.

```bash
cd backend
conda run -n mlops_project pytest tests/ -v              # Backend tests
conda run -n mlops_project pytest ../infra/tests/ -v     # Infra compose tests
conda run -n mlops_project mypy app/                     # Type check
conda run -n mlops_project ruff check app/               # Lint
conda run -n mlops_project ruff check app/ --fix         # Auto-fix lint

# Frontend adapter tests (Node 22.7+ required for --experimental-strip-types)
cd .. && node --test --experimental-strip-types src/lib/adapters.test.ts
```

**Test organization:**
- `tests/test_dependencies.py` — DI + auth
- `tests/test_seed.py` — seed data validation + DB integration
- `tests/test_models.py` — SQLAlchemy model structure
- `tests/test_skeleton.py` — config + app creation
- `tests/test_domain/` — Pydantic domain models (round-trip, enums, defaults)
- `tests/test_stores/` — PostgresStore + RedisStore CRUD
- `tests/test_services/` — classifier, embedding, vector search, architect, broadcast
- `tests/test_workers/` — WindowWorker, CorrelationWorker
- `tests/test_temporal/` — DynamicPlaybookExecutor, activities, correlation→Temporal
- `tests/test_api/` — all 8 route modules
- `tests/test_e2e_logs_to_incident.py` — Flows B/C/D (§27)
- `tests/test_e2e_full_pipeline.py` — complete design→runtime→resolution

**Infrastructure requirements for tests (skip gracefully if unavailable):**
- **Postgres tests** (stores, api, workers, e2e): need Postgres on **5432** — start via `docker compose -f infra/docker-compose.infra.yml up -d postgres`
- **Redis tests** (redis_store, workers, broadcast): need Redis on **6380** — start via `docker run -d -p 6380:6379 redis:7-alpine`. The app's own Redis is 6379; tests use 6380 to avoid dev-machine conflicts (e.g., Ray occupying 6379).
- **Temporal tests**: use `WorkflowEnvironment.start_time_skipping()` — no Docker needed.
- **Webhook integration test** (1 test): needs real Redis on 6379 (not 6380) — skipped when 6379 has Ray or similar non-Redis service.

## 8. Common Commands

```bash
# --- INFRASTRUCTURE ---
cd infra
docker compose up -d --build                            # Full stack
docker compose -f docker-compose.infra.yml up -d        # Infra only
docker compose logs -f api                              # Tail logs
docker compose ps                                       # Status
docker compose down -v                                  # Stop + wipe data

# --- BACKEND (from backend/) ---
pip install -e ".[dev]"                                 # Install deps
alembic upgrade head                                    # Migrate DB
alembic revision --autogenerate -m "message"            # New migration
python scripts/seed_tools.py                            # Seed tools
python scripts/seed_rules.py                            # Seed alert rules

# Run each process (separate terminals)
uvicorn main_api:app --reload --host 0.0.0.0 --port 8000
python main_window_worker.py
python main_correlation_worker.py
python main_temporal_worker.py
python -m app.services.classifier_server

# Tests / type / lint
conda run -n mlops_project pytest tests/ -v
conda run -n mlops_project pytest tests/test_api/ -v    # Route tests only
conda run -n mlops_project mypy app/
conda run -n mlops_project ruff check app/ --fix

# --- FRONTEND (from repo root) ---
npm install
npm run dev                                             # Dev server @ :3000
npm run build                                           # Production build
npm run start

# --- AUTH (from host) ---
# 1. Register admin via psycopg2 one-liner (or write a script)
# 2. Login:
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"pw"}'
```

## 9. Architecture Notes

**Source of truth:** `backend_architecture.md` (~2000 lines, 33 sections) contains the full backend specification. Read this before changing architecture.

**Key conventions in this codebase:**
- Stores use **free functions** that take an `AsyncSession` or `Redis` client param (not classes) — see DECISION-005. This keeps transaction control in the caller.
- Temporal activities create their own DB session via `_get_session()` helper — they can't use FastAPI DI.
- Temporal client uses `lazy=True` so API startup doesn't hang if Temporal is down (DECISION-004).
- Dedup and cooldown keys live in Redis with TTLs. Active incident state is a Redis hash with explicit delete on resolution (no TTL).
- pgvector queries use `CAST(:embedding AS vector)` not `::vector` (asyncpg parameter-parsing workaround — DECISION-011).
- Workflow auth for WebSocket uses `?token=` query param (DECISION-012) — necessary because browsers can't set custom headers on `WebSocket`.
- Refresh tokens are stateless JWTs with `type: "refresh"` claim (no DB storage — DECISION-007).
- Playbook versions have a state machine (draft→generated→validated→approved→published→archived) enforced at the service layer.

**See `DECISIONS.md`** for all 15 recorded architecture decisions with context/alternatives/consequences.

### Frontend conventions (Next.js 14)
- Path alias `@/*` → `./src/*`
- Tailwind CSS only (no CSS modules). Dark theme. Custom colors in `tailwind.config.js`.
- Fonts: DM Sans + JetBrains Mono via Google Fonts in `globals.css`
- All pages use `'use client'` directive (no server components currently)
- Icons: lucide-react
- State: React hooks only (no Redux/Zustand). ReactFlow state via `useNodesState` / `useEdgesState`.

### Python environment
Use conda env `mlops_project` for all local Python work. Install packages with **pip**, not conda:
```bash
conda activate mlops_project
pip install <package>
```

## 10. Known Limitations / TODO

**AI models — production paths wired; dev fallbacks still in place:**
- **Classifier (Model 1)** — both paths work. The `ClassifierClient` (`app/services/classifier_client.py`) auto-detects service shape and returns the 14-label core response regardless of which classifier it talks to. Set `AUTOMEND_CLASSIFIER_SERVICE_URL` + `AUTOMEND_CLASSIFIER_ENDPOINT=/predict_anomaly` to point at the real RoBERTa service in `inference_backend/ClassifierModel/` (DECISION-019); leave defaults to keep the rule-based stub (DECISION-009). Regex patterns live in the shared `app/services/log_patterns.py` module — the stub uses them ordered; the 7→14 taxonomy refinement (`app/services/classifier_taxonomy.py`, DECISION-021) uses them by-label to split coarse RoBERTa labels into finer core labels (e.g. `Resource_Exhaustion` + CUDA logs → `failure.gpu`, + OOMKilled → `failure.memory`, + disk-full → `failure.storage`). Until trained weights land in `inference_backend/ClassifierModel/models/`, the RoBERTa head is randomly initialized — the translation layer is correct but label predictions are meaningless, so most deployments continue to use the stub.
- **Architect (Model 2)** — **three providers** work behind one `ArchitectClient` (DECISION-022 + DECISION-029). Set `AUTOMEND_ARCHITECT_PROVIDER=anthropic` (default) to use Anthropic's Messages API via `AUTOMEND_ARCHITECT_API_KEY`; `AUTOMEND_ARCHITECT_PROVIDER=local` + `AUTOMEND_ARCHITECT_API_BASE_URL=http://localhost:8002` to hit the Qwen proxy (DECISION-020) at `AUTOMEND_ARCHITECT_LOCAL_ENDPOINT` (default `/generate_workflow`); or `AUTOMEND_ARCHITECT_PROVIDER=gemini` + a Google AI Studio key as `AUTOMEND_ARCHITECT_API_KEY` to hit Gemini 2.5 Flash/Pro via `generativelanguage.googleapis.com` (default model `gemini-2.5-flash`, override via `AUTOMEND_ARCHITECT_MODEL`). Prompt content (`_build_system_prompt`, RAG-selected tools, example playbooks, DSL schema) is identical across providers — only the HTTP envelope differs. The JSON extractor (`_extract_json`) tolerates prose-wrapped output from any provider: strips ``` fences, falls back to the widest `{...}` substring, logs a 1500-char preview on total failure.
- **Notifications (Slack)** — **two Slack modes** (DECISION-030). Set `AUTOMEND_SLACK_WEBHOOK_URL` to an incoming-webhook URL to use webhook mode (simple, no bot token required; channel baked into URL); `slack_notification_activity` POSTs `{text, attachments}` directly to the URL. Or set `AUTOMEND_SLACK_BOT_TOKEN` + `AUTOMEND_SLACK_DEFAULT_CHANNEL` for classic bot-API mode (`chat.postMessage` with Bearer auth, supports dynamic channels). Webhook wins if both are set. `slack_approval_activity` now also posts a best-effort `:warning: Approval required` notification when a workflow pauses for approval, using the same mode selection.
- **End-to-end integration test** — `backend/tests/test_e2e_inference_integration.py` skips gracefully when the inference services aren't running. When they come up (real or `mock_proxy.py`), the test drives real HTTP calls through `ClassifierClient`, `ArchitectClient(provider="local")`, and the integrated `WindowWorker`-free pipeline into Postgres.

**Stubbed or mocked in current implementation:**
- **Embedding service** — real OpenAI-compatible client with a zero-vector fallback when no API key is configured. Vector search returns no hits until real embeddings are generated.
- **pgvector IVFFlat indexes** — deferred to a post-seed migration (DECISION-003). Needs to be added after seed data is in place for good performance.

**Known gaps (not yet implemented):**
- **Playbook rename** — no PATCH endpoint for playbook metadata (name/description). The builder lets users edit the displayed workflow name locally, but it only sticks in the saved spec's `name` field — the Playbook row itself keeps its original name. The WorkflowsPopover shows that original name. Would need a new `PATCH /api/playbooks/{id}` route to fully close this gap (DECISION-018).
- **Incident filters by entity/type** — the incidents list only filters by status. Filtering by entity fields or incident_type would need query-string wiring + UI chips.
- **Temporal workflow progress streaming** — the incident detail page fetches workflow status once; it doesn't poll or stream step-by-step progress as the Temporal workflow advances. Live event timeline is driven by the incident_events table, which the correlation worker / DynamicPlaybookExecutor write into (so step completions do show up, but the Temporal-side status field is a snapshot).
- **Approval request UI** — backend has `approval_requests` table + `slack_approval_activity` that polls for decisions. No UI route for operators to approve/reject pending requests.
- **Per-session token revocation** — refresh tokens are stateless JWTs (DECISION-007); changing the JWT secret invalidates all tokens. For finer-grained revocation, add a Redis blocklist.
- **Temporal production deployment** — only tested via `WorkflowEnvironment.start_time_skipping()` and Docker Compose. Helm chart / production Temporal Cloud config not included.
- **Alembic autogenerate** — IVFFlat vector indexes can't be autogenerated by Alembic (custom operator class); any future migration involving vector indexes must be hand-edited.
- **Dev-machine Redis conflict** — Ray (or other services) using port 6379 interferes with the app's Redis. Tests work around this by using port 6380. Running the full stack via `docker compose up` on such a machine requires stopping the conflicting service first.

For the full recorded-decision history (with alternatives considered and consequences for each choice), see `DECISIONS.md`.
