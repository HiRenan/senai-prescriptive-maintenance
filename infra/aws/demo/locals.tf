locals {
  environment = "demo"
  name        = "${var.name_prefix}-${local.environment}"

  placeholder_account_id   = "000000000000"
  placeholder_image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

  required_tags = {
    Environment = local.environment
    Lifecycle   = "ephemeral-demo"
    ManagedBy   = "Terraform"
    Owner       = var.owner
    Profile     = "aws-demo"
    Project     = "prescriptive-maintenance"
    Ticket      = "SEN-67"
  }

  bucket_names = {
    artifacts = "${var.name_prefix}-artifacts-${var.aws_account_id}-${var.aws_region}"
    documents = "${var.name_prefix}-documents-${var.aws_account_id}-${var.aws_region}"
    frontend  = "${var.name_prefix}-frontend-${var.aws_account_id}-${var.aws_region}"
  }

  api_container_name = "api"
  api_container_port = 8000
  api_log_group_name = "/aws/ecs/${local.name}/api"
  api_queue_name     = "${local.name}-ingestion"
  dlq_name           = "${local.name}-ingestion-dlq"
  ecr_repository_arn = "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/${local.name}/api"
  api_queue_arn      = "arn:aws:sqs:${var.aws_region}:${var.aws_account_id}:${local.api_queue_name}"
  api_log_stream_arn = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:${local.api_log_group_name}:log-stream:*"
  bedrock_model_arn  = var.bedrock_model_id == null ? null : "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}"
}
