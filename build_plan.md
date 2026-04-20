# AutoMend — Build Plan for AI Coding Agents

> **What this file is:** The exact sequence of prompts to give Claude Code (or Cursor) to build the AutoMend backend, broken into small testable tasks. Each task includes acceptance criteria, tests, and instructions to update tracking files.

---

## How This Works

### Files the AI agent should maintain

| File | Purpose | Updated when |
|---|---|---|
| `CLAUDE.md` | Project context, conventions, what's built, how to run | Every task (append to "what's built") |
| `PROGRESS.md` | Task checklist with status, blockers, decisions made | Every task (check off completed, note blockers) |
| `backend_architecture.md` | The spec (source of truth) | Only if a design decision changes during build |
| `DECISIONS.md` | Architecture decisions made during implementation | When the agent deviates from spec or makes a choice |

This is a solid industry pattern. Many teams use an `ADR/` (Architecture Decision Records) directory, but for AI-assisted builds, a single `DECISIONS.md` plus `PROGRESS.md` is more practical because the agent can read/update them in one pass.

### The rule for every task

Every prompt you give Claude Code should end with:

```
After completing this task:
1. Run the tests you wrote and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

Copy-paste that as a suffix. Every time. Non-negotiable.

---

## Phase 0: Bootstrap & Analyze (Do This First)

### Task 0.1 — Read the existing frontend and create CLAUDE.md

```
Read the entire frontend/ directory. Examine every file — components, routes, pages,
API calls, state management, types/interfaces, package.json, and any mock data.

Create a detailed analysis covering:
- What framework/libraries are used (React, Next.js, Vite, etc.)
- What UI component library (if any)
- What state management approach
- Every page/route that exists
- Every API call the frontend makes (URL, method, request/response shape)
- Every TypeScript/JS interface or type related to backend data
- What is mocked vs what expects a real backend
- What's missing or incomplete in the frontend

Then create CLAUDE.md in the project root with:
- Project overview (read backend_architecture.md for context)
- Tech stack (frontend + planned backend)
- How to run the frontend
- The API contract the frontend already expects
- Coding conventions observed in the frontend (naming, file structure, etc.)
- "What's Built" section (currently: frontend only, list what exists)
- "What's Not Built" section (the entire backend)
- Common commands section

Also create PROGRESS.md with the full task list from the build plan.
Also create DECISIONS.md with a header and empty entries.

After completing this task:
1. Verify CLAUDE.md accurately describes the frontend
2. Update PROGRESS.md — mark this task done
3. Note in DECISIONS.md any frontend patterns that will influence backend design
   (e.g., "Frontend expects paginated responses with {data, pagination} shape")
```

### Task 0.2 — Create PROGRESS.md with full checklist

```
Read the build plan document (this file). Create PROGRESS.md with every task
listed as a checkbox. Group by phase. Include columns for:
- [ ] or [x] status
- Task ID
- Task name  
- Notes/blockers

Format:

## Phase 0: Bootstrap
- [x] 0.1 — Analyze frontend, create CLAUDE.md
- [ ] 0.2 — Create PROGRESS.md ← YOU ARE HERE
...

## Phase 1: Infrastructure
- [ ] 1.1 — Docker Compose for infrastructure
...

This file is the living checklist. You update it after every task.
```

---

## Phase 1: Infrastructure & Project Skeleton

### Task 1.1 — Docker Compose for infrastructure only

```
Read backend_architecture.md sections 28 (Docker Compose) and 3 (Technology Stack).

Create infra/docker-compose.infra.yml that starts ONLY the infrastructure services:
- Postgres 16 with pgvector extension (use pgvector/pgvector:pg16 image)
- Redis 7
- Temporal server (temporalio/auto-setup) with its own Postgres
- Temporal UI
- Prometheus (with a minimal config)
- Alertmanager (with a minimal config that points webhook to localhost:8000)
- Loki (minimal)

Do NOT include any application services yet.

Include health checks on Postgres and Redis.

Test: Run `docker compose -f infra/docker-compose.infra.yml up -d` and verify:
- `docker compose ps` shows all services healthy/running
- `psql -h localhost -U automend -d automend -c "SELECT 1"` works
- `redis-cli ping` returns PONG
- `curl http://localhost:9090/-/healthy` returns Prometheus healthy
- `curl http://localhost:7233` responds (Temporal)
- `curl http://localhost:8080` shows Temporal UI

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 1.2 — Python project skeleton

```
Read backend_architecture.md section 4 (Repository & Service Layout).

Create the backend/ directory with:
- pyproject.toml with ALL dependencies from Appendix A
- .env.example from section 26.2
- .env (copy of .env.example with dev defaults that work with docker-compose.infra.yml)
- app/__init__.py
- app/config.py — the full Settings class from section 26.1
- All the empty __init__.py files for the package structure
- main_api.py — minimal FastAPI app with just /health endpoint
- main_window_worker.py — placeholder that prints "window worker starting" and exits
- main_correlation_worker.py — placeholder that prints "correlation worker starting" and exits
- main_temporal_worker.py — placeholder that prints "temporal worker starting" and exits

Test:
- cd backend && pip install -e ".[dev]" succeeds
- python -c "from app.config import get_settings; s = get_settings(); print(s.postgres_url)" prints correct URL
- cd backend && uvicorn main_api:app --port 8000 & curl http://localhost:8000/health returns {"status": "ok"}

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section, add "How to run backend" commands
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 1.3 — SQLAlchemy models and Alembic migrations

```
Read backend_architecture.md section 5 (Database Schema) entirely.

Create:
- backend/app/models/__init__.py
- backend/app/models/db.py — ALL SQLAlchemy 2.0 ORM models for every table in section 5:
  - Tools, Playbooks, PlaybookVersions, TriggerRules, Incidents, IncidentEvents,
    ClassifierOutputs, ApprovalRequests, AlertRules, Users
  - Use mapped_column, Mapped[] type hints (SQLAlchemy 2.0 style)
  - Include the pgvector Vector column type for embedding fields
- backend/alembic.ini
- backend/alembic/env.py (async-compatible with asyncpg)
- Initial migration that creates ALL tables + pgvector extension + uuid-ossp extension

Test (with infra running):
- cd backend && alembic upgrade head — runs without errors
- psql -h localhost -U automend -d automend -c "\dt" — shows all 10 tables
- psql -h localhost -U automend -d automend -c "SELECT * FROM pg_extension WHERE extname = 'vector'" — returns 1 row
- python -c "from app.models.db import *; print('All models imported')" — no import errors

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 1.4 — Database connection and dependency injection

```
Read backend_architecture.md sections 23.1 (App Setup) and 25.3 (Auth Implementation).

Create:
- backend/app/dependencies.py with:
  - async get_db() — yields AsyncSession from async sessionmaker
  - async get_redis() — returns redis.asyncio.Redis client
  - Database engine creation using settings.postgres_url
  - Session factory
  - init_dependencies() and cleanup_dependencies() for app lifespan
- Update main_api.py to use the lifespan context manager that calls init/cleanup

Test (with infra running):
- Start the API: uvicorn main_api:app --port 8000
- Check logs show "Connected to Postgres" and "Connected to Redis" (or equivalent)
- curl http://localhost:8000/health still returns ok
- Write a quick test: backend/tests/test_db_connection.py
  - test_postgres_connection: get a session, execute "SELECT 1", assert result
  - test_redis_connection: ping redis, assert PONG
- Run: cd backend && pytest tests/test_db_connection.py -v — both pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

---

## Phase 2: Domain Models & Core Services

### Task 2.1 — Pydantic domain models

```
Read backend_architecture.md sections 6 (Canonical Incident Model), 9.4 (Classified Event Schema),
9.5 (Entity Key Construction), 11.3 (Internal Signal Schema), and 19.1 (DSL Schema).

Create:
- backend/app/domain/keys.py — entity key and incident key builders (exact code from section 9.5)
- backend/app/domain/events.py — Pydantic models for:
  - NormalizedLogEntry
  - ClassifiedEvent (section 9.4)
  - InternalSignal (section 11.3)
- backend/app/domain/incidents.py — full canonical incident models (section 12, exact code)
- backend/app/domain/playbooks.py — Pydantic models for:
  - PlaybookSpec (matching the DSL schema from section 19.1)
  - PlaybookStep
  - StepRetryConfig
  - TriggerConfig
  - All step type sub-models
- backend/app/domain/tools.py — Pydantic models for tool registry entries
- backend/app/domain/rules.py — Pydantic models for alert rules and trigger rules

Test:
- Write backend/tests/test_domain/test_keys.py:
  - test_build_entity_key_default_template
  - test_build_entity_key_custom_template
  - test_build_entity_key_missing_fields_fallback
  - test_build_incident_key
- Write backend/tests/test_domain/test_events.py:
  - test_normalized_log_entry_validation
  - test_classified_event_creation
  - test_internal_signal_creation
- Write backend/tests/test_domain/test_incidents.py:
  - test_canonical_incident_creation
  - test_incident_status_enum
  - test_severity_enum
- Write backend/tests/test_domain/test_playbooks.py:
  - test_playbook_spec_from_example_json (use the example from section 19.2)
  - test_playbook_spec_validation_rejects_invalid
- Run: cd backend && pytest tests/test_domain/ -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 2.2 — Postgres store (CRUD operations)

```
Read backend_architecture.md sections 5 (Schema), 18.1 (Playbook Service operations).

Create backend/app/stores/postgres_store.py with an async class PostgresStore that provides:
- Tool CRUD: create_tool, get_tool, list_tools, update_tool, deactivate_tool
- Playbook CRUD: create_playbook, get_playbook, list_playbooks
- PlaybookVersion CRUD: create_version, get_version, list_versions, transition_status
- TriggerRule CRUD: create_rule, find_rules_for_incident_type, list_rules
- Incident CRUD: create_incident, get_incident, get_incident_by_key, update_incident,
  list_incidents, add_incident_event
- ApprovalRequest CRUD: create_approval_request, get_approval_request, update_approval_decision
- AlertRule CRUD: create_alert_rule, list_alert_rules, update_alert_rule
- User CRUD: create_user, get_user_by_email
- ClassifierOutput: store_classifier_output

All methods take an AsyncSession parameter.
Use SQLAlchemy 2.0 select() style, not legacy Query.

Test (with infra running, migrations applied):
- Write backend/tests/test_stores/test_postgres_store.py
- Use a test database or transaction rollback pattern
- test_create_and_get_tool
- test_create_playbook_and_version
- test_transition_playbook_status (draft → validated → approved → published)
- test_create_and_query_incident
- test_add_incident_event
- test_find_trigger_rules_for_incident_type
- Run: cd backend && pytest tests/test_stores/ -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 2.3 — Redis store (windows, dedup, streams)

```
Read backend_architecture.md section 6 (Redis Key Design) entirely.

Create backend/app/stores/redis_store.py with an async class RedisStore that provides:

Window operations:
- add_to_window(entity_key, log_entry) — RPUSH + metadata update
- get_window(entity_key) — LRANGE all entries
- get_window_metadata(entity_key) — HGETALL
- close_window(entity_key) — DEL window + metadata keys
- list_open_windows() — SCAN for automend:window:meta:* keys
- check_window_should_close(entity_key, max_age_seconds, max_entries) -> bool

Dedup/cooldown:
- set_dedup(key_type, key, ttl_seconds) -> bool (returns False if already exists)
- check_dedup(key_type, key) -> bool
- set_cooldown(incident_key, ttl_seconds)
- check_cooldown(incident_key) -> bool

Active incidents:
- set_active_incident(incident_key, incident_id, status, workflow_id)
- get_active_incident(incident_key) -> dict or None
- remove_active_incident(incident_key)

Locks:
- acquire_lock(lock_type, key, worker_id, ttl_seconds) -> bool
- release_lock(lock_type, key, worker_id) -> bool

Stream operations:
- push_to_stream(stream_name, data) -> message_id
- read_from_stream(stream_name, group, consumer, count, block_ms) -> list
- ack_stream(stream_name, group, message_id)
- ensure_consumer_group(stream_name, group_name)

Test (with Redis running):
- Write backend/tests/test_stores/test_redis_store.py
- test_window_lifecycle (add entries, check metadata, close)
- test_dedup_set_and_check
- test_cooldown_set_and_check
- test_lock_acquire_and_release
- test_lock_contention (two workers, second fails)
- test_stream_push_and_read
- Run: cd backend && pytest tests/test_stores/test_redis_store.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 2.4 — Seed script for default tools

```
Read backend_architecture.md section 17.3 (Default Tools to Seed).

Create backend/scripts/seed_tools.py that:
- Connects to Postgres
- Inserts all 14 default tools from section 17.3 (upsert — don't fail if already exists)
- Skips embedding generation for now (leave embedding column NULL)
- Prints summary of tools seeded

Test (with infra running, migrations applied):
- python scripts/seed_tools.py — runs without errors, prints "Seeded 14 tools"
- python scripts/seed_tools.py — run again, still succeeds (idempotent), prints "14 tools already exist"
- psql -c "SELECT name, category, side_effect_level FROM tools ORDER BY name" — shows all 14

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section, add seed command to Common Commands
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

---

## Phase 3: Control Plane — API Routes

### Task 3.1 — Auth routes and middleware

```
Read backend_architecture.md sections 25 (Auth) and 23.2 (routes_auth.py).

Create:
- backend/app/api/routes_auth.py with:
  - POST /login (email + password → JWT access + refresh tokens)
  - POST /register (admin only, creates user)
  - GET /me (returns current user)
  - POST /refresh (refresh token → new access token)
- Update backend/app/dependencies.py with:
  - get_current_user dependency (JWT decode)
  - require_role(role) dependency factory
- Use passlib[bcrypt] for password hashing, PyJWT for tokens

Test:
- Write backend/tests/test_api/test_auth.py using FastAPI TestClient
- test_register_user (as admin)
- test_login_returns_tokens
- test_access_protected_route_with_token
- test_access_protected_route_without_token_returns_401
- test_refresh_token
- test_role_restriction (viewer can't access admin route)
- Run: cd backend && pytest tests/test_api/test_auth.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 3.2 — Tools API routes

```
Read backend_architecture.md section 23.2 (routes_tools.py summary).

Create backend/app/api/routes_tools.py:
- GET /api/tools — list all tools, supports ?category= filter, ?search= text filter
- GET /api/tools/{id} — get tool by ID
- POST /api/tools — create tool (admin/editor only)
- PUT /api/tools/{id} — update tool (admin/editor only)
- DELETE /api/tools/{id} — deactivate tool (admin only)

Create backend/app/services/tool_registry_service.py that the routes call into.
Use postgres_store.py methods.

All responses should use the pagination format from section 32.3.
All errors should use the error format from section 32.4.

Test:
- Write backend/tests/test_api/test_tools.py
- test_list_tools_returns_seeded_tools (seeds should exist from Task 2.4)
- test_get_tool_by_id
- test_create_tool
- test_update_tool
- test_deactivate_tool
- test_filter_tools_by_category
- test_unauthorized_create_returns_403
- Run: cd backend && pytest tests/test_api/test_tools.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 3.3 — Playbooks API routes

```
Read backend_architecture.md section 23.2 (routes_playbooks.py summary).

Create backend/app/api/routes_playbooks.py:
- GET /api/playbooks — list playbooks with filters
- GET /api/playbooks/{id} — get playbook with latest version
- GET /api/playbooks/{id}/versions — list all versions
- GET /api/playbooks/{id}/versions/{version_id} — get specific version with full spec
- DELETE /api/playbooks/{id} — archive all versions

Create backend/app/services/playbook_service.py with all methods from section 18.1.
Include the status transition validation logic from section 18.2.

Test:
- Write backend/tests/test_api/test_playbooks.py
- test_create_and_list_playbooks (via design routes later, but for now direct DB setup)
- test_get_playbook_with_versions
- test_archive_playbook
- test_status_transition_validation (can't go from draft to published directly)
- Run: cd backend && pytest tests/test_api/test_playbooks.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 3.4 — Incidents API routes

```
Read backend_architecture.md section 23.2 (routes_incidents.py summary).

Create backend/app/api/routes_incidents.py:
- GET /api/incidents — list with filters: status, severity, type, entity, time range
- GET /api/incidents/{id} — full detail with events timeline
- PATCH /api/incidents/{id} — update status, severity, add notes
- POST /api/incidents/{id}/acknowledge
- POST /api/incidents/{id}/resolve
- GET /api/incidents/{id}/events — paginated event timeline
- GET /api/incidents/stats — aggregate counts by status, severity, type

Create backend/app/services/incident_service.py with methods:
- create_incident, get_incident, list_incidents, update_incident
- acknowledge, resolve, add_event, get_events, get_stats

Test:
- Write backend/tests/test_api/test_incidents.py
- Create test incidents via service directly, then test API routes
- test_list_incidents_empty
- test_create_incident_and_get_by_id
- test_acknowledge_incident
- test_resolve_incident
- test_get_incident_events_timeline
- test_incidents_stats
- test_filter_by_status
- test_filter_by_severity
- Run: cd backend && pytest tests/test_api/test_incidents.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 3.5 — Rules and webhook routes

```
Read backend_architecture.md sections 23.2 (routes_rules.py) and 24 (Webhook Ingress).

Create backend/app/api/routes_rules.py:
- GET /api/rules — list alert rules
- POST /api/rules — create alert rule
- PUT /api/rules/{id} — update
- DELETE /api/rules/{id} — delete
- GET /api/rules/trigger-rules — list trigger rules (incident→playbook mappings)

Create backend/app/api/routes_webhooks.py:
- POST /api/webhooks/alertmanager — receives Alertmanager webhook JSON,
  transforms alerts using the transform_alertmanager_alert function from section 11.4,
  pushes internal signals to Redis Stream automend:stream:correlation_input
- POST /api/ingest/otlp — receives OTLP log export JSON,
  normalizes log records, pushes to Redis Stream automend:stream:normalized_logs

Create backend/app/services/rule_service.py

Test:
- Write backend/tests/test_api/test_rules.py
  - test_crud_alert_rules
  - test_list_trigger_rules
- Write backend/tests/test_api/test_webhooks.py
  - test_alertmanager_webhook_transforms_and_pushes (mock Redis, verify signal shape)
  - test_otlp_ingest_normalizes_and_pushes (mock Redis, verify log entry shape)
  - test_alertmanager_webhook_with_real_alertmanager_payload (use a real AM payload sample)
- Run: cd backend && pytest tests/test_api/test_rules.py tests/test_api/test_webhooks.py -v

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 3.6 — Workflow status API routes

```
Read backend_architecture.md section 23.2 (routes_workflows.py summary).

Create backend/app/api/routes_workflows.py:
- GET /api/workflows — list recent workflow executions (from incidents table temporal_workflow_id)
- GET /api/workflows/{workflow_id} — get workflow detail
  (For now, return data from incidents table. Temporal integration comes in Phase 5.)
- POST /api/workflows/{workflow_id}/signal — placeholder, returns 501
- POST /api/workflows/{workflow_id}/cancel — placeholder, returns 501

Create backend/app/services/workflow_service.py with placeholder methods.

Test:
- Write backend/tests/test_api/test_workflows.py
- test_list_workflows_empty
- test_get_workflow_not_found
- test_signal_returns_501
- Run: cd backend && pytest tests/test_api/test_workflows.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

---

## Phase 4: Intelligence Plane — Workers

### Task 4.1 — Window worker (core loop)

```
Read backend_architecture.md section 9 (Window Worker) entirely.

Implement backend/app/workers/window_worker.py with class WindowWorker:
- __init__: connects to Redis, sets up consumer group
- run(): main async loop — XREADGROUP from automend:stream:normalized_logs
- process_log_entry(entry): groups by entity_key, adds to window in Redis
- check_and_close_windows(): checks all open windows for age/size threshold
- close_window(entity_key): retrieves window data, calls classifier, emits classified event
- For now, MOCK the classifier call — just return a hardcoded classification
  (we'll connect the real classifier in Task 4.2)

Implement backend/main_window_worker.py entrypoint.

Include the background timer coroutine that scans for stale windows every 30 seconds.

Test:
- Write backend/tests/test_workers/test_window_worker.py
- test_process_log_entry_creates_window (push entry, verify Redis window exists)
- test_window_closes_after_max_entries (push MAX_WINDOW_ENTRIES entries, verify close triggered)
- test_window_closes_after_timeout (mock time, verify stale window detection)
- test_close_window_emits_classified_event (verify event in classified_events stream)
- test_dedup_prevents_reclassification
- Integration test: push 10 log entries to normalized_logs stream,
  start worker briefly, verify window created in Redis
- Run: cd backend && pytest tests/test_workers/test_window_worker.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 4.2 — Classifier client and service

```
Read backend_architecture.md section 10 (Model 1 Classifier Service) entirely.

Create:
- backend/app/services/classifier_client.py — the HTTP client (exact code from section 10.4)
  with ClassifierInput and ClassifierOutput Pydantic models
- backend/app/services/classifier_server.py — a standalone FastAPI app that:
  - Exposes POST /classify
  - For v1, uses an LLM (Anthropic or OpenAI) with the prompt from section 10.5
  - Falls back to a rule-based/regex classifier if no API key is configured
  - Returns structured JSON classification

The regex fallback classifier should detect at minimum:
- "out of memory" / "OOM" / "CUDA error" → failure.memory
- "connection refused" / "timeout" / "DNS" → failure.network  
- "401" / "403" / "authentication" / "unauthorized" → failure.authentication
- "no space left" / "disk full" / "I/O error" → failure.storage
- "CrashLoopBackOff" / "segfault" / "panic" → failure.crash
- Everything else → anomaly.pattern or normal

Update window_worker.py to use the real classifier_client instead of mock.

Test:
- Write backend/tests/test_services/test_classifier.py
- test_classifier_client_parses_response (mock HTTP)
- test_regex_fallback_memory_detection
- test_regex_fallback_network_detection
- test_regex_fallback_auth_detection
- test_regex_fallback_normal_for_clean_logs
- Integration: start classifier server, send test payload, verify response shape
- Run: cd backend && pytest tests/test_services/test_classifier.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section, add classifier commands
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 4.3 — Correlation worker

```
Read backend_architecture.md section 11 (Correlation Worker) entirely.

Implement backend/app/workers/correlation_worker.py with class CorrelationWorker:
- Consumes from TWO sources:
  1. Redis Stream automend:stream:classified_events
  2. Redis Stream automend:stream:correlation_input (from webhooks)
- For each signal:
  - Normalize to internal signal schema
  - Derive incident_key
  - Acquire correlation lock
  - Check for active incident
  - Decision logic (section 11.5):
    - No active incident: check cooldown/dedup, create incident, find playbook, start workflow
    - Active incident: add evidence, signal running workflow
  - Release lock

For now, the "start workflow" part should be a placeholder that:
- Sets temporal_workflow_id to "placeholder-{uuid}" on the incident
- Logs "Would start Temporal workflow for playbook {id}"
(Real Temporal integration in Phase 5)

Implement backend/main_correlation_worker.py entrypoint.

Test:
- Write backend/tests/test_workers/test_correlation_worker.py
- test_classifier_event_creates_incident
- test_prometheus_alert_creates_incident
- test_duplicate_signal_updates_existing_incident (not creates new)
- test_cooldown_suppresses_incident_creation
- test_severity_escalation (medium incident + high signal = high incident)
- test_source_merging (classifier + prometheus = both in sources array)
- test_playbook_lookup_finds_matching_rule
- test_no_playbook_creates_incident_without_workflow
- Run: cd backend && pytest tests/test_workers/test_correlation_worker.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 4.4 — End-to-end test: Logs → Incident

```
Write an integration test that runs the full logs-to-incident pipeline:

1. Push a batch of GPU OOM log entries to Redis Stream automend:stream:normalized_logs
2. Run the window worker for a few seconds (or trigger window close manually)
3. Verify the classifier was called and returned a classification
4. Verify a classified event appeared in automend:stream:classified_events
5. Run the correlation worker for a few seconds
6. Verify an incident was created in Postgres with:
   - Correct incident_type
   - Correct entity
   - Correct evidence
   - Source includes "log_classifier"
7. Verify incident_events table has a "created" event

This test exercises Tasks 4.1 + 4.2 + 4.3 together.

Test file: backend/tests/test_integration/test_logs_to_incident.py
Run: cd backend && pytest tests/test_integration/test_logs_to_incident.py -v

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

---

## Phase 5: Orchestration Plane — Temporal

### Task 5.1 — Temporal connection and basic workflow test

```
Read backend_architecture.md sections 20 (Temporal Setup) and 20.3 (Worker Entrypoint).

Create:
- Temporal client factory in app/dependencies.py: get_temporal_client()
- A simple test workflow (HelloWorld) to verify Temporal connectivity
- Update main_temporal_worker.py to register and start polling

Test:
- With Temporal running (docker-compose), start the temporal worker
- Use a test script to start the HelloWorld workflow and verify it completes
- Write backend/tests/test_temporal/test_connection.py
  - test_temporal_client_connects
  - test_hello_world_workflow_completes
- Run: cd backend && pytest tests/test_temporal/test_connection.py -v — passes

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 5.2 — Temporal activities (infrastructure + tool activities)

```
Read backend_architecture.md section 22 (Temporal Activities) entirely.

Create backend/app/temporal/activities.py with ALL activities listed:

Infrastructure activities:
- load_playbook_activity
- resolve_incident_activity
- update_incident_status_activity
- record_step_result_activity

Tool activities (implement with MOCK/stub implementations for now — real K8s/Slack/PD
integration is out of scope for v1 local dev):
- fetch_pod_logs_activity — returns mock log data
- query_prometheus_activity — calls real Prometheus if available, else returns mock
- restart_workload_activity — logs action, returns success
- scale_deployment_activity — logs action, returns success
- rollback_release_activity — logs action, returns success
- page_oncall_activity — logs action, returns mock page_id
- slack_notification_activity — logs action, returns mock ts
- slack_approval_activity — auto-approves after 2 seconds (for testing)
- open_ticket_activity — logs action, returns mock ticket_id
- describe_pod_activity — returns mock pod description
- get_node_status_activity — returns mock node status
- cordon_node_activity — logs action, returns success
- drain_node_activity — logs action, returns success
- run_diagnostic_script_activity — returns mock output

Each mock activity should log what it would do and return a realistic response shape.

Test:
- Write backend/tests/test_temporal/test_activities.py
- test_load_playbook_activity (create a playbook in DB first, verify load)
- test_resolve_incident_activity (create incident, resolve, verify status)
- test_record_step_result_activity (verify incident event created)
- test_mock_restart_workload_returns_success
- test_mock_query_prometheus_returns_result_shape
- Run: cd backend && pytest tests/test_temporal/test_activities.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 5.3 — DynamicPlaybookExecutor workflow

```
Read backend_architecture.md section 21 (DynamicPlaybookExecutor) entirely.

Create backend/app/temporal/workflows.py with the full DynamicPlaybookExecutor workflow.
Use the implementation guidance from section 21.2 but adapt as needed for the Temporal
Python SDK specifics.

Key features to implement:
- Load playbook from Postgres via activity
- Checksum validation
- Template expression resolution (${incident.X}, ${steps.Y.output.Z}, ${params.W})
- Step type dispatching: action, condition, delay, approval, notification
- on_success / on_failure transitions
- Retry policy from step config
- Signal handler for new_evidence
- Signal handler for abort
- on_complete: resolve incident
- on_abort: update incident status

Update main_temporal_worker.py to register this workflow + all activities.

Test:
- Write backend/tests/test_temporal/test_dynamic_executor.py
- test_simple_two_step_playbook (fetch_logs → notify, both succeed)
- test_playbook_with_condition_branch_true
- test_playbook_with_condition_branch_false
- test_playbook_failure_triggers_on_failure_step
- test_template_resolution_with_incident_data
- test_template_resolution_with_step_output
- test_delay_step (use short duration for test)
- test_abort_signal_stops_workflow
- test_checksum_mismatch_raises_error
- Run: cd backend && pytest tests/test_temporal/test_dynamic_executor.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 5.4 — Connect correlation worker to Temporal

```
Now connect the correlation worker's placeholder workflow start to real Temporal.

Update backend/app/workers/correlation_worker.py:
- When a matching playbook is found, actually start DynamicPlaybookExecutor via Temporal client
- Use workflow_id format: "automend-{incident_key_slugified}-{short_uuid}"
- Set the idempotency ID
- When a second signal arrives for an active incident with a running workflow,
  signal the workflow with new_evidence

Update backend/app/services/workflow_service.py:
- get_workflow_status(workflow_id) — query Temporal for workflow execution status
- signal_workflow(workflow_id, signal_name, payload)
- cancel_workflow(workflow_id)

Update routes_workflows.py to use real Temporal queries instead of 501s.

Test:
- Write backend/tests/test_integration/test_incident_to_workflow.py
- Create an incident with a matching published playbook
- Verify Temporal workflow is started
- Query workflow status, verify it's running
- Send a second signal, verify it's added as evidence via Temporal signal
- Wait for workflow to complete (mock activities auto-complete quickly)
- Verify incident is resolved
- Run: cd backend && pytest tests/test_integration/test_incident_to_workflow.py -v — passes

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

---

## Phase 6: Design Plane — AI-Powered Workflow Creation

### Task 6.1 — Embedding service

```
Read backend_architecture.md section 14 (Embedding Service).

Create backend/app/services/embedding_service.py with:
- EmbeddingService class
- embed(text) → list[float]
- embed_batch(texts) → list[list[float]]
- Support for OpenAI API (default) or a local fallback

For the local fallback (when no API key is configured):
- Use sentence-transformers all-MiniLM-L6-v2 (384 dimensions)
- Or, even simpler: use a hash-based fake embedding for testing (consistent but not semantic)

Update the seed_tools.py script to also generate and store embeddings for all tools.

Test:
- Write backend/tests/test_services/test_embedding.py
- test_embed_returns_correct_dimensions
- test_embed_batch_returns_correct_count
- test_similar_texts_have_high_cosine_similarity (if using real model)
- Run seed_tools.py with embeddings, verify tools.embedding is not NULL
- Run: cd backend && pytest tests/test_services/test_embedding.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 6.2 — Vector search service

```
Read backend_architecture.md section 15 (Vector Search).

Create backend/app/services/vector_search_service.py with:
- search_tools(query, limit, min_similarity) → list of tools with similarity scores
- search_playbooks(query, limit, min_similarity, status_filter) → list with scores

Uses EmbeddingService to embed the query, then pgvector cosine distance search.

Test (with seeded tools that have embeddings):
- Write backend/tests/test_services/test_vector_search.py
- test_search_tools_finds_restart_for_restart_query
- test_search_tools_finds_prometheus_for_metrics_query
- test_search_tools_respects_limit
- test_search_tools_respects_min_similarity
- test_search_playbooks_empty_when_none_exist
- Run: cd backend && pytest tests/test_services/test_vector_search.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 6.3 — Architect client (Model 2)

```
Read backend_architecture.md section 16 (Model 2 Architect Service).

Create backend/app/services/architect_client.py with:
- ArchitectClient class
- generate_workflow(intent, tools, example_playbooks, policies, target_incident_types) → dict
- Uses Anthropic API (or OpenAI, configurable)
- Builds the system prompt with tool list, examples, policies, DSL schema
- Parses returned JSON

For testing without an API key, create a template-based fallback that:
- Takes the intent and matched tools
- Returns a hardcoded but valid playbook spec template with the tools filled in
- This lets the design flow work end-to-end even without LLM access

Test:
- Write backend/tests/test_services/test_architect.py
- test_build_system_prompt_includes_tools
- test_build_user_prompt_includes_intent
- test_generate_workflow_with_mock_response (mock HTTP, return valid JSON)
- test_template_fallback_returns_valid_spec
- test_generated_spec_validates_against_dsl_schema
- Run: cd backend && pytest tests/test_services/test_architect.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 6.4 — Design API routes

```
Read backend_architecture.md section 13 (Design Plane Routes) entirely.

Create backend/app/api/routes_design.py with all 6 routes:
- POST /api/design/rag_search
- POST /api/design/generate_workflow
- POST /api/design/validate_workflow
- POST /api/design/save_playbook
- POST /api/design/publish_playbook
- GET /api/design/playbooks/{playbook_id}/versions

Implement the validate_workflow logic:
- JSON Schema validation against DSL
- Tool existence check
- Side-effect level warnings
- Unreachable step detection
- Timeout bounds checking

Test:
- Write backend/tests/test_api/test_design.py
- test_rag_search_returns_relevant_tools
- test_generate_workflow_returns_spec (with mock architect or fallback)
- test_validate_workflow_valid_spec_passes
- test_validate_workflow_invalid_tool_fails
- test_validate_workflow_missing_approval_warning
- test_save_playbook_creates_draft_version
- test_publish_playbook_lifecycle (save → validate → approve → publish)
- test_publish_creates_trigger_rules
- test_get_versions_returns_all
- Run: cd backend && pytest tests/test_api/test_design.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

---

## Phase 7: Full Integration & Frontend Connection

### Task 7.1 — Full end-to-end integration test

```
Write the ultimate integration test that exercises the entire system:

1. Seed tools with embeddings
2. Use design API to generate a playbook for GPU OOM incidents
3. Validate and publish the playbook
4. Push GPU OOM log entries to the normalized_logs stream
5. Run window worker → classifier → correlation worker
6. Verify incident is created
7. Verify Temporal workflow starts
8. Verify workflow executes steps (mock activities complete)
9. Verify incident is resolved when workflow completes
10. Verify all incident events are recorded

Test file: backend/tests/test_integration/test_full_pipeline.py
This is the most important test. It proves the entire system works end to end.

Run: cd backend && pytest tests/test_integration/test_full_pipeline.py -v -s (with output)

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 7.2 — WebSocket for real-time updates

```
Read backend_architecture.md section 32.1 (WebSocket).

Add to the FastAPI app:
- WS /api/ws/incidents — WebSocket endpoint
- Uses Redis Pub/Sub channel "automend:ws:incidents" as broadcast bus
- When incidents are created/updated or workflow steps complete,
  publish to this channel
- WebSocket endpoint subscribes and forwards to connected clients

Update incident_service.py and workflow events to publish to the pub/sub channel.

Message format:
{
  "type": "incident.created | incident.updated | workflow.step_completed | workflow.completed",
  "payload": { ... }
}

Test:
- Write backend/tests/test_api/test_websocket.py
- test_websocket_connects
- test_websocket_receives_incident_created (create incident, verify WS message)
- test_websocket_receives_incident_updated
- Run: cd backend && pytest tests/test_api/test_websocket.py -v — all pass

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done, note any issues or deviations
3. Update CLAUDE.md — add what was built to the "What's Built" section
4. If you made any design decisions not in backend_architecture.md, add them to DECISIONS.md
```

### Task 7.3 — CORS and frontend proxy setup

```
Ensure the frontend can connect to the backend.

1. Read the frontend code to determine what port it runs on and what API base URL it expects
2. Verify CORS is configured correctly in main_api.py for the frontend's origin
3. If the frontend uses a proxy config (vite.config.ts proxy, next.config.js rewrites, etc.),
   verify it points to localhost:8000
4. If the frontend has environment variables for API URL, document them
5. Create a simple test: start both frontend and backend, open browser, verify no CORS errors

Update CLAUDE.md with:
- How to run frontend + backend together
- Any env vars the frontend needs
- The complete "getting started" workflow

After completing this task:
1. Verify frontend can make requests to backend without CORS errors
2. Update PROGRESS.md — mark this task done
3. Update CLAUDE.md — add full "Getting Started" section
4. Update DECISIONS.md with any frontend integration decisions
```

### Task 7.4 — Application Docker Compose

```
Read backend_architecture.md section 28 (Docker Compose).

Create the full infra/docker-compose.yml that includes BOTH infrastructure AND application:
- All services from docker-compose.infra.yml
- api service
- window-worker service
- correlation-worker service
- temporal-worker service
- classifier service

Create the Dockerfiles:
- infra/dockerfiles/Dockerfile.api
- infra/dockerfiles/Dockerfile.worker
- infra/dockerfiles/Dockerfile.temporal-worker

Test:
- docker compose -f infra/docker-compose.yml build — all images build
- docker compose -f infra/docker-compose.yml up -d — all services start
- docker compose ps — all services healthy/running
- curl http://localhost:8000/health — API responds
- curl http://localhost:8000/api/tools — returns seeded tools
- docker compose logs window-worker — shows "Window worker starting"
- docker compose logs correlation-worker — shows "Correlation worker starting"

After completing this task:
1. Run the tests described above and confirm they pass
2. Update PROGRESS.md — mark this task done
3. Update CLAUDE.md — add Docker commands to Common Commands section
4. Update DECISIONS.md if any Dockerfile decisions were made
```

---

## Phase 8: Polish & Documentation

### Task 8.1 — Run full test suite

```
Run the entire test suite and fix any failures:

cd backend && pytest tests/ -v --tb=short

Fix any failures. Ensure all tests pass. Report the final count.

Then run type checking:
cd backend && mypy app/ --ignore-missing-imports

Fix any critical type errors (ignore minor ones from third-party libs).

Then run linting:
cd backend && ruff check app/

Fix any linting issues.

After completing this task:
1. All tests pass, type check is clean, linting passes
2. Update PROGRESS.md — mark this task done, note final test count
3. Update CLAUDE.md — add test/lint/type-check commands
```

### Task 8.2 — Final CLAUDE.md polish

```
Review and finalize CLAUDE.md to be a comprehensive project guide. It should have:

1. Project Overview — what AutoMend is, architecture summary
2. Tech Stack — full list
3. Getting Started:
   a. Prerequisites (Docker, Python 3.11+, Node.js)
   b. Start infrastructure: docker compose command
   c. Run migrations: alembic command
   d. Seed data: seed scripts
   e. Start backend: uvicorn command (or docker compose)
   f. Start workers: commands for each worker
   g. Start frontend: npm command
   h. Full Docker: single docker compose command
4. Project Structure — annotated tree
5. What's Built — complete list of everything implemented
6. API Routes — complete list with methods and paths
7. Testing — how to run tests, what's covered
8. Common Commands — every command a developer needs
9. Architecture Notes — key design decisions, reference to backend_architecture.md
10. Known Limitations / TODO — what's mocked, what needs real implementation

After completing this task:
1. CLAUDE.md is complete and accurate
2. Update PROGRESS.md — mark this task done, mark Phase 8 complete
```

### Task 8.3 — Final PROGRESS.md update

```
Review PROGRESS.md. Ensure every task is checked off or has a note explaining
why it was skipped/deferred. Add a summary section at the top:

## Summary
- Total tasks: X
- Completed: Y
- Skipped: Z (with reasons)
- Build date: {date}
- Test count: {number} tests passing
- Known issues: {list}
```

---

## Phase 9: Frontend Integration (Post-Backend)

> **Context:** Phases 0–8 build the full backend and a typed API client at `src/lib/api.ts`,
> but the existing frontend pages still use localStorage. This phase wires the UI to the
> backend so end users see real data flowing through the system.
>
> **Why this is a separate phase:** The frontend's domain model (`Project + Workflow`) does
> not match the backend's (`Playbook + PlaybookVersion`). We need adapter functions before
> we can swap localStorage for API calls. Also, the backend exposes functionality (incidents,
> live events, workflow executions) that the current UI has no views for — those are net-new
> screens. Doing this after backend completion avoids churn in both halves.

### Task 9.1 — Auth context + login page

```
Read DECISIONS.md (DECISION-013) and src/lib/api.ts for the auth methods already defined.

Build:
1. src/lib/auth-context.tsx — React context that exposes { user, login, logout, isAuthenticated }.
   - Wraps the app in layout.tsx (or a client-component wrapper since context needs 'use client')
   - On mount, calls api.auth.me() if a token is present; sets user state
   - login() calls api.auth.login() and redirects to /
   - logout() clears tokens and redirects to /login
2. src/app/login/page.tsx — email + password form, calls login(), shows errors, redirects on success
3. Protect the root layout: if !isAuthenticated and not on /login, redirect to /login

Test:
- Start backend, create an admin user via scripts/seed a user or hit POST /api/auth/register from a curl.
- npm run dev, visit localhost:3000, confirm redirect to /login
- Log in with valid credentials — lands on / dashboard
- Refresh the page — still authenticated (token in localStorage)
- Click logout — redirected to /login, token cleared

After completing this task:
1. Run npm run build — must succeed with no type errors
2. Update PROGRESS.md — mark done
3. Update CLAUDE.md — add login flow to the "Architecture" section
4. If you made any design decisions (e.g., token refresh strategy, where to store user state),
   add them to DECISIONS.md
```

### Task 9.2 — Adapter layer: Project/Workflow ↔ Playbook/PlaybookVersion

```
The frontend uses Project + Workflow (see src/lib/data.ts). The backend uses Playbook +
PlaybookVersion (see backend_architecture.md §5, §18). Build an adapter in src/lib/adapters.ts
so the existing UI can talk to the backend without a UI rewrite.

Decisions to make and record in DECISIONS.md:
- How does a frontend "Project" (a container for multiple workflows) map to a backend "Playbook"
  (which is itself a container for PlaybookVersions)? Options:
    (a) One Project = one Playbook. Each frontend "Workflow" inside a Project = one PlaybookVersion.
        → Simpler. But loses the frontend's "multiple workflows per project" affordance.
    (b) Keep Projects as a frontend-only grouping concept. Playbook = Workflow.
        → Cleaner mapping. Requires a new table `projects` on the backend (not in spec).
  Recommended: (a) for v1. Document the choice in DECISIONS.md.

- ReactFlow {nodes, edges} ↔ Playbook DSL workflow_spec. Write two pure functions:
    - reactFlowToSpec(nodes, edges, meta): returns a valid Playbook DSL JSON (§19)
    - specToReactFlow(spec): returns { nodes, edges } positioned automatically
  The DSL uses a linear step sequence with on_success/on_failure links; the React Flow
  model is a free-form graph. The simplest adapter walks edges outgoing from a trigger node
  in topological order.

- Frontend status 'active'|'paused'|'draft' ↔ backend PlaybookVersion.status. Map:
    'draft' → 'draft', 'active' → 'published', 'paused' → 'archived'

Test:
- Unit tests for reactFlowToSpec and specToReactFlow as pure functions (no network). Include
  round-trip tests: reactFlowToSpec(specToReactFlow(spec)) ≈ spec for a handful of sample specs.

After completing this task:
1. Run npm run build
2. Update PROGRESS.md — mark done
3. Update CLAUDE.md — describe the adapter layer in "Data Layer" section
4. Record model-mapping decision in DECISIONS.md
```

### Task 9.3 — Wire the dashboard and workflow builder to the backend

```
Replace every localStorage call in src/app/page.tsx and src/app/workflow/[id]/page.tsx
with calls through src/lib/api.ts and the adapter from Task 9.2.

Specifically:
- src/app/page.tsx:
    useEffect load → api.playbooks.list() + adapt to Project[]
    handleCreate → api.playbooks.create(name, description)
    handleDelete → api.playbooks.delete(id)
    handleRename → no direct PATCH /playbooks/{id}? — skip rename for now OR add a backend
                   endpoint later (note as a gap)
    handleStatusChange → api.playbooks.transitionStatus(...) with the mapped status
    WorkflowsPopover "Add New Workflow" → api.playbooks.saveVersion(playbookId, {...empty spec})
- src/app/workflow/[id]/page.tsx:
    useEffect load → api.playbooks.getVersion(playbookId, versionId) + specToReactFlow
    handleSave → reactFlowToSpec(...) + api.playbooks.saveVersion(playbookId, spec)
    handleChat → api.design.generateWorkflow(userMsg) + specToReactFlow to populate canvas
    Deploy button (enable it) → api.playbooks.transitionStatus chain:
        draft → validated → approved → published (with optimistic UI and error handling)

Keep localStorage as a fallback ONLY for: unsaved in-progress workflow edits (a draft buffer
so users don't lose work on a crashed browser tab). Clear that buffer after a successful save.

Test:
- With backend running: log in, create a project, add a workflow, save it.
- Refresh the page — data persists (from backend, not localStorage).
- Open a second browser — see the same project/workflow.
- In the workflow builder, type in the chat ("restart crashed pods") — canvas populates
  with the generated spec. Save it. Deploy it.

After completing this task:
1. Run npm run build
2. Run the backend test suite to confirm nothing regressed (cd backend && pytest)
3. Update PROGRESS.md — mark done, note any endpoints that are missing (e.g., rename playbook)
4. Update CLAUDE.md — remove "not yet wired" notes from the API client section
5. Update DECISIONS.md if you added a local draft buffer or handled unsupported operations
```

### Task 9.4 — Incidents dashboard with live updates

```
Add new routes to surface backend functionality that the current UI has no home for.

Build:
1. src/app/incidents/page.tsx — lists all incidents via api.incidents.list()
   - Filter tabs (all, open, acknowledged, in_progress, resolved)
   - Severity badges with color coding (matching the backend's critical/high/medium/low/info)
   - Stats cards at the top (from api.incidents.stats)
   - Click an incident → navigate to /incidents/[id]
   - Subscribe to WebSocket via connectIncidentEvents() — prepend new incidents and update
     status on incoming events. Show a small "live" indicator.
2. src/app/incidents/[id]/page.tsx — incident detail
   - Full incident info (type, severity, status, entity, sources, evidence)
   - Event timeline (api.incidents.events) — use nice timestamps, event-type-specific icons
   - Workflow panel: if temporal_workflow_id is set, fetch api.workflows.get(id) and show status
   - Actions: Acknowledge, Resolve buttons (operator+ only, hide for viewers)
   - Live: WebSocket filtered by this incident_id; refresh the event timeline on each new event
3. Add "Incidents" to the top nav (next to the existing Projects link)

Test:
- Manually trigger an incident via the alertmanager webhook or by posting to a Redis stream
- Open /incidents — incident appears in real time
- Click it → see the event timeline update as the workflow runs step by step
- Ack/resolve works

After completing this task:
1. npm run build
2. Update PROGRESS.md, CLAUDE.md, DECISIONS.md as usual
3. Suggest follow-up tasks if you find gaps (e.g., "no UI for filtering by entity")
```

---

## Phase 10: Integrate the real inference_backend (classifier + generator)

> **Context:** Phases 0–9 shipped stubs for the two model services — a rule-based regex
> classifier in `backend/app/services/classifier_server.py` (DECISION-009) and the
> Anthropic Claude `ArchitectClient`. The `inference_backend/` sibling repo now ships
> real model servers — a RoBERTa-base classifier (Track A) and a Qwen2.5-1.5B + LoRA
> generator served via vLLM behind a proxy (Track B). This phase swaps the stubs for
> the real services and reconciles the three mismatches:
>
> 1. **Classifier input:** inference service wants pre-tokenized `sequence_ids: list[int]`;
>    core produces `logs: list[{body, attributes}]`. Fix: tokenize inside the service
>    using the stock `RobertaTokenizer`.
> 2. **Classifier taxonomy:** inference emits 7 class names (`Normal`, `Resource_Exhaustion`,
>    …); core expects 14 dotted labels (`failure.memory`, `degradation.latency`, …). Fix:
>    mapping layer in the core `ClassifierClient`.
> 3. **Generator output + tool registry:** inference proxy hardcodes a 6-tool registry
>    and per-tool Pydantic validators; core is authoritative for the tool registry
>    (selected via pgvector RAG per request in `routes_design.generate_workflow`) and
>    validates in `_validate_spec`. Fix: make the proxy schema-agnostic (passthrough +
>    JSON repair only); core's `ArchitectClient` drives prompt content.
>
> **Ownership split:** the two `inference_backend/` services are the source of truth for
> model code + tokenization + serving. The core is the source of truth for the tool
> registry, taxonomy, validation, and the prompt that gets sent. Neither side should
> duplicate the other's concerns.

### Task 10.1 — ClassifierModel: accept raw logs, tokenize internally

```
Edit inference_backend/ClassifierModel/classifierModelAPI/ so the /predict_anomaly
endpoint accepts the same request shape the core backend's WindowWorker already emits
(app/workers/window_worker.py:196), and tokenizes with the stock RoBERTa tokenizer.

Change:
- schemas/anomaly.py — AnomalyRequest becomes:
    { entity_key: str,
      window_start: str, window_end: str,
      logs: list[dict],                   # each log has at least {body: str, attributes?: dict}
      max_logs: int = 200,
      entity_context: dict = {} }
  Keep AnomalyResponse shape ({class_id, confidence_score, label}) — the core will
  translate to its 14-label taxonomy in Task 10.3. Do NOT adopt the core's response
  shape; the service owns its own native taxonomy.
- inference.py —
    * Drop sequence-id / vocabulary code (keep it in a dead branch or delete).
    * On startup, load `RobertaTokenizer.from_pretrained("roberta-base")` alongside the
      model.
    * At inference: concatenate log bodies (bounded by max_logs) into a single string
      with newline separators, tokenize with `truncation=True, max_length=512,
      return_tensors="pt"`, run the classifier, softmax, argmax.
- app/main.py — update the route to use the new request/response.
- Update README.md to document the new request shape and note that the old
  sequence-id contract is gone.

Test:
- Update tests under the service's own test directory (the repo's existing tests for
  sequence_ids will break — rewrite them for the logs shape).
- Add a test that posts a realistic log window (e.g., 50 log lines with an OOM pattern)
  and asserts the service returns a class_id + confidence_score.
- Smoke test: `uvicorn app.main:app --port 8000`, POST sample payload via curl.

This task does NOT touch the core backend. After it, the service is callable with the
new shape but the core still points at the old stub classifier.

After completing this task:
1. Run the service's own test suite; confirm pass
2. Update PROGRESS.md — mark done, note tokenization choices (truncation length, body
   concatenation strategy)
3. Update CLAUDE.md — note that the RoBERTa classifier is wired for raw logs
4. Record in DECISIONS.md: "Classifier tokenizes internally with roberta-base tokenizer"
   and the reasoning (avoids shipping the training vocab into the core backend)
```

### Task 10.2 — GeneratorModel proxy: strip hardcoded tool schema, become a passthrough

```
Edit inference_backend/GeneratorModel/generatorModelAPI/ so the proxy stops enforcing
a 6-tool registry and instead forwards whatever the core builds.

Change:
- schemas/workflow.py — delete the per-tool Literal + per-tool param models. Replace
  with a minimal passthrough schema:
    class GenerateRequest(BaseModel):
        system_prompt: str
        user_message: str
        max_tokens: int = 4096
        temperature: float = 0.0         # must stay 0.0 per the README
    class GenerateResponse(BaseModel):
        success: bool
        workflow_spec: dict | None = None     # parsed JSON, whatever shape the LLM returned
        error: str | None = None
        details: str | None = None
        raw_output: str | None = None
- app/main.py — the /generate_workflow handler:
    * Build the vLLM request: {model: "/models/fused_model", messages: [{"role":"system",
      "content": system_prompt}, {"role":"user", "content": user_message}],
      temperature: 0.0, max_tokens}
    * Call vLLM's /v1/chat/completions
    * Pull data["choices"][0]["message"]["content"]
    * Run through guardrails.repair_json (keep this module — it's hygiene, not semantics)
    * json.loads; on failure return success=False with raw_output
    * Return success=True with workflow_spec set to whatever was parsed
- guardrails.py — keep as-is. No tool-specific logic.
- Delete integration tests that asserted on the 6-tool format; keep JSON-repair unit
  tests and proxy round-trip tests.

This service is now "dumb": no validation of step types, tool names, branching, or
anything else. That's the point — validation lives in core's _validate_spec.

Test:
- Unit tests for guardrails (existing, should still pass).
- Update proxy test to send a minimal system_prompt/user_message and assert the proxy
  returns whatever JSON shape the mocked vLLM responded with, unchanged.
- Smoke test against mock_proxy.py: confirm it works with arbitrary tool names in the
  mocked response (e.g., "scale_deployment" AND "query_prometheus" — the full 14-tool
  core registry).

After completing this task:
1. Run the proxy's unit tests; confirm pass
2. Update the proxy README — remove the "6-tool registry" section
3. Update PROGRESS.md — mark done
4. Record in DECISIONS.md: "Generator proxy is schema-agnostic; core is authoritative
   for tool registry and DSL validation"
```

### Task 10.3 — Core ClassifierClient: new request shape + 7→14 label mapping

```
Update the core backend to talk to the new RoBERTa service and map its 7-class output
to the 14-label taxonomy the rest of the system uses.

Files to edit:
- backend/app/services/classifier_client.py
- backend/app/config.py (possibly — if the threshold or url defaults change)
- backend/app/workers/window_worker.py (only if the wire shape changed — it probably
  didn't since the inference service now accepts what we already send)

The WindowWorker already emits the right request shape (entity_key, window_start,
window_end, logs, max_logs, entity_context — see window_worker.py:196). Confirm the
wire format lines up after Task 10.1 and adjust if needed.

Add a label mapping in a new module app/services/classifier_taxonomy.py. The mapping
has two tiers: a default 7→14 dict, then a refinement pass that uses log-content
regexes to disambiguate coarse model labels into finer core labels.

Tier 1 — default collapse:

    INFERENCE_TO_CORE: dict[str, str] = {
        "Normal":              "normal",
        "Resource_Exhaustion": "failure.resource_limit",  # refined below
        "System_Crash":        "failure.crash",
        "Network_Failure":     "failure.network",
        "Data_Drift":          "anomaly.pattern",
        "Auth_Failure":        "failure.authentication",
        "Permission_Denied":   "failure.authentication",  # collapse until we split
    }

    CORE_SEVERITY: dict[str, str] = {
        "failure.crash": "high", "failure.gpu": "high",
        "failure.memory": "high", "failure.storage": "high",
        "failure.resource_limit": "medium", "failure.network": "medium",
        "failure.authentication": "medium", "failure.dependency": "medium",
        "failure.configuration": "medium", "failure.deployment": "medium",
        "degradation.latency": "medium", "degradation.throughput": "low",
        "anomaly.pattern": "low", "normal": "info",
    }

Tier 2 — log-content refinement (only fires when Tier 1 lands on a coarse label):

    REFINEMENTS: dict[str, list[tuple[re.Pattern, str]]] = {
        # Resource_Exhaustion → split on log content
        "failure.resource_limit": [
            (re.compile(r"(?i)CUDA|nvidia|GPU|Xid|ECC"),           "failure.gpu"),
            (re.compile(r"(?i)OOM|out\s*of\s*memory|oom.kill"),    "failure.memory"),
            (re.compile(r"(?i)disk\s+full|no\s+space|PVC"),        "failure.storage"),
        ],
        # Network_Failure → split dependency vs raw network
        "failure.network": [
            (re.compile(r"(?i)502|503|upstream\s+(unavailable|timeout)"), "failure.dependency"),
        ],
        # Auth_Failure collapses with Permission_Denied into failure.authentication.
        # Could add a split here later if Permission_Denied should map to
        # failure.configuration instead.
    }

    def refine_label(coarse_label: str, logs: list[dict]) -> str:
        rules = REFINEMENTS.get(coarse_label)
        if not rules:
            return coarse_label
        body = "\n".join(l.get("body", "") for l in logs[:50])  # bounded scan
        for pattern, finer_label in rules:
            if pattern.search(body):
                return finer_label
        return coarse_label

Reuse (don't duplicate): these regex patterns already exist in
`app/services/classifier_server.py:PATTERNS`. Extract the shared ones into
`app/services/log_patterns.py` and import from both the stub classifier and the
taxonomy module. Keeps the stub working during/after the swap and avoids drift.

In ClassifierClient.classify(), after receiving the raw inference response:

    coarse     = INFERENCE_TO_CORE.get(resp["label"], "anomaly.pattern")
    core_label = refine_label(coarse, input_data["logs"])
    severity   = CORE_SEVERITY.get(core_label, "medium")
    return {
        "label": core_label,
        "confidence": resp["confidence_score"],
        "evidence": [l.get("body","") for l in input_data["logs"][:5]],
        "severity_suggestion": severity,
        "secondary_labels": [],
    }

Note: the inference service doesn't currently return secondary labels or evidence. We
populate evidence from the input logs as a reasonable proxy. When the model is
retrained with a finer taxonomy, delete the matching REFINEMENTS entries first, then
the INFERENCE_TO_CORE entries — keeps the diff small.

Tests:
- Update tests/test_services/test_classifier.py:
    * Mock the inference service HTTP response
    * Assert Tier 1 mapping: all 7 inference labels → expected coarse core labels
    * Assert Tier 2 refinement:
        - Resource_Exhaustion + "CUDA error" logs → failure.gpu
        - Resource_Exhaustion + "OOMKilled" logs → failure.memory
        - Resource_Exhaustion + "disk full" logs → failure.storage
        - Resource_Exhaustion + unrelated logs → stays failure.resource_limit
        - Network_Failure + "502 upstream timeout" → failure.dependency
        - Network_Failure + "connection refused" → stays failure.network
    * Assert severity_suggestion uses the refined label (e.g., gpu → high, not the
      medium default of resource_limit)
    * Assert evidence is the first-N log bodies
- Existing WindowWorker tests should still pass since the core-facing classify() return
  shape is unchanged.

After completing this task:
1. Run full backend pytest; confirm no regressions
2. Update PROGRESS.md — mark done. Note which inference classes you collapsed (e.g.,
   Permission_Denied → failure.authentication) and flag these as candidates for future
   taxonomy expansion.
3. Update CLAUDE.md — note the 7→14 label mapping in "Known limitations" if any
   information is lost (Data_Drift → anomaly.pattern is lossy)
4. Record in DECISIONS.md: "7-class inference taxonomy mapped to 14-label core
   taxonomy in ClassifierClient" with the rationale for each collapse
```

### Task 10.4 — Core ArchitectClient: target the Qwen vLLM proxy

```
Repoint the core's ArchitectClient from Anthropic's Messages API to the Qwen proxy
from Task 10.2, behind a provider config flag so Anthropic still works.

Files to edit:
- backend/app/config.py — add settings:
    architect_provider: Literal["anthropic", "local"] = "anthropic"
    # local = inference_backend generator proxy
    # keep architect_api_base_url, architect_api_key, architect_model as before —
    # they're reused for whichever provider is selected.
- backend/app/services/architect_client.py — branch on settings.architect_provider:
    if provider == "local":
        POST {base_url}/generate_workflow
        body: {"system_prompt": system, "user_message": user, "max_tokens": 4096,
               "temperature": 0.0}
        response: {"success": bool, "workflow_spec": dict, "error": str|None, ...}
        on success: return workflow_spec dict (already parsed by the proxy)
        on failure: raise an exception with error + details so the route returns 502
    else:  # anthropic, existing path
        same as today.
  Keep _build_system_prompt / _build_user_prompt unchanged — the prompt content is
  identical. Only the envelope changes.
- backend/.env.example — document AUTOMEND_ARCHITECT_PROVIDER and note the two modes.

Tests:
- Update tests/test_services/test_architect.py:
    * Add a second set of mock tests for provider="local" (use httpx MockTransport
      or monkeypatch). Assert the request body has {system_prompt, user_message} and
      the response-parsing extracts workflow_spec.
    * Keep the existing anthropic tests; parameterize if convenient.
- tests/test_api/test_routes_design.py — no change expected; those tests already mock
  ArchitectClient at a higher level.

After completing this task:
1. Run full backend pytest; confirm no regressions
2. Manually smoke-test with the proxy running (mock_proxy.py is fine for local):
    * Set AUTOMEND_ARCHITECT_PROVIDER=local,
      AUTOMEND_ARCHITECT_API_BASE_URL=http://localhost:8002
    * Log in to the UI, open a workflow, type an intent in the chat panel
    * Confirm the canvas populates from the proxy's response
3. Update PROGRESS.md — mark done
4. Update CLAUDE.md — add the architect_provider config to the "Getting Started" /
   "Architecture" sections
5. Record in DECISIONS.md: "Architect has two provider modes; prompt logic is shared.
   Keeps us unblocked on Anthropic API keys while we exercise the local model."
```

### Task 10.5 — End-to-end smoke: real classifier + real generator

```
Prove the integrated path works with both real services running.

Steps:
1. Bring up infra (docker-compose.infra.yml) + both inference services:
    * cd inference_backend/ClassifierModel/classifierModelAPI && uvicorn app.main:app --port 8000
    * cd inference_backend/GeneratorModel && python tests/mock_proxy.py   # or real vLLM if GPU
2. Bring up the core backend (4 processes + the classifier client now pointing at
   port 8000 instead of the stub).
3. Exercise Flow B+C:
    * POST a realistic log window to /api/webhooks/ingest/otlp (or push directly to
      the normalized_logs Redis stream)
    * Confirm the WindowWorker closes the window, hits the RoBERTa classifier,
      receives a label, translates it, writes a classified_event.
    * Confirm the CorrelationWorker picks it up, creates an incident, looks up a
      matching trigger rule, starts a Temporal workflow.
    * Verify in the UI at /incidents and /incidents/[id] that the incident + its
      timeline appear.
4. Exercise Flow A (design plane):
    * Open a workflow in the builder, type an intent in the chat panel (e.g., "if
      memory usage is high, scale up replicas and notify #mlops").
    * Confirm the generator proxy is called, the response is the core's §19 DSL
      shape, and the canvas populates.
    * Save the version, walk the deploy chain, confirm a trigger rule binds it.

Write this up as a test file tests/test_e2e_inference_integration.py that SKIPS when
either inference service is unreachable (like the Postgres/Redis skip pattern in
existing tests). Inside, drive the pipeline with real HTTP calls — no mocks.

After completing this task:
1. Run full backend pytest; confirm the new e2e test passes (or skips cleanly)
2. Update PROGRESS.md — mark Phase 10 complete with the final backend+inference pass
3. Update CLAUDE.md:
    * Move the classifier + architect from "Stubbed or mocked" to "What's Built"
    * Remove DECISION-009 from the "stubbed" notes (leave the decision record itself)
4. If the e2e run surfaced gaps (e.g., bad tokenization for certain log types, bad
   mappings for certain labels), log them in DECISIONS.md and PROGRESS.md as
   follow-ups.
```

---

## Phase 11: Containerise AutoMend + Helm chart (local Kubernetes)

> **Context:** Phase 10 left AutoMend running as six host-side Python processes plus
> a host `npm run dev` for the frontend, with infra in Docker Compose. This phase
> moves everything into a single Helm chart so `helm install automend ./infra/helm/automend -f values-local.yaml`
> replaces the entire six-terminal dance. The chart is designed to be re-used
> against GKE in Phase 12 — only the values file differs.
>
> **Why Helm not plain kubectl manifests:** six services × multiple environments ×
> image tag / replica / resource overrides gets unmanageable as plain YAML inside
> a year. Helm templating + a single `values.yaml` keeps env-specific deltas small.
>
> **Why Helm NOT Terraform for the workloads:** Terraform's Kubernetes provider
> works but fights Helm on templating and has awkward state problems. Clean seam:
> Terraform for cloud APIs (Phase 12), Helm for Kubernetes resources.
>
> **Why NOT Kustomize:** Kustomize is great for "same app, minor overlays" but
> AutoMend's values between local and GCP (managed DB on/off, secret sources,
> resource shapes, Ingress class, image tags) differ enough that a templated chart
> is the better fit.

### Task 11.1 — Frontend Dockerfile

```
The Next.js frontend doesn't have a Dockerfile yet (gap from Task 7.4 — see
CLAUDE.md "Known gaps"). Add one before building the Helm chart.

Build:
- `infra/dockerfiles/Dockerfile.frontend` — multi-stage:
    * Stage 1: node:20-alpine. COPY package.json + package-lock.json. `npm ci`.
      COPY the full src/ + next.config.js + tailwind.config.js + tsconfig.json
      + app/. `npm run build`.
    * Stage 2: node:20-alpine. COPY --from=1 the .next/standalone output (set
      `output: "standalone"` in next.config.js for a minimal runtime image).
      EXPOSE 3000. CMD ["node", "server.js"].
    * Use a non-root user (`USER node`) and a read-only filesystem where possible.
- Bake `NEXT_PUBLIC_API_BASE_URL` as a build-arg so the frontend knows where the
  API lives at runtime (default "" for same-origin via Ingress).
- Add to `infra/docker-compose.yml` as a new `frontend` service (port 3000,
  depends_on: api). This closes a pre-existing gap.

Test:
- `docker build -t automend/frontend:dev -f infra/dockerfiles/Dockerfile.frontend .`
  from the repo root. Image builds; final size under 300MB.
- `docker run -p 3000:3000 automend/frontend:dev` — browsable at localhost:3000.
- `docker compose -f infra/docker-compose.yml up -d --build` — full stack +
  frontend all come up. Login works.

After completing this task:
1. Run npm run build still passes (no regression on host-based dev)
2. Update PROGRESS.md — mark done, note the standalone-output switch in next.config.js
3. Update CLAUDE.md — remove "frontend has no Dockerfile" from Known gaps, note
   the new compose service
4. No new DECISION unless something surprising came up
```

### Task 11.2 — Helm chart scaffolding

```
Create the chart skeleton. No workload templates yet — just the machinery.

Build:
- `infra/helm/automend/Chart.yaml` — apiVersion v2, name automend, appVersion
  matching the backend __version__, description, maintainers.
- `infra/helm/automend/values.yaml` — sane production defaults:
    * `global.imageRegistry: ""` (empty for docker-hub / local)
    * `global.imageTag: "latest"`
    * `api.replicas: 2, resources: {requests, limits}, image.repository: ...`
    * Same for windowWorker, correlationWorker, temporalWorker, classifier,
      frontend
    * `ingress.enabled: true, className: "nginx", host: "automend.local"`
    * `config.corsOrigins: ["https://automend.local"]`
    * `secrets.create: true` — chart creates Secret from values (for dev only;
      production overrides with external-secrets)
    * `postgres.enabled: false, redis.enabled: false, temporal.enabled: false`
      — subcharts off by default (Phase 12 uses managed GCP)
- `infra/helm/automend/values-local.yaml` — overrides for kind:
    * Flips all three subchart enables to `true`
    * Sets image tags to `dev`, pullPolicy to `Never` (images loaded via
      `kind load docker-image`)
    * ingress.host: "localhost"
- `infra/helm/automend/templates/_helpers.tpl` — standard Helm helpers:
    * `automend.fullname`, `automend.labels`, `automend.selectorLabels`,
      `automend.serviceAccountName`
- `infra/helm/automend/templates/NOTES.txt` — post-install message showing how
  to port-forward and get the first login creds.

Test:
- `helm lint infra/helm/automend` passes with zero errors.
- `helm template automend infra/helm/automend -f infra/helm/automend/values-local.yaml`
  produces valid YAML (no output yet since no workload templates — this just
  confirms the scaffolding compiles).

After completing this task:
1. helm lint clean
2. Update PROGRESS.md — mark done
3. No CLAUDE.md change yet; that comes with 11.3-11.5
4. Record in DECISIONS.md: "Helm + Terraform split; subcharts for local, managed
   services for GCP" IF that split isn't already recorded.
```

### Task 11.3 — Workload templates: Deployments + Services

```
Add one Deployment + Service pair per component. Six pairs total.

Build:
- `templates/api-deployment.yaml` + `templates/api-service.yaml` — the FastAPI.
  env: from ConfigMap + Secret. readinessProbe: GET /health. livenessProbe: same
  with higher initialDelay. Replicas from values.
- `templates/window-worker-deployment.yaml` — no Service (outbound-only). Uses
  the same image as the API (differs only in command: ["python","main_window_worker.py"]).
- `templates/correlation-worker-deployment.yaml` — same pattern.
- `templates/temporal-worker-deployment.yaml` — same pattern.
- `templates/classifier-deployment.yaml` + `templates/classifier-service.yaml`
  — the stub classifier on port 8001 (cluster-internal only). This service is
  what `AUTOMEND_CLASSIFIER_SERVICE_URL` points at — e.g.
  http://automend-classifier.automend.svc.cluster.local:8001
- `templates/frontend-deployment.yaml` + `templates/frontend-service.yaml`
  — port 3000, served via Ingress.
- All deployments:
    * strategy.type: RollingUpdate
    * podAnti-affinity prefer (not require) spreading across nodes
    * topologySpreadConstraints for multi-replica components
    * resources from values.yaml (never hardcode)
    * serviceAccountName: {{ include "automend.serviceAccountName" . }}

Test:
- `helm template` produces valid manifests. `kubectl apply --dry-run=client -f -`
  against the rendered output passes.
- `helm install automend-test infra/helm/automend -n automend-test --create-namespace
  --dry-run --debug` — no schema errors.

After completing this task:
1. helm lint + helm template valid
2. Update PROGRESS.md
3. Update CLAUDE.md — mention the chart's 6-service topology
```

### Task 11.4 — ConfigMap, Secret, Ingress, ServiceAccount

```
Wire the environment. The chart needs to produce env vars that match
backend/.env.example exactly.

Build:
- `templates/configmap.yaml` — non-secret env vars: AUTOMEND_POSTGRES_HOST,
  AUTOMEND_REDIS_HOST, AUTOMEND_TEMPORAL_SERVER_URL, AUTOMEND_CLASSIFIER_*,
  AUTOMEND_ARCHITECT_PROVIDER, CORS_ORIGINS, etc.
  Values come from a deep-merged `config:` section in values.yaml.
  Subchart-aware: if postgres.enabled, point HOST at the subchart service name
  (e.g. "automend-postgresql"), otherwise use values.external.postgresHost.
- `templates/secret.yaml` — conditional on `secrets.create`. Contains
  AUTOMEND_POSTGRES_PASSWORD, AUTOMEND_ARCHITECT_API_KEY, AUTOMEND_JWT_SECRET.
  When false, the chart assumes a pre-existing Secret named `{{ .Values.secrets.existingSecret }}`
  (Phase 12 wires this to External Secrets Operator).
- `templates/ingress.yaml` — single Ingress for both frontend (/) and API (/api,
  /api/ws). TLS optional (cert-manager annotation).
- `templates/serviceaccount.yaml` — one SA for the app. Phase 12 binds it to a
  GCP service account via Workload Identity; locally it's vanilla.

Test:
- `helm template ... | kubectl apply --dry-run=client -f -` passes.
- A rendered ConfigMap contains every var from .env.example.
- Secret rendering with `secrets.create: false` produces no Secret object.

After completing this task:
1. helm lint clean
2. Update PROGRESS.md
3. Add to CLAUDE.md: how env vars flow (values.yaml → ConfigMap/Secret → Deployment)
```

### Task 11.5 — Subcharts for local infra

```
Add Postgres (with pgvector), Redis, and Temporal as optional subcharts so
`helm install -f values-local.yaml` brings up a full self-contained stack on kind.

Build:
- `Chart.yaml` dependencies:
    * bitnami/postgresql (condition: postgres.enabled)
    * bitnami/redis (condition: redis.enabled)
    * temporalio/temporal (condition: temporal.enabled)
- `values-local.yaml` overrides:
    * postgres.enabled: true, image: pgvector/pgvector (swap the image;
      bitnami's postgres doesn't include pgvector). Or use a postInstall Job
      that runs CREATE EXTENSION vector.
    * Apply the Alembic migrations via a Helm post-install Job that runs
      `alembic upgrade head` against the subchart's service.
    * Seed tools and rules via the same Job (or a separate one).
- `helm dependency update` baseline documented in the chart README.
- Document in values-local.yaml that the subcharts are for dev convenience only
  and are NOT suitable for production (no backups, no HA, no resource limits
  tuned for real load).

Test:
- `helm dependency update infra/helm/automend`
- `kind create cluster --name automend-demo`
- `docker build -t automend/api:dev -f infra/dockerfiles/Dockerfile.api backend`
  (and same for worker, temporal-worker, classifier, frontend)
- `kind load docker-image automend/api:dev --name automend-demo` (× all 5 images)
- `helm install automend infra/helm/automend -f infra/helm/automend/values-local.yaml`
- `kubectl get pods -w` — all pods Running + Ready within ~2 minutes
- `kubectl port-forward svc/automend-frontend 3000:3000` — login in browser

After completing this task:
1. Full helm install → healthy pods on kind
2. Update PROGRESS.md with the exact commands that worked (for MANUAL_TESTING.md)
3. Record in DECISIONS.md: pgvector image substitution for bitnami/postgres AND
   migrations/seeding-as-Helm-hook decision
```

### Task 11.6 — Helm test harness

```
Add automated checks that the chart is sane and the rendered manifests are valid.

Build:
- `infra/helm/automend/templates/tests/test-health.yaml` — a Helm test Pod that
  curls /health on each service after deploy; annotated "helm.sh/hook": test.
- `infra/helm/tests/test_chart.py` — pytest that runs `helm lint`, `helm template`,
  and parses the output with yaml.safe_load_all to assert:
    * At least 6 Deployments (one per component)
    * A valid ConfigMap with the required env var keys
    * An Ingress routing to both frontend and API services
    * No container runs as root (securityContext)
    * All resources have both requests and limits
- Keep the test file in `infra/helm/tests/` (separate from `backend/tests/`) so
  it runs independently — Helm isn't a backend concern.

Test:
- `pytest infra/helm/tests/ -v` — all checks pass.
- `helm test automend` after a real install returns 0.

After completing this task:
1. Chart tests pass
2. Update PROGRESS.md with the test file locations
```

### Task 11.7 — Rewrite MANUAL_TESTING.md for the helm flow

```
Replace the current six-terminal walkthrough with a helm-install flow. The
goal is: one command brings up everything except kind + Fluent Bit.

Rewrite sections 2–9 of MANUAL_TESTING.md:
- Section 2: Architecture diagram showing AutoMend-in-kind, kind-also-runs-
  the-monitored-workload, Fluent Bit shipping logs cluster-internally (no
  more host.docker.internal).
- Section 3: `kind create cluster` first, `helm install automend ...` second.
  No more Python-on-host, no more docker-compose.infra.yml.
- Section 4: seed data via helm post-install Job (it runs automatically).
- Section 5: `kubectl apply -f crashing-ml.yaml`.
- Section 6: Fluent Bit DaemonSet (unchanged except the host changes to the
  cluster-internal service: http://automend-api.automend.svc.cluster.local:8000)
- Section 7: Watch the pipeline (same terminal output expected, just kubectl
  logs instead of terminal scrollback).
- Section 8: UI walkthrough (unchanged — URL changes to the Ingress host).
- Section 9: Trigger rule registration (unchanged — still a curl command).

Keep the host-based Python flow as a collapsed "Advanced: faster dev loop"
section at the bottom, for people who want reload-on-save. Make the default
path the helm-install one.

Test:
- Follow the new MANUAL_TESTING.md from a clean laptop (no AutoMend state).
  Everything should work start-to-finish in under 15 minutes.

After completing this task:
1. Docs updated; no code changes
2. Mark Phase 11 complete in PROGRESS.md
3. Update CLAUDE.md §3 Getting Started — `helm install` becomes the primary
   path, with docker-compose + host-Python documented as alternatives
```

### Task 11.8 — Day-2 operability hardening (sub-tasks 11.8a–11.8f)

```
Context: After Phase 11 landed, the first live end-to-end run of
MANUAL_TESTING.md (the 2026-04-15 walkthrough captured in DECISION-026)
surfaced six Day-2 operability gaps that blocked a real incident from
being remediated end-to-end without hand-patching K8s state and the
DB. None of these are "fix the crash" — they're the seams between
frontend, DB, Temporal, and the K8s API that the happy-path tests
don't exercise. Task 11.8 is a coherent pass that closes all of them.
The six sub-tasks are mostly independent and can land in separate PRs
but share one theme: make the Day-2 experience match the Day-1
polish. Order matters only in one direction — 11.8a (RBAC) must land
before 11.8b (clusters endpoint) can work, and 11.8c (projects schema)
before 11.8d (UI picker + kill switch).
```

#### Task 11.8a — Helm chart: ship RBAC for the app ServiceAccount

```
Problem: `infra/helm/automend/templates/serviceaccount.yaml` creates
an SA but nothing binds a Role or ClusterRole. Every K8s-touching
Temporal activity (scale_deployment, rollback_release, fetch_pod_logs,
restart_workload, describe_pod, get_node_status, etc.) hits
`403 Forbidden` on a fresh install. The walk-around today is a
hand-written `automend-rbac.yaml` applied per target namespace
(see MANUAL_TESTING.md §15, "Activity fails with 403 Forbidden").

Scope:
- Add `values.yaml` key `rbac.targetNamespaces: []` (list of strings).
  When non-empty, the chart renders one `Role` + one `RoleBinding`
  per namespace granting get/list/watch/patch/update on
  `apps/deployments`, `apps/deployments/scale`, and get/list/watch on
  `pods`, `pods/log`. All bound to the app ServiceAccount.
- Add `values.yaml` key `rbac.clusterWide: false`. When true, render a
  single `ClusterRole` + `ClusterRoleBinding` with the same verbs
  cluster-wide + `get/list/watch` on `namespaces` (needed by 11.8b).
- New template `infra/helm/automend/templates/rbac.yaml` — conditional
  on either flag. Use `range` over targetNamespaces for the per-ns
  bindings.
- `values-local.yaml` sets `rbac.targetNamespaces: [ml, default]` and
  `rbac.clusterWide: true` (11.8b needs namespace-list). values.yaml
  keeps both empty — production deploys (Phase 12) opt in explicitly.
- Update `infra/helm/tests/test_chart.py`: render with rbac.clusterWide
  both on and off, assert the ClusterRoleBinding subject matches the
  ServiceAccount, assert per-ns Roles land in the right namespace.

Test:
- `helm lint` clean.
- New chart tests pass.
- On a clean `helm install ... -f values-local.yaml`:
  `kubectl auth can-i patch deployments.apps/reco-pod --as=system:serviceaccount:automend:automend -n ml`
  returns `yes`.
- Delete `automend-rbac.yaml` from the repo root; the walk-around is
  no longer needed.

After completing:
1. PROGRESS.md — mark 11.8a done, note it replaces the
   "Helm chart ships no RBAC" follow-up.
2. MANUAL_TESTING.md §15 — replace the "Activity fails with
   403 Forbidden" walk-around with a pointer to `rbac.targetNamespaces`.
3. DECISION entry if any deviation from the above (e.g. you chose
   ClusterRole-only because the per-ns pattern was uglier than expected).
```

#### Task 11.8b — Clusters module: list namespaces + resources in the API

```
Problem: The UI has no way to ask "what namespaces exist?" or
"what deployments are in namespace ml?". Today the Scale node's
Service field is a free-text input and IMPLICIT_DEFAULTS hardcodes
`namespace: "default"`. Users typo deployment names and hardcoded
namespaces force the per-playbook SQL patches we did on 2026-04-15.

Scope:
- New route module `backend/app/api/routes_clusters.py` mounted at
  `/api/clusters`.
- `GET /api/clusters/default/namespaces`
  - Auth: editor+ (read-only but not public).
  - Uses `kubernetes_asyncio.client.CoreV1Api` with in-cluster config
    (kubernetes_asyncio.config.load_incluster_config()).
  - Returns `list[{name, created_at, labels}]`.
  - Filters out system namespaces by default (kube-*, automend itself,
    logging). `?include_system=true` disables the filter.
  - 30s in-memory cache keyed by the filter; cluster list is
    stable enough for that.
- `GET /api/clusters/default/namespaces/{ns}/resources?kind=deployment`
  - Auth: editor+.
  - Supported kinds: `deployment` (AppsV1), `statefulset`, `daemonset`,
    `pod`. Start with `deployment`.
  - Returns `list[{name, namespace, replicas, labels, created_at}]`.
- Static cluster name "default" for now — the path shape supports a
  future multi-cluster story without breaking clients. Do NOT add
  a `clusters` table yet; one-cluster assumption is explicit.
- `src/lib/api.ts`: new `api.clusters.listNamespaces()` and
  `api.clusters.listResources(ns, kind)` typed wrappers.
- Backend tests: mock `CoreV1Api.list_namespace` / `AppsV1Api
  .list_namespaced_deployment`, assert shape + auth gate + cache hit.

Test:
- `curl -H "Authorization: Bearer $TOKEN" http://.../api/clusters/default/namespaces`
  returns `[{"name": "ml", ...}, {"name": "default", ...}]` in a kind
  cluster with 11.8a applied.
- Hitting the endpoint twice inside 30s hits the cache (logged).

After completing:
1. PROGRESS.md — mark 11.8b done.
2. CLAUDE.md §6 API Routes — add Clusters section.
3. Note in DECISIONS.md only if you diverge (e.g. you chose to add
   a `clusters` table now instead of the one-cluster assumption).
```

#### Task 11.8c — Projects schema: bind to namespace + kill switch

```
Problem: Projects today are free-form strings that users type in;
nothing ties them to a real namespace. Status (active/paused/draft)
is display-only and carries no execution semantics.

Scope:
- Alembic migration `003_projects_namespace_kill_switch.py`:
  - Add `namespace: text` column to projects (nullable initially).
  - Backfill: `UPDATE projects SET namespace = lower(replace(name, ' ', '-'))`
    — best-effort, operator can fix up manually. Log a warning if
    any rows end up with namespaces that don't exist in the cluster.
  - Add `UNIQUE (namespace)` constraint after backfill.
  - Add `playbooks_enabled: boolean NOT NULL DEFAULT true` column.
  - Drop `status` column (remove the enum type if no other table uses it).
- `app/models/db.py` Project model: replace `status` mapped_column with
  `namespace: str` + `playbooks_enabled: bool`.
- `app/domain/projects.py`: update Pydantic schemas. Remove
  `ProjectStatusValue` enum. Add `namespace: str` required on create,
  `playbooks_enabled: bool = True`.
- `app/api/routes_projects.py`:
  - `POST /api/projects` now requires `namespace` in body; 409 if
    another project already owns that namespace.
  - `PATCH /api/projects/{id}`: allow `playbooks_enabled`. Do NOT allow
    changing `namespace` after creation — a namespace rebind is
    semantically a new project (forces re-evaluation of playbooks).
  - `GET /api/projects` query param `?enabled=true|false` filters.
- `app/stores/postgres_store.py`: updates to project CRUD.
- CorrelationWorker change: before starting a Temporal workflow, load
  the project owning the incident's namespace (via
  `get_project_by_namespace(session, ns)`); if `playbooks_enabled is
  False`, log `"playbooks_disabled_for_namespace"` and do NOT start
  the workflow. Incident row is still created (for visibility).

Test:
- New test `test_api/test_projects.py::test_namespace_uniqueness` —
  POST two projects with the same namespace, second returns 409.
- New test `test_workers/test_correlation_kill_switch.py` — disable
  a project, feed a classified_event for its namespace, assert no
  workflow start call on the Temporal client mock.
- Migration round-trip: `alembic upgrade head` on an empty DB,
  `alembic downgrade -1`, `alembic upgrade head` — no errors.

After completing:
1. PROGRESS.md — mark 11.8c done, replaces the "project status is
   display-only" part of the DECISION-026 follow-ups.
2. CLAUDE.md §5 Backend — update the 11-table summary note +
   the Projects API bullets.
3. DECISION entry documenting the status → playbooks_enabled trade
   (keep backwards compat? we're not — status semantics were dead).
```

#### Task 11.8d — UI: namespace picker + kill switch + resource dropdowns

```
Problem: New Project form is a free-text name; Scale/Rollback's
Service is a free-text input; no way to pause a project's automation.
This task is the frontend companion to 11.8b + 11.8c.

Scope (frontend only):
- `src/app/page.tsx` (projects dashboard):
  - Replace status dropdown/filter tabs with a "Playbooks enabled"
    toggle on each project card. Toggle calls
    `api.projects.update(id, { playbooks_enabled: !current })`.
    Optimistic UI; rollback on error.
  - Remove status stat-cards.
- `src/components/ProjectCreateDialog.tsx` (new, extracted from the
  inline dialog in page.tsx):
  - On mount, call `api.clusters.listNamespaces()`. Populate a
    `<select>` in place of the Name field. Optional display-name
    field still appears; defaults to the picked namespace.
  - Disable namespaces already owned by another project (fetch
    projects.list() in parallel, intersect).
  - Show "No unclaimed namespaces" state with guidance to
    `kubectl create namespace <name>` if all are taken.
- `src/app/workflow/[id]/page.tsx`:
  - Fetch the playbook's project (via `api.projects.get(project_id)`)
    on mount to know which namespace to scope resource lookups to.
- `src/components/NodeConfigPanel.tsx`:
  - For Scale/Rollback node types, replace the free-text "Service"
    input with a `<select>` populated from
    `api.clusters.listResources(project.namespace, "deployment")`.
    Show namespace as a read-only label above ("Namespace: ml").
  - Remove the now-unused Namespace IMPLICIT_DEFAULT from
    `src/lib/adapters.ts` — the namespace comes from the project,
    filled in at save time by `reactFlowToSpec` using the project
    loaded above.
- `src/lib/adapters.ts`: add optional second arg
  `reactFlowToSpec(name, description, nodes, edges, { namespace })`;
  `buildStepInput` uses that namespace for Scale/Rollback instead
  of the hardcoded default.
- `src/lib/adapters.test.ts`: add a test passing the namespace option;
  update existing tests to still pass with and without it.

Test:
- Manual: on a kind cluster with `ml` + `default` namespaces,
  create a project bound to `ml`, open its workflow, drag a Scale
  node, see `reco-pod` in the Service dropdown (no typing), save,
  verify `workflow_spec.steps[0].input` in the DB has
  `namespace: "ml"` and `deployment_name: "reco-pod"`.
- Toggle the project's Playbooks enabled off, trigger an incident,
  verify no Temporal workflow starts but an incident appears in the
  /incidents list.

After completing:
1. PROGRESS.md — mark 11.8d done, retire the "UI: no Namespace field"
   follow-up from the 2026-04-15 list.
2. MANUAL_TESTING.md §11 — refresh the project-create + workflow-edit
   screenshots + text.
3. Drop `automend-rbac.yaml` + `slack-patch.yaml` + `rehash_spec.py`
   from the repo root; they're gitignored but the files linger.
```

#### Task 11.8e — Publish transition auto-repoints trigger rules

```
Problem: Publishing a new PlaybookVersion does NOT auto-update
`trigger_rules.playbook_version_id`. New incidents keep executing the
stale version. Walk-around today is manual SQL UPDATE after publish
(see MANUAL_TESTING.md §15 "TriggerRule still points at the old
version").

Scope:
- `app/services/playbook_service.py` (or wherever the status-transition
  lives — check `app/api/routes_playbooks.py::patch_version_status`):
  - When a version transitions to `published`, inside the same DB
    transaction: `UPDATE trigger_rules SET playbook_version_id = :new
    WHERE playbook_id = :pid AND is_active = true`.
  - Emit an `incident_events`-style audit row or a log line naming the
    previous version → new version, plus the count of rules updated.
- Additive, not breaking: rules pointing at a different playbook are
  not touched. Only active rules for THIS playbook get repointed.
- New API test `test_api/test_playbooks.py::test_publish_repoints_active_rules`:
  create a playbook, publish v1, register a trigger rule, publish v2,
  assert the rule now points at v2.

Test:
- Manual: repeat the 2026-04-15 flow — create a workflow, publish,
  save a change, re-publish — without the hand-written SQL UPDATE.
  Trigger an incident and confirm the newest spec executes.

After completing:
1. PROGRESS.md — mark 11.8e done, retire the "Trigger rules don't
   auto-repoint" follow-up.
2. MANUAL_TESTING.md §15 — remove the "TriggerRule still points at
   old version" walk-around entry.
```

#### Task 11.8f — Architect prompt preview button

```
Problem: The chat panel in the workflow builder sends the user's
intent to `/api/design/generate_workflow` which internally assembles
a system prompt + RAG-selected tools + examples + DSL schema before
calling the LLM. Operators + debuggers can't see what the LLM
actually saw — critical for understanding bad generations and for
tuning RAG retrieval.

Scope:
- Refactor `app/services/architect.py`:
  - Extract the prompt-building path (currently inside
    `ArchitectClient.generate_workflow`) into a pure
    `async def build_prompt(intent: str, target_incident_types:
    list[str] | None = None) -> dict`. Returns
    `{system_prompt: str, user_message: str, selected_tools:
    list[Tool], example_playbooks: list[dict]}`.
  - `generate_workflow` becomes: call `build_prompt`, then the
    provider-specific `_call_anthropic(...)` or `_call_local(...)`,
    then parse the response. No behavior change for the existing
    happy path.
- New route `POST /api/design/preview_prompt`:
  - Auth: editor+.
  - Body: `{intent: str, target_incident_types?: list[str]}`.
  - Returns the `build_prompt(...)` result directly. NO LLM call.
- `src/lib/api.ts`: `api.design.previewPrompt(intent, targetIncidentTypes?)`.
- `src/app/workflow/[id]/page.tsx` chat panel:
  - Add a secondary button "Preview prompt" next to "Generate".
  - On click, call `api.design.previewPrompt(userIntent)` and open a
    modal (`src/components/PromptPreviewModal.tsx`) with four
    collapsible sections: System prompt, Selected tools (name +
    display_name + description + input_schema), Example playbooks,
    Final user message. Copy-to-clipboard button on each section.

Test:
- New test `test_services/test_architect.py::test_build_prompt_is_pure`
  — `build_prompt` called twice with same args produces identical
  output (deterministic; RAG retrieval is stable-sorted by score).
- New test `test_api/test_design.py::test_preview_prompt_no_llm_call`
  — patch `ArchitectClient._call_anthropic` to raise; endpoint still
  returns 200.

After completing:
1. PROGRESS.md — mark 11.8f done.
2. CLAUDE.md §6 API Routes — add Design / `POST /preview_prompt`.
3. DECISIONS.md — decision on whether to eagerly deduplicate this
   with the generate_workflow route (we chose separate routes; one
   clean endpoint + one LLM-bound endpoint) or fold them via a flag.
```

---

## Phase 12: Deploy to GCP (Terraform + managed services)

> **Context:** Phase 11 shipped a Helm chart that runs AutoMend fully in-cluster
> with optional subcharts for Postgres/Redis/Temporal. Phase 12 stands up the
> production target on GCP: a GKE cluster with a real node pool, Cloud SQL for
> Postgres (with pgvector), Memorystore for Redis, Artifact Registry for images,
> and the IAM plumbing to let GKE workloads talk to managed services via Workload
> Identity. The same Helm chart installs unchanged — only the values file
> differs (`values-gcp.yaml` turns subcharts off and points at the Terraform
> outputs).
>
> **Why managed services not subcharts in production:** Postgres + Redis on
> StatefulSets work for dev but lose you backups, PITR, HA failover, zone
> redundancy, security patches, and maintenance windows — all things Cloud SQL
> and Memorystore give you for free. The operational tax of running
> stateful workloads on Kubernetes isn't worth it below a certain scale.

### Task 12.1 — Terraform root + remote state

```
Stand up the Terraform workspace.

Build:
- `infra/terraform/` directory:
    * `versions.tf` — required providers (google, google-beta, kubernetes, helm),
      pinned versions
    * `backend.tf` — GCS backend for remote state (bucket name via variable,
      versioning enabled)
    * `variables.tf` — project_id, region, zone, env (dev/staging/prod)
    * `providers.tf` — google + google-beta with project + region from vars
    * `outputs.tf` — empty for now
    * `main.tf` — stub root module that just calls the submodules as they land
    * `.terraform-version` — tfenv pin

Test:
- `gcloud auth application-default login`
- `terraform -chdir=infra/terraform init -backend-config="bucket=<your-bucket>"`
- `terraform -chdir=infra/terraform plan` — reports "No changes".

After completing this task:
1. terraform init succeeds
2. Create the GCS state bucket manually with versioning on (one-off, before
   init — chicken/egg)
3. Update PROGRESS.md + DECISIONS.md: why GCS for state, pinning strategy
```

### Task 12.2 — GKE module + Workload Identity

```
Provision the Kubernetes cluster. One regional cluster, one node pool, Workload
Identity enabled.

Build:
- `infra/terraform/modules/gke/` — inputs: project, region, cluster_name,
  node_count, machine_type, network, subnetwork. Outputs: cluster_endpoint,
  ca_certificate, cluster_ca, workload_identity_pool.
- Resource: google_container_cluster with:
    * workload_identity_config
    * release_channel: REGULAR
    * network_policy (enabled, provider CALICO)
    * private_cluster_config (private nodes, public endpoint with authorized
      networks for now — tighten in a follow-up)
    * enable_shielded_nodes: true
- Resource: google_container_node_pool — single pool of e2-standard-4 × 2
  nodes for now.
- `kubernetes` + `helm` provider configured from the cluster outputs so later
  modules can drive k8s resources from Terraform (not Helm-the-chart itself —
  Helm manages the app).

Test:
- `terraform apply` provisions the cluster (~8 minutes)
- `gcloud container clusters get-credentials automend-dev --region us-central1`
- `kubectl get nodes` shows 2 nodes Ready.

After completing this task:
1. Cluster accessible via kubectl
2. Update PROGRESS.md
3. Record in DECISIONS.md: regional vs zonal, private cluster decision,
   Workload Identity choice
```

### Task 12.3 — Cloud SQL + pgvector

```
Provision managed Postgres and enable pgvector.

Build:
- `infra/terraform/modules/cloud-sql/` — inputs: project, region, instance name,
  tier, db name, app service account email. Outputs: connection_name,
  private_ip, db_password_secret_id.
- Resources:
    * google_sql_database_instance — Postgres 15, tier db-custom-2-7680 for dev.
      PRIVATE IP only (no public IP), attached to the VPC created in 12.2.
      Backup config: daily, point-in-time recovery enabled.
    * google_sql_database — the automend database
    * google_sql_user — app user, password from google_secret_manager_secret
    * google_sql_database_flags — enable pg_stat_statements, etc.
- Enable pgvector via a Job in the Helm chart (post-install hook): connects to
  the instance and runs CREATE EXTENSION vector. Cloud SQL supports pgvector
  as of 2024.
- Grant the GKE service account (from 12.2) `cloudsql.client` and `cloudsql.instanceUser`.
  Bind via Workload Identity.

Test:
- `terraform apply` provisions the instance (~10 minutes)
- From a pod in the GKE cluster: `psql -h <private-ip> -U automend -d automend`
  succeeds using a password pulled from Secret Manager via the CSI driver.
- CREATE EXTENSION vector; — succeeds.

After completing this task:
1. Instance reachable from GKE, pgvector enabled
2. Update PROGRESS.md with the cloud-sql module inputs/outputs
3. Record in DECISIONS.md: private IP only, secret-manager for passwords,
   pgvector init strategy
```

### Task 12.4 — Memorystore Redis

```
Provision managed Redis.

Build:
- `infra/terraform/modules/memorystore/` — inputs: project, region, tier,
  memory_size_gb, network. Outputs: host, port, auth_secret_id.
- Resource: google_redis_instance — STANDARD_HA tier (1 read replica) for dev,
  auth_enabled = true, transit_encryption_mode = SERVER_AUTHENTICATION (mTLS).
  Version 7.x.
- Auth string stored in Secret Manager, exposed to pods via the same CSI path
  pattern as the Cloud SQL password.

Test:
- `terraform apply` (~8 minutes)
- From a pod: `redis-cli -h <host> -a <password> --tls PING` → PONG.
- The existing correlation-worker / window-worker connect successfully when
  Helm values point AUTOMEND_REDIS_HOST at the instance.

After completing this task:
1. Instance reachable from GKE
2. Update PROGRESS.md
3. Record in DECISIONS.md: STANDARD_HA vs BASIC, TLS-in-transit decision
```

### Task 12.5 — Artifact Registry + CI/CD pipeline

```
Container registry + build-push automation.

Build:
- `infra/terraform/modules/artifact-registry/` — creates a Docker repo in the
  project's region, grants the GKE node SA `artifactregistry.reader`.
- GitHub Actions workflow `.github/workflows/build-and-deploy.yaml`:
    * On push to main:
        - Build all 5 Docker images (api, worker, temporal-worker, classifier, frontend)
        - Tag with git SHA + `latest`
        - Push to Artifact Registry
        - Run `helm upgrade automend ./infra/helm/automend -f values-gcp.yaml
          --set global.imageTag=$GITHUB_SHA` against the GKE cluster
    * On PR: build images (don't push), run `helm template` + chart tests
- Authentication via Workload Identity Federation (no service account keys in
  GitHub secrets).

Test:
- A manually-triggered workflow run builds + pushes all 5 images.
- `gcloud artifacts docker images list` shows them.
- A helm upgrade against the cluster rolls out the new image tags.

After completing this task:
1. CI pipeline green end-to-end
2. Update PROGRESS.md
3. Record in DECISIONS.md: WIF vs SA keys, image-per-component vs monolithic
```

### Task 12.6 — values-gcp.yaml + External Secrets Operator

```
Wire the Helm chart to GCP managed services + secrets.

Build:
- Install External Secrets Operator via a Terraform `helm_release` (or a
  separate bootstrap step).
- `infra/helm/automend/values-gcp.yaml`:
    * postgres.enabled: false, external.postgresHost: <from Terraform output>
    * redis.enabled: false, external.redisHost: <from Terraform output>
    * temporal.enabled: false — decide later: Temporal Cloud vs self-hosted on
      GKE. For now, keep subchart on; revisit in Phase 13.
    * secrets.create: false, secrets.existingSecret: "automend-secrets"
    * ingress.className: "gce" (GCLB), ingress.host: "automend.<your-domain>"
    * resources scaled up to production sizes
- `templates/external-secret.yaml` (new chart template, conditional on
  ExternalSecrets enabled) — references the Secret Manager secrets from 12.3
  and 12.4 and materialises them into a k8s Secret in the same namespace.
- Document Workload Identity bindings for the ESO service account.

Test:
- `helm install automend ./infra/helm/automend -f values-gcp.yaml` against the
  GKE cluster
- Pods come up Ready; Postgres queries land on Cloud SQL (check via CloudSQL
  logs); Redis commands land on Memorystore (check via Memorystore metrics).
- DELETE a managed secret → ExternalSecret reconciles within 60s.

After completing this task:
1. Full stack running on GCP against managed services
2. Update PROGRESS.md + CLAUDE.md (add a "Running on GCP" subsection to
   Getting Started)
3. Record in DECISIONS.md: ESO vs CSI driver, GCLB vs NGINX Ingress
```

### Task 12.7 — DEPLOY_GCP.md

```
Write a standalone deploy runbook for first-time setup.

Build:
- `DEPLOY_GCP.md` at repo root covering:
    1. Prereqs: GCP project, billing enabled, gcloud CLI, tfenv, Terraform,
       helm, kubectl
    2. One-time bootstrap: create the state bucket, enable APIs
       (container, sqladmin, redis, artifactregistry, secretmanager, iam)
    3. Terraform apply order: root → gke → cloud-sql → memorystore →
       artifact-registry. Screen-capture or checklist for each.
    4. Build + push images (either via CI or locally with `gcloud auth
       configure-docker`)
    5. `helm install automend ./infra/helm/automend -f values-gcp.yaml --set
       global.imageTag=...`
    6. Smoke-test checklist: /health, login, create a project, view incidents
    7. Rollback plan (helm rollback + terraform state rollback if needed)
    8. Cost estimate (Cloud SQL db-custom-2-7680 + Memorystore STANDARD_HA 1GB
       + GKE 2 × e2-standard-4 ≈ $X/month)
    9. Teardown: helm uninstall → terraform destroy in reverse order

Test:
- A teammate with no AutoMend context follows DEPLOY_GCP.md end-to-end against
  a fresh GCP project. All commands work; UI is reachable at the Ingress host
  within ~30 minutes.

After completing this task:
1. DEPLOY_GCP.md validated by a cold reader
2. Mark Phase 12 complete in PROGRESS.md
3. Final CLAUDE.md update pointing at both MANUAL_TESTING.md (local) and
   DEPLOY_GCP.md (GCP)
```

---

## Appendix: Emergency Prompts

If Claude Code gets stuck or confused, use these recovery prompts:

### "Where were we?"
```
Read PROGRESS.md and CLAUDE.md. Tell me:
1. What task we're on
2. What's been completed
3. What's next
4. Are there any blockers noted?
```

### "Something broke"
```
Run the full test suite: cd backend && pytest tests/ -v --tb=long
Read the failures. Fix them one by one. After fixing, re-run the full suite.
Update PROGRESS.md with what broke and how you fixed it.
```

### "Start fresh on this task"
```
Read PROGRESS.md for the current task. Read backend_architecture.md for the relevant section.
Delete any partially-written code for this task and start over.
Run existing tests first to make sure nothing else is broken.
```

### "Context is getting long"
```
Read CLAUDE.md and PROGRESS.md to restore your understanding of the project.
The source of truth for what to build is backend_architecture.md.
The source of truth for what's done is PROGRESS.md.
Continue from the next unchecked task.
```
