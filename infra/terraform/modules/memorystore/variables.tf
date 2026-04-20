variable "project_id" {
  description = "GCP project id hosting the Memorystore instance."
  type        = string
}

variable "region" {
  description = "Region for the instance. Must match the VPC's region."
  type        = string
}

variable "instance_name" {
  description = "Memorystore instance name. ≤40 chars, lowercase + hyphens."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,38}[a-z0-9]$", var.instance_name))
    error_message = "instance_name must be a DNS-1123 label of length 2-40."
  }
}

variable "tier" {
  description = "`BASIC` (single primary, no failover — not recommended even for dev) or `STANDARD_HA` (1 read replica, automatic failover, ~2× cost). Default STANDARD_HA per task spec."
  type        = string
  default     = "STANDARD_HA"

  validation {
    condition     = contains(["BASIC", "STANDARD_HA"], var.tier)
    error_message = "tier must be BASIC or STANDARD_HA."
  }
}

variable "memory_size_gb" {
  description = "Memory size in GB. STANDARD_HA minimum is 1 GB. AutoMend's Redis usage (dedup keys + streams + broadcast) is well under 1 GB for dev."
  type        = number
  default     = 1
}

variable "redis_version" {
  description = "Memorystore Redis version. REDIS_7_2 is latest stable as of 2026-04."
  type        = string
  default     = "REDIS_7_2"
}

variable "authorized_network" {
  description = "Self-link of the VPC. Instance gets a private IP from the PSA peering range on this network."
  type        = string
}

variable "app_service_account_email" {
  description = "App SA email (from 12.3). Receives `roles/redis.editor` so it can describe the instance + `secretmanager.secretAccessor` on the auth secret."
  type        = string
}

variable "labels" {
  description = "Labels applied to the instance."
  type        = map(string)
  default     = {}
}
