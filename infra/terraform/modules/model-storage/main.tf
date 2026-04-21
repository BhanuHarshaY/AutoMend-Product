# ---------------------------------------------------------------------------
# GCS bucket — holds pretrained + finetuned model artifacts. Laid out by
# component:
#
#   gs://BUCKET/classifier/   - RoBERTa tokenizer + model weights
#   gs://BUCKET/architect/    - Qwen2.5 weights (or whatever arch is live)
#
# Pod initContainers pull these with `gcloud storage cp -r` before the main
# container starts. The mechanism is live even when the running pods ignore
# the weights (e.g. classifier service still on the regex stub) — that way
# a future `AUTOMEND_CLASSIFIER_ENDPOINT=/predict_anomaly` swap requires no
# infra change.
# ---------------------------------------------------------------------------

resource "google_storage_bucket" "models" {
  project                     = var.project_id
  name                        = var.bucket_name
  location                    = var.region
  storage_class               = var.storage_class
  force_destroy               = var.force_destroy
  uniform_bucket_level_access = true

  versioning {
    enabled = var.versioning_enabled
  }

  labels = var.labels
}

# ---------------------------------------------------------------------------
# IAM — app SA reads, CI SA (optional) writes.
# ---------------------------------------------------------------------------

resource "google_storage_bucket_iam_member" "app_reader" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.app_service_account_email}"
}

resource "google_storage_bucket_iam_member" "ci_writer" {
  count  = var.ci_service_account_email == "" ? 0 : 1
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.ci_service_account_email}"
}
