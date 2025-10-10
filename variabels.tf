variable "ecs_desired_count" {
  description = "Desired number of ECS tasks"
  type        = number
  default     = 0
}

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

variable "jira_host_name" {
  description = "Base URL of the Jira instance (e.g. https://example.atlassian.net)"
  type        = string
  sensitive   = true
}

variable "jira_user_name" {
  description = "Jira user email used for API authentication"
  type        = string
  sensitive   = true
}

variable "jira_api_token" {
  description = "Jira API token for the configured user"
  type        = string
  sensitive   = true
}

variable "jira_project_name" {
  description = "Jira project name to create issues in"
  type        = string
}

variable "jira_lambda_ai_labels" {
  description = "Comma-separated list of AI labels to add to Jira issues created by the Lambda function"
  type        = string
  default     = "RCA, AI"
}


variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-2"
}
