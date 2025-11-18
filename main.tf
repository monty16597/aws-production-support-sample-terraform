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
  function_name = "${var.project}-create-user"
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


resource "aws_sns_topic" "alarm_topic" {
  name = "${var.project}-alarms"
}
