variable "project_id" {
  description = "GCP project id hosting the cluster."
  type        = string
}

variable "region" {
  description = "Region for the regional cluster. Control plane is replicated across 3 zones in this region."
  type        = string
}

variable "cluster_name" {
  description = "GKE cluster name. Must be DNS-1123 label (≤40 chars to leave room for `-primary` node-pool suffix)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,38}[a-z0-9]$", var.cluster_name))
    error_message = "cluster_name must be a DNS-1123 label of length 2-40."
  }
}

variable "name_prefix" {
  description = "Prefix for auxiliary resources (service account, tags). Usually matches the root module's name_prefix."
  type        = string
  default     = "automend"
}

variable "network" {
  description = "Self-link of the VPC network the cluster attaches to. Create it in the root module so Cloud SQL / Memorystore can share it."
  type        = string
}

variable "subnetwork" {
  description = "Self-link of the subnetwork. Must have pod + service secondary ranges declared; see pod_range_name / service_range_name."
  type        = string
}

variable "pod_range_name" {
  description = "Name of the subnet's secondary range reserved for Pod IPs. Matches the range_name on google_compute_subnetwork."
  type        = string
  default     = "pods"
}

variable "service_range_name" {
  description = "Name of the subnet's secondary range reserved for Service ClusterIPs."
  type        = string
  default     = "services"
}

variable "node_count" {
  description = "Node count PER ZONE. A regional cluster lands 3 zones, so node_count=1 → 3 nodes total (one per zone, HA). For heavier demo workloads bump to 2 → 6 nodes."
  type        = number
  default     = 1
}

variable "machine_type" {
  description = "Compute Engine machine type for the node pool. e2-standard-4 = 4 vCPU / 16 GB; adequate for AutoMend's in-cluster Postgres/Redis/Temporal for small-team use."
  type        = string
  default     = "e2-standard-4"
}

variable "master_ipv4_cidr_block" {
  description = "CIDR for the control-plane peer network. Must be a /28 that doesn't overlap any other network in this project. Immutable after cluster creation."
  type        = string
  default     = "172.16.0.0/28"
}

variable "master_authorized_networks" {
  description = "CIDR blocks allowed to reach the control-plane API endpoint. Empty = the public endpoint is unreachable from the internet (operators use `gcloud container clusters get-credentials` through GCP's proxy). Add your office / home / VPN ranges here for direct kubectl access."
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

variable "labels" {
  description = "Labels applied to the cluster + every node in the pool."
  type        = map(string)
  default     = {}
}

variable "deletion_protection" {
  description = "Set true in prod to prevent accidental `terraform destroy`. Defaults false so dev can tear down freely."
  type        = bool
  default     = false
}
