# ---------------------------------------------------------------------------
# ESO namespace. Created explicitly so we can attach the WI annotation on
# the ServiceAccount before ESO's helm chart renders its own.
# ---------------------------------------------------------------------------

resource "kubernetes_namespace" "eso" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
      "app.kubernetes.io/part-of"    = "external-secrets"
    }
  }
}

# ---------------------------------------------------------------------------
# External Secrets Operator install. Pulled from the official chart repo.
# The chart creates a ServiceAccount named `external-secrets` by default;
# we annotate it for Workload Identity via chart values.
# ---------------------------------------------------------------------------

resource "helm_release" "external_secrets" {
  name       = "external-secrets"
  namespace  = kubernetes_namespace.eso.metadata[0].name
  repository = "https://charts.external-secrets.io"
  chart      = "external-secrets"
  version    = var.chart_version

  # Wait for CRDs + controller pods to be Ready before Terraform returns.
  # CRDs are the critical bit — chart templates that depend on ESO (in our
  # case `templates/external-secret.yaml`) fail if CRDs aren't installed yet.
  wait          = true
  timeout       = 300
  atomic        = true
  recreate_pods = false

  values = [yamlencode({
    installCRDs = true
    replicaCount = var.replica_count
    serviceAccount = {
      create = true
      name   = "external-secrets"
      annotations = {
        "iam.gke.io/gcp-service-account" = var.app_service_account_email
      }
    }
    # ESO has a webhook + cert-controller by default. 1 replica each is fine.
    webhook = {
      replicaCount = var.replica_count
    }
    certController = {
      replicaCount = var.replica_count
    }
  })]
}
