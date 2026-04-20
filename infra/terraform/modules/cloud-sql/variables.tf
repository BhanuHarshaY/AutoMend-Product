variable "project_id" {
  description = "GCP project id hosting the Cloud SQL instance."
  type        = string
}

variable "region" {
  description = "Region for the instance. Must match the VPC's region."
  type        = string
}

variable "instance_name" {
  description = "Cloud SQL instance name. Cloud SQL refuses to reuse an instance name for ~1 week after deletion, so prefer short but distinctive strings."
  type        = string
}

variable "db_name" {
  description = "Application database name. Created inside the instance."
  type        = string
  default     = "automend"
}

variable "db_user" {
  description = "Application database user. Password is auto-generated and stored in Secret Manager."
  type        = string
  default     = "automend"
}

variable "tier" {
  description = "Machine tier for the instance. `db-custom-<vCPUs>-<MB>`. Default is 2 vCPU / 7.5 GB for dev."
  type        = string
  default     = "db-custom-2-7680"
}

variable "postgres_version" {
  description = "Cloud SQL Postgres version. Must be 15 or newer for pgvector support."
  type        = string
  default     = "POSTGRES_15"
}

variable "availability_type" {
  description = "`ZONAL` (single zone, cheaper — dev default) or `REGIONAL` (synchronous replica in another zone — prod)."
  type        = string
  default     = "ZONAL"

  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.availability_type)
    error_message = "availability_type must be ZONAL or REGIONAL."
  }
}

variable "disk_size_gb" {
  description = "Initial disk size in GB. Autoresize is enabled so this is a starting point, not a cap."
  type        = number
  default     = 20
}

variable "disk_autoresize_limit_gb" {
  description = "Upper bound for disk autoresize. Prevents a runaway table from growing into a 5-figure bill."
  type        = number
  default     = 100
}

variable "network_self_link" {
  description = "Self-link of the VPC for private IP. Must have a Private Service Access connection already set up (owned by root module)."
  type        = string
}

variable "app_service_account_email" {
  description = "Email of the GCP service account the app pods will use. Receives `roles/cloudsql.client` + `roles/cloudsql.instanceUser` so it can dial the instance via the Cloud SQL Auth Proxy or IAM authentication."
  type        = string
}

variable "labels" {
  description = "Labels applied to the instance."
  type        = map(string)
  default     = {}
}

variable "deletion_protection" {
  description = "Cloud SQL's two-tier delete guard. Set true in prod to prevent accidental `terraform destroy` (and the web console's delete button)."
  type        = bool
  default     = false
}
