variable "project" {
  description = "Project name prefix"
  type        = string
  default     = "project1"
}


variable "lambda_runtime" {
  description = "Lambda runtime"
  type        = string
  default     = "python3.12"
}

variable "lambda_memory_mb" {
  description = "Lambda memory in MB"
  type        = number
  default     = 256
}

variable "lambda_timeout_s" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 10
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-2"
}
