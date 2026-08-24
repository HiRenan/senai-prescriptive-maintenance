resource "aws_sqs_queue" "ingestion_dlq" {
  name                       = local.dlq_name
  max_message_size           = 65536
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = 120

  tags = {
    Name    = local.dlq_name
    Purpose = "ingestion-dead-letter"
  }

  depends_on = [aws_budgets_budget.demo]
}

resource "aws_sqs_queue" "ingestion" {
  name                       = local.api_queue_name
  max_message_size           = 65536
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = 120

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingestion_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Name    = local.api_queue_name
    Purpose = "document-ingestion"
  }

  depends_on = [aws_budgets_budget.demo]
}

resource "aws_sqs_queue_redrive_allow_policy" "ingestion_dlq" {
  queue_url = aws_sqs_queue.ingestion_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ingestion.arn]
  })
}
