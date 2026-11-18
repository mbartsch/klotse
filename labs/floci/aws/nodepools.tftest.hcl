mock_provider "aws" {}



run "two_node_pools" {
  command = plan

  assert {
    condition     = length(aws_eks_node_group.lab) == 2 && alltrue([for pool in aws_eks_node_group.lab : pool.scaling_config[0].desired_size == 2 && pool.scaling_config[0].min_size == 2 && pool.scaling_config[0].max_size == 2])
    error_message = "Both clusters must have a pool configured with two nodes."
  }
}
