# Root-module inputs. Values come from terraform.tfvars (gitignored) or
# -var / TF_VAR_* env overrides. `project_id` has no default — you must
# be explicit about which project you're pointing at, always.

variable "project_id" {
  description = "GCP project that owns every resource Terraform creates."
  type        = string
}

variable "region" {
  description = "Default region for regional resources (GKE, Cloud SQL, Memorystore, Artifact Registry). us-central1 is the cheapest Gemini-enabled region as of 2026-04."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Default zone for any zonal resources that slip in (preferring regional resources wherever possible)."
  type        = string
  default     = "us-central1-a"
}

variable "env" {
  description = "Environment tag used in resource names and labels. One of dev / staging / prod."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.env)
    error_message = "env must be one of: dev, staging, prod."
  }
}

variable "name_prefix" {
  description = "Resource-name prefix. Applied to everything Terraform creates so parallel environments in the same project don't collide."
  type        = string
  default     = "automend"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,28}$", var.name_prefix))
    error_message = "name_prefix must start with a lowercase letter, contain only lowercase letters / digits / hyphens, and be ≤29 chars (leaves headroom for suffixes)."
  }
}

# ---------------------------------------------------------------------------
# Network + GKE inputs (Task 12.2)
# ---------------------------------------------------------------------------

variable "subnet_cidr" {
  description = "Primary CIDR for the VPC subnet hosting GKE nodes. Should not overlap pod_cidr or service_cidr."
  type        = string
  default     = "10.10.0.0/20"
}

variable "pod_cidr" {
  description = "Secondary range for Pod IPs. VPC-native GKE assigns each pod a routable address in this range. /16 gives ~65k pod IPs."
  type        = string
  default     = "10.20.0.0/16"
}

variable "service_cidr" {
  description = "Secondary range for Service ClusterIPs."
  type        = string
  default     = "10.30.0.0/20"
}

variable "master_ipv4_cidr_block" {
  description = "/28 CIDR for the GKE control-plane peer network. Must not overlap VPC subnets. Immutable after cluster creation."
  type        = string
  default     = "172.16.0.0/28"
}

variable "master_authorized_networks" {
  description = "CIDR blocks allowed direct access to the cluster master API. Empty = master only reachable through GCP's proxy. Add your office/home/VPN range for direct `kubectl` access."
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

variable "gke_node_count" {
  description = "Nodes per zone in the primary node pool. Regional cluster spans 3 zones, so 1 here = 3 nodes total. Scale up for real workloads."
  type        = number
  default     = 1
}

variable "gke_machine_type" {
  description = "Compute Engine machine type for nodes. e2-standard-4 = 4 vCPU / 16 GB."
  type        = string
  default     = "e2-standard-4"
}

# ---------------------------------------------------------------------------
# Cloud SQL inputs (Task 12.3)
# ---------------------------------------------------------------------------

variable "cloudsql_tier" {
  description = "Machine tier for the Cloud SQL Postgres instance. `db-custom-<vCPUs>-<MB>`. Default 2 vCPU / 7.5 GB for dev."
  type        = string
  default     = "db-custom-2-7680"
}

variable "cloudsql_disk_size_gb" {
  description = "Initial disk size for Cloud SQL. Autoresize is on (capped at module default of 100 GB)."
  type        = number
  default     = 20
}

# ---------------------------------------------------------------------------
# Memorystore inputs (Task 12.4)
# ---------------------------------------------------------------------------

variable "memorystore_tier" {
  description = "Memorystore tier. Dev can use BASIC (cheaper, no failover). Prod is always forced to STANDARD_HA by root main.tf regardless of this value."
  type        = string
  default     = "STANDARD_HA"

  validation {
    condition     = contains(["BASIC", "STANDARD_HA"], var.memorystore_tier)
    error_message = "memorystore_tier must be BASIC or STANDARD_HA."
  }
}

variable "memorystore_memory_size_gb" {
  description = "Memorystore memory in GB. STANDARD_HA minimum is 1 GB. AutoMend's Redis usage fits well under 1 GB."
  type        = number
  default     = 1
}

# ---------------------------------------------------------------------------
# CI/CD inputs (Task 12.5)
# ---------------------------------------------------------------------------

variable "k8s_namespace" {
  description = "Namespace where the Helm chart deploys AutoMend pods. Drives the Workload Identity binding (`serviceAccount:PROJECT.svc.id.goog[NAMESPACE/K8S_SA]`)."
  type        = string
  default     = "automend"
}

variable "k8s_service_account" {
  description = "Kubernetes ServiceAccount name the chart creates. Matches `automend.serviceAccountName` helper → release fullname = `automend` for the default install."
  type        = string
  default     = "automend"
}

variable "enable_external_secrets" {
  description = "Install External Secrets Operator + create the managed SM secrets the chart's ExternalSecret pulls (JWT, Gemini, Slack, etc.). Required when deploying with values-gcp.yaml. Safe to leave false if you're on values-gcp-quick (in-cluster subcharts + helm-managed Secret)."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "GitHub repository in `OWNER/REPO` form. When set, Terraform provisions a Workload Identity Federation pool + CI service account restricted to this repo's OIDC tokens. Empty string disables WIF + leaves the CI IAM bindings off (useful if you build/push images from a different CI system or manually)."
  type        = string
  default     = ""

  validation {
    condition     = var.github_repository == "" || can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must be empty or `OWNER/REPO` (e.g. `raghav52524/automend-ui-`)."
  }
}
