output "cluster_name" {
  description = "GKE cluster name, pass to `gcloud container clusters get-credentials`."
  value       = google_container_cluster.primary.name
}

output "location" {
  description = "Region of the cluster (regional clusters use the region as `location`)."
  value       = google_container_cluster.primary.location
}

output "cluster_endpoint" {
  description = "HTTPS endpoint of the cluster master. Marked sensitive so it doesn't splat into plan diffs."
  value       = google_container_cluster.primary.endpoint
  sensitive   = true
}

output "cluster_ca_certificate" {
  description = "Base64-encoded CA cert of the cluster master. Pass to the kubernetes/helm providers after base64decode()."
  value       = google_container_cluster.primary.master_auth[0].cluster_ca_certificate
  sensitive   = true
}

output "workload_identity_pool" {
  description = "Workload Identity pool id (`PROJECT.svc.id.goog`). Used in 12.3+ to bind k8s service accounts to GCP service accounts."
  value       = google_container_cluster.primary.workload_identity_config[0].workload_pool
}

output "node_service_account_email" {
  description = "Node pool's SA email. 12.5 attaches artifactregistry.reader to this SA so pulls just work."
  value       = google_service_account.nodes.email
}
