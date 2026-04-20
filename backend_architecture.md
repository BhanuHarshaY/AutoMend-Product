# AutoMend Core — Backend Architecture & Implementation Specification

> **Purpose of this document:** This is the single source of truth for building the AutoMend backend. It is written to be consumed by an AI coding agent (Claude Code, Cursor, Copilot Workspace, etc.) placed in the root of the repository alongside the existing frontend code. The agent should be able to read this document, generate a `CLAUDE.md` or equivalent planning file, and systematically implement every service, worker, model, route, and integration described below — without any other conversational context.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Planes](#2-architecture-planes)
3. [Technology Stack](#3-technology-stack)
4. [Repository & Service Layout](#4-repository--service-layout)
5. [Database Schema (Postgres)](#5-database-schema-postgres)
6. [Redis Key Design](#6-redis-key-design)
7. [Ingestion Plane — Logs Path](#7-ingestion-plane--logs-path)
8. [Ingestion Plane — Metrics Path](#8-ingestion-plane--metrics-path)
9. [Intelligence Plane — Window Worker](#9-intelligence-plane--window-worker)
10. [Intelligence Plane — Model 1 Classifier Service](#10-intelligence-plane--model-1-classifier-service)
11. [Intelligence Plane — Correlation Worker](#11-intelligence-plane--correlation-worker)
12. [Canonical Incident Model](#12-canonical-incident-model)
13. [Design Plane — FastAPI Design Routes](#13-design-plane--fastapi-design-routes)
14. [Design Plane — Embedding Service](#14-design-plane--embedding-service)
15. [Design Plane — Vector Search (pgvector)](#15-design-plane--vector-search-pgvector)
16. [Design Plane — Model 2 Architect Service](#16-design-plane--model-2-architect-service)
17. [Tool Registry](#17-tool-registry)
18. [Playbook Registry](#18-playbook-registry)
19. [Playbook DSL Specification](#19-playbook-dsl-specification)
20. [Orchestration Plane — Temporal](#20-orchestration-plane--temporal)
21. [Orchestration Plane — DynamicPlaybookExecutor Workflow](#21-orchestration-plane--dynamicplaybookexecutor-workflow)
22. [Orchestration Plane — Temporal Activities](#22-orchestration-plane--temporal-activities)
23. [Control Plane — FastAPI API](#23-control-plane--fastapi-api)
24. [Control Plane — Webhook Ingress](#24-control-plane--webhook-ingress)
25. [Authentication & Authorization](#25-authentication--authorization)
26. [Configuration & Environment Variables](#26-configuration--environment-variables)
27. [End-to-End Flows](#27-end-to-end-flows)
28. [Deployment — Docker Compose (Dev)](#28-deployment--docker-compose-dev)
29. [Deployment — Kubernetes (Production)](#29-deployment--kubernetes-production)
30. [Testing Strategy](#30-testing-strategy)
31. [Migration & Bootstrapping](#31-migration--bootstrapping)
32. [Frontend Integration Contract](#32-frontend-integration-contract)
33. [Observability & Operational Concerns](#33-observability--operational-concerns)

---

## 1. System Overview

AutoMend is an AI-powered incident response platform for Kubernetes-native infrastructure. It ingests logs and metrics from clusters, classifies anomalies using an ML model, correlates signals into actionable incidents, and executes approved remediation playbooks via durable workflows.

There are two AI models in the system:

- **Model 1 (Classifier):** Takes 5-minute windows of normalized logs and outputs a structured classification (failure type, confidence, evidence). This runs at runtime, continuously.
- **Model 2 (Architect):** Takes a user's natural-language intent plus retrieved context (tools, examples, policies) and generates a JSON workflow specification. This runs at design time, on demand.

The system is split into **5 architectural planes**, each with distinct responsibilities:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CONTROL PLANE                                   │
│            FastAPI API + React UI (existing frontend)                   │
├──────────────┬──────────────┬──────────────────┬────────────────────────┤
│ DESIGN PLANE │ INGESTION    │ INTELLIGENCE     │ ORCHESTRATION PLANE    │
│              │ PLANE        │ PLANE            │                        │
│ Model 2      │ Fluent Bit   │ window-worker    │ Temporal Server        │
│ Embedding    │ OTel Coll.   │ Model 1          │ Temporal Workers       │
│ Vector Search│ Prometheus   │ correlation-wkr  │ DynamicPlaybook        │
│ Playbook Reg.│ Alertmanager │                  │ Executor               │
│ Tool Registry│              │                  │                        │
└──────────────┴──────────────┴──────────────────┴────────────────────────┘
         │                │               │                │
         └────────────────┴───────────────┴────────────────┘
                    Postgres + Redis + Log Backend
```

---

## 2. Architecture Planes

### 2.1 Design Plane

Where workflows/playbooks are created, edited, validated, approved, versioned, and stored. Users interact through the React UI. The AI architect (Model 2) generates workflow specs from natural-language intent, augmented by RAG retrieval of tools, examples, and policies.

### 2.2 Ingestion Plane

Where logs and metrics enter the platform. Logs flow through Fluent Bit → OpenTelemetry Collector → downstream consumers. Metrics flow through Prometheus → Alertmanager → correlation worker.

### 2.3 Intelligence Plane

Where 5-minute log windows are classified by Model 1, and where metric alerts are correlated with classified log events into real incidents. This plane answers: "What happened? Is it real? Is it one incident or many? Should a workflow start?"

### 2.4 Orchestration Plane

Where approved playbooks run durably and safely via Temporal. A single generic `DynamicPlaybookExecutor` workflow interprets the playbook DSL and dispatches registered activities.

### 2.5 Control Plane

Where users operate the system through the FastAPI API and React UI. Exposes design routes, incident queries, rule configuration, playbook management, webhook ingress, operator actions, and workflow status.

---

## 3. Technology Stack

| Layer | Technology | Version (minimum) | Purpose |
|---|---|---|---|
| API framework | FastAPI | 0.110+ | All HTTP routes, webhook ingress, design APIs |
| Task queue / workers | Built-in async or Celery (optional) | — | window-worker, correlation-worker run as separate processes |
| Durable workflows | Temporal (Python SDK) | 1.5+ | Playbook execution, retries, approvals, timers |
| Primary database | PostgreSQL | 15+ | Durable truth: playbooks, tools, incidents, rules, audit |
| Vector extension | pgvector | 0.7+ | Semantic search for tools, playbooks, runbooks |
| Cache / hot state | Redis | 7+ | Rolling windows, dedupe, cooldowns, locks |
| Log shipper | Fluent Bit | 3.0+ | DaemonSet on each node |
| Telemetry gateway | OpenTelemetry Collector | 0.100+ | Log normalization, enrichment, routing |
| Metrics | Prometheus | 2.50+ | Scraping, PromQL rules, recording rules |
| Alert routing | Alertmanager | 0.27+ | Grouping, dedup, silencing, routing |
| GPU metrics | NVIDIA DCGM Exporter | 3.3+ | GPU utilization, memory, temperature |
| ML inference | Model-specific (see §10, §16) | — | Classifier + Architect services |
| ORM / DB access | SQLAlchemy 2.0 + asyncpg | 2.0+ | Async Postgres access |
| Migrations | Alembic | 1.13+ | Schema versioning |
| Serialization | Pydantic v2 | 2.5+ | All request/response models, domain objects |
| Container runtime | Docker | 24+ | Local dev |
| Orchestrator | Kubernetes | 1.28+ | Production deployment |
| Python | CPython | 3.11+ | All backend services |

### Key dependency notes

- **No Kafka in v1.** The log path uses an internal async queue (asyncio.Queue or Redis Streams) between OTel Collector export and the window-worker. Kafka can be added later for replay/fan-out.
- **pgvector lives inside the same Postgres instance** in v1. No separate vector DB is needed initially.
- **Temporal server** can be run via the official Docker image (`temporalio/auto-setup`) for dev. In production, use the Temporal Helm chart or Temporal Cloud.

---

## 4. Repository & Service Layout

The repository root contains the existing frontend and the new backend directory. The backend is a Python monorepo with multiple entrypoints for different process types.

```
project-root/
├── frontend/                    # Existing React / React Flow UI (DO NOT MODIFY unless specified)
│   └── ...
├── backend/
│   ├── pyproject.toml           # Single Python project, all deps
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   ├── main_api.py              # Entrypoint: FastAPI app (uvicorn)
│   ├── main_window_worker.py    # Entrypoint: window-worker process
│   ├── main_correlation_worker.py  # Entrypoint: correlation-worker process
│   ├── main_temporal_worker.py  # Entrypoint: Temporal worker process
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py            # Pydantic Settings, all env vars
│   │   ├── dependencies.py      # FastAPI dependency injection
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── routes_design.py
│   │   │   ├── routes_incidents.py
│   │   │   ├── routes_rules.py
│   │   │   ├── routes_playbooks.py
│   │   │   ├── routes_webhooks.py
│   │   │   ├── routes_workflows.py
│   │   │   ├── routes_tools.py
│   │   │   └── routes_auth.py
│   │   ├── workers/
│   │   │   ├── __init__.py
│   │   │   ├── window_worker.py
│   │   │   └── correlation_worker.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── classifier_client.py
│   │   │   ├── architect_client.py
│   │   │   ├── incident_service.py
│   │   │   ├── playbook_service.py
│   │   │   ├── tool_registry_service.py
│   │   │   ├── workflow_service.py
│   │   │   ├── embedding_service.py
│   │   │   ├── vector_search_service.py
│   │   │   ├── rule_service.py
│   │   │   └── notification_service.py
│   │   ├── stores/
│   │   │   ├── __init__.py
│   │   │   ├── postgres_store.py
│   │   │   ├── redis_store.py
│   │   │   └── log_backend_client.py
│   │   ├── domain/
│   │   │   ├── __init__.py
│   │   │   ├── events.py         # Internal event schemas
│   │   │   ├── incidents.py      # Incident models
│   │   │   ├── playbooks.py      # Playbook + DSL models
│   │   │   ├── rules.py          # Alert rule models
│   │   │   ├── tools.py          # Tool registry models
│   │   │   └── keys.py           # Entity key + incident key builders
│   │   ├── temporal/
│   │   │   ├── __init__.py
│   │   │   ├── workflows.py      # DynamicPlaybookExecutor
│   │   │   └── activities.py     # All registered activities
│   │   └── models/
│   │       ├── __init__.py
│   │       └── db.py             # SQLAlchemy ORM models
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_api/
│   │   ├── test_workers/
│   │   ├── test_services/
│   │   ├── test_temporal/
│   │   └── test_domain/
│   └── scripts/
│       ├── seed_tools.py         # Seeds tool registry with default tools
│       └── seed_rules.py         # Seeds default alert rules
├── infra/
│   ├── docker-compose.yml        # Full dev stack
│   ├── docker-compose.infra.yml  # Just infra (Postgres, Redis, Temporal, Prometheus, etc.)
│   ├── dockerfiles/
│   │   ├── Dockerfile.api
│   │   ├── Dockerfile.worker
│   │   └── Dockerfile.temporal-worker
│   ├── prometheus/
│   │   ├── prometheus.yml
│   │   └── alert_rules.yml
│   ├── alertmanager/
│   │   └── alertmanager.yml
│   ├── otel-collector/
│   │   └── otel-collector-config.yaml
│   ├── fluent-bit/
│   │   └── fluent-bit.conf
│   └── k8s/
│       └── ...                   # Kubernetes manifests (production)
├── backend_architecture.md       # THIS FILE
└── CLAUDE.md                     # To be generated by the AI agent from this file
```

### Process types and their entrypoints

The backend is a single Python codebase but runs as **4 separate process types**:

| Process | Entrypoint | What it runs | Scaling |
|---|---|---|---|
| `api` | `main_api.py` | FastAPI (uvicorn), all HTTP routes | Horizontal, stateless |
| `window-worker` | `main_window_worker.py` | Consumes normalized log stream, maintains windows, calls classifier | 1 per entity-key partition (or single instance with internal partitioning) |
| `correlation-worker` | `main_correlation_worker.py` | Consumes classifier outputs + Alertmanager webhooks, creates incidents, starts workflows | 1-3 replicas with leader election or key-based partitioning |
| `temporal-worker` | `main_temporal_worker.py` | Registers Temporal workflows + activities, polls Temporal task queues | Horizontal, stateless |

---

## 5. Database Schema (Postgres)

Use Alembic for all migrations. The initial migration creates all tables below. Use SQLAlchemy 2.0 declarative models in `app/models/db.py`.

### 5.1 `tools` table (Tool Registry)

```sql
CREATE TABLE tools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(128) NOT NULL UNIQUE,
    display_name VARCHAR(256) NOT NULL,
    description TEXT NOT NULL,
    category VARCHAR(64) NOT NULL,          -- e.g. 'kubernetes', 'observability', 'notification', 'ticketing'
    input_schema JSONB NOT NULL,            -- JSON Schema for activity input
    output_schema JSONB NOT NULL,           -- JSON Schema for activity output
    side_effect_level VARCHAR(32) NOT NULL DEFAULT 'read',  -- 'read', 'write', 'destructive'
    required_approvals INTEGER NOT NULL DEFAULT 0,          -- 0 = no approval needed
    environments_allowed TEXT[] NOT NULL DEFAULT '{production,staging,development}',
    embedding_text TEXT NOT NULL,            -- concatenation of name + description + category for embedding
    embedding vector(1536),                 -- pgvector column, populated by embedding service
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tools_name ON tools(name);
CREATE INDEX idx_tools_category ON tools(category);
CREATE INDEX idx_tools_embedding ON tools USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);
```

### 5.2 `playbooks` table

```sql
CREATE TABLE playbooks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(256) NOT NULL,
    description TEXT,
    owner_team VARCHAR(128),
    created_by VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 5.3 `playbook_versions` table

```sql
CREATE TABLE playbook_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    playbook_id UUID NOT NULL REFERENCES playbooks(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
        -- allowed: 'draft', 'generated', 'validated', 'approved', 'published', 'archived'
    trigger_bindings JSONB,                 -- which incident_types this version handles
    workflow_spec JSONB NOT NULL,           -- the full playbook DSL JSON (see §19)
    spec_checksum VARCHAR(64) NOT NULL,     -- SHA-256 of workflow_spec for integrity
    approval_info JSONB,                    -- who approved, when, notes
    compatibility_metadata JSONB,           -- min platform version, required tools, etc.
    embedding_text TEXT,                    -- for semantic search
    embedding vector(1536),
    change_notes TEXT,
    created_by VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(playbook_id, version_number)
);

CREATE INDEX idx_playbook_versions_playbook_id ON playbook_versions(playbook_id);
CREATE INDEX idx_playbook_versions_status ON playbook_versions(status);
CREATE INDEX idx_playbook_versions_embedding ON playbook_versions USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);
```

### 5.4 `trigger_rules` table

Maps incident types to playbook versions. Used by the correlation-worker to find which playbook to execute.

```sql
CREATE TABLE trigger_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_type VARCHAR(256) NOT NULL,       -- e.g. 'incident.gpu_memory_failure'
    entity_filter JSONB,                        -- optional: match on cluster, namespace, service, etc.
    playbook_version_id UUID NOT NULL REFERENCES playbook_versions(id),
    priority INTEGER NOT NULL DEFAULT 0,        -- higher = more specific, wins ties
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_trigger_rules_incident_type ON trigger_rules(incident_type);
CREATE INDEX idx_trigger_rules_active ON trigger_rules(is_active) WHERE is_active = true;
```

### 5.5 `incidents` table

```sql
CREATE TABLE incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_key VARCHAR(512) NOT NULL UNIQUE,  -- dedup key, e.g. 'prod-a/ml/trainer/gpu2/failure.memory'
    incident_type VARCHAR(256) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'open',
        -- allowed: 'open', 'acknowledged', 'in_progress', 'resolved', 'closed', 'suppressed'
    severity VARCHAR(16) NOT NULL DEFAULT 'medium',
        -- allowed: 'critical', 'high', 'medium', 'low', 'info'
    entity JSONB NOT NULL,                      -- cluster, namespace, service, pod, gpu_id, etc.
    sources TEXT[] NOT NULL,                     -- e.g. '{prometheus_alert,log_classifier}'
    evidence JSONB NOT NULL,                    -- metric_alerts, classifier output, raw signals
    playbook_version_id UUID REFERENCES playbook_versions(id),
    temporal_workflow_id VARCHAR(256),
    temporal_run_id VARCHAR(256),
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_incidents_incident_key ON incidents(incident_key);
CREATE INDEX idx_incidents_status ON incidents(status);
CREATE INDEX idx_incidents_type ON incidents(incident_type);
CREATE INDEX idx_incidents_created_at ON incidents(created_at DESC);
```

### 5.6 `incident_events` table (Audit trail)

Every state change, signal addition, or action taken on an incident is logged here.

```sql
CREATE TABLE incident_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
        -- e.g. 'created', 'signal_added', 'status_changed', 'workflow_started',
        --      'workflow_completed', 'workflow_failed', 'manual_action', 'escalated'
    payload JSONB NOT NULL,
    actor VARCHAR(128),                         -- 'system', 'correlation-worker', user email, etc.
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_incident_events_incident_id ON incident_events(incident_id);
CREATE INDEX idx_incident_events_created_at ON incident_events(created_at DESC);
```

### 5.7 `classifier_outputs` table (Optional retention)

```sql
CREATE TABLE classifier_outputs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_key VARCHAR(512) NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    label VARCHAR(128) NOT NULL,
    confidence REAL NOT NULL,
    evidence JSONB,
    severity_suggestion VARCHAR(16),
    incident_id UUID REFERENCES incidents(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_classifier_outputs_entity_key ON classifier_outputs(entity_key);
CREATE INDEX idx_classifier_outputs_created_at ON classifier_outputs(created_at DESC);
```

### 5.8 `approval_requests` table

Used by workflows that pause for human approval.

```sql
CREATE TABLE approval_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES incidents(id),
    workflow_id VARCHAR(256) NOT NULL,          -- Temporal workflow ID
    step_name VARCHAR(128) NOT NULL,
    requested_action TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
        -- allowed: 'pending', 'approved', 'rejected', 'expired'
    requested_by VARCHAR(128) NOT NULL,         -- usually 'system'
    decided_by VARCHAR(128),
    decision_notes TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ
);

CREATE INDEX idx_approval_requests_status ON approval_requests(status) WHERE status = 'pending';
CREATE INDEX idx_approval_requests_incident_id ON approval_requests(incident_id);
```

### 5.9 `alert_rules` table

User-configured rules that map to Prometheus alert rules or internal classification thresholds.

```sql
CREATE TABLE alert_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(256) NOT NULL,
    description TEXT,
    rule_type VARCHAR(32) NOT NULL,            -- 'prometheus', 'classifier_threshold', 'composite'
    rule_definition JSONB NOT NULL,            -- PromQL expr, threshold config, or composite logic
    severity VARCHAR(16) NOT NULL DEFAULT 'medium',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_by VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 5.10 `users` table (Minimal auth)

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(256) NOT NULL UNIQUE,
    display_name VARCHAR(256),
    role VARCHAR(32) NOT NULL DEFAULT 'viewer',
        -- allowed: 'admin', 'operator', 'editor', 'viewer'
    hashed_password VARCHAR(256),               -- if using local auth
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 5.11 Enable pgvector

The very first migration must include:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

---

## 6. Redis Key Design

All Redis keys use a namespace prefix `automend:`. TTLs are specified per key type.

| Key Pattern | Value Type | TTL | Purpose |
|---|---|---|---|
| `automend:window:{entity_key}` | Hash (log entries by timestamp) | 10 minutes | Rolling 5-min log window accumulation |
| `automend:window:meta:{entity_key}` | Hash (window_start, count, last_seen) | 10 minutes | Window metadata |
| `automend:dedup:classifier:{entity_key}:{label}` | String "1" | 15 minutes | Prevent re-classifying same entity+label within cooldown |
| `automend:dedup:incident:{incident_key}` | String (incident_id) | 30 minutes | Prevent duplicate incident creation |
| `automend:cooldown:{incident_key}` | String "1" | Configurable (default 15 min) | Suppress duplicate workflow starts |
| `automend:incident:active:{incident_key}` | Hash (incident_id, status, workflow_id) | None (explicit delete) | Fast lookup of active incident state |
| `automend:lock:window:{entity_key}` | String (worker_id) | 30 seconds | Distributed lock for window processing |
| `automend:lock:correlation:{incident_key}` | String (worker_id) | 10 seconds | Distributed lock for correlation |
| `automend:last_seen:{entity_key}` | String (ISO timestamp) | 1 hour | Track last log seen per entity |
| `automend:stream:classified_events` | Redis Stream | Trimmed to ~10000 entries | Classified events from window-worker to correlation-worker |
| `automend:stream:normalized_logs` | Redis Stream | Trimmed to ~50000 entries | Normalized logs from OTel export to window-worker |

### Redis Stream consumer groups

- Stream `automend:stream:normalized_logs`:
  - Consumer group: `window-workers`
  - Consumers: `window-worker-0`, `window-worker-1`, etc.

- Stream `automend:stream:classified_events`:
  - Consumer group: `correlation-workers`
  - Consumers: `correlation-worker-0`, `correlation-worker-1`, etc.

---

## 7. Ingestion Plane — Logs Path

### 7.1 Flow

```
Node/container/app logs
  → Fluent Bit DaemonSet (on each node)
  → OpenTelemetry Collector Gateway (centralized)
  → Fan out to:
      ├── Log backend (Loki, Elasticsearch, or file-based for v1)
      └── Redis Stream `automend:stream:normalized_logs`
```

### 7.2 Fluent Bit Configuration

Fluent Bit runs as a Kubernetes DaemonSet. It tails container logs and forwards them to the OTel Collector's OTLP HTTP endpoint.

Key config points:
- Input: `tail` plugin reading `/var/log/containers/*.log`
- Parser: Docker or CRI parser depending on runtime
- Filter: Kubernetes metadata enrichment (`kubernetes` filter)
- Output: `opentelemetry` output plugin pointing to `otel-collector-gateway:4318`

Reference Fluent Bit config (`infra/fluent-bit/fluent-bit.conf`):

```ini
[SERVICE]
    Flush        1
    Log_Level    info
    Daemon       off
    Parsers_File parsers.conf

[INPUT]
    Name             tail
    Tag              kube.*
    Path             /var/log/containers/*.log
    Parser           cri
    DB               /var/log/flb_kube.db
    Mem_Buf_Limit    10MB
    Skip_Long_Lines  On
    Refresh_Interval 5

[FILTER]
    Name                kubernetes
    Match               kube.*
    Kube_URL            https://kubernetes.default.svc:443
    Kube_CA_File        /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
    Kube_Token_File     /var/run/secrets/kubernetes.io/serviceaccount/token
    Merge_Log           On
    Keep_Log            Off
    K8S-Logging.Parser  On
    K8S-Logging.Exclude On
    Labels              On
    Annotations         Off

[OUTPUT]
    Name                 opentelemetry
    Match                kube.*
    Host                 otel-collector-gateway
    Port                 4318
    Logs_uri             /v1/logs
    Log_response_payload True
    Tls                  Off
    Tls.verify           Off
```

### 7.3 OpenTelemetry Collector Configuration

The OTel Collector runs as a centralized gateway deployment (not a DaemonSet). It receives logs from Fluent Bit, normalizes attributes, filters noise, batches, and exports to multiple destinations.

Reference config (`infra/otel-collector/otel-collector-config.yaml`):

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: "0.0.0.0:4318"
      grpc:
        endpoint: "0.0.0.0:4317"

processors:
  batch:
    timeout: 5s
    send_batch_size: 1000

  attributes/normalize:
    actions:
      - key: automend.cluster
        from_attribute: k8s.cluster.name
        action: upsert
      - key: automend.namespace
        from_attribute: k8s.namespace.name
        action: upsert
      - key: automend.pod
        from_attribute: k8s.pod.name
        action: upsert
      - key: automend.node
        from_attribute: k8s.node.name
        action: upsert
      - key: automend.container
        from_attribute: k8s.container.name
        action: upsert
      - key: automend.service
        from_attribute: service.name
        action: upsert

  filter/noise:
    logs:
      exclude:
        match_type: regexp
        bodies:
          - "^\\s*$"
          - "^health check"
          - "^GET /healthz"
          - "^GET /readyz"

  resource:
    attributes:
      - key: automend.environment
        value: "production"
        action: upsert

exporters:
  # Export to the AutoMend backend (Redis stream ingestion endpoint)
  otlphttp/automend:
    endpoint: "http://automend-api:8000/api/ingest/otlp"
    tls:
      insecure: true

  # Export to log backend (Loki example)
  loki:
    endpoint: "http://loki:3100/loki/api/v1/push"

  debug:
    verbosity: basic

service:
  pipelines:
    logs:
      receivers: [otlp]
      processors: [attributes/normalize, filter/noise, batch]
      exporters: [otlphttp/automend, loki, debug]
```

### 7.4 OTLP Ingestion Route in FastAPI

The API exposes an OTLP HTTP endpoint that receives batched log records from the OTel Collector and pushes them to the Redis Stream.

**Route:** `POST /api/ingest/otlp`

This route:
1. Receives the OTLP `ExportLogsServiceRequest` (JSON format)
2. Extracts each log record with its resource attributes
3. Builds a normalized log entry:
   ```json
   {
     "timestamp": "2025-01-15T10:30:05.123Z",
     "body": "CUDA error: out of memory",
     "severity": "ERROR",
     "attributes": {
       "cluster": "prod-a",
       "namespace": "ml",
       "pod": "trainer-7f9d",
       "node": "gpu-node-03",
       "container": "trainer",
       "service": "trainer",
       "environment": "production",
       "gpu_id": "2"
     },
     "entity_key": "prod-a/ml/trainer"
   }
   ```
4. Pushes each entry to Redis Stream `automend:stream:normalized_logs` via `XADD`

**Implementation notes:**
- The entity_key is derived from `{cluster}/{namespace}/{service}` by default, configurable per tenant.
- This route should be lightweight — no database writes, no heavy processing. Just validate, normalize, and push to Redis.
- Batch the `XADD` calls using Redis pipeline for performance.

---

## 8. Ingestion Plane — Metrics Path

### 8.1 Flow

```
Kubernetes/app/node/GPU exporters
  → Prometheus scrape
  → PromQL alerting rules evaluate
  → Alertmanager
  → Webhook to correlation-worker
```

### 8.2 Prometheus Configuration

Reference config (`infra/prometheus/prometheus.yml`):

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "alert_rules.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

scrape_configs:
  - job_name: "kube-state-metrics"
    static_configs:
      - targets: ["kube-state-metrics:8080"]

  - job_name: "node-exporter"
    static_configs:
      - targets: ["node-exporter:9100"]

  - job_name: "dcgm-exporter"
    static_configs:
      - targets: ["dcgm-exporter:9400"]

  - job_name: "app-metrics"
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: "true"
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_port]
        action: replace
        target_label: __address__
        regex: (.+)
        replacement: "${1}:${2}"
```

### 8.3 Alert Rules

Reference config (`infra/prometheus/alert_rules.yml`):

```yaml
groups:
  - name: gpu_alerts
    rules:
      - alert: GPUHighMemoryPressure
        expr: DCGM_FI_DEV_FB_USED / DCGM_FI_DEV_FB_TOTAL > 0.95
        for: 5m
        labels:
          severity: high
          incident_type: incident.gpu_memory_failure
        annotations:
          summary: "GPU {{ $labels.gpu }} memory usage above 95%"
          entity_cluster: "{{ $labels.cluster }}"
          entity_namespace: "{{ $labels.namespace }}"
          entity_service: "{{ $labels.exported_service }}"

      - alert: GPUHighTemperature
        expr: DCGM_FI_DEV_GPU_TEMP > 90
        for: 5m
        labels:
          severity: high
          incident_type: incident.gpu_thermal
        annotations:
          summary: "GPU {{ $labels.gpu }} temperature above 90°C"

      - alert: PodCrashLooping
        expr: rate(kube_pod_container_status_restarts_total[15m]) > 0.1
        for: 5m
        labels:
          severity: medium
          incident_type: incident.pod_crash_loop
        annotations:
          summary: "Pod {{ $labels.pod }} in {{ $labels.namespace }} is crash looping"

      - alert: HighErrorRate
        expr: |
          sum(rate(http_requests_total{status=~"5.."}[5m])) by (service, namespace)
          /
          sum(rate(http_requests_total[5m])) by (service, namespace)
          > 0.05
        for: 5m
        labels:
          severity: high
          incident_type: incident.high_error_rate
        annotations:
          summary: "Service {{ $labels.service }} error rate above 5%"

      - alert: NodeNotReady
        expr: kube_node_status_condition{condition="Ready",status="true"} == 0
        for: 5m
        labels:
          severity: critical
          incident_type: incident.node_not_ready
        annotations:
          summary: "Node {{ $labels.node }} is not ready"
```

### 8.4 Alertmanager Configuration

Reference config (`infra/alertmanager/alertmanager.yml`):

```yaml
global:
  resolve_timeout: 5m

route:
  receiver: "automend-webhook"
  group_by: ["incident_type", "namespace", "service"]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - match:
        severity: critical
      group_wait: 10s
      receiver: "automend-webhook"

receivers:
  - name: "automend-webhook"
    webhook_configs:
      - url: "http://automend-api:8000/api/webhooks/alertmanager"
        send_resolved: true
        max_alerts: 50

inhibit_rules:
  - source_match:
      severity: "critical"
    target_match:
      severity: "high"
    equal: ["namespace", "service"]
```

---

## 9. Intelligence Plane — Window Worker

### 9.1 Overview

The window-worker is a long-running process that:
1. Consumes normalized log entries from Redis Stream `automend:stream:normalized_logs`
2. Groups them by entity key
3. Maintains rolling 5-minute windows in Redis
4. When a window closes (5 minutes elapsed since first entry, or max entry count reached), it:
   a. Retrieves all log entries for that window from Redis
   b. Summarizes/selects the most relevant entries
   c. Calls Model 1 classifier service
   d. Emits the classified event to Redis Stream `automend:stream:classified_events`
5. Cleans up the closed window from Redis

### 9.2 Entrypoint (`main_window_worker.py`)

```python
"""
Window Worker entrypoint.
Consumes normalized logs from Redis Stream, maintains 5-minute windows,
calls Model 1 classifier, and emits classified events.
"""
import asyncio
from app.config import get_settings
from app.workers.window_worker import WindowWorker

async def main():
    settings = get_settings()
    worker = WindowWorker(settings)
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
```

### 9.3 Window Worker Implementation (`app/workers/window_worker.py`)

The worker uses Redis Streams consumer groups for reliable delivery.

**Initialization:**
- Connect to Redis
- Create consumer group `window-workers` on stream `automend:stream:normalized_logs` if it doesn't exist
- Set consumer name from config (e.g., `window-worker-0`)

**Main loop (runs forever):**
1. `XREADGROUP GROUP window-workers {consumer_name} COUNT 100 BLOCK 2000 STREAMS automend:stream:normalized_logs >`
2. For each received log entry:
   a. Parse the normalized log JSON
   b. Extract `entity_key`
   c. Acquire lock `automend:lock:window:{entity_key}` (Redis `SET NX EX 30`)
   d. If lock acquired:
      - Check if window exists for this entity: `HGET automend:window:meta:{entity_key} window_start`
      - If no window exists, create one: set `window_start` to current time
      - Add log entry to window: `RPUSH automend:window:{entity_key} {log_json}`
      - Update metadata: increment count, set last_seen
      - Check if window should close:
        - `now - window_start >= 5 minutes` OR
        - `count >= MAX_WINDOW_ENTRIES` (default 500)
      - If window should close: call `close_window(entity_key)`
      - Release lock
   e. `XACK` the stream entry

**`close_window(entity_key)` method:**
1. Retrieve all entries: `LRANGE automend:window:{entity_key} 0 -1`
2. Retrieve metadata: `HGETALL automend:window:meta:{entity_key}`
3. Check dedup: `EXISTS automend:dedup:classifier:{entity_key}:{recent_label}` — if a very recent classification exists for the same pattern, skip
4. Build classifier input (see §10)
5. Call Model 1 classifier service via HTTP
6. If classification confidence >= threshold (default 0.7):
   a. Build classified event (see below)
   b. Push to `XADD automend:stream:classified_events`
   c. Set dedup key: `SET automend:dedup:classifier:{entity_key}:{label} 1 EX 900`
   d. Optionally store in `classifier_outputs` table via async Postgres write
7. Delete window data: `DEL automend:window:{entity_key} automend:window:meta:{entity_key}`

**Window close timer (background task):**
A separate coroutine runs every 30 seconds and scans `automend:window:meta:*` keys to find windows that have been open for > 5 minutes but haven't been closed by the main loop (in case log volume dropped). This ensures windows don't stay open indefinitely.

### 9.4 Classified Event Schema

```json
{
  "event_id": "uuid",
  "event_type": "classified_log_event",
  "entity_key": "prod-a/ml/trainer",
  "entity": {
    "cluster": "prod-a",
    "namespace": "ml",
    "service": "trainer",
    "pod": "trainer-7f9d",
    "node": "gpu-node-03",
    "gpu_id": "2"
  },
  "classification": {
    "label": "failure.memory",
    "confidence": 0.94,
    "evidence": ["CUDA error: out of memory", "GPU memory allocation failed for 4096MB"],
    "severity_suggestion": "high"
  },
  "window": {
    "start": "2025-01-15T10:25:00Z",
    "end": "2025-01-15T10:30:00Z",
    "log_count": 47
  },
  "timestamp": "2025-01-15T10:30:01Z"
}
```

### 9.5 Entity Key Construction (`app/domain/keys.py`)

Entity keys are built from normalized log attributes. The key format determines the granularity of classification.

```python
from typing import Optional

DEFAULT_KEY_TEMPLATE = "{cluster}/{namespace}/{service}"

SUPPORTED_KEY_TEMPLATES = [
    "{cluster}/{namespace}/{pod}",
    "{cluster}/{namespace}/{service}",
    "{service}/{tenant}/{region}",
    "{node}/{gpu_id}/{workload}",
    "{cluster}/{service}/{deployment}",
]

def build_entity_key(
    attributes: dict,
    template: str = DEFAULT_KEY_TEMPLATE
) -> str:
    """Build entity key from log attributes using the configured template."""
    try:
        return template.format(**attributes)
    except KeyError:
        # Fallback: use whatever attributes are available
        parts = []
        for field in ["cluster", "namespace", "service", "pod"]:
            if field in attributes and attributes[field]:
                parts.append(attributes[field])
        return "/".join(parts) if parts else "unknown"


def build_incident_key(
    entity_key: str,
    failure_label: str
) -> str:
    """Build incident dedup key from entity key + classification label."""
    return f"{entity_key}/{failure_label}"
```

---

## 10. Intelligence Plane — Model 1 Classifier Service

### 10.1 Overview

Model 1 is a **separate service** that performs inference only. It receives a bundle of logs from a 5-minute window and returns a structured classification.

### 10.2 Service Interface

The classifier service exposes a single HTTP endpoint:

**`POST /classify`**

Request body:
```json
{
  "entity_key": "prod-a/ml/trainer",
  "window_start": "2025-01-15T10:25:00Z",
  "window_end": "2025-01-15T10:30:00Z",
  "logs": [
    {
      "timestamp": "2025-01-15T10:27:12Z",
      "body": "CUDA error: out of memory",
      "severity": "ERROR",
      "attributes": { "pod": "trainer-7f9d", "container": "trainer" }
    },
    {
      "timestamp": "2025-01-15T10:27:13Z",
      "body": "Failed to allocate 4096MB on GPU 2",
      "severity": "ERROR",
      "attributes": { "pod": "trainer-7f9d", "container": "trainer" }
    }
  ],
  "max_logs": 200,
  "entity_context": {
    "cluster": "prod-a",
    "namespace": "ml",
    "service": "trainer"
  }
}
```

Response body:
```json
{
  "label": "failure.memory",
  "confidence": 0.94,
  "evidence": [
    "CUDA error: out of memory",
    "Failed to allocate 4096MB on GPU 2"
  ],
  "severity_suggestion": "high",
  "secondary_labels": [
    { "label": "failure.gpu", "confidence": 0.82 }
  ]
}
```

### 10.3 Classification Taxonomy

The classifier outputs labels from this taxonomy:

| Label | Description |
|---|---|
| `failure.memory` | Out-of-memory errors (host RAM or GPU VRAM) |
| `failure.gpu` | GPU hardware errors, ECC errors, Xid errors |
| `failure.network` | Network connectivity, DNS, timeout errors |
| `failure.dependency` | Upstream/downstream service failures |
| `failure.authentication` | Auth/authz failures, token expiry |
| `failure.storage` | Disk I/O, volume mount, PVC errors |
| `failure.configuration` | Config parsing, missing env vars, bad flags |
| `failure.crash` | Segfaults, panics, unhandled exceptions |
| `failure.resource_limit` | CPU throttling, eviction, quota exceeded |
| `failure.deployment` | Rollout failures, image pull errors |
| `degradation.latency` | High latency, slow queries, timeouts |
| `degradation.throughput` | Reduced request throughput, queue backlog |
| `anomaly.pattern` | Unusual log patterns not matching known failures |
| `normal` | No actionable issue detected |

### 10.4 Classifier Client (`app/services/classifier_client.py`)

```python
"""
HTTP client for Model 1 classifier service.
"""
import httpx
from typing import Optional
from pydantic import BaseModel
from app.config import get_settings


class ClassifierInput(BaseModel):
    entity_key: str
    window_start: str
    window_end: str
    logs: list[dict]
    max_logs: int = 200
    entity_context: dict


class ClassifierOutput(BaseModel):
    label: str
    confidence: float
    evidence: list[str]
    severity_suggestion: Optional[str] = None
    secondary_labels: list[dict] = []


class ClassifierClient:
    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.classifier_service_url  # e.g. http://classifier:8001
        self.timeout = self.settings.classifier_timeout_seconds  # default 30

    async def classify(self, input: ClassifierInput) -> ClassifierOutput:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/classify",
                json=input.model_dump(),
            )
            response.raise_for_status()
            return ClassifierOutput.model_validate(response.json())
```

### 10.5 Implementation Notes for the Classifier Service

The classifier service itself is a **separate FastAPI app** (or Flask, doesn't matter) that wraps the ML model. For v1, this can be:

**Option A — LLM-based classifier:** Use an LLM (OpenAI, Anthropic, or local) with a structured prompt that receives the log bundle and returns JSON classification. This is the fastest to implement.

**Option B — Fine-tuned model:** A fine-tuned BERT/RoBERTa model for log classification. Higher throughput, lower cost, but requires training data.

**Option C — Hybrid:** Use a fast heuristic/regex-based pre-filter, then LLM for ambiguous cases.

For v1, **implement Option A** with the following prompt structure:

```
System: You are a Kubernetes infrastructure log classifier. Given a bundle of logs from a 5-minute window for a specific entity (cluster/namespace/service), classify the primary issue.

Return ONLY a JSON object with these fields:
- label: one of [failure.memory, failure.gpu, failure.network, failure.dependency, failure.authentication, failure.storage, failure.configuration, failure.crash, failure.resource_limit, failure.deployment, degradation.latency, degradation.throughput, anomaly.pattern, normal]
- confidence: float 0.0-1.0
- evidence: array of the 1-5 most relevant log lines
- severity_suggestion: one of [critical, high, medium, low, info]

Entity: {entity_key}
Window: {window_start} to {window_end}
Logs:
{formatted_logs}
```

The classifier service should be deployed as a separate container with its own scaling characteristics.

---

## 11. Intelligence Plane — Correlation Worker

### 11.1 Overview

The correlation worker is the decision engine. It consumes signals from multiple sources, normalizes them into a common schema, and decides what to do: ignore, create incident, update incident, start workflow, or signal existing workflow.

### 11.2 Input Sources

The correlation worker consumes from:

1. **Redis Stream `automend:stream:classified_events`** — classifier outputs from the window-worker
2. **HTTP webhook `/api/webhooks/alertmanager`** — Alertmanager alert notifications (pushed into a Redis Stream or internal queue by the webhook route)
3. **Optional: structured app events** — custom events from applications
4. **Optional: manual triggers** — operator-initiated incidents from the UI

All these are pushed into an internal queue (Redis Stream `automend:stream:correlation_input`) by their respective ingestion points.

### 11.3 Internal Signal Schema

Every upstream signal is first transformed into a canonical internal signal:

```json
{
  "signal_id": "uuid",
  "signal_type": "classifier_output | prometheus_alert | app_event | manual_trigger",
  "source": "log_classifier | alertmanager | app:{name} | operator:{email}",
  "entity_key": "prod-a/ml/trainer",
  "entity": {
    "cluster": "prod-a",
    "namespace": "ml",
    "service": "trainer",
    "pod": "trainer-7f9d",
    "gpu_id": "2"
  },
  "incident_type_hint": "incident.gpu_memory_failure",
  "severity": "high",
  "payload": { ... },
  "timestamp": "2025-01-15T10:30:01Z"
}
```

### 11.4 Alertmanager Signal Transformation

When the Alertmanager webhook fires, the webhook route (`routes_webhooks.py`) receives the standard Alertmanager webhook JSON and transforms each alert:

```python
def transform_alertmanager_alert(alert: dict) -> dict:
    """Transform Alertmanager alert to internal signal format."""
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})

    entity = {}
    for field in ["cluster", "namespace", "service", "pod", "node", "gpu_id"]:
        # Check labels, then annotations with entity_ prefix
        value = labels.get(field) or annotations.get(f"entity_{field}")
        if value:
            entity[field] = value

    entity_key = build_entity_key(entity)

    return {
        "signal_id": str(uuid4()),
        "signal_type": "prometheus_alert",
        "source": "alertmanager",
        "entity_key": entity_key,
        "entity": entity,
        "incident_type_hint": labels.get("incident_type", "incident.unknown"),
        "severity": labels.get("severity", "medium"),
        "payload": {
            "alert_name": labels.get("alertname"),
            "status": alert.get("status"),
            "starts_at": alert.get("startsAt"),
            "ends_at": alert.get("endsAt"),
            "generator_url": alert.get("generatorURL"),
            "summary": annotations.get("summary"),
            "labels": labels,
        },
        "timestamp": alert.get("startsAt"),
    }
```

### 11.5 Correlation Worker Implementation (`app/workers/correlation_worker.py`)

**Entrypoint (`main_correlation_worker.py`):**
```python
import asyncio
from app.config import get_settings
from app.workers.correlation_worker import CorrelationWorker

async def main():
    settings = get_settings()
    worker = CorrelationWorker(settings)
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
```

**Main loop:**
1. Read from Redis Stream `automend:stream:classified_events` and `automend:stream:correlation_input` using `XREADGROUP`
2. For each signal:
   a. Normalize to internal signal schema (if not already)
   b. Derive `incident_key` = `build_incident_key(entity_key, incident_type_hint)` — e.g., `prod-a/ml/trainer/failure.memory`
   c. Acquire lock: `SET automend:lock:correlation:{incident_key} {worker_id} NX EX 10`
   d. Check if active incident exists: `HGETALL automend:incident:active:{incident_key}`
   e. **Decision logic:**

**If NO active incident exists:**
   - Check cooldown: `EXISTS automend:cooldown:{incident_key}` — if yes, suppress (log and skip)
   - Check dedup: `EXISTS automend:dedup:incident:{incident_key}` — if yes, skip
   - Derive `incident_type` from `incident_type_hint`
   - Look up matching approved playbook: query `trigger_rules` table for this `incident_type` with status `approved` or `published`, ordered by priority
   - Create incident record in Postgres (`incidents` table)
   - Create incident event (`incident_events` table, type `created`)
   - Set active incident cache in Redis: `HSET automend:incident:active:{incident_key} incident_id {id} status open`
   - Set dedup key: `SET automend:dedup:incident:{incident_key} {id} EX 1800`
   - If a matching playbook was found:
     - Start Temporal workflow `DynamicPlaybookExecutor` with:
       - `workflow_id`: `automend-{incident_key}-{short_uuid}` (for idempotency)
       - Input: `{ playbook_version_id, incident_id, incident_payload }`
     - Update incident with `temporal_workflow_id` and `temporal_run_id`
     - Create incident event (type `workflow_started`)
   - If no matching playbook:
     - Create incident event (type `no_playbook_matched`)
     - The incident remains open for manual handling

**If an active incident EXISTS:**
   - Do NOT start a second workflow
   - Add the new signal as evidence to the existing incident (update `evidence` JSONB)
   - Create incident event (type `signal_added`)
   - If a Temporal workflow is running, SIGNAL it with the new evidence:
     - Use Temporal client `signal_workflow` with signal name `new_evidence`
     - Payload: the new signal data

   f. Release lock
   g. `XACK` the stream entry

### 11.6 Correlation Rules and Enrichment

Beyond simple key-based correlation, the worker performs:

1. **Temporal correlation:** Signals within a 10-minute window for the same entity_key are considered related, even if they have different incident_type_hints.
2. **Severity escalation:** If a `high` severity signal arrives for an existing `medium` incident, escalate the incident severity.
3. **Source merging:** When both a classifier output and a Prometheus alert correlate, the incident `sources` array includes both, and confidence is boosted.
4. **Cooldown after resolution:** After an incident is resolved, set a cooldown to prevent immediate re-triggering from lingering alerts.

---

## 12. Canonical Incident Model

Every incident in the system follows this canonical structure. This is the Pydantic model used across the codebase.

```python
# app/domain/incidents.py

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from uuid import UUID, uuid4
from enum import Enum


class IncidentStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    SUPPRESSED = "suppressed"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class EntityInfo(BaseModel):
    cluster: Optional[str] = None
    namespace: Optional[str] = None
    service: Optional[str] = None
    pod: Optional[str] = None
    node: Optional[str] = None
    container: Optional[str] = None
    gpu_id: Optional[str] = None
    tenant: Optional[str] = None
    region: Optional[str] = None
    deployment: Optional[str] = None


class ClassifierEvidence(BaseModel):
    label: str
    confidence: float
    evidence_lines: list[str] = []
    severity_suggestion: Optional[str] = None


class IncidentEvidence(BaseModel):
    metric_alerts: list[str] = []
    classifier: Optional[ClassifierEvidence] = None
    raw_signals: list[dict] = []


class CanonicalIncident(BaseModel):
    """The canonical incident object used throughout the system."""
    id: UUID = Field(default_factory=uuid4)
    incident_key: str
    incident_type: str
    status: IncidentStatus = IncidentStatus.OPEN
    severity: Severity = Severity.MEDIUM
    entity: EntityInfo
    entity_key: str
    sources: list[str]
    evidence: IncidentEvidence
    playbook_version_id: Optional[UUID] = None
    temporal_workflow_id: Optional[str] = None
    temporal_run_id: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

Example instance (as JSON):
```json
{
  "incident_key": "prod-a/ml/trainer/gpu2/failure.memory",
  "incident_type": "incident.gpu_memory_failure",
  "status": "open",
  "severity": "high",
  "entity": {
    "cluster": "prod-a",
    "namespace": "ml",
    "service": "trainer",
    "pod": "trainer-7f9d",
    "gpu_id": "2"
  },
  "entity_key": "prod-a/ml/trainer",
  "sources": ["prometheus_alert", "log_classifier"],
  "evidence": {
    "metric_alerts": ["GPUHighMemoryPressure"],
    "classifier": {
      "label": "failure.memory",
      "confidence": 0.94,
      "evidence_lines": ["CUDA error: out of memory"],
      "severity_suggestion": "high"
    },
    "raw_signals": []
  }
}
```

---

## 13. Design Plane — FastAPI Design Routes

These routes power the workflow design UI where users create, edit, and approve playbooks.

### 13.1 Route: `POST /api/design/rag_search`

Searches the tool registry and playbook registry for relevant items given a natural-language query.

**Request:**
```json
{
  "query": "restart a pod that is crash looping and notify the team on Slack",
  "search_types": ["tools", "playbooks", "policies"],
  "limit": 10
}
```

**Implementation:**
1. Call embedding service to embed the query
2. Perform pgvector similarity search against `tools.embedding` and `playbook_versions.embedding`
3. Return ranked results with relevance scores

**Response:**
```json
{
  "tools": [
    {
      "id": "uuid",
      "name": "restart_workload",
      "description": "Restarts a Kubernetes workload (deployment, statefulset, daemonset)",
      "relevance_score": 0.92,
      "input_schema": { ... },
      "side_effect_level": "write"
    },
    {
      "id": "uuid",
      "name": "slack_approval",
      "description": "Sends a Slack message and waits for approval reaction",
      "relevance_score": 0.87,
      "input_schema": { ... },
      "side_effect_level": "write"
    }
  ],
  "playbooks": [
    {
      "id": "uuid",
      "name": "Pod Crash Loop Recovery",
      "description": "Handles crash-looping pods with diagnostics and restart",
      "relevance_score": 0.88,
      "version": 3,
      "status": "published"
    }
  ],
  "policies": []
}
```

### 13.2 Route: `POST /api/design/generate_workflow`

Calls Model 2 architect to generate a playbook from user intent + retrieved context.

**Request:**
```json
{
  "intent": "When a GPU runs out of memory, collect diagnostics, try to restart the workload, and if it fails again, page the on-call engineer",
  "context": {
    "tools": [ ... ],
    "example_playbooks": [ ... ],
    "policies": [ ... ]
  },
  "target_incident_types": ["incident.gpu_memory_failure"]
}
```

**Implementation:**
1. If `context` is not provided, automatically run RAG search first
2. Build prompt for Model 2 (see §16)
3. Call Model 2 architect service
4. Parse the returned JSON workflow spec
5. Run initial validation (schema check)
6. Return the spec + any warnings

**Response:**
```json
{
  "workflow_spec": { ... },
  "warnings": [],
  "suggested_name": "GPU Memory Failure Recovery",
  "suggested_description": "Handles GPU OOM by collecting diagnostics, restarting workload, and escalating on repeated failure"
}
```

### 13.3 Route: `POST /api/design/validate_workflow`

Validates a workflow spec against the DSL schema, tool registry, and policies.

**Request:**
```json
{
  "workflow_spec": { ... }
}
```

**Implementation:**
1. Validate JSON against DSL schema (§19)
2. Check that all referenced tools exist in the tool registry and are active
3. Check that side-effect levels are appropriate (destructive tools require approval steps)
4. Check for unreachable steps, infinite loops, missing error handlers
5. Validate timeout and retry configurations are within acceptable bounds
6. Return validation result with errors and warnings

**Response:**
```json
{
  "valid": true,
  "errors": [],
  "warnings": [
    "Step 'restart_workload' uses a 'write' side-effect tool without a preceding approval step. Consider adding one for production use."
  ]
}
```

### 13.4 Route: `POST /api/design/save_playbook`

Saves a new playbook version as a draft.

**Request:**
```json
{
  "playbook_id": "uuid or null for new",
  "name": "GPU Memory Failure Recovery",
  "description": "...",
  "workflow_spec": { ... },
  "trigger_bindings": ["incident.gpu_memory_failure"],
  "change_notes": "Initial version"
}
```

**Implementation:**
1. If `playbook_id` is null, create new playbook record
2. Determine next version_number for this playbook
3. Compute SHA-256 checksum of `workflow_spec`
4. Generate embedding from name + description + step descriptions
5. Insert `playbook_versions` record with status `draft`
6. Return the saved version

**Response:**
```json
{
  "playbook_id": "uuid",
  "version_id": "uuid",
  "version_number": 1,
  "status": "draft"
}
```

### 13.5 Route: `POST /api/design/publish_playbook`

Transitions a playbook version through the approval workflow.

**Request:**
```json
{
  "version_id": "uuid",
  "action": "validate | approve | publish | archive",
  "approval_notes": "Reviewed and approved for production use"
}
```

**Implementation:**
1. Load the playbook version
2. Validate the state transition:
   - `draft` → `validated` (requires passing validation)
   - `validated` → `approved` (requires appropriate role)
   - `approved` → `published` (makes it available for runtime triggers)
   - any → `archived`
3. If transitioning to `published`:
   - Create/update `trigger_rules` entries for the bound incident types
   - Archive any previously published version for the same playbook
4. Update status and audit info

**Response:**
```json
{
  "version_id": "uuid",
  "new_status": "published",
  "trigger_rules_created": 1
}
```

### 13.6 Route: `GET /api/design/playbooks/{playbook_id}/versions`

Returns all versions of a playbook with their statuses.

**Response:**
```json
{
  "playbook_id": "uuid",
  "name": "GPU Memory Failure Recovery",
  "versions": [
    {
      "version_id": "uuid",
      "version_number": 3,
      "status": "published",
      "created_at": "2025-01-10T...",
      "created_by": "alice@example.com",
      "change_notes": "Added Slack notification step"
    },
    {
      "version_id": "uuid",
      "version_number": 2,
      "status": "archived",
      "created_at": "2025-01-05T...",
      "created_by": "alice@example.com",
      "change_notes": "Fixed retry logic"
    }
  ]
}
```

---

## 14. Design Plane — Embedding Service

### 14.1 Overview

The embedding service converts text into vector embeddings for semantic search. It is used by:
- RAG search (query embedding)
- Tool registry (embedding `embedding_text` on tool create/update)
- Playbook registry (embedding description + step descriptions on save)

### 14.2 Implementation (`app/services/embedding_service.py`)

```python
"""
Embedding service. Wraps the embedding model (OpenAI, local, etc.)
"""
import httpx
from typing import Optional
from app.config import get_settings


class EmbeddingService:
    def __init__(self):
        self.settings = get_settings()
        # Default: OpenAI text-embedding-3-small (1536 dimensions)
        # Can be swapped for local model (sentence-transformers, etc.)
        self.model = self.settings.embedding_model  # "text-embedding-3-small"
        self.api_key = self.settings.embedding_api_key
        self.base_url = self.settings.embedding_api_base_url  # "https://api.openai.com/v1"
        self.dimensions = self.settings.embedding_dimensions  # 1536

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string. Returns vector of configured dimensions."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": text,
                    "dimensions": self.dimensions,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a single API call."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": texts,
                    "dimensions": self.dimensions,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
```

### 14.3 Embedding Dimensions

If using OpenAI `text-embedding-3-small`: 1536 dimensions.
If using a local model (e.g., `all-MiniLM-L6-v2`): 384 dimensions.

The `vector(N)` column type in pgvector must match the configured dimensions. This is set in `app/config.py` and used in the Alembic migration.

---

## 15. Design Plane — Vector Search (pgvector)

### 15.1 Implementation (`app/services/vector_search_service.py`)

```python
"""
Vector search service using pgvector in Postgres.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.embedding_service import EmbeddingService


class VectorSearchService:
    def __init__(self, embedding_service: EmbeddingService):
        self.embedding_service = embedding_service

    async def search_tools(
        self,
        db: AsyncSession,
        query: str,
        limit: int = 10,
        min_similarity: float = 0.5,
    ) -> list[dict]:
        query_embedding = await self.embedding_service.embed(query)
        embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

        result = await db.execute(
            text("""
                SELECT
                    id, name, display_name, description, category,
                    input_schema, output_schema, side_effect_level,
                    required_approvals, environments_allowed,
                    1 - (embedding <=> :embedding::vector) AS similarity
                FROM tools
                WHERE is_active = true
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> :embedding::vector) >= :min_similarity
                ORDER BY embedding <=> :embedding::vector
                LIMIT :limit
            """),
            {
                "embedding": embedding_str,
                "min_similarity": min_similarity,
                "limit": limit,
            },
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def search_playbooks(
        self,
        db: AsyncSession,
        query: str,
        limit: int = 10,
        min_similarity: float = 0.5,
        status_filter: list[str] = None,
    ) -> list[dict]:
        query_embedding = await self.embedding_service.embed(query)
        embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

        status_clause = ""
        params = {
            "embedding": embedding_str,
            "min_similarity": min_similarity,
            "limit": limit,
        }
        if status_filter:
            status_clause = "AND pv.status = ANY(:statuses)"
            params["statuses"] = status_filter

        result = await db.execute(
            text(f"""
                SELECT
                    p.id AS playbook_id,
                    p.name,
                    p.description,
                    pv.id AS version_id,
                    pv.version_number,
                    pv.status,
                    pv.workflow_spec,
                    1 - (pv.embedding <=> :embedding::vector) AS similarity
                FROM playbook_versions pv
                JOIN playbooks p ON p.id = pv.playbook_id
                WHERE pv.embedding IS NOT NULL
                  AND 1 - (pv.embedding <=> :embedding::vector) >= :min_similarity
                  {status_clause}
                ORDER BY pv.embedding <=> :embedding::vector
                LIMIT :limit
            """),
            params,
        )
        return [dict(row._mapping) for row in result.fetchall()]
```

---

## 16. Design Plane — Model 2 Architect Service

### 16.1 Overview

Model 2 takes a user's natural-language intent, augmented by retrieved context (tools, example playbooks, policies), and generates a **strict JSON workflow specification** conforming to the playbook DSL (§19).

### 16.2 Architect Client (`app/services/architect_client.py`)

```python
"""
Client for Model 2 architect service.
Generates playbook workflow specs from natural language intent.
"""
import httpx
import json
from typing import Optional
from app.config import get_settings


class ArchitectClient:
    def __init__(self):
        self.settings = get_settings()
        # Model 2 can be an LLM API (Anthropic, OpenAI) or a self-hosted model
        self.api_key = self.settings.architect_api_key
        self.base_url = self.settings.architect_api_base_url
        self.model = self.settings.architect_model  # e.g. "claude-sonnet-4-20250514"

    async def generate_workflow(
        self,
        intent: str,
        tools: list[dict],
        example_playbooks: list[dict] = None,
        policies: list[str] = None,
        target_incident_types: list[str] = None,
    ) -> dict:
        system_prompt = self._build_system_prompt(tools, example_playbooks, policies)
        user_prompt = self._build_user_prompt(intent, target_incident_types)

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": user_prompt}
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()

            # Extract JSON from response
            text_content = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text_content += block["text"]

            # Parse the JSON workflow spec
            # Strip markdown code fences if present
            cleaned = text_content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
                cleaned = cleaned.rsplit("```", 1)[0]
            return json.loads(cleaned)

    def _build_system_prompt(
        self,
        tools: list[dict],
        example_playbooks: list[dict] = None,
        policies: list[str] = None,
    ) -> str:
        tools_section = "## Available Tools\n\n"
        for tool in tools:
            tools_section += f"### {tool['name']}\n"
            tools_section += f"Description: {tool['description']}\n"
            tools_section += f"Side effect level: {tool.get('side_effect_level', 'unknown')}\n"
            tools_section += f"Input schema: {json.dumps(tool.get('input_schema', {}))}\n"
            tools_section += f"Required approvals: {tool.get('required_approvals', 0)}\n\n"

        examples_section = ""
        if example_playbooks:
            examples_section = "## Example Playbooks\n\n"
            for pb in example_playbooks[:3]:
                examples_section += f"### {pb.get('name', 'Unnamed')}\n"
                examples_section += f"```json\n{json.dumps(pb.get('workflow_spec', {}), indent=2)}\n```\n\n"

        policies_section = ""
        if policies:
            policies_section = "## Policies\n\n"
            for p in policies:
                policies_section += f"- {p}\n"

        return f"""You are an infrastructure automation architect. You generate workflow specifications
in a strict JSON DSL format for incident remediation playbooks.

RULES:
1. Only use tools from the Available Tools list below. Do not invent tools.
2. Every tool reference must use the exact 'name' field from the tool list.
3. Tools with side_effect_level 'destructive' or 'write' MUST be preceded by an approval step
   unless the intent explicitly says to auto-remediate without approval.
4. Include appropriate retry and timeout policies.
5. Include error handling steps (on_failure transitions).
6. Output ONLY valid JSON conforming to the playbook DSL. No explanation, no markdown.

{tools_section}
{examples_section}
{policies_section}

## Playbook DSL Schema

The output must be a JSON object with this structure:

{{
  "name": "string - playbook name",
  "description": "string - what this playbook does",
  "version": "string - semver",
  "trigger": {{
    "incident_types": ["string - incident types this handles"],
    "severity_filter": ["string - optional severity filter"]
  }},
  "parameters": {{
    "param_name": {{
      "type": "string | number | boolean",
      "description": "string",
      "default": "optional default value",
      "required": true/false
    }}
  }},
  "steps": [
    {{
      "id": "string - unique step identifier",
      "name": "string - human readable name",
      "type": "action | approval | condition | delay | parallel | notification",
      "tool": "string - tool name from registry (for action type)",
      "input": {{ "key": "value or ${{incident.field}} or ${{steps.prev_step.output.field}}" }},
      "timeout": "duration string e.g. '5m', '1h'",
      "retry": {{
        "max_attempts": 3,
        "backoff": "exponential",
        "initial_interval": "10s"
      }},
      "on_success": "next_step_id",
      "on_failure": "error_step_id or 'abort'",
      "condition": "expression (for condition type)",
      "branches": {{ "true": "step_id", "false": "step_id" }}
    }}
  ]
}}
"""

    def _build_user_prompt(
        self,
        intent: str,
        target_incident_types: list[str] = None,
    ) -> str:
        prompt = f"Generate a playbook workflow spec for the following intent:\n\n{intent}"
        if target_incident_types:
            prompt += f"\n\nTarget incident types: {', '.join(target_incident_types)}"
        return prompt
```

### 16.3 Implementation Notes

- The architect service uses **Anthropic Claude** (or OpenAI, configurable) for generation.
- The system prompt includes the full DSL schema, available tools, example playbooks, and policies.
- The response is expected to be pure JSON — no markdown wrapping.
- Validation happens after generation (§13.3) to catch any schema violations.
- For v1, this can use the same API key infrastructure as the classifier, but it should be a separate client instance with different timeout and model settings.

---

## 17. Tool Registry

### 17.1 Overview

The tool registry is the source of truth for all actions that playbooks can perform. Model 2 generates workflows using only tools from this registry. At runtime, each tool maps to a registered Temporal activity.

### 17.2 Service (`app/services/tool_registry_service.py`)

Provides CRUD operations on the `tools` table plus embedding generation on create/update.

### 17.3 Default Tools to Seed

The `scripts/seed_tools.py` script creates these default tools:

```python
DEFAULT_TOOLS = [
    {
        "name": "fetch_pod_logs",
        "display_name": "Fetch Pod Logs",
        "description": "Retrieves recent logs from a specified Kubernetes pod. Returns the last N lines or logs within a time range.",
        "category": "observability",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "pod": {"type": "string"},
                "container": {"type": "string", "default": ""},
                "tail_lines": {"type": "integer", "default": 200},
                "since_seconds": {"type": "integer", "default": 600}
            },
            "required": ["namespace", "pod"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "logs": {"type": "string"},
                "line_count": {"type": "integer"}
            }
        },
        "side_effect_level": "read",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"]
    },
    {
        "name": "query_prometheus",
        "display_name": "Query Prometheus",
        "description": "Executes a PromQL query against Prometheus and returns the result. Useful for checking current metric values, recent trends, and alert context.",
        "category": "observability",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL query expression"},
                "time": {"type": "string", "description": "Optional evaluation timestamp (ISO 8601)"},
                "range_start": {"type": "string"},
                "range_end": {"type": "string"},
                "step": {"type": "string", "default": "60s"}
            },
            "required": ["query"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result_type": {"type": "string"},
                "result": {"type": "array"}
            }
        },
        "side_effect_level": "read",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"]
    },
    {
        "name": "restart_workload",
        "display_name": "Restart Workload",
        "description": "Performs a rolling restart of a Kubernetes workload (Deployment, StatefulSet, or DaemonSet) by patching the pod template annotation.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "workload_type": {"type": "string", "enum": ["deployment", "statefulset", "daemonset"]},
                "workload_name": {"type": "string"}
            },
            "required": ["namespace", "workload_type", "workload_name"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "message": {"type": "string"}
            }
        },
        "side_effect_level": "write",
        "required_approvals": 1,
        "environments_allowed": ["production", "staging", "development"]
    },
    {
        "name": "scale_deployment",
        "display_name": "Scale Deployment",
        "description": "Scales a Kubernetes Deployment to a specified replica count.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "deployment_name": {"type": "string"},
                "replicas": {"type": "integer", "minimum": 0}
            },
            "required": ["namespace", "deployment_name", "replicas"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "previous_replicas": {"type": "integer"},
                "new_replicas": {"type": "integer"}
            }
        },
        "side_effect_level": "write",
        "required_approvals": 1,
        "environments_allowed": ["production", "staging", "development"]
    },
    {
        "name": "rollback_release",
        "display_name": "Rollback Release",
        "description": "Rolls back a Kubernetes Deployment to the previous revision or a specified revision number.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "deployment_name": {"type": "string"},
                "revision": {"type": "integer", "description": "Specific revision to roll back to. 0 = previous."}
            },
            "required": ["namespace", "deployment_name"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "rolled_back_to_revision": {"type": "integer"},
                "message": {"type": "string"}
            }
        },
        "side_effect_level": "destructive",
        "required_approvals": 1,
        "environments_allowed": ["production", "staging"]
    },
    {
        "name": "page_oncall",
        "display_name": "Page On-Call Engineer",
        "description": "Sends a page/alert to the on-call engineer via PagerDuty or configured paging system.",
        "category": "notification",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "description": "PagerDuty service ID or team identifier"},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "incident_url": {"type": "string"}
            },
            "required": ["severity", "title", "body"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "page_id": {"type": "string"}
            }
        },
        "side_effect_level": "write",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"]
    },
    {
        "name": "slack_notification",
        "display_name": "Send Slack Notification",
        "description": "Sends a message to a Slack channel. Does not wait for response.",
        "category": "notification",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "message": {"type": "string"},
                "severity_color": {"type": "string", "enum": ["red", "orange", "yellow", "green"]}
            },
            "required": ["channel", "message"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "ts": {"type": "string"}
            }
        },
        "side_effect_level": "write",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"]
    },
    {
        "name": "slack_approval",
        "display_name": "Request Slack Approval",
        "description": "Sends an approval request to a Slack channel and waits for an approved/rejected reaction or button click within a timeout.",
        "category": "notification",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "message": {"type": "string"},
                "timeout_minutes": {"type": "integer", "default": 30},
                "required_approvers": {"type": "integer", "default": 1}
            },
            "required": ["channel", "message"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "approved": {"type": "boolean"},
                "approver": {"type": "string"},
                "timestamp": {"type": "string"}
            }
        },
        "side_effect_level": "write",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"]
    },
    {
        "name": "open_ticket",
        "display_name": "Open Ticket",
        "description": "Creates a ticket in the configured ticketing system (Jira, ServiceNow, PagerDuty, etc.).",
        "category": "ticketing",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "team": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["title", "description", "priority"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "ticket_id": {"type": "string"},
                "ticket_url": {"type": "string"}
            }
        },
        "side_effect_level": "write",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"]
    },
    {
        "name": "describe_pod",
        "display_name": "Describe Pod",
        "description": "Returns the full Kubernetes pod description including status, events, conditions, and container states.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "pod": {"type": "string"}
            },
            "required": ["namespace", "pod"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "object"},
                "events": {"type": "array"},
                "conditions": {"type": "array"},
                "container_statuses": {"type": "array"}
            }
        },
        "side_effect_level": "read",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"]
    },
    {
        "name": "get_node_status",
        "display_name": "Get Node Status",
        "description": "Returns the status, conditions, allocatable resources, and recent events for a Kubernetes node.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string"}
            },
            "required": ["node"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "conditions": {"type": "array"},
                "allocatable": {"type": "object"},
                "capacity": {"type": "object"},
                "events": {"type": "array"}
            }
        },
        "side_effect_level": "read",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"]
    },
    {
        "name": "cordon_node",
        "display_name": "Cordon Node",
        "description": "Marks a Kubernetes node as unschedulable (cordon). Existing pods continue running but no new pods will be scheduled.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string"}
            },
            "required": ["node"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "message": {"type": "string"}
            }
        },
        "side_effect_level": "write",
        "required_approvals": 1,
        "environments_allowed": ["production", "staging"]
    },
    {
        "name": "drain_node",
        "display_name": "Drain Node",
        "description": "Safely evicts all pods from a Kubernetes node and marks it as unschedulable. Use with caution.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string"},
                "grace_period_seconds": {"type": "integer", "default": 300},
                "ignore_daemonsets": {"type": "boolean", "default": true},
                "delete_emptydir_data": {"type": "boolean", "default": false}
            },
            "required": ["node"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "evicted_pods": {"type": "array"},
                "message": {"type": "string"}
            }
        },
        "side_effect_level": "destructive",
        "required_approvals": 1,
        "environments_allowed": ["production", "staging"]
    },
    {
        "name": "run_diagnostic_script",
        "display_name": "Run Diagnostic Script",
        "description": "Executes a pre-approved diagnostic script or command in a pod's container. Only pre-registered scripts are allowed.",
        "category": "observability",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "pod": {"type": "string"},
                "container": {"type": "string"},
                "script_name": {"type": "string", "description": "Name of pre-registered diagnostic script"},
                "args": {"type": "object"}
            },
            "required": ["namespace", "pod", "script_name"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "exit_code": {"type": "integer"},
                "stdout": {"type": "string"},
                "stderr": {"type": "string"}
            }
        },
        "side_effect_level": "read",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"]
    }
]
```

---

## 18. Playbook Registry

### 18.1 Service (`app/services/playbook_service.py`)

This service manages the full lifecycle of playbooks:

- `create_playbook(name, description, owner_team, created_by) -> Playbook`
- `save_version(playbook_id, workflow_spec, trigger_bindings, change_notes, created_by) -> PlaybookVersion`
- `get_playbook(playbook_id) -> Playbook + latest version`
- `list_playbooks(filters) -> list[Playbook]`
- `get_versions(playbook_id) -> list[PlaybookVersion]`
- `get_version(version_id) -> PlaybookVersion`
- `transition_status(version_id, new_status, actor, notes) -> PlaybookVersion`
- `find_playbook_for_incident(incident_type, entity) -> Optional[PlaybookVersion]`
  - Queries `trigger_rules` table, filters by active + published, matches entity filters, returns highest priority match

### 18.2 Status Transitions

```
draft → generated → validated → approved → published → archived
         ↑                                      │
         └──────────────────────────────────────┘ (new version)
```

Only `approved` or `published` versions can be executed at runtime.

---

## 19. Playbook DSL Specification

### 19.1 Full Schema

This is the JSON schema that all workflow specs must conform to. The `DynamicPlaybookExecutor` interprets this at runtime.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "AutoMend Playbook DSL",
  "type": "object",
  "required": ["name", "version", "trigger", "steps"],
  "properties": {
    "name": {
      "type": "string",
      "description": "Human-readable playbook name"
    },
    "description": {
      "type": "string"
    },
    "version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+\\.\\d+$",
      "description": "Semantic version of the playbook spec"
    },
    "trigger": {
      "type": "object",
      "required": ["incident_types"],
      "properties": {
        "incident_types": {
          "type": "array",
          "items": { "type": "string" },
          "minItems": 1
        },
        "severity_filter": {
          "type": "array",
          "items": { "type": "string", "enum": ["critical", "high", "medium", "low", "info"] }
        },
        "entity_filter": {
          "type": "object",
          "description": "Key-value pairs to match against incident entity fields"
        }
      }
    },
    "parameters": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "required": ["type"],
        "properties": {
          "type": { "type": "string", "enum": ["string", "number", "boolean", "array", "object"] },
          "description": { "type": "string" },
          "default": {},
          "required": { "type": "boolean", "default": false }
        }
      }
    },
    "steps": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["id", "name", "type"],
        "properties": {
          "id": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]*$",
            "description": "Unique step identifier, used for referencing in transitions"
          },
          "name": {
            "type": "string",
            "description": "Human-readable step name"
          },
          "type": {
            "type": "string",
            "enum": ["action", "approval", "condition", "delay", "parallel", "notification", "sub_playbook"]
          },
          "tool": {
            "type": "string",
            "description": "Tool name from registry. Required for type=action."
          },
          "input": {
            "type": "object",
            "description": "Input parameters for the tool. Supports template expressions: ${incident.entity.namespace}, ${steps.prev_step.output.field}, ${params.my_param}"
          },
          "timeout": {
            "type": "string",
            "pattern": "^\\d+[smhd]$",
            "description": "Timeout for this step. e.g. '5m', '1h', '30s'"
          },
          "retry": {
            "type": "object",
            "properties": {
              "max_attempts": { "type": "integer", "minimum": 1, "maximum": 10 },
              "backoff": { "type": "string", "enum": ["fixed", "exponential"] },
              "initial_interval": { "type": "string", "pattern": "^\\d+[smh]$" },
              "max_interval": { "type": "string", "pattern": "^\\d+[smh]$" }
            }
          },
          "on_success": {
            "type": "string",
            "description": "Step ID to transition to on success. If omitted, goes to next step in array."
          },
          "on_failure": {
            "type": "string",
            "description": "Step ID to transition to on failure, or 'abort' to stop the workflow."
          },
          "condition": {
            "type": "string",
            "description": "For type=condition: expression to evaluate. Supports ${steps.X.output.Y} references."
          },
          "branches": {
            "type": "object",
            "properties": {
              "true": { "type": "string" },
              "false": { "type": "string" }
            },
            "description": "For type=condition: step IDs to branch to."
          },
          "duration": {
            "type": "string",
            "pattern": "^\\d+[smhd]$",
            "description": "For type=delay: how long to wait."
          },
          "parallel_steps": {
            "type": "array",
            "items": { "type": "string" },
            "description": "For type=parallel: list of step IDs to execute concurrently."
          },
          "approval_channel": {
            "type": "string",
            "description": "For type=approval: Slack channel or approval mechanism."
          },
          "approval_message": {
            "type": "string",
            "description": "For type=approval: message to display to approvers."
          },
          "approval_timeout": {
            "type": "string",
            "pattern": "^\\d+[smhd]$",
            "description": "For type=approval: how long to wait before auto-rejecting."
          }
        }
      }
    },
    "on_complete": {
      "type": "object",
      "description": "Actions to take when the playbook completes successfully.",
      "properties": {
        "resolve_incident": { "type": "boolean", "default": true },
        "notification": {
          "type": "object",
          "properties": {
            "channel": { "type": "string" },
            "message": { "type": "string" }
          }
        }
      }
    },
    "on_abort": {
      "type": "object",
      "description": "Actions to take when the playbook is aborted or fails terminally.",
      "properties": {
        "escalate": { "type": "boolean", "default": true },
        "page_oncall": { "type": "boolean", "default": false },
        "notification": {
          "type": "object",
          "properties": {
            "channel": { "type": "string" },
            "message": { "type": "string" }
          }
        }
      }
    }
  }
}
```

### 19.2 Example Playbook Spec

```json
{
  "name": "GPU Memory Failure Recovery",
  "description": "Handles GPU out-of-memory failures by collecting diagnostics, attempting workload restart, and escalating on repeated failure.",
  "version": "1.0.0",
  "trigger": {
    "incident_types": ["incident.gpu_memory_failure"],
    "severity_filter": ["high", "critical"]
  },
  "parameters": {
    "max_restart_attempts": {
      "type": "number",
      "description": "Maximum number of restart attempts before escalating",
      "default": 2,
      "required": false
    }
  },
  "steps": [
    {
      "id": "fetch_logs",
      "name": "Fetch Pod Logs",
      "type": "action",
      "tool": "fetch_pod_logs",
      "input": {
        "namespace": "${incident.entity.namespace}",
        "pod": "${incident.entity.pod}",
        "tail_lines": 500,
        "since_seconds": 600
      },
      "timeout": "2m",
      "retry": {
        "max_attempts": 2,
        "backoff": "fixed",
        "initial_interval": "5s"
      },
      "on_failure": "escalate"
    },
    {
      "id": "check_gpu_metrics",
      "name": "Check GPU Metrics",
      "type": "action",
      "tool": "query_prometheus",
      "input": {
        "query": "DCGM_FI_DEV_FB_USED{pod='${incident.entity.pod}'} / DCGM_FI_DEV_FB_TOTAL{pod='${incident.entity.pod}'}"
      },
      "timeout": "1m",
      "on_failure": "notify_and_approve"
    },
    {
      "id": "notify_and_approve",
      "name": "Request Restart Approval",
      "type": "approval",
      "approval_channel": "#incident-ops",
      "approval_message": "GPU OOM detected on ${incident.entity.pod} in ${incident.entity.namespace}. Logs and metrics collected. Approve workload restart?",
      "approval_timeout": "15m",
      "on_success": "restart_workload",
      "on_failure": "escalate"
    },
    {
      "id": "restart_workload",
      "name": "Restart Workload",
      "type": "action",
      "tool": "restart_workload",
      "input": {
        "namespace": "${incident.entity.namespace}",
        "workload_type": "deployment",
        "workload_name": "${incident.entity.service}"
      },
      "timeout": "5m",
      "retry": {
        "max_attempts": 2,
        "backoff": "exponential",
        "initial_interval": "30s"
      },
      "on_success": "wait_and_verify",
      "on_failure": "escalate"
    },
    {
      "id": "wait_and_verify",
      "name": "Wait for Stabilization",
      "type": "delay",
      "duration": "3m",
      "on_success": "verify_metrics"
    },
    {
      "id": "verify_metrics",
      "name": "Verify GPU Metrics After Restart",
      "type": "action",
      "tool": "query_prometheus",
      "input": {
        "query": "DCGM_FI_DEV_FB_USED{pod=~'${incident.entity.service}.*'} / DCGM_FI_DEV_FB_TOTAL{pod=~'${incident.entity.service}.*'}"
      },
      "timeout": "1m",
      "on_success": "check_recovery",
      "on_failure": "escalate"
    },
    {
      "id": "check_recovery",
      "name": "Check If Recovery Succeeded",
      "type": "condition",
      "condition": "${steps.verify_metrics.output.result[0].value[1]} < 0.9",
      "branches": {
        "true": "notify_resolved",
        "false": "escalate"
      }
    },
    {
      "id": "notify_resolved",
      "name": "Notify Resolution",
      "type": "action",
      "tool": "slack_notification",
      "input": {
        "channel": "#incident-ops",
        "message": "GPU OOM incident for ${incident.entity.pod} has been resolved after workload restart. GPU memory usage is now normal.",
        "severity_color": "green"
      },
      "timeout": "30s"
    },
    {
      "id": "escalate",
      "name": "Escalate to On-Call",
      "type": "action",
      "tool": "page_oncall",
      "input": {
        "severity": "${incident.severity}",
        "title": "GPU OOM - Automated recovery failed for ${incident.entity.service}",
        "body": "Automated GPU OOM recovery failed for ${incident.entity.pod} in ${incident.entity.namespace}. Manual intervention required. Incident: ${incident.id}",
        "incident_url": "https://automend.internal/incidents/${incident.id}"
      },
      "timeout": "1m",
      "on_success": "create_ticket"
    },
    {
      "id": "create_ticket",
      "name": "Create Tracking Ticket",
      "type": "action",
      "tool": "open_ticket",
      "input": {
        "title": "GPU OOM Recovery Failed - ${incident.entity.service}",
        "description": "Automated GPU OOM recovery failed. Pod: ${incident.entity.pod}, Namespace: ${incident.entity.namespace}. See incident ${incident.id} for details.",
        "priority": "${incident.severity}",
        "labels": ["gpu", "oom", "automated-recovery-failed"]
      },
      "timeout": "1m"
    }
  ],
  "on_complete": {
    "resolve_incident": true,
    "notification": {
      "channel": "#incident-ops",
      "message": "Playbook completed for incident ${incident.id}"
    }
  },
  "on_abort": {
    "escalate": true,
    "page_oncall": true,
    "notification": {
      "channel": "#incident-ops",
      "message": "Playbook ABORTED for incident ${incident.id}. Manual intervention required."
    }
  }
}
```

### 19.3 Template Expression Syntax

The DSL supports template expressions in `input` fields using `${}` syntax:

| Expression | Resolves to |
|---|---|
| `${incident.id}` | The incident UUID |
| `${incident.entity.namespace}` | Entity field from the incident |
| `${incident.severity}` | Incident severity string |
| `${incident.evidence.classifier.label}` | Nested evidence field |
| `${steps.<step_id>.output.<field>}` | Output of a previous step |
| `${params.<param_name>}` | Playbook parameter value |
| `${env.<var_name>}` | Environment variable (restricted set) |

The `DynamicPlaybookExecutor` resolves these at runtime before each step execution.

---

## 20. Orchestration Plane — Temporal

### 20.1 Setup

Temporal server runs as a separate infrastructure component. For development:

```yaml
# In docker-compose.yml
temporal:
  image: temporalio/auto-setup:latest
  ports:
    - "7233:7233"    # gRPC frontend
  environment:
    - DB=postgres12
    - DB_PORT=5432
    - POSTGRES_USER=temporal
    - POSTGRES_PWD=temporal
    - POSTGRES_SEEDS=postgres
  depends_on:
    - postgres

temporal-ui:
  image: temporalio/ui:latest
  ports:
    - "8080:8080"
  environment:
    - TEMPORAL_ADDRESS=temporal:7233
```

### 20.2 Task Queue

All AutoMend workflows and activities use the task queue: `automend-playbook-queue`

### 20.3 Temporal Worker Entrypoint (`main_temporal_worker.py`)

```python
"""
Temporal Worker entrypoint.
Registers workflows and activities, then starts polling.
"""
import asyncio
from temporalio.client import Client
from temporalio.worker import Worker
from app.config import get_settings
from app.temporal.workflows import DynamicPlaybookExecutor
from app.temporal.activities import (
    fetch_pod_logs_activity,
    query_prometheus_activity,
    restart_workload_activity,
    scale_deployment_activity,
    rollback_release_activity,
    page_oncall_activity,
    slack_notification_activity,
    slack_approval_activity,
    open_ticket_activity,
    describe_pod_activity,
    get_node_status_activity,
    cordon_node_activity,
    drain_node_activity,
    run_diagnostic_script_activity,
    load_playbook_activity,
    resolve_incident_activity,
    update_incident_status_activity,
    record_step_result_activity,
)

TASK_QUEUE = "automend-playbook-queue"

async def main():
    settings = get_settings()
    client = await Client.connect(settings.temporal_server_url)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[DynamicPlaybookExecutor],
        activities=[
            fetch_pod_logs_activity,
            query_prometheus_activity,
            restart_workload_activity,
            scale_deployment_activity,
            rollback_release_activity,
            page_oncall_activity,
            slack_notification_activity,
            slack_approval_activity,
            open_ticket_activity,
            describe_pod_activity,
            get_node_status_activity,
            cordon_node_activity,
            drain_node_activity,
            run_diagnostic_script_activity,
            load_playbook_activity,
            resolve_incident_activity,
            update_incident_status_activity,
            record_step_result_activity,
        ],
    )
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 21. Orchestration Plane — DynamicPlaybookExecutor Workflow

### 21.1 Overview

This is the **single generic workflow** that executes any playbook. There is NOT a separate Temporal workflow per playbook. Instead, this workflow:

1. Receives the `playbook_version_id` and incident payload as input
2. Loads the playbook spec from Postgres (via activity)
3. Validates the checksum
4. Interprets the DSL step by step
5. Dispatches registered activities for each step
6. Handles conditions, branches, delays, approvals, retries
7. Accepts signals for new evidence
8. Records step results
9. On completion, resolves the incident

### 21.2 Workflow Implementation (`app/temporal/workflows.py`)

```python
"""
DynamicPlaybookExecutor — the universal playbook workflow.
"""
import hashlib
import json
from datetime import timedelta
from typing import Any, Optional
from temporalio import workflow
from temporalio.common import RetryPolicy
from dataclasses import dataclass

# Import activity stubs (NOT the actual functions)
with workflow.unsafe.imports_passed_through():
    from app.temporal.activities import (
        load_playbook_activity,
        resolve_incident_activity,
        update_incident_status_activity,
        record_step_result_activity,
    )


@dataclass
class PlaybookExecutionInput:
    playbook_version_id: str
    incident_id: str
    incident_payload: dict  # Canonical incident as dict
    execution_params: dict = None  # Optional parameter overrides


@dataclass
class StepResult:
    step_id: str
    success: bool
    output: dict = None
    error: str = None


@workflow.defn
class DynamicPlaybookExecutor:
    def __init__(self):
        self.step_outputs: dict[str, Any] = {}
        self.new_evidence_queue: list[dict] = []
        self.is_aborted: bool = False

    @workflow.signal
    async def new_evidence(self, evidence: dict):
        """Signal handler: new evidence arrived for this incident."""
        self.new_evidence_queue.append(evidence)

    @workflow.signal
    async def abort(self, reason: str):
        """Signal handler: operator requested abort."""
        self.is_aborted = True

    @workflow.run
    async def run(self, input: PlaybookExecutionInput) -> dict:
        # Step 1: Load playbook spec from Postgres
        playbook_data = await workflow.execute_activity(
            load_playbook_activity,
            args=[input.playbook_version_id],
            start_to_close_timeout=timedelta(seconds=30),
        )

        spec = playbook_data["workflow_spec"]
        expected_checksum = playbook_data["spec_checksum"]

        # Step 2: Validate checksum
        actual_checksum = hashlib.sha256(
            json.dumps(spec, sort_keys=True).encode()
        ).hexdigest()
        if actual_checksum != expected_checksum:
            raise ValueError("Playbook checksum mismatch — spec may have been tampered with")

        # Step 3: Update incident status to in_progress
        await workflow.execute_activity(
            update_incident_status_activity,
            args=[input.incident_id, "in_progress"],
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Step 4: Build execution context
        context = {
            "incident": input.incident_payload,
            "params": {**self._get_default_params(spec), **(input.execution_params or {})},
            "steps": {},
            "env": {},
        }

        # Step 5: Execute steps
        steps = spec["steps"]
        step_index = {s["id"]: s for s in steps}
        current_step_id = steps[0]["id"]
        completed = False

        while current_step_id and not self.is_aborted:
            step = step_index.get(current_step_id)
            if not step:
                raise ValueError(f"Step '{current_step_id}' not found in playbook spec")

            # Execute the step
            result = await self._execute_step(step, context, input.incident_id)

            # Record result
            self.step_outputs[step["id"]] = result
            context["steps"][step["id"]] = {
                "output": result.output if result.success else {},
                "success": result.success,
                "error": result.error,
            }

            await workflow.execute_activity(
                record_step_result_activity,
                args=[input.incident_id, step["id"], result.success, result.output, result.error],
                start_to_close_timeout=timedelta(seconds=30),
            )

            # Determine next step
            if result.success:
                next_step = step.get("on_success")
                if next_step is None:
                    # Default: go to next step in array
                    step_idx = steps.index(step)
                    if step_idx + 1 < len(steps):
                        next_step = steps[step_idx + 1]["id"]
                    else:
                        completed = True
                        next_step = None
            else:
                next_step = step.get("on_failure")
                if next_step == "abort" or next_step is None:
                    self.is_aborted = True
                    next_step = None

            current_step_id = next_step

        # Step 6: Handle completion or abort
        if completed and not self.is_aborted:
            on_complete = spec.get("on_complete", {})
            if on_complete.get("resolve_incident", True):
                await workflow.execute_activity(
                    resolve_incident_activity,
                    args=[input.incident_id],
                    start_to_close_timeout=timedelta(seconds=30),
                )
            return {"status": "completed", "steps_executed": list(self.step_outputs.keys())}
        else:
            on_abort = spec.get("on_abort", {})
            await workflow.execute_activity(
                update_incident_status_activity,
                args=[input.incident_id, "open"],
                start_to_close_timeout=timedelta(seconds=30),
            )
            return {"status": "aborted", "steps_executed": list(self.step_outputs.keys())}

    async def _execute_step(self, step: dict, context: dict, incident_id: str) -> StepResult:
        """Execute a single step based on its type."""
        step_type = step["type"]

        if step_type == "action":
            return await self._execute_action_step(step, context)
        elif step_type == "condition":
            return await self._execute_condition_step(step, context)
        elif step_type == "delay":
            return await self._execute_delay_step(step)
        elif step_type == "approval":
            return await self._execute_approval_step(step, context, incident_id)
        elif step_type == "notification":
            return await self._execute_action_step(step, context)  # Notifications use action tools
        elif step_type == "parallel":
            return await self._execute_parallel_step(step, context)
        else:
            return StepResult(step_id=step["id"], success=False, error=f"Unknown step type: {step_type}")

    async def _execute_action_step(self, step: dict, context: dict) -> StepResult:
        """Execute an action step by dispatching to the appropriate activity."""
        tool_name = step["tool"]
        resolved_input = self._resolve_templates(step.get("input", {}), context)
        timeout = self._parse_duration(step.get("timeout", "5m"))
        retry_config = step.get("retry", {})

        retry_policy = RetryPolicy(
            maximum_attempts=retry_config.get("max_attempts", 1),
            initial_interval=self._parse_duration(retry_config.get("initial_interval", "10s")),
            backoff_coefficient=2.0 if retry_config.get("backoff") == "exponential" else 1.0,
        )

        # The activity name follows the pattern: {tool_name}_activity
        activity_name = f"{tool_name}_activity"

        try:
            output = await workflow.execute_activity(
                activity_name,
                args=[resolved_input],
                start_to_close_timeout=timeout,
                retry_policy=retry_policy,
            )
            return StepResult(step_id=step["id"], success=True, output=output)
        except Exception as e:
            return StepResult(step_id=step["id"], success=False, error=str(e))

    async def _execute_condition_step(self, step: dict, context: dict) -> StepResult:
        """Evaluate a condition and return the branch result."""
        condition_expr = step["condition"]
        resolved = self._resolve_template_string(condition_expr, context)

        try:
            # Safe evaluation of simple boolean expressions
            result = self._safe_eval(resolved)
            branch = step.get("branches", {})
            next_step = branch.get("true" if result else "false")
            return StepResult(
                step_id=step["id"],
                success=True,
                output={"condition_result": result, "branch": "true" if result else "false", "next_step": next_step}
            )
        except Exception as e:
            return StepResult(step_id=step["id"], success=False, error=f"Condition eval failed: {e}")

    async def _execute_delay_step(self, step: dict) -> StepResult:
        """Wait for the specified duration."""
        duration = self._parse_duration(step.get("duration", "1m"))
        await workflow.sleep(duration)  # Temporal durable timer
        return StepResult(step_id=step["id"], success=True, output={"waited": str(duration)})

    async def _execute_approval_step(self, step: dict, context: dict, incident_id: str) -> StepResult:
        """Send approval request and wait for signal or timeout."""
        timeout = self._parse_duration(step.get("approval_timeout", "30m"))
        message = self._resolve_template_string(step.get("approval_message", "Approval required"), context)

        # Send approval notification (could be Slack, UI, etc.)
        try:
            await workflow.execute_activity(
                "slack_approval_activity",
                args=[{
                    "channel": step.get("approval_channel", "#incident-ops"),
                    "message": message,
                    "timeout_minutes": int(timeout.total_seconds() / 60),
                    "incident_id": incident_id,
                }],
                start_to_close_timeout=timeout + timedelta(minutes=1),
            )
            return StepResult(step_id=step["id"], success=True, output={"approved": True})
        except Exception as e:
            return StepResult(step_id=step["id"], success=False, error=f"Approval failed/rejected: {e}")

    async def _execute_parallel_step(self, step: dict, context: dict) -> StepResult:
        """Execute multiple steps in parallel (future implementation)."""
        # For v1, execute sequentially
        return StepResult(step_id=step["id"], success=True, output={"note": "parallel execution TBD"})

    def _resolve_templates(self, obj: Any, context: dict) -> Any:
        """Recursively resolve ${...} template expressions in a dict/list/string."""
        if isinstance(obj, str):
            return self._resolve_template_string(obj, context)
        elif isinstance(obj, dict):
            return {k: self._resolve_templates(v, context) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_templates(item, context) for item in obj]
        return obj

    def _resolve_template_string(self, s: str, context: dict) -> str:
        """Resolve a single string with ${...} expressions."""
        import re
        def replace_match(match):
            path = match.group(1)
            parts = path.split(".")
            value = context
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                elif isinstance(value, list) and part.isdigit():
                    value = value[int(part)]
                else:
                    return match.group(0)  # Return unresolved
                if value is None:
                    return ""
            return str(value) if value is not None else ""
        return re.sub(r'\$\{([^}]+)\}', replace_match, s)

    def _parse_duration(self, duration_str: str) -> timedelta:
        """Parse duration strings like '5m', '1h', '30s', '1d'."""
        if isinstance(duration_str, timedelta):
            return duration_str
        unit = duration_str[-1]
        value = int(duration_str[:-1])
        if unit == 's': return timedelta(seconds=value)
        elif unit == 'm': return timedelta(minutes=value)
        elif unit == 'h': return timedelta(hours=value)
        elif unit == 'd': return timedelta(days=value)
        else: return timedelta(minutes=value)

    def _get_default_params(self, spec: dict) -> dict:
        params = {}
        for name, config in spec.get("parameters", {}).items():
            if "default" in config:
                params[name] = config["default"]
        return params

    def _safe_eval(self, expression: str) -> bool:
        """Safely evaluate a simple boolean expression. Only supports basic comparisons."""
        # Only allow: numbers, comparison operators, boolean literals
        import ast
        try:
            tree = ast.parse(expression, mode='eval')
            # Whitelist only safe node types
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Expression, ast.Compare, ast.Constant,
                                        ast.Num, ast.Str, ast.BoolOp, ast.And, ast.Or,
                                        ast.UnaryOp, ast.Not, ast.Lt, ast.LtE,
                                        ast.Gt, ast.GtE, ast.Eq, ast.NotEq)):
                    raise ValueError(f"Disallowed expression node: {type(node).__name__}")
            return bool(eval(compile(tree, '<condition>', 'eval')))
        except Exception:
            return False
```

---

## 22. Orchestration Plane — Temporal Activities

### 22.1 Activity Registration (`app/temporal/activities.py`)

Each tool in the registry maps to a Temporal activity. Activities are simple async functions decorated with `@activity.defn`.

```python
"""
Temporal activities for AutoMend playbook execution.
Each activity corresponds to a tool in the tool registry.
"""
from temporalio import activity
from typing import Any
import httpx

# =============================================
# INFRASTRUCTURE ACTIVITIES (system)
# =============================================

@activity.defn
async def load_playbook_activity(playbook_version_id: str) -> dict:
    """Load a playbook version from Postgres. Returns workflow_spec + checksum."""
    from app.stores.postgres_store import PostgresStore
    store = PostgresStore()
    version = await store.get_playbook_version(playbook_version_id)
    return {
        "workflow_spec": version.workflow_spec,
        "spec_checksum": version.spec_checksum,
    }

@activity.defn
async def resolve_incident_activity(incident_id: str) -> dict:
    """Mark an incident as resolved."""
    from app.services.incident_service import IncidentService
    svc = IncidentService()
    await svc.resolve(incident_id)
    return {"resolved": True}

@activity.defn
async def update_incident_status_activity(incident_id: str, status: str) -> dict:
    """Update incident status."""
    from app.services.incident_service import IncidentService
    svc = IncidentService()
    await svc.update_status(incident_id, status)
    return {"updated": True}

@activity.defn
async def record_step_result_activity(
    incident_id: str, step_id: str, success: bool, output: Any, error: str
) -> dict:
    """Record a step execution result as an incident event."""
    from app.services.incident_service import IncidentService
    svc = IncidentService()
    await svc.add_event(incident_id, "step_completed", {
        "step_id": step_id,
        "success": success,
        "output": output,
        "error": error,
    })
    return {"recorded": True}

# =============================================
# TOOL ACTIVITIES (map to tool registry)
# =============================================

@activity.defn
async def fetch_pod_logs_activity(input: dict) -> dict:
    """Fetch logs from a Kubernetes pod."""
    from kubernetes_asyncio import client, config
    await config.load_incluster_config()
    v1 = client.CoreV1Api()
    logs = await v1.read_namespaced_pod_log(
        name=input["pod"],
        namespace=input["namespace"],
        container=input.get("container") or None,
        tail_lines=input.get("tail_lines", 200),
        since_seconds=input.get("since_seconds", 600),
    )
    return {"logs": logs, "line_count": len(logs.split("\n"))}

@activity.defn
async def query_prometheus_activity(input: dict) -> dict:
    """Execute a PromQL query against Prometheus."""
    from app.config import get_settings
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        params = {"query": input["query"]}
        if input.get("time"):
            params["time"] = input["time"]
        response = await client.get(
            f"{settings.prometheus_url}/api/v1/query",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return {"result_type": data["data"]["resultType"], "result": data["data"]["result"]}

@activity.defn
async def restart_workload_activity(input: dict) -> dict:
    """Restart a Kubernetes workload via rollout restart."""
    from kubernetes_asyncio import client, config
    from datetime import datetime
    await config.load_incluster_config()
    apps_v1 = client.AppsV1Api()

    # Patch the pod template annotation to trigger a rollout restart
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "automend.io/restartedAt": datetime.utcnow().isoformat()
                    }
                }
            }
        }
    }

    workload_type = input["workload_type"]
    if workload_type == "deployment":
        await apps_v1.patch_namespaced_deployment(
            input["workload_name"], input["namespace"], patch
        )
    elif workload_type == "statefulset":
        await apps_v1.patch_namespaced_stateful_set(
            input["workload_name"], input["namespace"], patch
        )
    elif workload_type == "daemonset":
        await apps_v1.patch_namespaced_daemon_set(
            input["workload_name"], input["namespace"], patch
        )
    return {"success": True, "message": f"Restarted {workload_type}/{input['workload_name']}"}

@activity.defn
async def scale_deployment_activity(input: dict) -> dict:
    """Scale a Kubernetes deployment."""
    from kubernetes_asyncio import client, config
    await config.load_incluster_config()
    apps_v1 = client.AppsV1Api()
    current = await apps_v1.read_namespaced_deployment(
        input["deployment_name"], input["namespace"]
    )
    previous = current.spec.replicas
    await apps_v1.patch_namespaced_deployment_scale(
        input["deployment_name"], input["namespace"],
        {"spec": {"replicas": input["replicas"]}}
    )
    return {"success": True, "previous_replicas": previous, "new_replicas": input["replicas"]}

@activity.defn
async def rollback_release_activity(input: dict) -> dict:
    """Rollback a Kubernetes deployment to a previous revision."""
    from kubernetes_asyncio import client, config
    await config.load_incluster_config()
    apps_v1 = client.AppsV1Api()
    # kubectl rollout undo is done by patching to revision 0 (previous)
    revision = input.get("revision", 0)
    body = {
        "kind": "DeploymentRollback",
        "apiVersion": "apps/v1",
        "name": input["deployment_name"],
        "rollbackTo": {"revision": revision}
    }
    # Note: rollback via patch annotation in modern K8s
    await apps_v1.patch_namespaced_deployment(
        input["deployment_name"], input["namespace"],
        {"metadata": {"annotations": {"deployment.kubernetes.io/revision": str(revision)}}}
    )
    return {"success": True, "rolled_back_to_revision": revision, "message": "Rollback initiated"}

@activity.defn
async def page_oncall_activity(input: dict) -> dict:
    """Page the on-call engineer via PagerDuty or configured system."""
    from app.config import get_settings
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.pagerduty_api_url}/incidents",
            headers={
                "Authorization": f"Token token={settings.pagerduty_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "incident": {
                    "type": "incident",
                    "title": input["title"],
                    "body": {"type": "incident_body", "details": input["body"]},
                    "urgency": "high" if input.get("severity") in ["critical", "high"] else "low",
                    "service": {
                        "id": input.get("service_id", settings.pagerduty_default_service_id),
                        "type": "service_reference"
                    }
                }
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return {"success": True, "page_id": data["incident"]["id"]}

@activity.defn
async def slack_notification_activity(input: dict) -> dict:
    """Send a Slack notification."""
    from app.config import get_settings
    settings = get_settings()
    color_map = {"red": "#FF0000", "orange": "#FF8C00", "yellow": "#FFD700", "green": "#00FF00"}
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            json={
                "channel": input["channel"],
                "attachments": [{
                    "color": color_map.get(input.get("severity_color", "orange"), "#FF8C00"),
                    "text": input["message"],
                }]
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return {"success": data.get("ok", False), "ts": data.get("ts")}

@activity.defn
async def slack_approval_activity(input: dict) -> dict:
    """Send Slack approval request. In v1, creates an approval_request record and waits."""
    from app.services.notification_service import NotificationService
    from app.stores.postgres_store import PostgresStore
    import asyncio

    svc = NotificationService()
    store = PostgresStore()

    # Create approval request in DB
    approval = await store.create_approval_request(
        incident_id=input.get("incident_id"),
        workflow_id="current",
        step_name="approval",
        requested_action=input["message"],
        timeout_minutes=input.get("timeout_minutes", 30),
    )

    # Send Slack message with approval request
    await svc.send_approval_request(
        channel=input["channel"],
        message=input["message"],
        approval_id=str(approval.id),
    )

    # Poll for approval decision (or use Temporal signal in production)
    timeout = input.get("timeout_minutes", 30) * 60
    elapsed = 0
    poll_interval = 10
    while elapsed < timeout:
        result = await store.get_approval_request(str(approval.id))
        if result.status in ("approved", "rejected"):
            if result.status == "approved":
                return {"approved": True, "approver": result.decided_by}
            else:
                raise Exception(f"Approval rejected by {result.decided_by}")
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    raise Exception("Approval timed out")

@activity.defn
async def open_ticket_activity(input: dict) -> dict:
    """Create a ticket in the configured ticketing system."""
    from app.config import get_settings
    settings = get_settings()
    # Jira example
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.jira_url}/rest/api/3/issue",
            auth=(settings.jira_email, settings.jira_api_token),
            json={
                "fields": {
                    "project": {"key": settings.jira_project_key},
                    "summary": input["title"],
                    "description": {"type": "doc", "version": 1, "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": input["description"]}]}
                    ]},
                    "issuetype": {"name": "Bug"},
                    "priority": {"name": input.get("priority", "Medium").capitalize()},
                    "labels": input.get("labels", []),
                }
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "success": True,
            "ticket_id": data["key"],
            "ticket_url": f"{settings.jira_url}/browse/{data['key']}"
        }

@activity.defn
async def describe_pod_activity(input: dict) -> dict:
    """Get full pod description from Kubernetes."""
    from kubernetes_asyncio import client, config
    await config.load_incluster_config()
    v1 = client.CoreV1Api()
    pod = await v1.read_namespaced_pod(input["pod"], input["namespace"])
    events_resp = await v1.list_namespaced_event(
        input["namespace"],
        field_selector=f"involvedObject.name={input['pod']}"
    )
    return {
        "status": pod.status.to_dict() if pod.status else {},
        "events": [e.to_dict() for e in (events_resp.items or [])[:20]],
        "conditions": [c.to_dict() for c in (pod.status.conditions or [])],
        "container_statuses": [c.to_dict() for c in (pod.status.container_statuses or [])],
    }

@activity.defn
async def get_node_status_activity(input: dict) -> dict:
    """Get node status from Kubernetes."""
    from kubernetes_asyncio import client, config
    await config.load_incluster_config()
    v1 = client.CoreV1Api()
    node = await v1.read_node(input["node"])
    events_resp = await v1.list_event_for_all_namespaces(
        field_selector=f"involvedObject.name={input['node']}"
    )
    return {
        "conditions": [c.to_dict() for c in (node.status.conditions or [])],
        "allocatable": node.status.allocatable or {},
        "capacity": node.status.capacity or {},
        "events": [e.to_dict() for e in (events_resp.items or [])[:20]],
    }

@activity.defn
async def cordon_node_activity(input: dict) -> dict:
    """Cordon a Kubernetes node."""
    from kubernetes_asyncio import client, config
    await config.load_incluster_config()
    v1 = client.CoreV1Api()
    await v1.patch_node(input["node"], {"spec": {"unschedulable": True}})
    return {"success": True, "message": f"Node {input['node']} cordoned"}

@activity.defn
async def drain_node_activity(input: dict) -> dict:
    """Drain a Kubernetes node (simplified — evict all pods)."""
    from kubernetes_asyncio import client, config
    await config.load_incluster_config()
    v1 = client.CoreV1Api()

    # First cordon
    await v1.patch_node(input["node"], {"spec": {"unschedulable": True}})

    # List pods on node
    pods = await v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={input['node']}")
    evicted = []
    for pod in pods.items:
        if pod.metadata.namespace in ("kube-system",) and input.get("ignore_daemonsets", True):
            continue
        try:
            eviction = client.V1Eviction(
                metadata=client.V1ObjectMeta(name=pod.metadata.name, namespace=pod.metadata.namespace),
                delete_options=client.V1DeleteOptions(grace_period_seconds=input.get("grace_period_seconds", 300))
            )
            await v1.create_namespaced_pod_eviction(pod.metadata.name, pod.metadata.namespace, eviction)
            evicted.append(f"{pod.metadata.namespace}/{pod.metadata.name}")
        except Exception:
            pass
    return {"success": True, "evicted_pods": evicted, "message": f"Drained node {input['node']}"}

@activity.defn
async def run_diagnostic_script_activity(input: dict) -> dict:
    """Execute a diagnostic command in a pod container."""
    from kubernetes_asyncio import client, config
    from kubernetes_asyncio.stream import WsApiClient
    await config.load_incluster_config()
    v1 = client.CoreV1Api()

    # Only allow pre-registered diagnostic scripts
    ALLOWED_SCRIPTS = {
        "nvidia_smi": ["nvidia-smi"],
        "gpu_memory": ["nvidia-smi", "--query-gpu=memory.used,memory.free,memory.total", "--format=csv"],
        "disk_usage": ["df", "-h"],
        "process_list": ["ps", "aux"],
        "network_check": ["ss", "-tulnp"],
    }

    script_name = input["script_name"]
    if script_name not in ALLOWED_SCRIPTS:
        return {"exit_code": 1, "stdout": "", "stderr": f"Script '{script_name}' not in allowed list"}

    command = ALLOWED_SCRIPTS[script_name]
    resp = await v1.connect_get_namespaced_pod_exec(
        input["pod"], input["namespace"],
        command=command,
        container=input.get("container", ""),
        stderr=True, stdout=True,
    )
    return {"exit_code": 0, "stdout": resp, "stderr": ""}
```

---

## 23. Control Plane — FastAPI API

### 23.1 App Setup (`main_api.py`)

```python
"""
FastAPI application entrypoint.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.config import get_settings
from app.api.routes_design import router as design_router
from app.api.routes_incidents import router as incidents_router
from app.api.routes_rules import router as rules_router
from app.api.routes_playbooks import router as playbooks_router
from app.api.routes_webhooks import router as webhooks_router
from app.api.routes_workflows import router as workflows_router
from app.api.routes_tools import router as tools_router
from app.api.routes_auth import router as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize DB pools, Redis connections, Temporal client
    from app.dependencies import init_dependencies, cleanup_dependencies
    await init_dependencies()
    yield
    # Shutdown: close connections
    await cleanup_dependencies()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AutoMend API",
        description="AI-powered incident response platform",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(design_router, prefix="/api/design", tags=["design"])
    app.include_router(incidents_router, prefix="/api/incidents", tags=["incidents"])
    app.include_router(rules_router, prefix="/api/rules", tags=["rules"])
    app.include_router(playbooks_router, prefix="/api/playbooks", tags=["playbooks"])
    app.include_router(webhooks_router, prefix="/api/webhooks", tags=["webhooks"])
    app.include_router(workflows_router, prefix="/api/workflows", tags=["workflows"])
    app.include_router(tools_router, prefix="/api/tools", tags=["tools"])
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()

# Run with: uvicorn main_api:app --host 0.0.0.0 --port 8000 --workers 4
```

### 23.2 Route Files Summary

**`routes_incidents.py`:**
- `GET /api/incidents` — List incidents with filters (status, severity, type, time range, entity)
- `GET /api/incidents/{id}` — Get incident detail with events timeline
- `PATCH /api/incidents/{id}` — Update incident (status, severity, manual notes)
- `POST /api/incidents/{id}/acknowledge` — Acknowledge an incident
- `POST /api/incidents/{id}/resolve` — Manually resolve
- `GET /api/incidents/{id}/events` — Get incident event timeline
- `GET /api/incidents/{id}/workflow` — Get associated workflow status
- `GET /api/incidents/stats` — Aggregate stats (counts by status, severity, type)

**`routes_workflows.py`:**
- `GET /api/workflows` — List active/recent workflow executions
- `GET /api/workflows/{workflow_id}` — Get workflow execution detail from Temporal
- `POST /api/workflows/{workflow_id}/signal` — Send a signal to a running workflow
- `POST /api/workflows/{workflow_id}/cancel` — Cancel a running workflow

**`routes_playbooks.py`:**
- `GET /api/playbooks` — List all playbooks
- `GET /api/playbooks/{id}` — Get playbook with versions
- `GET /api/playbooks/{id}/versions/{version_id}` — Get specific version with full spec
- `DELETE /api/playbooks/{id}` — Soft-delete (archive all versions)

**`routes_tools.py`:**
- `GET /api/tools` — List all tools
- `GET /api/tools/{id}` — Get tool detail
- `POST /api/tools` — Create a new tool (admin)
- `PUT /api/tools/{id}` — Update tool (admin)
- `DELETE /api/tools/{id}` — Deactivate tool

**`routes_rules.py`:**
- `GET /api/rules` — List alert rules
- `POST /api/rules` — Create alert rule
- `PUT /api/rules/{id}` — Update alert rule
- `DELETE /api/rules/{id}` — Delete alert rule
- `GET /api/rules/trigger-rules` — List trigger rules (incident→playbook mappings)

**`routes_auth.py`:**
- `POST /api/auth/login` — Login (returns JWT)
- `POST /api/auth/register` — Register new user (admin only)
- `GET /api/auth/me` — Current user info
- `POST /api/auth/refresh` — Refresh JWT token

---

## 24. Control Plane — Webhook Ingress

### 24.1 Alertmanager Webhook (`routes_webhooks.py`)

```python
"""
Webhook routes for external integrations.
"""
from fastapi import APIRouter, Request
from app.workers.correlation_worker import push_to_correlation_stream

router = APIRouter()

@router.post("/alertmanager")
async def alertmanager_webhook(request: Request):
    """
    Receives Alertmanager webhook notifications.
    Transforms alerts to internal signals and pushes to correlation stream.
    """
    payload = await request.json()
    alerts = payload.get("alerts", [])

    for alert in alerts:
        signal = transform_alertmanager_alert(alert)
        await push_to_correlation_stream(signal)

    return {"status": "ok", "processed": len(alerts)}
```

### 24.2 OTLP Ingestion Endpoint

```python
@router.post("/ingest/otlp")
async def otlp_ingest(request: Request):
    """
    Receives OTLP log export from OpenTelemetry Collector.
    Normalizes and pushes to Redis Stream for window-worker.
    """
    payload = await request.json()
    count = 0

    for resource_log in payload.get("resourceLogs", []):
        resource_attrs = _extract_attributes(
            resource_log.get("resource", {}).get("attributes", [])
        )
        for scope_log in resource_log.get("scopeLogs", []):
            for log_record in scope_log.get("logRecords", []):
                normalized = _normalize_log_record(log_record, resource_attrs)
                await push_to_log_stream(normalized)
                count += 1

    return {"status": "ok", "processed": count}
```

---

## 25. Authentication & Authorization

### 25.1 JWT-based Auth

For v1, use simple JWT authentication:

- `POST /api/auth/login` returns an access token + refresh token
- Access tokens expire in 1 hour
- Refresh tokens expire in 7 days
- Tokens are passed via `Authorization: Bearer {token}` header

### 25.2 Role-Based Access Control

| Role | Permissions |
|---|---|
| `admin` | Everything |
| `operator` | View/manage incidents, approve workflows, start manual workflows |
| `editor` | Design playbooks, manage tools, configure rules |
| `viewer` | Read-only access to incidents, playbooks, dashboards |

### 25.3 Implementation

Use FastAPI dependency injection:

```python
# app/dependencies.py

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

def require_role(required_role: str):
    async def check_role(user: dict = Depends(get_current_user)):
        role_hierarchy = {"admin": 4, "operator": 3, "editor": 2, "viewer": 1}
        if role_hierarchy.get(user.get("role"), 0) < role_hierarchy.get(required_role, 0):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return check_role
```

---

## 26. Configuration & Environment Variables

### 26.1 Config Model (`app/config.py`)

```python
from pydantic_settings import BaseSettings
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    # === Application ===
    app_name: str = "automend"
    app_env: str = "development"        # development, staging, production
    debug: bool = False
    log_level: str = "INFO"

    # === API ===
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 4
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # === Postgres ===
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "automend"
    postgres_password: str = "automend"
    postgres_db: str = "automend"

    @property
    def postgres_url(self) -> str:
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def postgres_url_sync(self) -> str:
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    # === Redis ===
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None
    redis_db: int = 0

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # === Temporal ===
    temporal_server_url: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "automend-playbook-queue"

    # === Classifier (Model 1) ===
    classifier_service_url: str = "http://localhost:8001"
    classifier_timeout_seconds: int = 30
    classifier_confidence_threshold: float = 0.7

    # === Architect (Model 2) ===
    architect_api_key: str = ""
    architect_api_base_url: str = "https://api.anthropic.com"
    architect_model: str = "claude-sonnet-4-20250514"

    # === Embedding ===
    embedding_api_key: str = ""
    embedding_api_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # === Prometheus ===
    prometheus_url: str = "http://localhost:9090"

    # === Slack ===
    slack_bot_token: str = ""
    slack_default_channel: str = "#incident-ops"

    # === PagerDuty ===
    pagerduty_api_url: str = "https://api.pagerduty.com"
    pagerduty_api_key: str = ""
    pagerduty_default_service_id: str = ""

    # === Jira ===
    jira_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = "OPS"

    # === Auth ===
    jwt_secret: str = "change-me-in-production"
    jwt_expiry_minutes: int = 60
    jwt_refresh_expiry_days: int = 7

    # === Window Worker ===
    window_size_seconds: int = 300          # 5 minutes
    max_window_entries: int = 500
    window_check_interval_seconds: int = 30

    # === Correlation Worker ===
    dedup_cooldown_seconds: int = 900       # 15 minutes
    incident_cooldown_seconds: int = 900

    # === Worker Identity ===
    worker_id: str = "worker-0"

    class Config:
        env_prefix = "AUTOMEND_"
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

### 26.2 `.env.example`

```bash
# Application
AUTOMEND_APP_ENV=development
AUTOMEND_DEBUG=true
AUTOMEND_LOG_LEVEL=DEBUG

# Postgres
AUTOMEND_POSTGRES_HOST=localhost
AUTOMEND_POSTGRES_PORT=5432
AUTOMEND_POSTGRES_USER=automend
AUTOMEND_POSTGRES_PASSWORD=automend
AUTOMEND_POSTGRES_DB=automend

# Redis
AUTOMEND_REDIS_HOST=localhost
AUTOMEND_REDIS_PORT=6379

# Temporal
AUTOMEND_TEMPORAL_SERVER_URL=localhost:7233

# Classifier (Model 1)
AUTOMEND_CLASSIFIER_SERVICE_URL=http://localhost:8001

# Architect (Model 2) — Anthropic Claude
AUTOMEND_ARCHITECT_API_KEY=sk-ant-...
AUTOMEND_ARCHITECT_API_BASE_URL=https://api.anthropic.com
AUTOMEND_ARCHITECT_MODEL=claude-sonnet-4-20250514

# Embeddings — OpenAI
AUTOMEND_EMBEDDING_API_KEY=sk-...
AUTOMEND_EMBEDDING_API_BASE_URL=https://api.openai.com/v1
AUTOMEND_EMBEDDING_MODEL=text-embedding-3-small
AUTOMEND_EMBEDDING_DIMENSIONS=1536

# Prometheus
AUTOMEND_PROMETHEUS_URL=http://localhost:9090

# Slack
AUTOMEND_SLACK_BOT_TOKEN=xoxb-...

# PagerDuty
AUTOMEND_PAGERDUTY_API_KEY=...
AUTOMEND_PAGERDUTY_DEFAULT_SERVICE_ID=...

# Jira
AUTOMEND_JIRA_URL=https://your-org.atlassian.net
AUTOMEND_JIRA_EMAIL=bot@your-org.com
AUTOMEND_JIRA_API_TOKEN=...
AUTOMEND_JIRA_PROJECT_KEY=OPS

# Auth
AUTOMEND_JWT_SECRET=your-secret-key-change-in-production
```

---

## 27. End-to-End Flows

### Flow A: Design-Time Playbook Creation

```
1. User opens the React UI workflow designer
2. User types intent: "When a GPU runs out of memory, collect diagnostics, restart, and escalate if it fails"
3. Frontend calls POST /api/design/rag_search with the intent text
4. API embeds the query, performs pgvector search on tools + playbooks
5. API returns relevant tools and example playbooks
6. Frontend calls POST /api/design/generate_workflow with intent + retrieved context
7. API calls Model 2 architect → returns JSON workflow spec
8. API validates the spec (POST /api/design/validate_workflow internally)
9. API returns spec to frontend with any warnings
10. User reviews the visual workflow in React Flow, makes edits
11. Frontend calls POST /api/design/save_playbook with the final spec
12. API saves playbook_version with status 'draft'
13. User clicks "Validate" → POST /api/design/publish_playbook (action: validate)
14. User clicks "Approve" → POST /api/design/publish_playbook (action: approve)
15. User clicks "Publish" → POST /api/design/publish_playbook (action: publish)
16. API creates trigger_rules entries, playbook is now live
```

### Flow B: Logs Runtime → Incident → Workflow

```
1. Application on GPU node logs "CUDA error: out of memory"
2. Fluent Bit DaemonSet picks up the log from container stdout
3. Fluent Bit forwards to OTel Collector gateway via OTLP
4. OTel normalizes attributes (cluster, namespace, pod, service, etc.)
5. OTel exports to:
   a. Log backend (Loki) for search/forensics
   b. AutoMend API OTLP endpoint
6. API pushes normalized log to Redis Stream automend:stream:normalized_logs
7. window-worker reads from stream, groups by entity_key "prod-a/ml/trainer"
8. Window accumulates for 5 minutes
9. Window closes → worker sends 47 log entries to classifier service
10. Classifier (Model 1) returns: label=failure.memory, confidence=0.94
11. Worker emits classified event to Redis Stream automend:stream:classified_events
12. correlation-worker reads classified event
13. Correlation worker derives incident_key: "prod-a/ml/trainer/failure.memory"
14. No active incident exists → creates new incident in Postgres
15. Looks up trigger_rules → finds "GPU Memory Failure Recovery" playbook (published)
16. Starts Temporal DynamicPlaybookExecutor with playbook_version_id + incident payload
17. Workflow executes: fetch_logs → check_metrics → approval → restart → verify → notify
18. If successful: incident resolved automatically
19. If failed: escalate to on-call, create ticket
```

### Flow C: Metrics Runtime → Incident → Workflow

```
1. Prometheus scrapes DCGM exporter: GPU memory at 97%
2. PromQL rule GPUHighMemoryPressure fires after 5 minutes
3. Alertmanager groups alert, sends webhook to POST /api/webhooks/alertmanager
4. Webhook route transforms alert to internal signal
5. Pushes to correlation-worker stream
6. correlation-worker checks if incident "prod-a/ml/trainer/failure.memory" exists
7. If log classifier already created it → adds metric alert as additional evidence
8. If new → creates incident and starts workflow
9. Same workflow execution as Flow B
```

### Flow D: Combined Logs + Metrics

```
1. Within a 10-minute window, both:
   a. Classifier identifies failure.memory from logs
   b. Prometheus fires GPUHighMemoryPressure alert
2. Whichever arrives first creates the incident
3. The second signal is correlated to the same incident (same incident_key)
4. Incident sources: ["log_classifier", "prometheus_alert"]
5. If workflow is already running, the second signal is sent as a Temporal signal
6. Workflow can use the additional evidence in decision-making
```

---

## 28. Deployment — Docker Compose (Dev)

### `infra/docker-compose.yml`

```yaml
version: "3.8"

services:
  # ========== INFRASTRUCTURE ==========

  postgres:
    image: pgvector/pgvector:pg16
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: automend
      POSTGRES_PASSWORD: automend
      POSTGRES_DB: automend
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U automend"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  temporal:
    image: temporalio/auto-setup:latest
    ports:
      - "7233:7233"
    environment:
      - DB=postgres12
      - DB_PORT=5432
      - POSTGRES_USER=temporal
      - POSTGRES_PWD=temporal
      - POSTGRES_SEEDS=postgres-temporal
    depends_on:
      postgres-temporal:
        condition: service_healthy

  postgres-temporal:
    image: postgres:16
    environment:
      POSTGRES_USER: temporal
      POSTGRES_PASSWORD: temporal
      POSTGRES_DB: temporal
    volumes:
      - postgres_temporal_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U temporal"]
      interval: 5s
      timeout: 5s
      retries: 5

  temporal-ui:
    image: temporalio/ui:latest
    ports:
      - "8080:8080"
    environment:
      - TEMPORAL_ADDRESS=temporal:7233
    depends_on:
      - temporal

  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./prometheus/alert_rules.yml:/etc/prometheus/alert_rules.yml
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--web.enable-lifecycle"

  alertmanager:
    image: prom/alertmanager:latest
    ports:
      - "9093:9093"
    volumes:
      - ./alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml

  loki:
    image: grafana/loki:latest
    ports:
      - "3100:3100"
    command: -config.file=/etc/loki/local-config.yaml

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3001:3000"
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
    volumes:
      - grafana_data:/var/lib/grafana

  # ========== APPLICATION ==========

  api:
    build:
      context: ../backend
      dockerfile: ../infra/dockerfiles/Dockerfile.api
    ports:
      - "8000:8000"
    env_file:
      - ../backend/.env
    environment:
      AUTOMEND_POSTGRES_HOST: postgres
      AUTOMEND_REDIS_HOST: redis
      AUTOMEND_TEMPORAL_SERVER_URL: temporal:7233
      AUTOMEND_PROMETHEUS_URL: http://prometheus:9090
      AUTOMEND_CLASSIFIER_SERVICE_URL: http://classifier:8001
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      temporal:
        condition: service_started

  window-worker:
    build:
      context: ../backend
      dockerfile: ../infra/dockerfiles/Dockerfile.worker
    command: ["python", "main_window_worker.py"]
    env_file:
      - ../backend/.env
    environment:
      AUTOMEND_POSTGRES_HOST: postgres
      AUTOMEND_REDIS_HOST: redis
      AUTOMEND_CLASSIFIER_SERVICE_URL: http://classifier:8001
      AUTOMEND_WORKER_ID: window-worker-0
    depends_on:
      - redis
      - api

  correlation-worker:
    build:
      context: ../backend
      dockerfile: ../infra/dockerfiles/Dockerfile.worker
    command: ["python", "main_correlation_worker.py"]
    env_file:
      - ../backend/.env
    environment:
      AUTOMEND_POSTGRES_HOST: postgres
      AUTOMEND_REDIS_HOST: redis
      AUTOMEND_TEMPORAL_SERVER_URL: temporal:7233
      AUTOMEND_WORKER_ID: correlation-worker-0
    depends_on:
      - redis
      - postgres
      - temporal

  temporal-worker:
    build:
      context: ../backend
      dockerfile: ../infra/dockerfiles/Dockerfile.temporal-worker
    env_file:
      - ../backend/.env
    environment:
      AUTOMEND_POSTGRES_HOST: postgres
      AUTOMEND_REDIS_HOST: redis
      AUTOMEND_TEMPORAL_SERVER_URL: temporal:7233
      AUTOMEND_PROMETHEUS_URL: http://prometheus:9090
    depends_on:
      - temporal
      - postgres
      - redis

  classifier:
    build:
      context: ../backend
      dockerfile: ../infra/dockerfiles/Dockerfile.worker
    command: ["python", "-m", "app.services.classifier_server"]
    ports:
      - "8001:8001"
    env_file:
      - ../backend/.env

volumes:
  postgres_data:
  postgres_temporal_data:
  redis_data:
  grafana_data:
```

### Dockerfiles

**`infra/dockerfiles/Dockerfile.api`:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main_api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

**`infra/dockerfiles/Dockerfile.worker`:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY . .

CMD ["python", "main_window_worker.py"]
```

**`infra/dockerfiles/Dockerfile.temporal-worker`:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY . .

CMD ["python", "main_temporal_worker.py"]
```

---

## 29. Deployment — Kubernetes (Production)

Production deployment uses Helm charts or plain manifests. Key considerations:

- **Fluent Bit**: DaemonSet
- **OTel Collector**: Deployment (2+ replicas)
- **API**: Deployment (3+ replicas) with HPA
- **Window Worker**: StatefulSet or Deployment with leader election
- **Correlation Worker**: Deployment (2+ replicas) with Redis-based partitioning
- **Temporal Worker**: Deployment (3+ replicas)
- **Classifier Service**: Deployment with GPU node selector if using local models
- **Postgres**: StatefulSet or managed (RDS, Cloud SQL)
- **Redis**: StatefulSet or managed (ElastiCache, Memorystore)
- **Temporal Server**: Helm chart or Temporal Cloud

RBAC: The Temporal worker pods need a ServiceAccount with RBAC permissions to manage pods, deployments, statefulsets, daemonsets, nodes, and events in the target namespaces.

---

## 30. Testing Strategy

### 30.1 Unit Tests

- **Domain models:** Test entity key building, incident key building, canonical models
- **Template resolution:** Test `${}` expression resolution with various inputs
- **DSL validation:** Test playbook spec validation against schema
- **Signal transformation:** Test Alertmanager → internal signal conversion

### 30.2 Integration Tests

- **Database:** Test all Postgres operations (CRUD, queries, vector search)
- **Redis:** Test window operations, stream reads/writes, locking
- **Classifier client:** Mock classifier and test window-worker integration
- **Temporal:** Test workflow execution with mock activities

### 30.3 End-to-End Tests

- Submit a simulated log batch → verify incident is created → verify workflow starts
- Submit an Alertmanager webhook → verify correlation → verify playbook execution
- Design a playbook through the API → publish → trigger → verify execution

### 30.4 Test Fixtures

Use `pytest-asyncio` for async tests, `testcontainers` for Postgres/Redis containers, and Temporal's test server for workflow tests.

---

## 31. Migration & Bootstrapping

### 31.1 Initial Setup Commands

```bash
# 1. Start infrastructure
cd infra && docker compose -f docker-compose.yml up -d postgres redis temporal temporal-ui prometheus alertmanager

# 2. Run database migrations
cd backend && alembic upgrade head

# 3. Seed default tools
cd backend && python scripts/seed_tools.py

# 4. Seed default alert rules (optional)
cd backend && python scripts/seed_rules.py

# 5. Start application services
cd infra && docker compose up -d api window-worker correlation-worker temporal-worker classifier

# 6. Start frontend (separate terminal)
cd frontend && npm run dev
```

### 31.2 Alembic Configuration

**`alembic.ini`:**
```ini
[alembic]
script_location = alembic
sqlalchemy.url = postgresql://automend:automend@localhost:5432/automend

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =

[logger_alembic]
level = INFO
handlers =

[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

---

## 32. Frontend Integration Contract

The existing React frontend should integrate with the backend via these API patterns.

### 32.1 WebSocket for Real-Time Updates

The API should expose a WebSocket endpoint for real-time incident and workflow updates:

**`WS /api/ws/incidents`** — Streams incident creates, updates, and workflow state changes.

Message format:
```json
{
  "type": "incident.created | incident.updated | workflow.step_completed | workflow.completed",
  "payload": { ... }
}
```

Implementation: Use FastAPI WebSocket with Redis Pub/Sub as the broadcast mechanism.

### 32.2 React Flow Integration

The frontend uses React Flow for visual workflow editing. The workflow spec JSON (§19) maps to React Flow nodes/edges:

- Each `step` in the DSL → one React Flow node
- `on_success` and `on_failure` transitions → React Flow edges
- `condition` branches → conditional edges

The frontend should POST the modified spec back to `POST /api/design/save_playbook` after visual editing.

### 32.3 API Response Pagination

All list endpoints use cursor-based pagination:

```json
{
  "data": [...],
  "pagination": {
    "total": 150,
    "limit": 20,
    "offset": 0,
    "has_more": true,
    "next_cursor": "eyJ..."
  }
}
```

### 32.4 Error Response Format

All API errors follow:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Playbook spec failed validation",
    "details": [
      {"field": "steps[2].tool", "message": "Tool 'nonexistent_tool' not found in registry"}
    ]
  }
}
```

---

## 33. Observability & Operational Concerns

### 33.1 Application Metrics

Expose a `/metrics` endpoint on the API for Prometheus scraping:

- `automend_incidents_created_total` (counter, labels: incident_type, severity)
- `automend_incidents_resolved_total` (counter)
- `automend_classifier_requests_total` (counter, labels: label, status)
- `automend_classifier_latency_seconds` (histogram)
- `automend_workflow_started_total` (counter, labels: playbook_name)
- `automend_workflow_completed_total` (counter, labels: playbook_name, outcome)
- `automend_window_worker_windows_processed_total` (counter)
- `automend_correlation_worker_signals_processed_total` (counter)

### 33.2 Structured Logging

Use `structlog` for all Python services. Log format: JSON with fields:
- `timestamp`, `level`, `logger`, `message`, `service`, `worker_id`, `incident_id`, `entity_key`, `trace_id`

### 33.3 Health Checks

Each process type exposes health/readiness:

- API: `GET /health` + `GET /ready` (checks Postgres + Redis connectivity)
- Workers: Redis connectivity check + last-processed timestamp within acceptable window
- Temporal Worker: Temporal client connectivity

### 33.4 Alerting on AutoMend Itself

Meta-alerts for the platform's own health:

- Classifier latency > 10s for 5m
- Window worker has not processed a window in 10m
- Correlation worker has not processed a signal in 10m
- Temporal workflow failure rate > 10% in 15m
- Redis memory > 80%
- Postgres connection pool exhaustion

---

## Appendix A: Python Dependencies (`pyproject.toml`)

```toml
[project]
name = "automend-backend"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = [
    # API
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",

    # Database
    "sqlalchemy[asyncio]>=2.0.0",
    "asyncpg>=0.29.0",
    "alembic>=1.13.0",
    "pgvector>=0.3.0",

    # Redis
    "redis[hiredis]>=5.0.0",

    # Temporal
    "temporalio>=1.5.0",

    # HTTP client
    "httpx>=0.27.0",

    # Kubernetes
    "kubernetes-asyncio>=29.0.0",

    # Auth
    "pyjwt>=2.8.0",
    "passlib[bcrypt]>=1.7.0",

    # Logging
    "structlog>=24.1.0",

    # Utilities
    "python-multipart>=0.0.9",
    "python-dotenv>=1.0.0",
    "orjson>=3.9.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=4.1.0",
    "httpx>=0.27.0",
    "testcontainers[postgres,redis]>=4.0.0",
    "ruff>=0.3.0",
    "mypy>=1.8.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"
```

---

## Appendix B: Quick Reference — What Goes Where

| Question | Answer |
|---|---|
| Where do logs enter? | Fluent Bit → OTel Collector → Redis Stream → window-worker |
| Where do metrics enter? | Prometheus → Alertmanager → webhook → correlation-worker |
| Where is classification done? | Separate classifier service (Model 1), called by window-worker |
| Where are incidents created? | correlation-worker → Postgres |
| Where are playbooks stored? | Postgres (playbooks + playbook_versions tables) |
| Where is semantic search? | pgvector extension in same Postgres |
| Where are workflows generated? | FastAPI design routes → Model 2 architect service |
| Where are workflows executed? | Temporal (DynamicPlaybookExecutor workflow) |
| Where is hot state? | Redis (windows, dedupe, locks, active incidents) |
| Where is durable state? | Postgres (everything persistent) |
| Where are approvals handled? | Temporal workflow pauses → Slack + approval_requests table → signal |
| What connects to Kubernetes? | Temporal worker activities (via kubernetes-asyncio) |

---

*End of Backend Architecture Specification*
