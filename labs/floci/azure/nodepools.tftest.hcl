mock_provider "azurerm" {}



run "two_node_pools" {
  command = plan

  assert {
    condition     = length(azurerm_kubernetes_cluster.lab) == 2 && alltrue([for pool in azurerm_kubernetes_cluster.lab : pool.default_node_pool[0].node_count == 2])
    error_message = "Both clusters must have a pool configured with two nodes."
  }
}
