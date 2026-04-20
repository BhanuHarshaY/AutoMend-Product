# AutoMend — Local Manual Test Walkthrough

This guide stands up a tiny Kubernetes cluster on your laptop, deploys a deliberately-crashing ML service, and walks through the AutoMend UI end-to-end. Everything runs in the cluster via a single `helm install` — **no Python-on-host, no Docker Compose, no six terminals**. End-to-end in ~15 minutes on a clean machine.

**What you won't need:**
- Trained ML models (the stub classifier handles OOM/crash/GPU patterns out of the box; the AI chat panel is optional)
- A GPU
- Anthropic or OpenAI API keys (UI works without them; the chat panel will return 502, which is fine for this test)

**Target cluster:** `kind` (one node, Docker Desktop).

---

## 1. Prerequisites

One-time installs:

| Tool | Why | Install (Windows) |
|------|-----|-------------------|
| Docker Desktop 24+ | Runs kind | https://docker.com/products/docker-desktop |
| `kind` | Kubernetes in Docker | `winget install Kubernetes.kind` |
| `kubectl` | Talk to the cluster | `winget install Kubernetes.kubectl` |
| `helm` | Install the chart | `winget install Helm.Helm` |

Verify:
```powershell
docker --version       # 24+
kind --version
kubectl version --client
helm version --short
```

---

## 2. Architecture

```
┌──────────────────────────── kind cluster ────────────────────────────┐
│                                                                      │
│  ┌─ namespace: automend ────────────────────────────────────────┐    │
│  │  Deployments:                                                │    │
│  │    api, classifier, frontend                                 │    │
│  │    window-worker, correlation-worker, temporal-worker        │    │
│  │    postgres (pgvector), redis, temporal (auto-setup SQLite)  │    │
│  │  post-install Job: alembic upgrade head + seed tools/rules   │    │
│  │  Services: api:8000, classifier:8001, frontend:3000          │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                        ▲                                             │
│          cluster DNS   │                                             │
│                        │                                             │
│  ┌─ namespace: logging ┴────────────────────────────────────────┐    │
│  │  Fluent Bit DaemonSet — tails /var/log/containers/ and POSTs │    │
│  │    http://automend-api.automend.svc.cluster.local:8000       │    │
│  │          /api/webhooks/ingest/otlp                           │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                        ▲                                             │
│         log stdout     │                                             │
│                        │                                             │
│  ┌─ namespace: ml ─────┴────────────────────────────────────────┐    │
│  │  reco-pod — emits OOM/CUDA-style log lines every 2s          │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

All IPC stays inside the cluster — no `host.docker.internal`, no host-side processes.

---

## 3. Create the kind cluster

```powershell
kind create cluster --name automend-demo
kubectl cluster-info --context kind-automend-demo
kubectl get nodes    # 1 node, STATUS=Ready
```

First-time cluster creation takes ~30-60 seconds.

---

## 4. Build and load the 4 images

From the repo root:

```powershell
docker build -t automend/api:dev             -f infra/dockerfiles/Dockerfile.api             backend
docker build -t automend/worker:dev          -f infra/dockerfiles/Dockerfile.worker          backend
docker build -t automend/temporal-worker:dev -f infra/dockerfiles/Dockerfile.temporal-worker backend
docker build -t automend/frontend:dev        -f infra/dockerfiles/Dockerfile.frontend        .
```

Builds share layers — expect the first to take ~3 min (downloads Python + slim base), the rest ~30s each. Frontend is ~1 min due to `npm ci` + `next build`.

Load each image into kind (so `imagePullPolicy: Never` in values-local.yaml can find them):

```powershell
kind load docker-image automend/api:dev             --name automend-demo
kind load docker-image automend/worker:dev          --name automend-demo
kind load docker-image automend/temporal-worker:dev --name automend-demo
kind load docker-image automend/frontend:dev        --name automend-demo
```

Also pre-load the dev-dep images so the cluster doesn't hit Docker Hub rate limits mid-install.

**Use `docker save --platform` + `kind load image-archive`, not `kind load docker-image`.** `kind load docker-image` invokes `ctr images import --all-platforms --digests`, which demands every platform entry in a multi-arch manifest list have its layers physically present locally. Docker Desktop's containerd image store keeps the full manifest list after a pull but only the current platform's layers — the other platforms' digests are missing, and kind refuses to import (`ERROR: failed to load image: content digest ... not found`). The `image-archive` path + `docker save --platform` (Docker 25+) flattens the manifest list to a single platform before saving, sidestepping the issue.

The `--platform` flag below matches kind-on-Docker-Desktop-Windows; swap to `linux/arm64` on Apple Silicon.

```powershell
docker pull pgvector/pgvector:pg16
docker pull redis:7-alpine
docker pull temporalio/auto-setup:1.24.2
docker pull curlimages/curl:8.7.1

docker save --platform linux/amd64 pgvector/pgvector:pg16       -o pgvector.tar
docker save --platform linux/amd64 redis:7-alpine               -o redis.tar
docker save --platform linux/amd64 temporalio/auto-setup:1.24.2 -o temporal.tar
docker save --platform linux/amd64 curlimages/curl:8.7.1        -o curl.tar

kind load image-archive pgvector.tar --name automend-demo
kind load image-archive redis.tar    --name automend-demo
kind load image-archive temporal.tar --name automend-demo
kind load image-archive curl.tar     --name automend-demo

Remove-Item pgvector.tar, redis.tar, temporal.tar, curl.tar
```

**Docker < 25 (no `--platform` on save):** disable Docker Desktop's containerd image store via Settings → General → uncheck "Use containerd for pulling and storing images" → restart, then drop the `--platform` flag from the saves. The legacy image store only holds one platform's layers, so the tarball won't reference missing digests.

---

## 5. Install AutoMend

```powershell
kubectl create namespace automend
helm install automend ./infra/helm/automend `
  --namespace automend `
  --values ./infra/helm/automend/values-local.yaml `
  --wait --timeout 5m
```

`--wait --timeout 5m` blocks until all pods are Ready AND the `alembic upgrade head` + seed Job finishes. Typical duration: 60-90 seconds after the images are loaded.

If the install stalls, check pod state in another terminal:
```powershell
kubectl -n automend get pods -w
```

---

## 6. Verify the install

```powershell
# All 10 pods should be Running + Ready
kubectl -n automend get pods

# Expected:
#   automend-api-xxx                    1/1 Running
#   automend-classifier-xxx             1/1 Running
#   automend-correlation-worker-xxx     1/1 Running
#   automend-frontend-xxx               1/1 Running
#   automend-postgres-xxx               1/1 Running
#   automend-redis-xxx                  1/1 Running
#   automend-temporal-xxx               1/1 Running
#   automend-temporal-worker-xxx        1/1 Running
#   automend-window-worker-xxx          1/1 Running
# (migrations Job will be gone — it completes and is reaped)
```

Run the chart's smoke test to confirm the HTTP services are reachable from within the cluster:

```powershell
helm test automend --namespace automend
```

Expected: one Pod runs curl against `/health` on api + classifier and `/` on frontend, all succeed, Pod is reaped.

**Bootstrap an admin user** so the UI has a login:

```powershell
kubectl exec -n automend deployment/automend-api -- `
  env ADMIN_EMAIL=admin@local ADMIN_PASSWORD=admin123 `
  python scripts/bootstrap_admin.py
# Output: "Created admin user admin@local."
```

`bootstrap_admin.py` is idempotent — re-running it is a no-op if the user already exists.

---

## 7. Access the UI via port-forward

The chart renders an Ingress (`host: automend.local`) but `kind` has no Ingress controller by default, so port-forward is the path of least resistance:

```powershell
# Terminal A — frontend
kubectl -n automend port-forward svc/automend-frontend 3000:3000

# Terminal B — API (only needed if you want to curl it directly)
kubectl -n automend port-forward svc/automend-api 8000:8000
```

Open http://localhost:3000 in a browser. The AuthGuard redirects to `/login`; sign in with `admin@local` / `admin123`. Empty Projects dashboard appears.

**Optional — nginx-ingress for "pretty" URLs:** install nginx-ingress in kind, then AutoMend becomes reachable at http://automend.local:
```powershell
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.2/deploy/static/provider/kind/deploy.yaml
kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=180s
# Add to C:\Windows\System32\drivers\etc\hosts:
#   127.0.0.1  automend.local
```
Skip this if port-forward is fine.

---

## 8. Deploy a deliberately-crashing ML service

Save as `crashing-ml.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: ml
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: reco-pod
  namespace: ml
spec:
  replicas: 1
  selector:
    matchLabels: { app: reco-pod }
  template:
    metadata:
      labels: { app: reco-pod }
    spec:
      containers:
      - name: app
        image: python:3.11-slim
        # Emits realistic OOM/CUDA log lines every 2s. Doesn't actually OOM —
        # we don't want k8s restarting the pod constantly.
        command: ["python","-u","-c"]
        args:
        - |
          import time, random
          patterns = [
            "CUDA error: out of memory",
            "Failed to allocate 4096MB on GPU 2",
            "cgroup memory limit exceeded",
            "pod OOMKilled",
            "ERROR RuntimeError: CUDA out of memory",
          ]
          while True:
            line = random.choice(patterns)
            print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ')} ERROR {line}", flush=True)
            time.sleep(2)
        resources:
          requests: { memory: "32Mi", cpu: "10m" }
          limits:   { memory: "64Mi", cpu: "50m" }
```

Apply:

```powershell
kubectl apply -f crashing-ml.yaml
kubectl -n ml rollout status deployment/reco-pod
kubectl -n ml logs -l app=reco-pod --tail=5
# Should see lines like "2026-04-14T19:03:22Z ERROR CUDA error: out of memory"
```

---

## 9. Install Fluent Bit to ship logs into AutoMend

Save as `fluentbit.yaml`. Note the webhook URL is the **cluster-internal** service name now — no `host.docker.internal`.

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: logging
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: fluent-bit-config
  namespace: logging
data:
  fluent-bit.conf: |
    [SERVICE]
        Flush        2
        Log_Level    info
        Parsers_File parsers.conf
    [INPUT]
        Name          tail
        Path          /var/log/containers/reco-pod*_ml_*.log
        Parser        cri
        Tag           kube.*
        Read_from_Head false
    [OUTPUT]
        # Plain http output with Format json (array of records). The api
        # endpoint accepts BOTH OTLP HTTP/JSON AND flat JSON shipped this
        # way. Don't use Fluent Bit's `opentelemetry` output — it sends
        # OTLP/protobuf, which the endpoint can't parse.
        Name           http
        Match          *
        Host           automend-api.automend.svc.cluster.local
        Port           8000
        URI            /api/webhooks/ingest/otlp
        Format         json
        json_date_key  timestamp
        json_date_format iso8601
        tls            off
  parsers.conf: |
    [PARSER]
        Name         cri
        Format       regex
        Regex        ^(?<time>[^ ]+) (?<stream>stdout|stderr) [^ ]* (?<log>.*)$
        Time_Key     time
        Time_Format  %Y-%m-%dT%H:%M:%S.%L%z
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: fluent-bit
  namespace: logging
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: { name: fluent-bit-read }
rules:
- apiGroups: [""]
  resources: [pods, namespaces]
  verbs: [get, list, watch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: { name: fluent-bit-read }
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: fluent-bit-read
subjects:
- kind: ServiceAccount
  name: fluent-bit
  namespace: logging
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: fluent-bit
  namespace: logging
spec:
  selector: { matchLabels: { app: fluent-bit } }
  template:
    metadata: { labels: { app: fluent-bit } }
    spec:
      serviceAccountName: fluent-bit
      containers:
      - name: fluent-bit
        image: fluent/fluent-bit:3.0
        volumeMounts:
        - { name: varlog, mountPath: /var/log }
        - { name: config, mountPath: /fluent-bit/etc/ }
      volumes:
      - name: varlog
        hostPath: { path: /var/log }
      - name: config
        configMap: { name: fluent-bit-config }
```

Apply:
```powershell
kubectl apply -f fluentbit.yaml
kubectl -n logging rollout status ds/fluent-bit
kubectl -n logging logs -l app=fluent-bit --tail=20
# Look for "[ok] starting ingestion" style messages.
```

Within ~15 seconds you should see Fluent Bit POSTing to the webhook. Verify AutoMend is receiving them:

```powershell
kubectl -n automend logs -l app.kubernetes.io/component=api --tail=30 | Select-String "ingest/otlp"
# Should see incoming POSTs
```

Or check the Redis stream directly:

```powershell
kubectl -n automend exec deployment/automend-redis -- redis-cli XLEN normalized_logs
# Should grow as Fluent Bit ships logs
```

---

## 10. Watch the pipeline light up

The window worker batches logs into 5-minute windows by default. For a faster feedback loop, shrink the window to 30s via a helm upgrade:

```powershell
helm upgrade automend ./infra/helm/automend `
  --namespace automend `
  --values ./infra/helm/automend/values-local.yaml `
  --set config.windowSizeSeconds=30 `
  --wait
```

Watch the pipeline in three terminals:

```powershell
# Terminal 1 — window worker
kubectl -n automend logs -f -l app.kubernetes.io/component=window-worker

# Terminal 2 — correlation worker
kubectl -n automend logs -f -l app.kubernetes.io/component=correlation-worker

# Terminal 3 — API (for webhook + WebSocket broadcasts)
kubectl -n automend logs -f -l app.kubernetes.io/component=api
```

Expected sequence within ~30-60 seconds:

1. **API logs** — `POST /api/webhooks/ingest/otlp` repeated.
2. **Window worker** — `Closing window prod/ml/reco-pod... classifier returned label=failure.memory confidence=0.7` (pattern match on OOM lines).
3. **Correlation worker** — `New incident created: incident.failure.memory / prod/ml/reco-pod`.

---

## 11. UI walkthrough

Open http://localhost:3000 (the frontend port-forward from §7) and log in.

### Projects dashboard (Task 11.8d)
1. Click **New Project**. The dialog fetches live namespaces from the cluster (`GET /api/clusters/default/namespaces`) and renders a dropdown. Namespaces already owned by another project are disabled; if every namespace is taken the dialog tells you to `kubectl create namespace <name>` first.
2. Pick `ml` from the Namespace dropdown. Leave Display Name blank (it defaults to the namespace) or type "ML Platform". Add a description → **Create Project**.
3. The card shows the project's namespace (`ns ml`) + a green **Enabled** pill. Click the pill once to flip to **Disabled** (remediation is paused across that namespace — incidents still get created for visibility, no Temporal workflow fires). Click again to re-enable. This is the kill switch from Task 11.8c.
4. Filter tabs across the top: `all / enabled / disabled`. Search box filters by name **or** namespace.
5. Click **View Workflows** → **Add New Workflow**. You're taken to the React Flow builder, and the workflow is tagged with the project's namespace automatically.

### Workflow builder
1. Drag a **Trigger** node onto the canvas. Click → set Metric = `memory`, Threshold = `0.9`, Window = `5min`.
2. Drag **Scale Deployment**. Click the node → the config panel shows `Namespace: ml` (read from the project binding) and the Service field is a dropdown populated from `GET /api/clusters/default/namespaces/ml/resources?kind=deployment`. Pick `reco-pod`. Set Replicas = `3`, Direction = `up`.
3. Drag **Send Alert** → set Channel = `#mlops-alerts`.
4. Connect with edges (drag from a node's right handle to the next node's left handle).
5. Click **Save**. The badge shows `draft`. The saved spec's scale step has `deployment_name: "reco-pod"` and `namespace: "ml"` filled in by the adapter — no hand-patching required.
6. Click **Deploy**. The button walks the state machine (draft → validated → approved → published). Badge ends at `published`.

The chat panel ("Generative Architect") needs an Anthropic API key to work. Without one it returns 502 — the rest of the UI still functions.

### Incidents dashboard
1. Click **Incidents** in the top nav.
2. You should see an incident row: type `incident.failure.memory`, severity `high`, status `open`, entity `prod / reco-pod`, recent timestamp. The "Live" pill should be green.
3. Click the row → detail page with:
   - Summary card
   - Entity panel
   - Evidence panel (matched log lines)
   - Remediation workflow panel (populated once a trigger rule is wired — §12)
   - Event timeline
4. **Acknowledge** → status flips. **Resolve** → resolved_at fills in.

### Real-time test
1. Leave the Incidents page open.
2. Delete and recreate the crashing pod:
   ```powershell
   kubectl -n ml delete pod -l app=reco-pod
   ```
3. Within ~30-60 seconds a new incident appears in the list **without a page refresh** (WebSocket push via Redis Pub/Sub).

---

## 12. (Optional) Trigger rule so a Temporal workflow runs

To see an actual Temporal workflow fire (rather than just "no_playbook_matched"), bind your published playbook to the `incident.failure.memory` incident type.

After publishing the workflow in §11, grab the `playbook_version_id` from the UI (hit `/api/playbooks` via the API port-forward), then:

```powershell
$TOKEN = (curl -Method POST http://localhost:8000/api/auth/login `
  -Headers @{"Content-Type"="application/json"} `
  -Body '{"email":"admin@local","password":"admin123"}').access_token

curl -Method POST http://localhost:8000/api/rules/trigger-rules `
  -Headers @{"Authorization"="Bearer $TOKEN"; "Content-Type"="application/json"} `
  -Body '{
    "incident_type": "incident.failure.memory",
    "playbook_version_id": "<published-version-uuid>",
    "priority": 100
  }'
```

Next incident fires → correlation worker starts a Temporal workflow → the remediation panel on the incident detail page lights up with a workflow ID.

The Temporal UI shipped inside the `temporalio/auto-setup` image isn't exposed. For visibility, temporary port-forward:
```powershell
kubectl -n automend port-forward deploy/automend-temporal 8080:8080
# http://localhost:8080 shows the Temporal web UI
```

---

## 13. Tear down

```powershell
helm uninstall automend -n automend
kubectl delete namespace automend ml logging
kind delete cluster --name automend-demo
```

---

## 14. Advanced — host-based dev loop for faster iteration

If you're hacking on backend code, the helm flow requires `docker build && kind load && helm upgrade` per change, which gets old. For tight inner-loop iteration, run backend processes on the host against the kind cluster's Postgres/Redis/Temporal via port-forwards.

1. Bring up just the infra (no AutoMend app pods):
   ```powershell
   cd infra
   docker compose -f docker-compose.infra.yml up -d
   cd ..
   ```
2. Port-forward is unnecessary — infra Docker Compose exposes Postgres on 5432, Redis on 6379, Temporal on 7233 directly.
3. Install deps: `pip install -e ".[dev]"` in backend/, `npm install` at repo root.
4. Run each process in its own terminal (6 total):
   ```bash
   # A — API
   uvicorn main_api:app --reload --host 0.0.0.0 --port 8000
   # B — classifier
   python -m app.services.classifier_server
   # C — window worker
   AUTOMEND_WINDOW_SIZE_SECONDS=30 python main_window_worker.py
   # D — correlation worker
   python main_correlation_worker.py
   # E — Temporal worker
   python main_temporal_worker.py
   # F — frontend
   cd .. && npm run dev
   ```
5. Alembic + seed data as one-off:
   ```bash
   alembic upgrade head
   python scripts/seed_tools.py
   python scripts/seed_rules.py
   ADMIN_EMAIL=admin@local ADMIN_PASSWORD=admin123 python scripts/bootstrap_admin.py
   ```
6. Open http://localhost:3000.

This path trades the realistic deployment model for reload-on-save + Python debugger attachability. Use for backend code changes; go back to the helm flow for integration testing, trigger rule testing, and anything that touches the k8s manifests.

---

## 15. Troubleshooting

A lot of the issues below are first-time kind + helm gotchas that were discovered walking through this guide. Each entry lists the **symptom** exactly as you'll see it in your terminal, then the **fix**.

### Image loading

**`kind load docker-image` fails with `content digest sha256:... not found`**
Symptom: `ctr import --all-platforms --digests ...` rejects the image. Root cause: Docker Desktop's containerd image store keeps the full multi-arch manifest list locally, but only the current platform's layers. kind demands all platforms' digests. Fix: use `docker save --platform` (Docker 25+) + `kind load image-archive`:
```powershell
docker save --platform linux/amd64 <image>:<tag> -o img.tar
kind load image-archive img.tar --name automend-demo
Remove-Item img.tar
```
On Apple Silicon swap `linux/amd64` → `linux/arm64`. For Docker < 25, disable the containerd image store (Docker Desktop → Settings → General → uncheck "Use containerd for pulling and storing images").

**Pod `ImagePullBackOff` or `ErrImageNeverPull`**
Fix: load the image into kind (command above), then delete the pod so it respawns:
```powershell
kubectl -n automend delete pod -l app.kubernetes.io/component=<component>
```

### Pod security constraints

**`container has runAsNonRoot and image will run as root`**
Python base images (`python:3.11-slim`) have `USER root`. The chart sets `podSecurityContext.runAsNonRoot: true` but **without** `runAsUser` kubelet has no UID to verify. Fixed permanently in `values.yaml` — every backend component sets `runAsUser: 1000`. If you see this on a custom component, add the same three fields (`runAsUser`, `runAsGroup`, `fsGroup`) to its podSecurityContext.

**`container has runAsNonRoot and image has non-numeric user (nextjs), cannot verify user is non-root`**
The frontend Dockerfile does `USER nextjs` (username, not UID). kubelet can't resolve `nextjs` → UID without `/etc/passwd` lookup. Fixed permanently in `values.yaml`: frontend's podSecurityContext has `runAsUser: 1001` matching the UID created in `Dockerfile.frontend`.

### Environment variable collisions

**Backend pods crash with `api_port: Input should be a valid integer, unable to parse string as an integer [input_value='tcp://10.96.x.y:8000']`**
Kubernetes injects service env vars into every pod in the namespace — a Service named `automend-api` generates `AUTOMEND_API_PORT=tcp://10.96.x.y:8000`. Our backend's pydantic-settings reads `AUTOMEND_*` env vars and expects `api_port: int`. Fix (already applied): every Deployment's pod spec has `enableServiceLinks: false`. Use cluster DNS (`automend-api.automend.svc`) for service discovery instead.

### Database + migrations

**`helm install` / `helm upgrade` stuck on "waiting for migrations job"**
First: check the Job's pod:
```powershell
kubectl -n automend logs -l job-name=automend-migrations --all-containers=true --tail=100
```
Common causes in order of likelihood:
- **`ModuleNotFoundError: No module named 'app'`** — `alembic.ini` is missing `prepend_sys_path = .`. Fixed in this repo (commit the change, rebuild the api image).
- **`No module named 'psycopg2'`** — alembic's sync driver wasn't in `pyproject.toml`. Fixed: `psycopg2-binary>=2.9.0` is now listed alongside `asyncpg` (async driver for the app, sync driver for alembic).
- **Postgres PVC stuck `Pending`** — kind's default storage class isn't present. `kubectl get pvc -n automend` and `kubectl get sc`. Recreate kind with default config.
- **Schema half-created from a prior crash** — nuke the PVC and reinstall: `helm uninstall automend -n automend && kubectl -n automend delete pvc --all && helm install ...`.

**Postgres runs but `automend-temporal` stuck in `Init:0/1`**
Init container polls for `temporal` and `temporal_visibility` databases before starting auto-setup. On a fresh PVC these are created by `postgres-dev.yaml`'s initdb ConfigMap. On an **existing** PVC the init scripts are skipped — meaning if you enabled Temporal mid-stream, the DBs never got created. Fix: wipe the postgres PVC so initdb runs fresh:
```powershell
kubectl -n automend delete pod -l app.kubernetes.io/component=temporal
kubectl -n automend delete pvc automend-postgres
kubectl -n automend delete pod -l app.kubernetes.io/component=postgres
# PVC + initdb run again; auto-setup succeeds on next attempt
```

### Admin + authentication

**`bootstrap_admin.py` fails with `ModuleNotFoundError: No module named 'app'`**
On an old image before the sys.path shim was added to `backend/scripts/bootstrap_admin.py`. Workaround: pass `PYTHONPATH=/app` to the exec:
```powershell
kubectl exec -n automend deployment/automend-api -- `
  env ADMIN_EMAIL=admin@local ADMIN_PASSWORD=admin123 PYTHONPATH=/app `
  python scripts/bootstrap_admin.py
```
Rebuilding the api image picks up the permanent fix (the script now adds `/app` to sys.path itself).

**Bootstrap crashes with `error reading bcrypt version` + `password cannot be longer than 72 bytes`**
passlib 1.7.x is incompatible with bcrypt 4.1+ — it reads a `.__about__` attribute that's been removed, and its internal self-test trips on bcrypt's stricter length enforcement. Fixed in `pyproject.toml`: bcrypt is pinned to `>=4.0.0,<4.1.0`. If you still see this, the image is stale — rebuild.

**Login returns 500 but api logs only show `/health` requests**
The login request isn't reaching the api pod. That means the frontend's Next.js `rewrites()` is proxying to the wrong host. `next.config.js`'s `API_PROXY_TARGET` is **baked in at `next build` time** (Next.js standalone serializes rewrites to `routes-manifest.json` — runtime env changes don't apply). Fix: rebuild the frontend image with the correct build-arg:
```powershell
docker build -t automend/frontend:dev `
  --build-arg API_PROXY_TARGET=http://automend-api:8000 `
  -f infra/dockerfiles/Dockerfile.frontend .
```
The Dockerfile now defaults to `http://automend-api:8000` (matches the in-cluster service), so you only need `--build-arg` for non-default deployments.

### Log pipeline

**Fluent Bit posts succeed but `processed: 0`**
The `/api/webhooks/ingest/otlp` endpoint accepts both OTLP HTTP/JSON *and* Fluent Bit's flat `{log, timestamp, stream}` JSON shape. `processed: 0` means the request body didn't match either. The classic cause: Fluent Bit's `opentelemetry` output is sending OTLP/**protobuf** (`content-type: application/x-protobuf`), not JSON, so the endpoint's JSON parser fails silently. Fix: use Fluent Bit's `http` output with `Format json` (the current `fluentbit.yaml` has this).

**Fluent Bit shows `HTTP status=500 UnicodeDecodeError: 'utf-8' codec can't decode byte 0x85`**
Same root cause — protobuf bytes fed to a JSON parser. Switch Fluent Bit output from `opentelemetry` to `http` + `Format json`.

**Logs reach Redis but `entity_key=unknown`**
Fluent Bit's flat records don't carry k8s metadata by default. Fix: the `[FILTER] Name kubernetes` block in `fluentbit.yaml` enriches each record with `kubernetes.namespace_name`, `kubernetes.pod_name`, `kubernetes.container_name`, etc. The api webhook then flattens these into `namespace` / `pod` / `service` that `build_entity_key` looks for. If you're still seeing `unknown`, check the filter is applied:
```powershell
kubectl -n logging logs ds/fluent-bit --tail=20 | Select-String "kubernetes"
# Expect: "[filter:kubernetes:kubernetes.0] ..." and no 4xx errors from the API server
```
The Fluent Bit ServiceAccount needs `get/list/watch pods,namespaces` — the `ClusterRole fluent-bit-read` in `fluentbit.yaml` covers this.

**Incidents created with `entity_key=unknown`**
Stale pre-kubernetes-filter data closing out. Wait for the next window cycle (30s with `values-local.yaml`, 5min otherwise) — the next incident will have proper labels.

### Helm chart

**`helm test` reports `TEST SUITE: None`**
The test-health Pod wasn't packaged into the chart. Root cause was `tests/` in `.helmignore` matching both `tests/` (intended) and `templates/tests/` (unintended). Fixed: `.helmignore` now uses leading-slash absolute patterns (`/tests/`).

**`helm upgrade` reports release `STATUS: failed` but pods are Running**
A `post-upgrade` hook (migrations Job) failed; helm marks the release failed but doesn't roll back the other resources. Fix the Job's error (check logs — usually one of the "Database + migrations" issues above), then run `helm upgrade` again to clear the release status.

**`helm install --wait` times out at 5min but pods are converging**
Cold start of a kind cluster with 9 pods + a migrations Job can exceed 5 min if Docker Hub pulls are slow (you didn't pre-load dev-dep images). Either wait and run `helm upgrade` again (the release completes once the Job succeeds), or pre-load images per §4.

### Network / WebSocket

**Incidents page pill stays on "Connecting…"**
WebSocket at `/api/ws/incidents?token=...` isn't reaching the api. In the port-forward path it should Just Work (Next.js proxies WS the same way it proxies HTTP). Check the frontend logs for `ECONNREFUSED` — that'd indicate the API_PROXY_TARGET bake-in issue above.

**Fluent Bit can't resolve `automend-api.automend.svc.cluster.local`**
CoreDNS didn't start or got OOM-killed:
```powershell
kubectl -n kube-system get pods -l k8s-app=kube-dns
kubectl -n kube-system logs -l k8s-app=kube-dns --tail=30
```
Restart CoreDNS: `kubectl -n kube-system rollout restart deployment/coredns`.

### Workflow execution (Day-2 operability)

**Activity fails with `KeyError: 'deployment_name'` (or `'namespace'`) in temporal-worker logs**
The `workflow_spec` stored in Postgres uses the frontend's field labels (e.g. `service`, `replicas`) instead of the backend tool's `input_schema` keys (`deployment_name`, `namespace`, `replicas`). Either (a) the spec was saved by an old frontend build (before `INPUT_KEY_MAP` landed in `src/lib/adapters.ts`), or (b) the user left a required field blank in the config panel — empty strings are filtered out by `buildStepInput`, so an empty "Service" produces a spec with no `deployment_name` at all. Fix:
```powershell
# 1. Check what's actually in the spec
kubectl -n automend exec deploy/automend-postgres -- psql -U automend -d automend -c "SELECT pv.id, pv.workflow_spec->'steps'->0->'input' FROM playbook_versions pv JOIN trigger_rules tr ON tr.playbook_version_id=pv.id WHERE tr.is_active=true;"
```
If it shows `{"service": "..."}` — rebuild + reload the frontend image, hard-refresh the browser (Ctrl+Shift+R), open the workflow, fill all fields explicitly, save + deploy. If it shows `{"replicas": 3, ...}` with no `deployment_name` — the Service field was empty when saved; reopen, fill it, re-save.

**Activity fails with `403 Forbidden: ... cannot get resource "deployments"`**
The app ServiceAccount is missing RBAC in the target namespace. As of Task 11.8a the Helm chart ships RBAC via two values keys (`rbac.clusterWide` and `rbac.targetNamespaces`). `values-local.yaml` already sets both — if you're on a stock local install, verify:
```powershell
kubectl auth can-i patch deployments.apps/reco-pod --as=system:serviceaccount:automend:automend -n ml
# Expect: yes
kubectl get clusterrolebinding automend -o yaml
# Subject should be ServiceAccount automend/automend
```
If the answer is `no`, either you're running on an older chart version (upgrade the release) or you've overridden the rbac values. Re-enable via:
```powershell
helm upgrade automend ./infra/helm/automend `
  --namespace automend `
  --values ./infra/helm/automend/values-local.yaml `
  --set rbac.clusterWide=true `
  --set rbac.targetNamespaces="{ml,default}"
```
For a custom namespace list (e.g. `ml,payments,search`) use the same `--set rbac.targetNamespaces="{ml,payments,search}"` form — each entry becomes a Role + RoleBinding in that namespace. Cluster-wide grant is additive; leave `clusterWide=false` if you want a tighter scope.

**Activity fails with `403 Forbidden: ... in the namespace "default"` when the pod is actually in `ml`**
Legacy workflow spec from before Task 11.8d — the namespace was hardcoded to `"default"` by the old adapter. Fix: in the UI, open the workflow, make sure the parent project is bound to the correct namespace (create one via the new-project dialog if it isn't), then reselect the deployment from the Scale node's dropdown and re-save. The adapter fills in `namespace` from the project binding on the next save — no SQL needed.

**Workflow fails with `ValueError: Playbook checksum mismatch — spec may have been tampered with`**
Only reachable if `workflow_spec` got hand-edited via raw SQL (not via the UI's Save button, which always recomputes `spec_checksum`). Most common cause today: a prior session left a legacy SQL-patched row behind. Fix: open the workflow in the UI and re-save it — the new spec write recomputes the hash. If you need a bulk fix for multiple versions, write a small one-off Python script that selects each version, computes `sha256(json.dumps(spec, sort_keys=True))`, and UPDATEs `spec_checksum`. An in-repo `rehash_spec.py` helper existed during Task 11.8a–c; it was removed when Task 11.8d shipped the namespace picker and eliminated the routine need for SQL hand-edits.

**`TriggerRule` still points at the old version after publishing a new one**
Historical issue — fixed in Task 11.8e. `transition_version_status` now auto-repoints every active `trigger_rule` whose current target is a sibling version of the same playbook, inside the same DB transaction as the publish. If you see this symptom today on a pre-11.8e image, rebuild the api image; otherwise verify the API server log for the repoint INFO line and check `SELECT id, playbook_version_id FROM trigger_rules WHERE is_active=true` to see where each active rule points.

**`slack_notification_activity` fails with `httpx.LocalProtocolError: Illegal header value b'Bearer '`**
`AUTOMEND_SLACK_BOT_TOKEN` is empty. The activity builds `Authorization: Bearer ` and httpx rejects the malformed header before sending. Common cause: the Helm chart's Secret keys are NOT prefixed (`SLACK_BOT_TOKEN` not `AUTOMEND_SLACK_BOT_TOKEN`), while pydantic-settings reads the prefixed form. Fix — patch the secret with the prefixed key and restart the worker:
```powershell
# slack-patch.yaml — at repo root
# stringData:
#   AUTOMEND_SLACK_BOT_TOKEN: xoxb-your-real-token-here   # or xoxb-dummy for testing
kubectl -n automend patch secret automend-secrets --patch-file slack-patch.yaml
kubectl -n automend rollout restart deploy/automend-temporal-worker
```
A dummy token (e.g. `xoxb-dummy-for-manual-testing`) lets the header pass; the POST will get a 200 from `slack.com/api/chat.postMessage` with `ok: false` in the body, which httpx treats as success. Workflow completes cleanly. For real Slack delivery use a token from a bot with `chat:write` scope invited to the configured channel.

### Misc

**Chat panel returns 502**
No Anthropic API key. Either drag workflow nodes manually, or:
```powershell
helm upgrade automend ./infra/helm/automend `
  --namespace automend `
  --values ./infra/helm/automend/values-local.yaml `
  --set secrets.values.architectApiKey=sk-ant-... --wait
```
Or switch to the Qwen proxy (Phase 10.4): set `config.architectProvider=local` + point `config.architectApiBaseUrl` at the in-cluster proxy.

**"No playbook matched" in correlation-worker logs**
Trigger rule for the incident_type isn't registered (§12). The correlation worker creates the incident regardless; only the Temporal workflow part is gated on a matching rule.

**`helm upgrade` wipes a custom `--set` override**
Helm upgrades merge `--set` only at the CLI moment — subsequent upgrades without the flag revert to the values file. Bake persistent dev overrides into `values-local.yaml` instead. Example: `config.windowSizeSeconds: 30` is already baked in so you don't need the `--set` hack.

---

## What this doesn't cover

- **Real Kubernetes remediation** — the tool adapters in `app/temporal/activities.py` (scale_deployment etc.) need `KUBECONFIG` pointing at a cluster they can mutate. Wiring the Temporal worker pod to the kind cluster's API server is a follow-up.
- **Prometheus / Alertmanager alerts** — this guide uses log-based classification only. To exercise `POST /api/webhooks/alertmanager`, deploy kube-prometheus-stack in the cluster and point Alertmanager at `http://automend-api.automend.svc.cluster.local:8000`.
- **RoBERTa / Qwen with real weights** — Phase 10.1/10.2 shipped the services and the client wiring but the trained weights + GPU aren't included. When they land, flip `AUTOMEND_CLASSIFIER_ENDPOINT=/predict_anomaly` (via `--set config.classifierEndpoint=/predict_anomaly`) and `AUTOMEND_ARCHITECT_PROVIDER=local` pointing at the Qwen proxy.
- **Production deploy** — see `build_plan.md` Phase 12 (GKE + Cloud SQL + Memorystore via Terraform) and the companion `DEPLOY_GCP.md` when it lands.
