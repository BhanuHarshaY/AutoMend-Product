output "repository_id" {
  description = "Short repository id."
  value       = google_artifact_registry_repository.main.repository_id
}

output "repository_name" {
  description = "Fully-qualified repository resource name (projects/.../locations/.../repositories/...)."
  value       = google_artifact_registry_repository.main.name
}

output "repository_url" {
  description = "Registry URL prefix for `docker push` / `docker pull`: `REGION-docker.pkg.dev/PROJECT/REPO`. Append `/<image>:<tag>` for full image references."
  value       = "${google_artifact_registry_repository.main.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.main.repository_id}"
}
