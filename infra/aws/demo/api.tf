resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${local.name}"
  log_group_class   = "STANDARD"
  retention_in_days = var.log_retention_days

  tags = {
    Name    = "${local.name}-api-gateway"
    Purpose = "api-access-logs"
  }

  depends_on = [aws_budgets_budget.demo]
}

resource "aws_apigatewayv2_api" "demo" {
  name                         = local.name
  protocol_type                = "HTTP"
  disable_execute_api_endpoint = false

  cors_configuration {
    allow_credentials = false
    allow_headers     = ["authorization", "content-type"]
    allow_methods     = ["GET", "POST", "OPTIONS"]
    allow_origins     = ["https://${var.frontend_domain_name}"]
    max_age           = 300
  }

  tags = {
    Name    = local.name
    Purpose = "authenticated-api"
  }

  depends_on = [aws_budgets_budget.demo]
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.demo.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "cognito"

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.demo.id]
    issuer   = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.demo.id}"
  }
}

resource "aws_apigatewayv2_vpc_link" "api" {
  name               = "${local.name}-api"
  security_group_ids = [aws_security_group.vpc_link.id]
  subnet_ids         = [aws_subnet.private.id]

  tags = {
    Name    = "${local.name}-api"
    Purpose = "private-api-integration"
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.demo.id
  connection_id          = aws_apigatewayv2_vpc_link.api.id
  connection_type        = "VPC_LINK"
  description            = "Proxy privado para a imagem OCI da API executada pelo ECS Fargate."
  integration_method     = "ANY"
  integration_type       = "HTTP_PROXY"
  integration_uri        = aws_service_discovery_service.api.arn
  payload_format_version = "1.0"
  timeout_milliseconds   = 29000

  request_parameters = {
    "overwrite:path" = "$request.path"
  }
}

resource "aws_apigatewayv2_route" "default" {
  api_id             = aws_apigatewayv2_api.demo.id
  route_key          = "$default"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.demo.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format = jsonencode({
      apiId                   = "$context.apiId"
      integrationErrorMessage = "$context.integrationErrorMessage"
      latency                 = "$context.responseLatency"
      requestId               = "$context.requestId"
      responseLength          = "$context.responseLength"
      routeKey                = "$context.routeKey"
      status                  = "$context.status"
    })
  }

  default_route_settings {
    detailed_metrics_enabled = false
    throttling_burst_limit   = 20
    throttling_rate_limit    = 10
  }

  tags = {
    Name    = "${local.name}-default"
    Purpose = "authenticated-api"
  }

  depends_on = [aws_apigatewayv2_route.default]
}
