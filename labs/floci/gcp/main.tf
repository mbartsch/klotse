# GKE requires Floci's container.* host routing, not a /container/ base path.
# Resolve container.localhost to 127.0.0.1 before running Terraform.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.36"
    }
  }
}

provider "google" {
  project      = "klotse-local"
  region       = "us-central1"
  access_token = "floci-local-test-token"
  # Non-public universe skips the Google provider's external userinfo probe.
  universe_domain           = "localhost"
  user_project_override     = false
  container_custom_endpoint = "http://container.localhost:4588/"
}

locals {
  clusters = toset(["klotse-gke-1", "klotse-gke-2"])
}

resource "google_container_cluster" "lab" {
  for_each                 = local.clusters
  name                     = each.key
  location                 = "us-central1-a"
  initial_node_count       = 1
  remove_default_node_pool = true
  deletion_protection      = false
}

resource "google_container_node_pool" "lab" {
  for_each                  = local.clusters
  name                      = "lab"
  location                  = google_container_cluster.lab[each.key].location
  cluster                   = google_container_cluster.lab[each.key].name
  initial_node_count        = 2
  ignore_node_count_changes = true
}

output "clusters" {
  value = { for name, cluster in google_container_cluster.lab : name => {
    id       = cluster.id
    endpoint = cluster.endpoint
  } }
}
