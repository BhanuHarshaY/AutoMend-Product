output "instance_name" {
  description = "Instance name."
  value       = google_redis_instance.primary.name
}

output "host" {
  description = "Private IP of the Redis primary. Reachable from any pod on the VPC."
  value       = google_redis_instance.primary.host
}

output "port" {
  description = "Redis port (always 6379, but surfaced as output for symmetry + future-proofing)."
  value       = google_redis_instance.primary.port
}

output "current_location_id" {
  description = "Zone of the current primary (for STANDARD_HA — the replica lives in the sibling zone)."
  value       = google_redis_instance.primary.current_location_id
}

output "auth_secret_id" {
  description = "Fully-qualified Secret Manager resource id holding the AUTH string."
  value       = google_secret_manager_secret.auth.id
}

output "auth_secret_name" {
  description = "Short form of the auth secret — just the `secret_id`. For ExternalSecret ObjectName references."
  value       = google_secret_manager_secret.auth.secret_id
}

output "server_ca_cert" {
  description = "Memorystore's server CA cert (PEM). Pods need this in their TLS trust store to connect over 6379/TLS. Not a secret — mount as a ConfigMap. Marked sensitive to keep it out of regular plan diffs."
  value       = length(google_redis_instance.primary.server_ca_certs) > 0 ? google_redis_instance.primary.server_ca_certs[0].cert : ""
  sensitive   = true
}
