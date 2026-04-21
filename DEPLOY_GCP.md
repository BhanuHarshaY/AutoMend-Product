# DEPLOY_GCP.md — Production GCP deploy

Infrastructure-as-code deploy of AutoMend to a fresh GCP project.

**What this gets you:** regional GKE Standard cluster, Cloud SQL Postgres
(pgvector), Memorystore Redis, Artifact Registry, Workload Identity
Federation for CI, External Secrets Operator for GCP Secret Manager sync,
GCS bucket for model artifacts. All Terraform-managed; all pod-side
credentials sourced from Secret Manager.

**Takes ~30 minutes end-to-end on a fresh GCP project.** Slowest steps
are GKE provisioning (~5 min) and Cloud SQL (~10 min) — they run in
parallel where possible.

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [One-time bootstrap](#2-one-time-bootstrap)
3. [Terraform apply](#3-terraform-apply)
4. [Seed model artifacts](#4-seed-model-artifacts)
5. [Populate application secrets](#5-populate-application-secrets)
6. [Build + push container images via CI](#6-build--push-container-images-via-ci)
7. [Verify the deploy](#7-verify-the-deploy)
8. [Smoke-test checklist](#8-smoke-test-checklist)
9. [Rollback plan](#9-rollback-plan)
10. [Teardown](#10-teardown)

---

## 1. Prerequisites

### GCP account
- A project with billing enabled (https://console.cloud.google.com/billing).
  Phase 12 uses paid services (GKE Standard, Cloud SQL, Memorystore).
- You have `roles/owner` or the equivalent combination of
  `roles/resourcemanager.projectIamAdmin`,
  `roles/servicenetworking.networksAdmin`, `roles/compute.networkAdmin`,
  `roles/container.admin`, `roles/cloudsql.admin`, `roles/redis.admin`,
  `roles/artifactregistry.admin`, `roles/iam.serviceAccountAdmin`,
  `roles/secretmanager.admin`, `roles/workloadidentitypools.admin`,
  `roles/storage.admin`.

### Workstation tools

| Tool | Version | Install |
|---|---|---|
| **gcloud CLI** | latest | https://cloud.google.com/sdk/docs/install |
| **Terraform** | 1.9.x (matches `.terraform-version`) | https://developer.hashicorp.com/terraform/install — or via `tfenv` |
| **kubectl** | latest | `gcloud components install kubectl` |
| **helm** | 3.15+ | https://helm.sh/docs/intro/install/ |
| **Python** | 3.11+ | Only needed if seeding default model artifacts locally — CI-path skips this. |

### Third-party credentials
- **Google AI Studio API key** for the Gemini architect
  (https://aistudio.google.com/apikey). Required unless you flip
  `architectProvider` to `anthropic` or `local` post-install.
- **Slack incoming-webhook URL** for alert + approval pings. Optional —
  empty value = Slack activities no-op gracefully.

---

## 2. One-time bootstrap

### Create the Terraform state bucket

Terraform state lives in a GCS bucket that has to exist before
`terraform init` can store state in it (chicken-and-egg). Create it once
per project:

```bash
export PROJECT_ID="YOUR-PROJECT-ID"
export REGION="us-central1"
export STATE_BUCKET="automend-tf-state-${PROJECT_ID}"

gcloud config set project "$PROJECT_ID"
gcloud services enable storage.googleapis.com --project="$PROJECT_ID"
gcloud storage buckets create "gs://$STATE_BUCKET" \
  --project="$PROJECT_ID" \
  --location="$REGION" \
  --uniform-bucket-level-access
gcloud storage buckets update "gs://$STATE_BUCKET" --versioning
```

### Authenticate Terraform

```bash
gcloud auth application-default login
```

### Configure `terraform.tfvars`

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
```

Edit `infra/terraform/terraform.tfvars`:

```hcl
project_id  = "YOUR-PROJECT-ID"
region      = "us-central1"           # must match $REGION above
env         = "prod"                   # triggers REGIONAL Cloud SQL, STANDARD_HA Memorystore, deletion_protection on
name_prefix = "automend"

# Your laptop's public IPv4 — needed for direct kubectl access to the GKE master.
# Fetch with: curl https://api.ipify.org
master_authorized_networks = [
  { cidr_block = "YOUR.IP.ADDRESS/32", display_name = "laptop" },
]

# Enables WIF for GitHub Actions. Point at the repo holding the AutoMend source.
github_repository = "YOUR-ORG/YOUR-REPO"

# Enables External Secrets Operator + the managed-SM-credential flow.
enable_external_secrets = true
```

---

## 3. Terraform apply

```bash
terraform -chdir=infra/terraform init -backend-config="bucket=$STATE_BUCKET"
terraform -chdir=infra/terraform apply
```

Takes **~10–15 minutes**. Apply creates ~60 resources across these slices:

| Slice | Duration | What it creates |
|---|---|---|
| Google APIs | ~30 s each, parallel | 9 APIs enabled (compute, container, cloud sql admin, etc.) |
| Network | ~30 s | VPC + subnet + Cloud Router + Cloud NAT + PSA peering |
| GKE Standard cluster | ~5–7 min | Regional control plane, node pool (e2-standard-4 × 3), Workload Identity, Shielded nodes |
| Cloud SQL | ~10 min | Postgres 15, private IP only, daily backups + PITR |
| Memorystore | ~5 min | Redis 7.2 STANDARD_HA, AUTH + server-auth TLS |
| Artifact Registry + WIF | ~1 min | Docker repo + WIF pool + CI service account |
| Model storage | ~30 s | GCS bucket for RoBERTa + Qwen weights |
| App secrets | ~1 min | 7 Secret Manager entries (JWT auto-generated, Gemini / Slack / PagerDuty / Jira empty placeholders) |
| External Secrets Operator | ~2 min | Helm release in the `external-secrets` namespace |

### Enable pgvector

Cloud SQL ships pgvector but doesn't enable it by default. One-shot from
a throwaway pod with VPC access to the private IP:

```bash
INSTANCE=$(terraform -chdir=infra/terraform output -raw cloudsql_instance_name)
PRIVATE_IP=$(terraform -chdir=infra/terraform output -raw cloudsql_private_ip)
PASSWORD=$(gcloud secrets versions access latest --secret="${INSTANCE}-db-app-password")

gcloud container clusters get-credentials \
  "$(terraform -chdir=infra/terraform output -raw gke_cluster_name)" \
  --region="$(terraform -chdir=infra/terraform output -raw gke_location)"

kubectl run psql-pgvector --rm -i --restart=Never --image=postgres:15 \
  --env="PGPASSWORD=$PASSWORD" -- \
  psql -h "$PRIVATE_IP" -U automend -d automend \
       -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Expected output: `CREATE EXTENSION`.

---

## 4. Seed model artifacts

Upload classifier + architect pretrained weights to the GCS model bucket.
Pod initContainers pull from here on every deploy. RoBERTa-base for
classifier (~500 MB), Qwen2.5-0.5B-Instruct for architect (~1 GB).

```bash
pip install huggingface_hub google-cloud-storage

BUCKET=$(terraform -chdir=infra/terraform output -raw models_bucket_name)
python scripts/seed_default_models.py --bucket "$BUCKET"
```

Verify:

```bash
gcloud storage ls "gs://$BUCKET/classifier/"
gcloud storage ls "gs://$BUCKET/architect/"
```

You should see 6–7 files in each prefix (config + tokenizer + safetensors).

For real finetuned weights later, upload to the same prefixes — pods
pick them up on the next rollout.

---

## 5. Populate application secrets

Terraform created empty Secret Manager entries for operator-supplied
credentials. Populate the ones you want active:

```bash
PREFIX=$(terraform -chdir=infra/terraform output -raw app_secrets_prefix)

# Gemini — required for the Architect chat panel
echo -n "AIza-YOUR-KEY" | gcloud secrets versions add "${PREFIX}-architect-api-key" --data-file=-

# Slack — optional, enables alert + approval notifications
echo -n "https://hooks.slack.com/services/..." | gcloud secrets versions add "${PREFIX}-slack-webhook-url" --data-file=-

# Optional integrations — leave empty to disable the feature
# echo -n "..." | gcloud secrets versions add "${PREFIX}-pagerduty-api-key" --data-file=-
# echo -n "..." | gcloud secrets versions add "${PREFIX}-jira-api-token" --data-file=-
```

ExternalSecret reconciles within ~1 minute and pods pick up the new
values on next restart (or immediately via file-watch if the operator
supports it).

The JWT secret and database passwords are Terraform-generated — don't
overwrite them.

---

## 6. Build + push container images via CI

With `github_repository` set in tfvars, Terraform provisioned a WIF pool
+ CI service account restricted to that repo. Copy the 8 outputs into
GitHub repo secrets:

```bash
terraform -chdir=infra/terraform output -raw workload_identity_provider   # → WIF_PROVIDER
terraform -chdir=infra/terraform output -raw ci_service_account_email     # → WIF_SA_EMAIL
terraform -chdir=infra/terraform output -raw artifact_registry_url        # → AR_REPO_URL
terraform -chdir=infra/terraform output -raw gke_cluster_name             # → GKE_CLUSTER
terraform -chdir=infra/terraform output -raw gke_location                 # → GKE_LOCATION
echo "$PROJECT_ID"                                                         # → GCP_PROJECT_ID
terraform -chdir=infra/terraform output -raw models_bucket_name           # → MODELS_BUCKET
terraform -chdir=infra/terraform output -raw app_service_account_email    # → APP_SA_EMAIL
```

GitHub → repo → Settings → Secrets and variables → Actions → **New
repository secret** for each.

Trigger the workflow:

```bash
git push origin main
# or via the UI: Actions → build-and-deploy → Run workflow
```

The `build-and-push` job runs the 4-image matrix in parallel (api,
worker, temporal-worker, frontend); the `deploy` job then `helm
install`s / `upgrade`s against the cluster using `values-gcp.yaml`.
~8 minutes total once caches are warm.

---

## 7. Verify the deploy

```bash
gcloud container clusters get-credentials \
  "$(terraform -chdir=infra/terraform output -raw gke_cluster_name)" \
  --region="$(terraform -chdir=infra/terraform output -raw gke_location)"

kubectl -n automend get pods
# Expect 9 pods Running: api, classifier, frontend, temporal, 3 workers, temporal-worker, migrations Completed
kubectl -n automend get externalsecret
# Expect: automend-secrets    STATUS: SecretSynced
kubectl -n automend get secret automend-secrets -o jsonpath='{.data}' | jq 'keys'
# Expect 9 AUTOMEND_* keys
```

### Bootstrap admin user

```bash
kubectl -n automend exec deploy/automend-api -- \
  env ADMIN_EMAIL=admin@local ADMIN_PASSWORD=admin123 \
  python scripts/bootstrap_admin.py
```

### Access the UI

```bash
# Port-forward (fastest)
kubectl -n automend port-forward svc/automend-frontend 3000:3000
# → http://localhost:3000

# Or use the GCLB Ingress (takes ~5 min to provision the public IP)
kubectl -n automend get ingress automend
```

---

## 8. Smoke-test checklist

- [ ] **`kubectl get pods`** — all Running, migrations Completed.
- [ ] **`/health`** — `curl http://localhost:8000/health` → `{"status":"ok"}`.
- [ ] **Login** — `admin@local` / `admin123`.
- [ ] **Create a project** — pick `default` from the namespace dropdown.
- [ ] **Incidents page** — WebSocket live pill green.
- [ ] **Gemini chat panel** — "Scale a deployment on memory spikes" → workflow appears within ~5s.
- [ ] **Cloud SQL connectivity** — GCP Console → Cloud SQL → Monitoring → active connections > 0 from GKE IPs.
- [ ] **Memorystore connectivity** — GCP Console → Memorystore → Monitoring → ops count > 0.

---

## 9. Rollback plan

### Helm-level rollback (most common)

```bash
helm -n automend history automend
helm -n automend rollback automend <REVISION>
```

~1 min. Persistent data (Cloud SQL, Memorystore) is untouched.

### Terraform state rollback

GCS backend versioning keeps every state snapshot:

```bash
gcloud storage ls "gs://$STATE_BUCKET/automend/state/" --versions
gcloud storage cp "gs://$STATE_BUCKET/automend/state/default.tfstate#NNNNN" \
  "gs://$STATE_BUCKET/automend/state/default.tfstate"
terraform -chdir=infra/terraform refresh
terraform -chdir=infra/terraform plan
```

### Database rollback

Cloud SQL has daily backups + PITR on. Restore to a point-in-time via
`gcloud sql backups restore` or the console — reverts database only, app
pods keep running and connect to the restored data.

---

## 10. Teardown

```bash
# 1. Uninstall the app
helm uninstall automend -n automend
kubectl delete namespace automend

# 2. Destroy everything Terraform owns
terraform -chdir=infra/terraform destroy
```

Takes ~10–15 minutes. Cloud SQL deletion can stall if
`deletion_protection = true` (prod) — temporarily set `env = "dev"` in
tfvars + apply, then retry destroy.

### Gotchas

- **Cloud SQL names are reserved for ~1 week** after deletion. Re-apply
  inside that window fails with `instance name not available`. The
  `env` suffix in the instance name prevents dev↔prod collisions.
- **Artifact Registry repos must be empty** — cleanup policies handle
  this automatically.
- **VPC peering** (Private Service Access) sometimes hangs on teardown —
  retry `terraform destroy` or delete the peering manually under VPC
  network → VPC network peering.

---

## See also

- [`infra/terraform/README.md`](infra/terraform/README.md) — Terraform
  module-level details, bucket bootstrap, apply workflows.
- [`scripts/seed_default_models.py`](scripts/seed_default_models.py) —
  seeds the GCS model bucket.
- [`.github/workflows/build-and-deploy.yaml`](.github/workflows/build-and-deploy.yaml) —
  CI pipeline that this runbook ties into.
