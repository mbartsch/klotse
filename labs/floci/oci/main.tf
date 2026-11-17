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

variable "oci_config_file" {
  type        = string
  default     = "~/.oci/floci-config"
  description = "Throwaway Floci OCI profile (INI, [DEFAULT]). Its tenancy, user, fingerprint, key_file and region are read here so they cannot drift from the signing key. Never point this at a real OCI config."
}

locals {
  # Parse key=value lines (the [DEFAULT] header is skipped). Values are read
  # verbatim, so OCIDs and colon-separated fingerprints need no quoting.
  oci_profile_lines = [
    for line in split("\n", file(pathexpand(var.oci_config_file))) :
    trimspace(line) if can(regex("^[a-z_]+=", trimspace(line)))
  ]
  oci_profile = {
    for line in local.oci_profile_lines :
    trimspace(split("=", line)[0]) => trimspace(join("=", slice(split("=", line), 1, length(split("=", line)))))
  }
  tenancy = local.oci_profile["tenancy"]
}

provider "oci" {
  auth                 = "APIKey"
  tenancy_ocid         = local.tenancy
  user_ocid            = local.oci_profile["user"]
  fingerprint          = local.oci_profile["fingerprint"]
  private_key_path     = pathexpand(local.oci_profile["key_file"])
  region               = local.oci_profile["region"]
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
