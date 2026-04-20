variable "project_id" {
  description = "GCP project id hosting the registry."
  type        = string
}

variable "region" {
  description = "Region for the registry. Must match the region where GKE nodes run so image pulls are local and free."
  type        = string
}

variable "repository_id" {
  description = "Short id for the repository (e.g. `automend`). Final image URL shape: `REGION-docker.pkg.dev/PROJECT/REPOSITORY_ID/IMAGE:TAG`."
  type        = string
  default     = "automend"
}

variable "description" {
  description = "Human-readable description shown in the GCP console."
  type        = string
  default     = "AutoMend container images — built by GitHub Actions, pulled by GKE."
}

variable "node_service_account_email" {
  description = "GKE node SA email. Receives `roles/artifactregistry.reader` on this repo so nodes can pull images without extra configuration."
  type        = string
}

variable "ci_service_account_email" {
  description = "CI/CD service account email (the one GitHub Actions impersonates via WIF). Receives `roles/artifactregistry.writer` so it can push new images. Optional — leave empty and wire the binding elsewhere if CI lives outside this module's scope."
  type        = string
  default     = ""
}

variable "keep_recent_count" {
  description = "Per-image cleanup policy: keep this many most-recent tagged versions. Older tagged versions are auto-deleted."
  type        = number
  default     = 10
}

variable "delete_untagged_after_days" {
  description = "Delete untagged images (orphans from failed builds) after this many days."
  type        = number
  default     = 7
}

variable "labels" {
  description = "Labels applied to the repository."
  type        = map(string)
  default     = {}
}
