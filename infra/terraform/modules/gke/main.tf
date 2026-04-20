# ---------------------------------------------------------------------------
# Node service account — minimum-privilege replacement for the default
# compute-engine SA (which gets the project Editor role and that's way too
# much for nodes to carry).
# ---------------------------------------------------------------------------

resource "google_service_account" "nodes" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-gke-nodes"
  display_name = "GKE node SA for ${var.cluster_name}"
  description  = "Least-privilege SA for kubelet + stackdriver agents. Pod-level perms come through Workload Identity, not this SA."
}

# Roles the kubelet + logging/monitoring agents actually need. Explicit list —
# the older "default node SA" approach handed out Editor which includes
# compute.instanceAdmin etc.
locals {
  node_roles = [
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    # Pull images from Artifact Registry in the same project (12.5 populates it).
    "roles/artifactregistry.reader",
  ]
}

resource "google_project_iam_member" "nodes" {
  for_each = toset(local.node_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.nodes.email}"
}

# ---------------------------------------------------------------------------
# The cluster. Regional (3-zone control plane + nodes). Private nodes so only
# egress traffic leaves the VPC; master endpoint is public but gated by
# master_authorized_networks.
# ---------------------------------------------------------------------------

resource "google_container_cluster" "primary" {
  provider = google-beta  # a few fields (dataplane v2 alternatives, stable-channel enrollments) need beta

  project  = var.project_id
  name     = var.cluster_name
  location = var.region

  # Idiom: declare the cluster with an initial default pool (size 1) then
  # remove it — Terraform lets you manage node pools as separate resources
  # only after the cluster exists.
  remove_default_node_pool = true
  initial_node_count       = 1

  network         = var.network
  subnetwork      = var.subnetwork
  networking_mode = "VPC_NATIVE"

  ip_allocation_policy {
    cluster_secondary_range_name  = var.pod_range_name
    services_secondary_range_name = var.service_range_name
  }

  # Workload Identity — the foundation for every "pod impersonates a GCP SA"
  # pattern we'll use in 12.3 (Cloud SQL auth), 12.5 (pushing telemetry), etc.
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  release_channel {
    channel = "REGULAR"
  }

  # CALICO network policy per task spec. Google also offers Dataplane v2
  # (which is a Cilium-based in-kernel eBPF alternative) — revisit when
  # bumping this cluster if CALICO becomes deprecated.
  network_policy {
    enabled  = true
    provider = "CALICO"
  }

  addons_config {
    network_policy_config {
      disabled = false
    }
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = var.master_ipv4_cidr_block
  }

  # Explicit-list the CIDRs that may hit the control plane. Empty list is
  # fine — it locks the API down entirely from the public internet; operators
  # then use `gcloud container clusters get-credentials` which routes through
  # GCP's proxy.
  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.master_authorized_networks
      content {
        cidr_block   = cidr_blocks.value.cidr_block
        display_name = cidr_blocks.value.display_name
      }
    }
  }

  enable_shielded_nodes = true

  # Drop the legacy client cert (Google already defaults to off for new
  # clusters but being explicit makes security-review easier to grep).
  master_auth {
    client_certificate_config {
      issue_client_certificate = false
    }
  }

  resource_labels = var.labels

  deletion_protection = var.deletion_protection

  lifecycle {
    # The default pool is immediately removed; ignore drift on its nested
    # node_config so Terraform doesn't fight itself.
    ignore_changes = [node_config]
  }
}

# ---------------------------------------------------------------------------
# Primary node pool: e2-standard-4, Shielded + Workload-Identity-capable.
# ---------------------------------------------------------------------------

resource "google_container_node_pool" "primary" {
  project  = var.project_id
  name     = "${var.cluster_name}-primary"
  location = var.region
  cluster  = google_container_cluster.primary.name

  # Regional cluster → one count per zone (3 zones per region).
  node_count = var.node_count

  node_config {
    machine_type    = var.machine_type
    service_account = google_service_account.nodes.email

    # Broad oauth scope is required for Workload Identity — pod-level access
    # is narrowed via IAM role bindings, not scope.
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    # GKE metadata server is required for Workload Identity to work inside
    # pods (translates k8s SA annotations into GCP access tokens).
    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    labels = var.labels
    tags   = ["gke-node", "${var.name_prefix}-gke-node"]
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  upgrade_settings {
    max_surge       = 1
    max_unavailable = 0
    strategy        = "SURGE"
  }
}
