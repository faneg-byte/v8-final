output "sql_private_ip" {
  description = "Cloud SQL private IP"
  value       = google_sql_database_instance.pg.private_ip_address
}

output "sql_connection_name" {
  description = "Cloud SQL connection name"
  value       = google_sql_database_instance.pg.connection_name
}

output "dashboard_url" {
  description = "Dashboard URL"
  value       = google_cloud_run_v2_service.dashboard.uri
}

output "artifact_registry" {
  description = "Docker registry path"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/v8-services"
}
