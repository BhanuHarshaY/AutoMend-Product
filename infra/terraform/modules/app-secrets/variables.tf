variable "project_id" {
  description = "GCP project id."
  type        = string
}

variable "name_prefix" {
  description = "Name prefix applied to every secret id — lets multiple envs coexist in one project."
  type        = string
}

variable "app_service_account_email" {
  description = "App SA email. Receives `secretmanager.secretAccessor` on each secret so the pod can read them via ESO."
  type        = string
}

variable "labels" {
  description = "Labels applied to each secret."
  type        = map(string)
  default     = {}
}
