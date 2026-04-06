terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ─────────────────────────────────────────────
# Networking
# ─────────────────────────────────────────────

resource "google_compute_network" "vpc" {
  name                    = "v8-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name          = "v8-subnet"
  ip_cidr_range = "10.0.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
}

resource "google_compute_global_address" "sql_private" {
  name          = "v8-sql-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "sql_vpc" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql_private.name]
}

resource "google_vpc_access_connector" "connector" {
  name          = "v8-connector"
  region        = var.region
  network       = google_compute_network.vpc.name
  ip_cidr_range = "10.8.0.0/28"
  min_instances = 2
  max_instances = 3
}

# ─────────────────────────────────────────────
# Cloud SQL — Postgres 16
# ─────────────────────────────────────────────

resource "google_sql_database_instance" "pg" {
  name             = "v8-citadel"
  database_version = "POSTGRES_16"
  region           = var.region

  depends_on = [google_service_networking_connection.sql_vpc]

  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    disk_size         = 10
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }

    backup_configuration {
      enabled    = true
      start_time = "04:00"

      backup_retention_settings {
        retained_backups = 7
      }
    }

    database_flags {
      name  = "max_connections"
      value = "50"
    }
  }

  deletion_protection = true
}

resource "google_sql_database" "db" {
  name     = "v8engine"
  instance = google_sql_database_instance.pg.name
}

resource "google_sql_user" "operator" {
  name     = var.db_user
  instance = google_sql_database_instance.pg.name
  password = var.db_password
}

# ─────────────────────────────────────────────
# IAM + Secrets
# ─────────────────────────────────────────────

resource "google_service_account" "runner" {
  account_id   = "v8-runner"
  display_name = "V8 Engine Runner"
}

resource "google_project_iam_member" "runner_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_project_iam_member" "runner_secrets" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_project_iam_member" "runner_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_secret_manager_secret" "db_pass" {
  secret_id = "v8-db-password"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "db_pass_v" {
  secret      = google_secret_manager_secret.db_pass.id
  secret_data = var.db_password
}

# ─────────────────────────────────────────────
# Artifact Registry
# ─────────────────────────────────────────────

resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = "v8-services"
  format        = "DOCKER"
}

# ─────────────────────────────────────────────
# Cloud Run Jobs (batch compute — pay per second)
# ─────────────────────────────────────────────

resource "google_cloud_run_v2_job" "ingestor" {
  name     = "v8-ingestor"
  location = var.region

  template {
    task_count = 1

    template {
      timeout     = "600s"
      max_retries = 1

      vpc_access {
        connector = google_vpc_access_connector.connector.id
        egress    = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image = "us-docker.pkg.dev/cloudrun/container/hello:latest"

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        env {
          name  = "DB_HOST"
          value = google_sql_database_instance.pg.private_ip_address
        }

        env {
          name  = "DB_NAME"
          value = google_sql_database.db.name
        }

        env {
          name  = "DB_USER"
          value = var.db_user
        }

        env {
          name = "DB_PASSWORD"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.db_pass.secret_id
              version = "latest"
            }
          }
        }
      }

      service_account = google_service_account.runner.email
    }
  }
}

resource "google_cloud_run_v2_job" "modeler" {
  name     = "v8-modeler"
  location = var.region

  template {
    task_count = 1

    template {
      timeout     = "1800s"
      max_retries = 1

      vpc_access {
        connector = google_vpc_access_connector.connector.id
        egress    = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image = "us-docker.pkg.dev/cloudrun/container/hello:latest"

        resources {
          limits = {
            cpu    = "2"
            memory = "4Gi"
          }
        }

        env {
          name  = "DB_HOST"
          value = google_sql_database_instance.pg.private_ip_address
        }

        env {
          name  = "DB_NAME"
          value = google_sql_database.db.name
        }

        env {
          name  = "DB_USER"
          value = var.db_user
        }

        env {
          name = "DB_PASSWORD"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.db_pass.secret_id
              version = "latest"
            }
          }
        }
      }

      service_account = google_service_account.runner.email
    }
  }
}

resource "google_cloud_run_v2_job" "scanner" {
  name     = "v8-scanner"
  location = var.region

  template {
    task_count = 1

    template {
      timeout     = "300s"
      max_retries = 1

      vpc_access {
        connector = google_vpc_access_connector.connector.id
        egress    = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image = "us-docker.pkg.dev/cloudrun/container/hello:latest"

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        env {
          name  = "DB_HOST"
          value = google_sql_database_instance.pg.private_ip_address
        }

        env {
          name  = "DB_NAME"
          value = google_sql_database.db.name
        }

        env {
          name  = "DB_USER"
          value = var.db_user
        }

        env {
          name = "DB_PASSWORD"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.db_pass.secret_id
              version = "latest"
            }
          }
        }
      }

      service_account = google_service_account.runner.email
    }
  }
}

# Dashboard — Cloud Run Service (scales to zero)
resource "google_cloud_run_v2_service" "dashboard" {
  name     = "v8-dashboard"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello:latest"

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "DB_HOST"
        value = google_sql_database_instance.pg.private_ip_address
      }

      env {
        name  = "DB_NAME"
        value = google_sql_database.db.name
      }

      env {
        name  = "DB_USER"
        value = var.db_user
      }

      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_pass.secret_id
            version = "latest"
          }
        }
      }
    }

    service_account = google_service_account.runner.email
  }
}

# ─────────────────────────────────────────────
# Cloud Scheduler
# ─────────────────────────────────────────────

resource "google_cloud_scheduler_job" "ingest" {
  name     = "v8-ingest-nightly"
  schedule = "0 2 * * *"
  time_zone = "America/New_York"

  http_target {
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/v8-ingestor:run"
    http_method = "POST"

    oidc_token {
      service_account_email = google_service_account.runner.email
    }
  }
}

resource "google_cloud_scheduler_job" "model" {
  name     = "v8-model-nightly"
  schedule = "0 3 * * *"
  time_zone = "America/New_York"

  http_target {
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/v8-modeler:run"
    http_method = "POST"

    oidc_token {
      service_account_email = google_service_account.runner.email
    }
  }
}

resource "google_cloud_scheduler_job" "scan" {
  name     = "v8-scan"
  schedule = "0 4 * * *"
  time_zone = "America/New_York"

  http_target {
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/v8-scanner:run"
    http_method = "POST"

    oidc_token {
      service_account_email = google_service_account.runner.email
    }
  }
}

# ─────────────────────────────────────────────
# Budget Alert
# ─────────────────────────────────────────────

resource "google_billing_budget" "budget" {
  billing_account = var.billing_account
  display_name    = "V8 Engine Budget"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = "50"
    }
  }

  threshold_rules {
    threshold_percent = 0.6
  }

  threshold_rules {
    threshold_percent = 0.8
  }

  threshold_rules {
    threshold_percent = 1.0
  }
}
