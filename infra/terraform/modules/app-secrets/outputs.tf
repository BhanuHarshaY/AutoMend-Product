output "secret_ids" {
  description = "Map of short logical key → fully-qualified Secret Manager resource id. The ExternalSecret chart template references these."
  value       = { for k, s in google_secret_manager_secret.managed : k => s.secret_id }
}

output "secret_id_prefix" {
  description = "Shared prefix applied to every managed secret name. Useful if a consumer wants to list / filter them."
  value       = var.name_prefix
}
