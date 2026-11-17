# Local only: the OCI SDK reads this exact environment variable, not an HCL
# provider argument. Export TF_VAR_CLIENT_HOST_OVERRIDES before plan/apply/destroy.
# Do NOT pass it with -var or a tfvars file: those do not configure the SDK.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "9.12.1"
    }
  }
}

variable "CLIENT_HOST_OVERRIDES" {
  type        = string
  description = "Export TF_VAR_CLIENT_HOST_OVERRIDES=oci_containerengine.ContainerEngineClient=http://127.0.0.1:4599; never pass through -var/tfvars."
  validation {
    condition     = var.CLIENT_HOST_OVERRIDES == "oci_containerengine.ContainerEngineClient=http://127.0.0.1:4599"
    error_message = "Only the lab's loopback ContainerEngineClient override is allowed."
  }
}

variable "private_key_path" {
  type        = string
  default     = "./test_key.pem"
  description = "Locally generated throwaway RSA API signing key; never use a real OCI key."
}

locals {
  tenancy = "ocid1.tenancy.oc1..flocilocaltenancy0000000000000000000000000000000000000000"
}

provider "oci" {
  auth                 = "APIKey"
  tenancy_ocid         = local.tenancy
  user_ocid            = "ocid1.user.oc1..flociiacuser0000000000000000000000000000000000000000000000"
  fingerprint          = "aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99"
  private_key_path     = var.private_key_path
  region               = "us-ashburn-1"
  disable_auto_retries = true
}

# Synthetic network IDs match the emulator fixture: no real VCN is created.
resource "oci_containerengine_cluster" "lab" {
  for_each           = toset(["klotse-oke-1", "klotse-oke-2"])
  compartment_id     = local.tenancy
  name               = each.key
  vcn_id             = "ocid1.vcn.oc1.iad.tfvcn"
  kubernetes_version = "v1.30.1"
  type               = "ENHANCED_CLUSTER"

  endpoint_config {
    subnet_id            = "ocid1.subnet.oc1.iad.tfapisubnet"
    is_public_ip_enabled = false
    nsg_ids              = []
  }

  cluster_pod_network_options {
    cni_type = "FLANNEL_OVERLAY"
  }
}

resource "oci_containerengine_node_pool" "lab" {
  for_each           = oci_containerengine_cluster.lab
  cluster_id         = each.value.id
  compartment_id     = local.tenancy
  name               = "${each.key}-pool"
  kubernetes_version = "v1.30.1"
  node_shape         = "VM.Standard.E4.Flex"

  # Synthetic placement matches this local-only emulator's synthetic VCN.
  node_config_details {
    size = 2
    placement_configs {
      availability_domain = "floci:US-ASHBURN-AD-1"
      subnet_id           = "ocid1.subnet.oc1.iad.tfapisubnet"
    }
  }
}

output "clusters" {
  value = { for name, cluster in oci_containerengine_cluster.lab : name => cluster.id }
}
