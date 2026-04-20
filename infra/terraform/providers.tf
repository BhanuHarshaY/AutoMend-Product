# Cloud providers configured from root-module variables. Both google and
# google-beta are wired because some resources we'll provision in later
# tasks (pgvector on Cloud SQL, certain GKE Autopilot flags) are only
# exposed by the beta provider.
#
# The `kubernetes` + `helm` providers are intentionally NOT configured
# here — Task 12.2 brings up the GKE cluster and passes its endpoint +
# CA cert into those providers as inputs. Leaving them unconfigured at
# this stage is fine: Terraform lazily configures providers only when
# a resource references them.

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# ---------------------------------------------------------------------------
# Kubernetes + Helm providers configured from the GKE module's outputs.
# Task 12.6 will use `helm_release` to install AutoMend via these; for now
# no resources reference them, so they just sit idle.
#
# NB: the first `terraform apply` creates the cluster and then the providers
# become usable. This is the standard "provider depends on resource in same
# root" pattern — Terraform handles unknown-at-plan-time values correctly so
# long as no resource in this plan actually references the provider.
# ---------------------------------------------------------------------------

data "google_client_config" "default" {}

provider "kubernetes" {
  host                   = "https://${module.gke.cluster_endpoint}"
  cluster_ca_certificate = base64decode(module.gke.cluster_ca_certificate)
  token                  = data.google_client_config.default.access_token
}

provider "helm" {
  kubernetes {
    host                   = "https://${module.gke.cluster_endpoint}"
    cluster_ca_certificate = base64decode(module.gke.cluster_ca_certificate)
    token                  = data.google_client_config.default.access_token
  }
}
