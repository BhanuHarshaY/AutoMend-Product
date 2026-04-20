# AutoMend — Terraform (Phase 12)

Provisions the GCP production target: GKE Autopilot cluster, Cloud SQL for
Postgres (pgvector), Memorystore Redis, Artifact Registry, and the IAM /
Workload Identity plumbing to tie them together. Then runs a
`helm_release` against the cluster with `values-gcp.yaml`.

Task 12.1 scaffolds this workspace — no resources yet. Later tasks add
modules.

## One-time bootstrap

Terraform state lives in a GCS bucket that has to exist before
`terraform init` can use it (chicken-and-egg). Create it once per
project:

```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"
export BUCKET="automend-tf-state-$PROJECT_ID"

# Enable the storage API
gcloud services enable storage.googleapis.com --project="$PROJECT_ID"

# Create the bucket with uniform-bucket-level-access + versioning
gcloud storage buckets create "gs://$BUCKET" \
  --project="$PROJECT_ID" \
  --location="$REGION" \
  --uniform-bucket-level-access
gcloud storage buckets update "gs://$BUCKET" --versioning
```

Lock the bucket down — only the identities running Terraform should have
`roles/storage.objectAdmin` on it. For solo-dev that's your own user
account via application-default credentials; for a team use a CI service
account.

## Authenticate

```bash
gcloud auth application-default login
```

The `google` + `google-beta` providers pick up Application Default
Credentials automatically.

## Init + plan

From the repo root:

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
# edit infra/terraform/terraform.tfvars — set project_id at minimum

terraform -chdir=infra/terraform init \
  -backend-config="bucket=$BUCKET"

terraform -chdir=infra/terraform plan
```

After Task 12.1 scaffolding, `plan` reports `No changes. Your
infrastructure matches the configuration.` — there are no resources to
create yet. Subsequent tasks (12.2+) add the module calls and `plan`
will show actual additions.

## File layout

| File                        | Purpose                                          |
|-----------------------------|--------------------------------------------------|
| `.terraform-version`        | tfenv pin (Terraform 1.9.8)                      |
| `versions.tf`               | `required_version` + provider pins                |
| `backend.tf`                | GCS remote-state backend (partial config)         |
| `variables.tf`              | Root inputs: project_id, region, zone, env, name_prefix |
| `providers.tf`              | google / google-beta configured from vars         |
| `main.tf`                   | Root module (stub, submodules added in 12.2+)     |
| `outputs.tf`                | Placeholder (populated in 12.2+)                  |
| `terraform.tfvars.example`  | Copy-to-fill example for local vars               |
| `terraform.tfvars`          | **gitignored** — your actual values               |

## Checked-in vs generated

| Path                     | Git tracked? | Reason                             |
|--------------------------|--------------|------------------------------------|
| `.terraform.lock.hcl`    | Yes          | Pins provider hashes; team reproducibility |
| `.terraform/`            | No           | Provider binaries / cache          |
| `terraform.tfvars`       | No           | May hold project id + secrets      |
| `*.tfstate*`             | No           | State lives in GCS; local copies are accidents |
| `*.tfplan`               | No           | Saved plans are short-lived        |

## Upcoming task map

- **12.2** ✅ — `modules/gke/` (regional Standard cluster + Workload Identity)
- **12.3** ✅ — `modules/cloud-sql/` (Postgres 15 + pgvector-capable, private IP only)
- **12.4** ✅ — `modules/memorystore/` (Redis STANDARD_HA + AUTH + server-auth TLS)
- **12.5** ✅ — `modules/artifact-registry/` + Workload Identity Federation for GitHub Actions + `.github/workflows/build-and-deploy.yaml`
- **12.6** — `helm_release` "automend" + External Secrets Operator
- **12.7** — `DEPLOY_GCP.md` runbook (production replacement for
  `DEPLOY_GCP_QUICK.md`)

## After 12.5: wire GitHub Actions via WIF

1. Set `github_repository = "OWNER/REPO"` in `terraform.tfvars` to enable the
   WIF pool. Apply.
2. Grab the Terraform outputs and add them as **repo-level GitHub secrets**
   (Settings → Secrets and variables → Actions → New repository secret):

   ```bash
   terraform -chdir=infra/terraform output -raw workload_identity_provider
   # → paste as WIF_PROVIDER

   terraform -chdir=infra/terraform output -raw ci_service_account_email
   # → paste as WIF_SA_EMAIL

   terraform -chdir=infra/terraform output -raw artifact_registry_url
   # → paste as AR_REPO_URL

   terraform -chdir=infra/terraform output -raw gke_cluster_name
   # → paste as GKE_CLUSTER

   terraform -chdir=infra/terraform output -raw gke_location
   # → paste as GKE_LOCATION

   # And the plain project id:
   # → paste as GCP_PROJECT_ID
   ```

3. Trigger the workflow: push a commit to `main`, or from the Actions tab
   click "Run workflow" on `build-and-deploy`.

4. Watch the run: 4 parallel image builds → deploy job runs `helm upgrade`.

5. Verify images landed in the registry:
   ```bash
   gcloud artifacts docker images list "$(terraform -chdir=infra/terraform output -raw artifact_registry_url)/automend/api" --include-tags
   ```

## After 12.3: verify the DB is reachable

Cloud SQL is on a private IP (no public endpoint), so you can't `psql`
from your laptop directly. Two ways to verify:

```bash
# A. From a throwaway pod on the GKE cluster:
kubectl run psql-test --rm -it --restart=Never --image=postgres:15 -- bash
# inside the pod:
PGPASSWORD="$(gcloud secrets versions access latest --secret=<secret_id>)" \
  psql -h <private_ip> -U automend -d automend -c 'SELECT version();'

# B. Via gcloud's beta connect (tunnels through Google):
gcloud sql connect <instance_name> --user=automend --database=automend
```

The instance supports pgvector but the extension is NOT enabled by default.
Task 12.6's Helm post-install Job runs `CREATE EXTENSION IF NOT EXISTS vector`
against the app database. For a manual one-off before 12.6 ships:

```bash
# `gcloud sql connect` is interactive — launches psql and waits. For a
# one-shot, use a throwaway kubectl pod instead:
INSTANCE=$(terraform -chdir=infra/terraform output -raw cloudsql_instance_name)
PRIVATE_IP=$(terraform -chdir=infra/terraform output -raw cloudsql_private_ip)
PASSWORD=$(gcloud secrets versions access latest --secret="${INSTANCE}-db-app-password")

kubectl run psql-pgvector --rm -i --restart=Never --image=postgres:15 --env="PGPASSWORD=$PASSWORD" -- \
  psql -h "$PRIVATE_IP" -U automend -d automend -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

## After 12.4: verify Redis is reachable

Memorystore uses server-auth TLS so the client must connect with `--tls`
and trust the server CA cert (public, not a secret — output from the
module). One-shot via kubectl:

```bash
HOST=$(terraform -chdir=infra/terraform output -raw memorystore_host)
PORT=$(terraform -chdir=infra/terraform output -raw memorystore_port)
AUTH=$(gcloud secrets versions access latest --secret="$(terraform -chdir=infra/terraform output -raw memorystore_auth_secret_id | sed 's|.*/||')")
terraform -chdir=infra/terraform output -raw memorystore_server_ca_cert > /tmp/redis-ca.pem

kubectl run redis-test --rm -it --restart=Never --image=redis:7 -- \
  redis-cli -h "$HOST" -p "$PORT" -a "$AUTH" --tls --cacert /tmp/redis-ca.pem PING
```

Expect `PONG`. If you see `NOAUTH` or a TLS handshake error, double-check the
secret value matches the instance's current `auth_string` (rotation would
invalidate it; `terraform apply` bumps the secret version on rotate).

## After 12.2: connect kubectl to the new cluster

```bash
gcloud container clusters get-credentials "$(terraform -chdir=infra/terraform output -raw gke_cluster_name)" \
  --region="$(terraform -chdir=infra/terraform output -raw gke_location)"
kubectl get nodes   # expect 3 Ready nodes (one per zone)
```

If you leave `master_authorized_networks` empty (the default), you must use
`gcloud container clusters get-credentials` — the cluster master is
unreachable from the public internet otherwise. The gcloud proxy handles
auth transparently through GCP's backbone.
