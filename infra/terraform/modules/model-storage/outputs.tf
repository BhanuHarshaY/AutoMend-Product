output "bucket_name" {
  description = "GCS bucket name (just the name, not the `gs://` URL)."
  value       = google_storage_bucket.models.name
}

output "bucket_url" {
  description = "Full `gs://BUCKET` URL. Feed to `gcloud storage cp` or pod initContainers."
  value       = google_storage_bucket.models.url
}
