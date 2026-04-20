# Remote state in Google Cloud Storage.
#
# The bucket itself is NOT managed by Terraform — it's a bootstrap resource
# that has to exist before `terraform init` can store any state in it
# (chicken-and-egg). Create it one-off before the first `init`:
#
#   gcloud storage buckets create gs://<BUCKET_NAME> \
#     --project=<PROJECT_ID> --location=<REGION> --uniform-bucket-level-access
#   gcloud storage buckets update gs://<BUCKET_NAME> --versioning
#
# Then init with the bucket name passed as partial-config:
#
#   terraform -chdir=infra/terraform init \
#     -backend-config="bucket=<BUCKET_NAME>"
#
# The `prefix` is baked in so multiple environments could later share the same
# bucket with different prefixes (e.g. `automend/dev/state`) without colliding.

terraform {
  backend "gcs" {
    prefix = "automend/state"
    # bucket is deliberately NOT set here — passed via -backend-config
    # at init time so the same code works for dev / staging / prod buckets
    # without a file-per-env.
  }
}
