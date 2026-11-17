# Local emulator only. Do not repurpose this provider for a cloud account.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    ec2 = "http://127.0.0.1:4566"
    eks = "http://127.0.0.1:4566"
    iam = "http://127.0.0.1:4566"
    sts = "http://127.0.0.1:4566"
  }
}

locals {
  clusters = toset(["klotse-eks-1", "klotse-eks-2"])
}

resource "aws_vpc" "lab" {
  cidr_block = "10.80.0.0/16"
  tags       = { Name = "klotse-local-lab" }
}

resource "aws_subnet" "lab" {
  for_each          = { a = "10.80.1.0/24", b = "10.80.2.0/24" }
  vpc_id            = aws_vpc.lab.id
  cidr_block        = each.value
  availability_zone = "us-east-1${each.key}"
}

resource "aws_iam_role" "cluster" {
  name = "klotse-local-eks"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "eks.amazonaws.com" }
    }]
  })
}

resource "aws_eks_cluster" "lab" {
  for_each = local.clusters
  name     = each.key
  role_arn = aws_iam_role.cluster.arn

  vpc_config {
    subnet_ids = [for subnet in aws_subnet.lab : subnet.id]
  }
}

output "clusters" {
  value = { for name, cluster in aws_eks_cluster.lab : name => {
    id       = cluster.id
    endpoint = cluster.endpoint
  } }
}
