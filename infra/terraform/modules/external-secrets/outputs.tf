output "namespace" {
  description = "Namespace ESO runs in."
  value       = kubernetes_namespace.eso.metadata[0].name
}

output "service_account_name" {
  description = "k8s ServiceAccount ESO uses. Target for the Workload Identity binding on the GCP side."
  value       = "external-secrets"
}
