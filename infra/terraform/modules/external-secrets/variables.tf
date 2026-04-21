variable "namespace" {
  description = "Kubernetes namespace to install ESO into. Convention: `external-secrets`."
  type        = string
  default     = "external-secrets"
}

variable "chart_version" {
  description = "external-secrets chart version from https://charts.external-secrets.io. 0.10.x is the current stable series."
  type        = string
  default     = "0.10.3"
}

variable "replica_count" {
  description = "ESO controller replicas. 1 is fine for dev; 2+ for prod HA."
  type        = number
  default     = 1
}

variable "app_service_account_email" {
  description = "App GCP SA email. ESO's k8s ServiceAccount will be annotated with this for Workload Identity — ESO pods impersonate the app SA to pull from Secret Manager."
  type        = string
}
