# ---------------------------------------------------------------------------
# Docker repository. Region-local so image pulls from GKE nodes in the same
# region are free + low-latency.
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "main" {
  project       = var.project_id
  location      = var.region
  repository_id = var.repository_id
  description   = var.description
  format        = "DOCKER"

  labels = var.labels

  # Automatic cleanup — prevents the registry growing unboundedly as CI pushes
  # new SHA-tagged images on every merge.
  cleanup_policies {
    id     = "keep-recent-tagged"
    action = "KEEP"
    most_recent_versions {
      keep_count = var.keep_recent_count
    }
  }

  cleanup_policies {
    id     = "delete-old-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "${var.delete_untagged_after_days * 24}h"
    }
  }
}

# ---------------------------------------------------------------------------
# IAM — grant the GKE node SA read access so pods can pull images without
# imagePullSecrets.
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository_iam_member" "node_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.main.location
  repository = google_artifact_registry_repository.main.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${var.node_service_account_email}"
}

# ---------------------------------------------------------------------------
# IAM — grant the CI SA write access (if supplied). Optional so the module
# works standalone; the root module passes the CI SA from its WIF setup.
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository_iam_member" "ci_writer" {
  count      = var.ci_service_account_email == "" ? 0 : 1
  project    = var.project_id
  location   = google_artifact_registry_repository.main.location
  repository = google_artifact_registry_repository.main.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${var.ci_service_account_email}"
}
