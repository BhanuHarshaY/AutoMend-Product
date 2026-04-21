variable "project_id" {
  description = "GCP project hosting the model-artifacts bucket."
  type        = string
}

variable "region" {
  description = "Region for the bucket. Should match the GKE region so pod-to-bucket pulls stay in-region (free egress)."
  type        = string
}

variable "bucket_name" {
  description = "Globally-unique bucket name. GCS bucket names are flat — no project prefixing for free — so include the project id or a distinctive suffix to avoid collisions."
  type        = string
}

variable "app_service_account_email" {
  description = "Pod-level app SA. Receives `roles/storage.objectViewer` so initContainers can `gcloud storage cp` from the bucket without a key."
  type        = string
}

variable "ci_service_account_email" {
  description = "CI/CD SA. Receives `roles/storage.objectAdmin` so future CI pipelines can push new model artifacts (e.g. post-training). Leave empty to skip."
  type        = string
  default     = ""
}

variable "storage_class" {
  description = "Bucket storage class. STANDARD for small / frequently-accessed model artifacts. NEARLINE / COLDLINE for archival snapshots."
  type        = string
  default     = "STANDARD"
}

variable "versioning_enabled" {
  description = "GCS object versioning. true = you can roll back a bad model push by restoring the prior version. Minor cost; negligible for small artifacts."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels applied to the bucket."
  type        = map(string)
  default     = {}
}

variable "force_destroy" {
  description = "Required to be true for `terraform destroy` to succeed on a non-empty bucket. Keep false in prod to avoid accidentally nuking uploaded weights."
  type        = bool
  default     = false
}
