# ---------------------------------------------------------------------------
# GKE (Task 12.2)
# ---------------------------------------------------------------------------

output "gke_cluster_name" {
  description = "Name of the GKE cluster. Feed to `gcloud container clusters get-credentials`."
  value       = module.gke.cluster_name
}

output "gke_location" {
  description = "Region of the regional cluster."
  value       = module.gke.location
}

output "gke_cluster_endpoint" {
  description = "HTTPS endpoint of the cluster master."
  value       = module.gke.cluster_endpoint
  sensitive   = true
}

output "gke_cluster_ca_certificate" {
  description = "Base64-encoded cluster CA cert."
  value       = module.gke.cluster_ca_certificate
  sensitive   = true
}

output "workload_identity_pool" {
  description = "Workload Identity pool (`PROJECT.svc.id.goog`). 12.3+ bind k8s service accounts to GCP SAs via this pool."
  value       = module.gke.workload_identity_pool
}

output "gke_node_service_account_email" {
  description = "SA used by nodes for kubelet / logging / AR pulls."
  value       = module.gke.node_service_account_email
}

# ---------------------------------------------------------------------------
# Networking (needed by Task 12.3 / 12.4 for private service access)
# ---------------------------------------------------------------------------

output "vpc_self_link" {
  description = "Self-link of the VPC that hosts GKE + will host Cloud SQL / Memorystore in 12.3/12.4."
  value       = google_compute_network.vpc.self_link
}

output "subnet_self_link" {
  description = "Self-link of the primary subnetwork."
  value       = google_compute_subnetwork.subnet.self_link
}

# ---------------------------------------------------------------------------
# Cloud SQL (Task 12.3)
# ---------------------------------------------------------------------------

output "cloudsql_instance_name" {
  description = "Cloud SQL instance name (unqualified)."
  value       = module.cloud_sql.instance_name
}

output "cloudsql_connection_name" {
  description = "`PROJECT:REGION:INSTANCE` — pass to Cloud SQL Auth Proxy / IAM connections."
  value       = module.cloud_sql.connection_name
}

output "cloudsql_private_ip" {
  description = "Private IP of the instance, reachable from any pod on the VPC."
  value       = module.cloud_sql.private_ip
}

output "cloudsql_db_name" {
  description = "Application database inside the instance."
  value       = module.cloud_sql.db_name
}

output "cloudsql_db_user" {
  description = "Application user name."
  value       = module.cloud_sql.db_user
}

output "cloudsql_db_password_secret_id" {
  description = "Fully-qualified Secret Manager resource id for the app password. Feed to External Secrets / CSI."
  value       = module.cloud_sql.db_password_secret_id
}

# ---------------------------------------------------------------------------
# App service account (shared across 12.3 / 12.4 / 12.6)
# ---------------------------------------------------------------------------

output "app_service_account_email" {
  description = "Pod-level GCP SA. Workload Identity binds this to the Helm chart's k8s ServiceAccount in 12.6."
  value       = google_service_account.app.email
}

# ---------------------------------------------------------------------------
# Memorystore (Task 12.4)
# ---------------------------------------------------------------------------

output "memorystore_instance_name" {
  description = "Memorystore instance name."
  value       = module.memorystore.instance_name
}

output "memorystore_host" {
  description = "Private IP of the Redis primary."
  value       = module.memorystore.host
}

output "memorystore_port" {
  description = "Redis port (always 6379)."
  value       = module.memorystore.port
}

output "memorystore_auth_secret_id" {
  description = "Fully-qualified Secret Manager resource id for the Redis AUTH string."
  value       = module.memorystore.auth_secret_id
}

output "memorystore_server_ca_cert" {
  description = "Memorystore server CA cert (PEM). Pods need it in their TLS trust store. Marked sensitive to avoid splatting into diff output."
  value       = module.memorystore.server_ca_cert
  sensitive   = true
}

# ---------------------------------------------------------------------------
# Artifact Registry + CI/CD (Task 12.5)
# ---------------------------------------------------------------------------

output "artifact_registry_repository_id" {
  description = "Short repository id (e.g. `automend`)."
  value       = module.artifact_registry.repository_id
}

output "artifact_registry_url" {
  description = "Registry URL prefix: `REGION-docker.pkg.dev/PROJECT/REPO`. Append `/<image>:<tag>` for full image references. Feed to `global.imageRegistry` in the Helm chart."
  value       = module.artifact_registry.repository_url
}

output "ci_service_account_email" {
  description = "CI service account email. Empty string if WIF is disabled (var.github_repository = \"\")."
  value       = length(google_service_account.ci) > 0 ? google_service_account.ci[0].email : ""
}

output "workload_identity_provider" {
  description = "Fully-qualified Workload Identity provider resource name to paste into GitHub Actions' `google-github-actions/auth@v2` step. Empty if WIF is disabled."
  value       = length(google_iam_workload_identity_pool_provider.github) > 0 ? google_iam_workload_identity_pool_provider.github[0].name : ""
}
