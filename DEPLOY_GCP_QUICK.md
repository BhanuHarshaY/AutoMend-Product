# DEPLOY_GCP_QUICK.md — Ship AutoMend to GKE in ~20 minutes

A minimum-viable, demo-grade deployment to Google Kubernetes Engine. NOT
production grade. Data lives in node-local PVCs (no Cloud SQL), no TLS,
no real DNS. Same Helm chart you tested on kind, just pointed at GKE
with a Gemini architect backend.

For the production story (Cloud SQL + Memorystore + Workload Identity +
Terraform + External Secrets), see Phase 12 in `build_plan.md`.

---

## 0. Prerequisites

On your workstation:

- **gcloud CLI** — install from https://cloud.google.com/sdk/docs/install
- **kubectl** — comes with gcloud: `gcloud components install kubectl`
- **helm 3.x** — already installed for local testing
- **Docker Desktop** — already running for kind
- Local images already built: `automend/api:dev`, `automend/worker:dev`,
  `automend/temporal-worker:dev`, `automend/frontend:dev`. If not,
  build them first:
  ```powershell
  docker build -t automend/api:dev              -f infra/dockerfiles/Dockerfile.api              backend
  docker build -t automend/worker:dev           -f infra/dockerfiles/Dockerfile.worker           backend
  docker build -t automend/temporal-worker:dev  -f infra/dockerfiles/Dockerfile.temporal-worker  backend
  docker build -t automend/frontend:dev         -f infra/dockerfiles/Dockerfile.frontend         .
  ```

In the GCP Console:

- **A project with billing enabled.** Create one at
  https://console.cloud.google.com/projectcreate. Link it to a billing
  account: https://console.cloud.google.com/billing. GKE Autopilot
  requires billing — free tier alone isn't enough.
- **A Google AI Studio API key** for Gemini 2.5 Pro. Get one at
  https://aistudio.google.com/apikey — takes 30 seconds, free for the
  first 60 requests/min.

---

## 1. Set your variables

In PowerShell:

```powershell
$PROJECT_ID     = "YOUR-PROJECT-ID"           # e.g. "automend-demo-2026"
$REGION         = "us-central1"               # closest GCP region
$CLUSTER        = "automend"
$AR_REPO        = "automend"                  # Artifact Registry repo name
$GEMINI_API_KEY = "AIza-YOUR-KEY"             # from aistudio.google.com/apikey

$REGISTRY = "$REGION-docker.pkg.dev/$PROJECT_ID/$AR_REPO"
```

---

## 2. Enable GCP APIs (one-time per project)

```powershell
gcloud config set project $PROJECT_ID
gcloud services enable `
  container.googleapis.com `
  artifactregistry.googleapis.com
```

(~60 seconds. You'll see "Operation finished successfully" when done.)

---

## 3. Create an Artifact Registry repo

```powershell
gcloud artifacts repositories create $AR_REPO `
  --repository-format=docker `
  --location=$REGION `
  --description="AutoMend container images"
```

---

## 4. Create a GKE Autopilot cluster (runs in background)

```powershell
gcloud container clusters create-auto $CLUSTER --region=$REGION
```

This takes **3–5 minutes**. Let it run; do step 5 in the same or a new
terminal while it provisions.

---

## 5. Tag + push the four app images to Artifact Registry

```powershell
# Authenticate Docker against Artifact Registry (one-time per workstation)
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet

# Retag and push in a loop
foreach ($i in @('api','worker','temporal-worker','frontend')) {
  docker tag  "automend/${i}:dev" "$REGISTRY/automend/${i}:dev"
  docker push "$REGISTRY/automend/${i}:dev"
}
```

Over a typical home connection this takes **3–5 minutes** for the four
images (~700 MB total pushed). Speed depends on upload bandwidth.

---

## 6. Connect kubectl to the cluster

Wait for step 4 to finish (the `gcloud container clusters create-auto`
command returns), then:

```powershell
gcloud container clusters get-credentials $CLUSTER --region=$REGION
kubectl get nodes   # should list one or more Autopilot nodes
```

---

## 7. Install the chart

```powershell
kubectl create namespace automend

helm install automend ./infra/helm/automend `
  --namespace automend `
  --values ./infra/helm/automend/values-gcp-quick.yaml `
  --set "global.imageRegistry=$REGISTRY" `
  --set "secrets.values.jwtSecret=dev-jwt-$([guid]::NewGuid())" `
  --set "secrets.values.postgresPassword=automend" `
  --set "secrets.values.architectApiKey=$GEMINI_API_KEY" `
  --wait --timeout 10m
```

~2–4 minutes. Helm waits for:

1. All Deployments to be Ready (postgres, redis, temporal, api,
   classifier, frontend, three workers)
2. The migrations Job (`alembic upgrade head` + seed scripts) to succeed

If the `--wait` times out, list pods to see what's stuck:
```powershell
kubectl -n automend get pods
kubectl -n automend describe pod <failing-pod>
```

---

## 8. Create an admin user

```powershell
kubectl -n automend exec deploy/automend-api -- `
  env ADMIN_EMAIL=admin@local ADMIN_PASSWORD=admin123 `
  python scripts/bootstrap_admin.py
```

Expected output: `Created admin user: admin@local` (or
`User already exists` if you re-run).

---

## 9. Access the UI

**Option A — port-forward (fastest, localhost-only):**

```powershell
# In one terminal:
kubectl -n automend port-forward svc/automend-frontend 3000:3000
# In another:
kubectl -n automend port-forward svc/automend-api 8000:8000
```

Open http://localhost:3000, log in as `admin@local` / `admin123`.

**Option B — public LoadBalancer IP (demo-friendly, ~1 extra minute):**

```powershell
kubectl -n automend patch svc automend-frontend -p '{"spec":{"type":"LoadBalancer"}}'
kubectl -n automend patch svc automend-api      -p '{"spec":{"type":"LoadBalancer"}}'

# Wait ~60s then:
kubectl -n automend get svc automend-frontend automend-api
```

Both services will show an `EXTERNAL-IP`. The frontend Next.js image has
`API_PROXY_TARGET=http://automend-api:8000` baked in at build time, which
resolves via cluster DNS, so the frontend's `/api/*` calls still work
through the cluster-internal Service — you only need the frontend IP
for browser access.

`http://<frontend-external-ip>/` → AutoMend UI.

---

## 10. Verify the architect is wired to Gemini

Log in, open any workflow, click the chat panel, type:

> "Scale reco-pod to 5 replicas and notify #mlops-alerts on memory spikes"

Click Generate. If you see nodes appear on the canvas within ~5 seconds,
Gemini is working. If you see a `502` or an error toast, check:

```powershell
kubectl -n automend logs deploy/automend-api --tail=80 | Select-String "architect|gemini"
```

Common Gemini error causes:
- **`400 model not found`** — your key doesn't have access to
  `gemini-2.5-pro` yet. Fall back:
  `helm upgrade automend ... --set config.architectModel=gemini-1.5-pro --reuse-values`
- **`403 API key not valid`** — key was copied wrong or not yet activated.
  New keys can take ~60s to propagate.

---

## 11. Connect a workload to monitor (optional)

If you want end-to-end remediation (same as `MANUAL_TESTING.md` §8–9),
deploy the crashing ML pod + Fluent Bit DaemonSet on the GKE cluster:

```powershell
kubectl apply -f crashing-ml.yaml
kubectl apply -f fluentbit.yaml
```

Then in the AutoMend UI, create a project bound to the `ml` namespace
(Autopilot creates `ml` when you `apply` the crashing manifest), build a
workflow with the deployment-picker dropdowns, and publish.

Note: the Fluent Bit HTTP output posts to the AutoMend API via the
in-cluster DNS name `automend-api.automend.svc.cluster.local:8000`,
which already resolves correctly from any namespace.

---

## 12. Teardown (to stop billing)

GKE Autopilot bills per pod-request-second. The cluster alone with
AutoMend running costs roughly **$15–25/day**. Tear it down when done:

```powershell
helm uninstall automend -n automend
kubectl delete namespace automend
gcloud container clusters delete $CLUSTER --region=$REGION --quiet
gcloud artifacts repositories delete $AR_REPO --location=$REGION --quiet
```

Leave the GCP project itself around (free unless you have billable
resources in it).

---

## Troubleshooting — common GKE-specific snags

**`helm install` timing out, migrations Job in `Pending`**
Autopilot provisions PVCs lazily; the Postgres pod can take ~2 min to
get its disk. Wait, then check:
```powershell
kubectl -n automend get pvc
kubectl -n automend describe pod -l app.kubernetes.io/component=migrations
```

**`ImagePullBackOff` on api / worker / frontend pods**
Your push in step 5 didn't complete, or the Autopilot node can't reach
Artifact Registry. Verify:
```powershell
gcloud artifacts docker images list "$REGISTRY/automend"
```
Should list all four images. If missing, re-push.

**Frontend loads but shows network errors in DevTools**
Check the page is hitting `localhost:3000` (if port-forwarding) or the
external LB IP (if Option B). The frontend baked `API_PROXY_TARGET` at
build time, so any `/api/*` request goes to the in-cluster
`automend-api.automend.svc.cluster.local:8000`. This works from any
cluster IP. If the browser is hitting the external LB IP directly and
`/api/*` 404s, the Ingress or LB doesn't have routing — just use
port-forward (Option A) instead.

**Cluster creation fails with `INSUFFICIENT_QUOTA`**
Default GCP quotas sometimes block Autopilot in busy regions. Try
`us-central1`, `us-east1`, or `europe-west1`. Or request a quota
increase in the console (instant for most SKUs).

**Gemini rate-limited (`429` errors in api logs)**
Free-tier Google AI keys cap at 60 req/min. For a demo, this is plenty;
for anything beyond, upgrade the key or switch to Vertex AI.

---

## What this doesn't include (and you may want later)

- **TLS** — no cert-manager / ManagedCertificate. Site is HTTP-only.
- **Real DNS** — use nip.io hostnames, e.g. `<LB-IP>.nip.io`, for a
  semi-real hostname without buying a domain.
- **Cloud SQL / Memorystore** — data is in Autopilot PVCs and is lost
  when you `helm uninstall`. For real persistence, switch to
  managed services per Phase 12.
- **Workload Identity** — the ServiceAccount uses the default Autopilot
  node identity. For GCP API access (logs to BigQuery, secrets from
  Secret Manager), bind to a GCP SA with Workload Identity.
- **Autoscaling** — single-replica everywhere. Scale replicas in the
  values file for real load.
