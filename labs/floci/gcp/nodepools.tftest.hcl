mock_provider "google" {}



run "two_node_pools" {
  command = plan

  assert {
    condition     = length(google_container_node_pool.lab) == 2 && alltrue([for pool in google_container_node_pool.lab : pool.initial_node_count == 2])
    error_message = "Both clusters must have a pool configured with two nodes."
  }
}
