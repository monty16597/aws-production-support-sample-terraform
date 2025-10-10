# DynamoDB table with PK "id"
resource "aws_dynamodb_table" "users" {
  name         = "${var.project}-user-table"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "id"

  attribute {
    name = "id"
    type = "S"
  }

  tags = {
    App = "user-writer"
  }
}

# IAM role for Lambda
resource "aws_iam_role" "lambda_exec_role" {
  name               = "${var.project}-exec-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# CloudWatch Logs basic permissions
resource "aws_iam_role_policy_attachment" "cwlogs" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# DynamoDB PutItem permission (scoped to this table)
data "aws_iam_policy_document" "ddb_put" {
  statement {
    actions = [
      "dynamodb:PutItem",
    ]
    resources = [aws_dynamodb_table.users.arn]
  }
}

resource "aws_iam_policy" "ddb_put_policy" {
  name   = "${var.project}-ddb-put"
  policy = data.aws_iam_policy_document.ddb_put.json
}

# Lambda function
resource "aws_lambda_function" "user_writer" {
  function_name = "${var.project}-create-user-writer"
  role          = aws_iam_role.lambda_exec_role.arn
  runtime       = var.lambda_runtime
  handler       = "index.handler"
  filename      = "${path.module}/lambda/create-user/build.zip"
  # Ensure Terraform detects changes to the lambda package by hashing the zip
  source_code_hash = filebase64sha256("${path.module}/lambda/create-user/build.zip")
  memory_size      = var.lambda_memory_mb
  timeout          = var.lambda_timeout_s

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.users.name
    }
  }
}

# Public Lambda Function URL (no auth for simplicity)
resource "aws_lambda_function_url" "fn_url" {
  function_name      = aws_lambda_function.user_writer.arn
  authorization_type = "NONE"
  cors {
    allow_origins = ["*"]
    allow_methods = ["*"]
    allow_headers = ["*"]
  }
}

# (Optional) Log group with retention
resource "aws_cloudwatch_log_group" "lambda_lg" {
  name              = "/aws/lambda/${aws_lambda_function.user_writer.function_name}"
  retention_in_days = 14
}

output "lambda_function_url" {
  value       = aws_lambda_function_url.fn_url.function_url
  description = "Public Function URL for POST requests"
}

# SNS topic for alarm notifications
resource "aws_sns_topic" "alarm_topic" {
  name = "${var.project}-alarms"
}

resource "random_id" "secret_manager" {
  byte_length = 4
}
# Secret storing Jira connection details for the alarm handler
resource "aws_secretsmanager_secret" "jira_credentials" {
  name = "${var.project}/jira-${random_id.secret_manager.hex}"
}

resource "aws_secretsmanager_secret_version" "jira_credentials_version" {
  secret_id = aws_secretsmanager_secret.jira_credentials.id
  secret_string = jsonencode({
    JIRA_HOST         = var.jira_host_name
    JIRA_EMAIL        = var.jira_user_name
    JIRA_API_TOKEN    = var.jira_api_token
    JIRA_PROJECT_NAME = var.jira_project_name
  })
}

# Execution role for the Jira creator Lambda function
resource "aws_iam_role" "jira_lambda_exec_role" {
  name               = "${var.project}-jira-exec-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "jira_lambda_logs" {
  role       = aws_iam_role.jira_lambda_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "jira_lambda_secret_access" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.jira_credentials.arn]
  }
}

resource "aws_iam_role_policy" "jira_lambda_secret_access" {
  name   = "${var.project}-jira-secrets-access"
  role   = aws_iam_role.jira_lambda_exec_role.id
  policy = data.aws_iam_policy_document.jira_lambda_secret_access.json
}


# Lambda function that creates Jira issues from CloudWatch alarms
resource "aws_lambda_function" "jira_creator" {
  function_name = "${var.project}-jira-creator"
  role          = aws_iam_role.jira_lambda_exec_role.arn
  runtime       = var.lambda_runtime
  handler       = "index.handler"
  filename      = "${path.module}/lambda/jira_issue/build.zip"
  # Ensure Terraform detects changes to the lambda package by hashing the zip
  source_code_hash = filebase64sha256("${path.module}/lambda/jira_issue/build.zip")
  memory_size      = var.lambda_memory_mb
  timeout          = var.lambda_timeout_s

  environment {
    variables = {
      JIRA_SECRET_NAME = aws_secretsmanager_secret.jira_credentials.name
      JIRA_ALARM_LABEL = var.jira_lambda_ai_labels
    }
  }


  depends_on = [
    aws_iam_role_policy_attachment.jira_lambda_logs,
    aws_iam_role_policy.jira_lambda_secret_access,
    aws_secretsmanager_secret_version.jira_credentials_version,
  ]
}

# Subscribe the Jira Lambda to the alarm topic
resource "aws_sns_topic_subscription" "jira_lambda" {
  topic_arn = aws_sns_topic.alarm_topic.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.jira_creator.arn

  depends_on = [aws_lambda_permission.allow_sns_to_invoke_jira]
}

resource "aws_lambda_permission" "allow_sns_to_invoke_jira" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.jira_creator.arn
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alarm_topic.arn
}

# Metric filter: trigger on ANY ERROR, and extract the function name as a dimension
resource "aws_cloudwatch_log_metric_filter" "any_error" {
  name           = "${var.project}-any-error-filter"
  log_group_name = aws_cloudwatch_log_group.lambda_lg.name
  pattern        = "{ $.level = \"ERROR\" }"

  metric_transformation {
    name      = "ErrorCount"
    namespace = "Project1Lambda"
    value     = "1"
  }

  depends_on = [aws_cloudwatch_log_group.lambda_lg]
}

# Alarm: must select the SAME dimension key/value
resource "aws_cloudwatch_metric_alarm" "any_error_alarm" {
  alarm_name          = "${var.project}-error-alarm"
  alarm_description   = "Alarm for any ERROR logs in ${aws_cloudwatch_log_group.lambda_lg.name}"
  namespace           = "Project1Lambda"
  metric_name         = "ErrorCount"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarm_topic.arn]
}
