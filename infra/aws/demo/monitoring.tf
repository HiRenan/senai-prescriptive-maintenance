resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name}-api-5xx"
  alarm_description   = "A API autenticada devolveu ao menos um erro 5xx em um minuto."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "5xx"
  namespace           = "AWS/ApiGateway"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  alarm_actions             = []
  insufficient_data_actions = []
  ok_actions                = []

  dimensions = {
    ApiId = aws_apigatewayv2_api.demo.id
    Stage = aws_apigatewayv2_stage.default.name
  }

  tags = {
    Name    = "${local.name}-api-5xx"
    Purpose = "api-alarm"
  }

  depends_on = [aws_budgets_budget.demo]
}

resource "aws_cloudwatch_metric_alarm" "api_cpu" {
  alarm_name          = "${local.name}-api-cpu"
  alarm_description   = "A task única da API permaneceu acima de 80% de CPU por cinco minutos."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 5
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = 80
  treat_missing_data  = "notBreaching"

  alarm_actions             = []
  insufficient_data_actions = []
  ok_actions                = []

  dimensions = {
    ClusterName = aws_ecs_cluster.demo.name
    ServiceName = aws_ecs_service.api.name
  }

  tags = {
    Name    = "${local.name}-api-cpu"
    Purpose = "compute-alarm"
  }

  depends_on = [aws_budgets_budget.demo]
}

resource "aws_cloudwatch_metric_alarm" "queue_age" {
  alarm_name          = "${local.name}-queue-age"
  alarm_description   = "O item mais antigo da fila de ingestão aguarda há mais de cinco minutos."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 300
  treat_missing_data  = "notBreaching"

  alarm_actions             = []
  insufficient_data_actions = []
  ok_actions                = []

  dimensions = {
    QueueName = aws_sqs_queue.ingestion.name
  }

  tags = {
    Name    = "${local.name}-queue-age"
    Purpose = "queue-alarm"
  }

  depends_on = [aws_budgets_budget.demo]
}

resource "aws_cloudwatch_metric_alarm" "dlq_messages" {
  alarm_name          = "${local.name}-dlq-messages"
  alarm_description   = "A DLQ contém ao menos uma mensagem que exige inspeção."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  alarm_actions             = []
  insufficient_data_actions = []
  ok_actions                = []

  dimensions = {
    QueueName = aws_sqs_queue.ingestion_dlq.name
  }

  tags = {
    Name    = "${local.name}-dlq-messages"
    Purpose = "queue-alarm"
  }

  depends_on = [aws_budgets_budget.demo]
}
