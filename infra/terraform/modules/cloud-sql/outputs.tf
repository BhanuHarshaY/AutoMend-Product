output "instance_name" {
  description = "Instance name (not the fully-qualified connection string)."
  value       = google_sql_database_instance.primary.name
}

output "connection_name" {
  description = "Fully-qualified connection string `PROJECT:REGION:INSTANCE`. Pass to the Cloud SQL Auth Proxy via `-instances`."
  value       = google_sql_database_instance.primary.connection_name
}

output "private_ip" {
  description = "Private IP address of the instance, reachable from any pod on the VPC."
  value       = google_sql_database_instance.primary.private_ip_address
}

output "db_name" {
  description = "Application database name inside the instance."
  value       = google_sql_database.app.name
}

output "db_user" {
  description = "Application user name."
  value       = google_sql_user.app.name
}

output "db_password_secret_id" {
  description = "Secret Manager secret id holding the application password. Resource id (`projects/…/secrets/…`)."
  value       = google_secret_manager_secret.db_app_password.id
}

output "db_password_secret_name" {
  description = "Short form of the password secret — just the `secret_id`. Useful for External Secrets ObjectName references."
  value       = google_secret_manager_secret.db_app_password.secret_id
}
