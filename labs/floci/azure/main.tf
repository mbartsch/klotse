# Floci Azure must have TLS enabled and its local CA trusted by Terraform.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
  use_cli         = false
  use_msi         = false
  use_oidc        = false
  environment     = "stack"
  metadata_host   = "localhost:4577"
  subscription_id = "00000000-0000-0000-0000-000000000001"
  tenant_id       = "00000000-0000-0000-0000-000000000002"
  client_id       = "11111111-1111-1111-1111-111111111111"
  client_secret   = "floci-az-dev-secret"
}

resource "azurerm_resource_group" "lab" {
  name     = "klotse-local-lab"
  location = "eastus"
}

resource "azurerm_kubernetes_cluster" "lab" {
  for_each            = toset(["klotse-aks-1", "klotse-aks-2"])
  name                = each.key
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  dns_prefix          = each.key

  default_node_pool {
    name       = "lab"
    node_count = 2
    vm_size    = "Standard_D2_v2"
  }

  identity {
    type = "SystemAssigned"
  }
}

output "clusters" {
  value = { for name, cluster in azurerm_kubernetes_cluster.lab : name => cluster.id }
}

output "kubeconfigs" {
  sensitive = true
  value     = { for name, cluster in azurerm_kubernetes_cluster.lab : name => cluster.kube_config_raw }
}
