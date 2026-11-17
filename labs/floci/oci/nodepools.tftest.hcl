mock_provider "oci" {}

variables { CLIENT_HOST_OVERRIDES = "oci_containerengine.ContainerEngineClient=http://127.0.0.1:4599" }

run "two_node_pools" {
  command = plan

  assert {
    condition     = length(oci_containerengine_node_pool.lab) == 2 && alltrue([for pool in oci_containerengine_node_pool.lab : pool.node_config_details[0].size == 2])
    error_message = "Both clusters must have a pool configured with two nodes."
  }
}
