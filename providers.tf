terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
  backend "s3" {
    bucket       = "opsfabric-terraform-states"
    key          = "sample-project/terraform.tfstate"
    region       = "ca-central-1"
    profile      = "vaishal"
    use_lockfile = true
  }
}

provider "aws" {
  region  = var.region
  profile = "vaishal"
}
