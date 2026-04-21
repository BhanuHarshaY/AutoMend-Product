# Root module. No resources yet — Task 12.1 scaffolds the workspace,
# later tasks add modules:
#
#   Task 12.2 — modules/gke              (Autopilot cluster + Workload Identity pool)
#   Task 12.3 — modules/cloudsql         (Postgres + pgvector, private IP only)
#   Task 12.4 — modules/memorystore      (Redis STANDARD_HA + AUTH + TLS)
#   Task 12.5 — modules/artifact_registry + IAM bindings
#   Task 12.6 — helm_release "automend"  (with values-gcp.yaml + ExternalSecrets)
#
# After scaffolding:
#   terraform -chdir=infra/terraform init -backend-config="bucket=<NAME>"
#   terraform -chdir=infra/terraform plan
# should report `No changes. Your infrastructure matches the configuration.`

# Labels applied to every Google resource created by this stack, so
# billing + audit views can filter by env / managed-by.
locals {
  common_labels = {
    app        = "automend"
    env        = var.env
    managed-by = "terraform"
  }

  # Derived names so we don't repeat `${var.name_prefix}-${var.env}` in every resource.
  resource_suffix = "${var.name_prefix}-${var.env}"
}

# ---------------------------------------------------------------------------
# Required Google APIs. `disable_on_destroy = false` keeps the APIs enabled
# if Terraform is torn down — other projects + resources in the account may
# depend on them, and re-enabling is free.
# ---------------------------------------------------------------------------

locals {
  enabled_services = [
    "compute.googleapis.com",
    "container.googleapis.com",          # GKE
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",     # Workload Identity token minting
    "artifactregistry.googleapis.com",   # Task 12.5
    "sqladmin.googleapis.com",           # Task 12.3
    "servicenetworking.googleapis.com",  # Private service access for Cloud SQL / Memorystore
    "redis.googleapis.com",              # Task 12.4
    "secretmanager.googleapis.com",      # Task 12.6 (External Secrets source)
  ]
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.enabled_services)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# Networking. One VPC + one subnet with pod + service secondary ranges for
# the VPC-native GKE cluster. Cloud SQL / Memorystore attach to this same
# VPC via private service access in Task 12.3/12.4.
# ---------------------------------------------------------------------------

resource "google_compute_network" "vpc" {
  project                 = var.project_id
  name                    = "${local.resource_suffix}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"

  depends_on = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "subnet" {
  project       = var.project_id
  name          = "${local.resource_suffix}-subnet"
  ip_cidr_range = var.subnet_cidr
  region        = var.region
  network       = google_compute_network.vpc.self_link

  # Enables Google API access from instances with internal IPs only (our
  # private GKE nodes rely on this for pulling images through the VPC).
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pod_cidr
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.service_cidr
  }
}

# Cloud NAT so private nodes can reach the public internet for e.g. Slack
# webhooks + Gemini API + any other outbound calls activities make.
resource "google_compute_router" "router" {
  project = var.project_id
  name    = "${local.resource_suffix}-router"
  region  = var.region
  network = google_compute_network.vpc.self_link
}

resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = "${local.resource_suffix}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# ---------------------------------------------------------------------------
# GKE cluster (Task 12.2).
# ---------------------------------------------------------------------------

module "gke" {
  source = "./modules/gke"

  project_id             = var.project_id
  region                 = var.region
  cluster_name           = local.resource_suffix
  name_prefix            = var.name_prefix
  network                = google_compute_network.vpc.self_link
  subnetwork             = google_compute_subnetwork.subnet.self_link
  pod_range_name         = "pods"
  service_range_name     = "services"
  node_count             = var.gke_node_count
  machine_type           = var.gke_machine_type
  master_ipv4_cidr_block = var.master_ipv4_cidr_block

  master_authorized_networks = var.master_authorized_networks

  labels              = local.common_labels
  deletion_protection = var.env == "prod"
}

# ---------------------------------------------------------------------------
# Private Service Access (PSA) — peering between our VPC and Google's
# service-producer network, so Cloud SQL + Memorystore can expose PRIVATE
# IPs that pods can dial directly. One global allocation + one connection
# shared by Tasks 12.3 and 12.4.
# ---------------------------------------------------------------------------

resource "google_compute_global_address" "psa_range" {
  project       = var.project_id
  name          = "${local.resource_suffix}-psa"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.self_link

  depends_on = [google_project_service.enabled]
}

resource "google_service_networking_connection" "psa" {
  network                 = google_compute_network.vpc.self_link
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa_range.name]

  depends_on = [google_project_service.enabled]
}

# ---------------------------------------------------------------------------
# Application service account — distinct from the node SA. This is the one
# that AutoMend pods impersonate (via Workload Identity) to access Cloud SQL
# (12.3), Memorystore (12.4), Secret Manager (12.6), etc.
# ---------------------------------------------------------------------------

resource "google_service_account" "app" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-app"
  display_name = "AutoMend application SA (${var.env})"
  description  = "Pod-level GCP identity via Workload Identity. Receives cloudsql.client, memorystore access, secret accessor roles from the respective modules."
}

# Workload Identity binding — lets the k8s ServiceAccount in the automend
# namespace impersonate the app GCP SA. Required for pod initContainers
# (model fetch from GCS, Cloud SQL Auth Proxy, secret access via GCP APIs,
# etc.) to get GCP tokens without JSON keys.
#
# The k8s SA name comes from the Helm chart's `serviceAccount.name` default
# (`automend.serviceAccountName` helper → release fullname). When the Helm
# release name is `automend` in the `automend` namespace, the full identity
# is `serviceAccount:PROJECT.svc.id.goog[automend/automend]`.
resource "google_service_account_iam_member" "app_wi_binding" {
  service_account_id = google_service_account.app.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${var.k8s_namespace}/${var.k8s_service_account}]"
}

# ---------------------------------------------------------------------------
# Cloud SQL (Task 12.3).
# ---------------------------------------------------------------------------

module "cloud_sql" {
  source = "./modules/cloud-sql"

  project_id                = var.project_id
  region                    = var.region
  instance_name             = "${local.resource_suffix}-pg"
  db_name                   = "automend"
  db_user                   = "automend"
  tier                      = var.cloudsql_tier
  availability_type         = var.env == "prod" ? "REGIONAL" : "ZONAL"
  disk_size_gb              = var.cloudsql_disk_size_gb
  network_self_link         = google_compute_network.vpc.self_link
  app_service_account_email = google_service_account.app.email

  labels              = local.common_labels
  deletion_protection = var.env == "prod"

  depends_on = [google_service_networking_connection.psa]
}

# ---------------------------------------------------------------------------
# Memorystore Redis (Task 12.4).
# ---------------------------------------------------------------------------

module "memorystore" {
  source = "./modules/memorystore"

  project_id                = var.project_id
  region                    = var.region
  instance_name             = "${local.resource_suffix}-redis"
  tier                      = var.env == "prod" ? "STANDARD_HA" : var.memorystore_tier
  memory_size_gb            = var.memorystore_memory_size_gb
  authorized_network        = google_compute_network.vpc.self_link
  app_service_account_email = google_service_account.app.email

  labels = local.common_labels

  depends_on = [google_service_networking_connection.psa]
}

# ---------------------------------------------------------------------------
# CI/CD Workload Identity Federation (Task 12.5)
#
# Lets GitHub Actions impersonate a GCP service account without checking
# any long-lived SA keys into GitHub secrets. The flow:
#
#   1. GitHub Actions mints an OIDC token that identifies
#      `repo:OWNER/REPO:ref:refs/heads/main`.
#   2. That token is exchanged at Google's STS endpoint for a federated token.
#   3. The federated token is used to impersonate the CI service account.
#   4. The CI SA pushes to Artifact Registry and helm-upgrades the cluster.
#
# We restrict principal access via `attribute.repository` so only the exact
# GitHub repo set in var.github_repository can use this SA.
# ---------------------------------------------------------------------------

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_iam_workload_identity_pool" "github" {
  count = var.github_repository == "" ? 0 : 1

  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-gh-${var.env}"
  display_name              = "GitHub Actions (${var.env})"
  description               = "OIDC federation pool for GitHub Actions workflows in ${var.github_repository}"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count = var.github_repository == "" ? 0 : 1

  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # Only accept tokens from our specific repo. Without this condition,
  # any GitHub repo on the internet could swap in their own OIDC token.
  attribute_condition = "assertion.repository == \"${var.github_repository}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "ci" {
  count = var.github_repository == "" ? 0 : 1

  project      = var.project_id
  account_id   = "${var.name_prefix}-ci"
  display_name = "AutoMend CI/CD SA (${var.env})"
  description  = "Impersonated by GitHub Actions via Workload Identity Federation. Pushes images + runs helm upgrade."
}

# Bind the WIF pool's principal (restricted to this repo) to the CI SA.
resource "google_service_account_iam_member" "ci_wif_binding" {
  count = var.github_repository == "" ? 0 : 1

  service_account_id = google_service_account.ci[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github[0].workload_identity_pool_id}/attribute.repository/${var.github_repository}"
}

# Project-level roles the CI SA needs for its job. artifactregistry.writer
# on the specific repo is granted inside the artifact-registry module.
locals {
  ci_project_roles = [
    # container.admin is a superset of container.developer and includes
    # container.clusters.update — needed so the workflow can add/remove
    # its runner IP from master_authorized_networks around each deploy.
    "roles/container.admin",
    "roles/container.clusterViewer",
  ]
}

resource "google_project_iam_member" "ci_project_roles" {
  for_each = var.github_repository == "" ? toset([]) : toset(local.ci_project_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.ci[0].email}"
}

# The CI SA needs to bind the app SA to the k8s ServiceAccount the Helm
# chart creates (for Workload Identity). `serviceAccountTokenCreator` on
# the specific app SA (not project-wide) is the minimum privilege.
resource "google_service_account_iam_member" "ci_impersonate_app" {
  count = var.github_repository == "" ? 0 : 1

  service_account_id = google_service_account.app.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.ci[0].email}"
}

# ---------------------------------------------------------------------------
# Artifact Registry (Task 12.5).
# ---------------------------------------------------------------------------

module "artifact_registry" {
  source = "./modules/artifact-registry"

  project_id                 = var.project_id
  region                     = var.region
  repository_id              = var.name_prefix
  node_service_account_email = module.gke.node_service_account_email
  # If WIF isn't enabled (github_repository=""), leave ci_service_account_email empty
  # so the module doesn't create the IAM binding.
  ci_service_account_email = var.github_repository == "" ? "" : google_service_account.ci[0].email

  labels = local.common_labels
}

# ---------------------------------------------------------------------------
# Model artifacts bucket — pod initContainers pull classifier + architect
# weights from here on startup. See `modules/model-storage` + the classifier
# Deployment template for the pull mechanism.
# ---------------------------------------------------------------------------

module "model_storage" {
  source = "./modules/model-storage"

  project_id                = var.project_id
  region                    = var.region
  bucket_name               = "${local.resource_suffix}-models-${var.project_id}"
  app_service_account_email = google_service_account.app.email
  ci_service_account_email  = var.github_repository == "" ? "" : google_service_account.ci[0].email

  labels        = local.common_labels
  force_destroy = var.env != "prod"  # dev/staging can `terraform destroy` even with uploaded weights
}

# ---------------------------------------------------------------------------
# App secrets — Secret Manager entries for every non-auto-generated
# credential the app reads. DB password + Redis AUTH come from the
# cloud-sql / memorystore modules; this module covers everything else
# (JWT secret, Gemini key, Slack, PagerDuty, Jira).
# ---------------------------------------------------------------------------

module "app_secrets" {
  source = "./modules/app-secrets"
  count  = var.enable_external_secrets ? 1 : 0

  project_id                = var.project_id
  name_prefix               = local.resource_suffix
  app_service_account_email = google_service_account.app.email
  labels                    = local.common_labels
}

# ---------------------------------------------------------------------------
# External Secrets Operator — materializes GCP Secret Manager entries into
# a k8s Secret in the automend namespace. Required for values-gcp.yaml
# (chart's secrets.create: false path). Skip if you're on values-gcp-quick
# and pass secrets through helm directly.
# ---------------------------------------------------------------------------

module "external_secrets" {
  source = "./modules/external-secrets"
  count  = var.enable_external_secrets ? 1 : 0

  namespace                 = "external-secrets"
  app_service_account_email = google_service_account.app.email

  # Don't try to install ESO before the cluster exists.
  depends_on = [module.gke]
}

# Workload Identity binding — ESO's k8s ServiceAccount
# (`external-secrets/external-secrets`) impersonates the app GCP SA so it
# can read from Secret Manager.
resource "google_service_account_iam_member" "eso_wi_binding" {
  count              = var.enable_external_secrets ? 1 : 0
  service_account_id = google_service_account.app.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[external-secrets/external-secrets]"
}
