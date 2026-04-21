# ---------------------------------------------------------------------------
# Secret Manager entries for all non-auto-generated credentials AutoMend
# pods read via envFrom. ESO pulls these into a single k8s Secret named
# `automend-secrets` (see chart template `external-secret.yaml`).
#
# Terraform creates the secret RESOURCES + initial versions; the app SA
# gets accessor permission on each. Values come from:
#   - JWT secret: Terraform generates a random 64-char password, writes it here.
#   - Architect API key, Slack webhook/bot token, PagerDuty, Jira: created
#     with empty string; operator populates via `gcloud secrets versions add`
#     after the stack is up. Empty value means "feature off" in the backend.
#
# DB password (Cloud SQL, module 12.3) + Redis AUTH (Memorystore, module 12.4)
# are created by THOSE modules, not here — they're tied to the instance
# lifecycle. The ExternalSecret template pulls them from the same SM.
# ---------------------------------------------------------------------------

resource "random_password" "jwt" {
  length  = 64
  special = true
  override_special = "!#$%&*()_-=+[]{}<>:?"
}

locals {
  # Map of secret-id-suffix → initial value. Keys must match the ExternalSecret
  # template's `remoteRef.key` names.
  managed = {
    "jwt-secret"            = random_password.jwt.result
    "architect-api-key"     = ""
    "embedding-api-key"     = ""
    "slack-webhook-url"     = ""
    "slack-bot-token"       = ""
    "pagerduty-api-key"     = ""
    "jira-api-token"        = ""
  }
}

resource "google_secret_manager_secret" "managed" {
  for_each  = local.managed
  project   = var.project_id
  secret_id = "${var.name_prefix}-${each.key}"

  replication {
    auto {}
  }

  labels = var.labels
}

resource "google_secret_manager_secret_version" "managed" {
  for_each = local.managed

  secret      = google_secret_manager_secret.managed[each.key].id
  secret_data = each.value

  # Allow operator to overwrite with `gcloud secrets versions add` without
  # Terraform fighting them. Terraform still owns the secret RESOURCE; only
  # the initial version is ours.
  lifecycle {
    ignore_changes = [secret_data]
  }
}

# App SA reads every managed secret.
resource "google_secret_manager_secret_iam_member" "app_accessor" {
  for_each  = local.managed
  project   = var.project_id
  secret_id = google_secret_manager_secret.managed[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.app_service_account_email}"
}
