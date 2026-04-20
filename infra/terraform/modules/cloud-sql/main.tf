# ---------------------------------------------------------------------------
# Application password — generated once by Terraform, stored in Secret
# Manager. Plaintext lives in Terraform state (unavoidable — Cloud SQL needs
# the string at create time) but never appears in source or outputs.
# ---------------------------------------------------------------------------

resource "random_password" "db_app" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"  # omit quotes / slashes — friendly to pgconn strings
}

resource "google_secret_manager_secret" "db_app_password" {
  project   = var.project_id
  secret_id = "${var.instance_name}-db-app-password"

  replication {
    auto {}
  }

  labels = var.labels
}

resource "google_secret_manager_secret_version" "db_app_password" {
  secret      = google_secret_manager_secret.db_app_password.id
  secret_data = random_password.db_app.result
}

# ---------------------------------------------------------------------------
# The instance. Postgres 15, private IP only, daily backups + PITR.
# ---------------------------------------------------------------------------

resource "google_sql_database_instance" "primary" {
  project          = var.project_id
  name             = var.instance_name
  region           = var.region
  database_version = var.postgres_version

  deletion_protection = var.deletion_protection

  settings {
    tier              = var.tier
    availability_type = var.availability_type
    disk_type         = "PD_SSD"
    disk_size         = var.disk_size_gb
    disk_autoresize   = true
    disk_autoresize_limit = var.disk_autoresize_limit_gb

    user_labels = var.labels

    # Private IP only — no public endpoint. Relies on a Private Service Access
    # peering set up in the root module.
    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = var.network_self_link
      enable_private_path_for_google_cloud_services = true
    }

    # Daily backups at 02:00 UTC + point-in-time recovery via WAL archival.
    backup_configuration {
      enabled                        = true
      start_time                     = "02:00"
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }

    # IAM database auth — lets the app SA (from var.app_service_account_email)
    # authenticate without a password via the Cloud SQL Auth Proxy's
    # automatic-iam-authn mode.
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    # Performance / visibility flags.
    database_flags {
      name  = "pg_stat_statements.track"
      value = "all"
    }
    database_flags {
      name  = "log_min_duration_statement"
      value = "1000" # log queries slower than 1s
    }
    database_flags {
      name  = "log_connections"
      value = "on"
    }
    database_flags {
      name  = "log_disconnections"
      value = "on"
    }

    # pgvector must be in `cloudsql.enable_google_ml_integration` OR
    # explicitly `CREATE EXTENSION vector;` — Cloud SQL ships the
    # binary but the extension is not enabled by default. Task 12.6's
    # Helm post-install Job runs `CREATE EXTENSION IF NOT EXISTS vector;`
    # against this instance.

    insights_config {
      query_insights_enabled  = true
      query_plans_per_minute  = 5
      query_string_length     = 1024
      record_application_tags = false
      record_client_address   = false
    }

    maintenance_window {
      day          = 7 # Sunday
      hour         = 3 # 03:00 UTC
      update_track = "stable"
    }
  }

  lifecycle {
    # Don't let a flag add/remove trigger a full instance rebuild if Cloud SQL
    # reorders them in its API response.
    ignore_changes = [settings[0].database_flags]
  }
}

# ---------------------------------------------------------------------------
# The application database inside the instance.
# ---------------------------------------------------------------------------

resource "google_sql_database" "app" {
  project  = var.project_id
  name     = var.db_name
  instance = google_sql_database_instance.primary.name

  # UTF-8 + en_US collation, same as the alembic migration assumes.
  charset   = "UTF8"
  collation = "en_US.UTF8"
}

# ---------------------------------------------------------------------------
# Application user — password-based. IAM-authn is also enabled above so the
# Helm chart can choose either auth mode; password works with the existing
# connection string shape (`postgresql+asyncpg://USER:PASS@HOST/DB`).
# ---------------------------------------------------------------------------

resource "google_sql_user" "app" {
  project  = var.project_id
  name     = var.db_user
  instance = google_sql_database_instance.primary.name
  password = random_password.db_app.result
  type     = "BUILT_IN"
}

# ---------------------------------------------------------------------------
# IAM — give the app service account cloudsql.client (Auth-Proxy connections)
# + cloudsql.instanceUser (IAM-authn logins).
# ---------------------------------------------------------------------------

resource "google_project_iam_member" "app_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${var.app_service_account_email}"
}

resource "google_project_iam_member" "app_cloudsql_instance_user" {
  project = var.project_id
  role    = "roles/cloudsql.instanceUser"
  member  = "serviceAccount:${var.app_service_account_email}"
}

# Secret Manager read access so the pods (via Workload Identity) can
# fetch the DB password at startup. Task 12.6 mounts this via the CSI
# driver or External Secrets Operator.
resource "google_secret_manager_secret_iam_member" "app_password_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.db_app_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.app_service_account_email}"
}
