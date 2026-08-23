data "aws_caller_identity" "current" {
  count = var.offline_validation ? 0 : 1
}

data "aws_iam_policy_document" "ecs_tasks_assume_role" {
  statement {
    sid     = "AllowEcsTasks"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "api_execution" {
  statement {
    sid       = "AuthenticateToEcr"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "PullApiImage"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [local.ecr_repository_arn]
  }

  statement {
    sid = "WriteApiLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [local.api_log_stream_arn]
  }
}

data "aws_iam_policy_document" "api_task" {
  statement {
    sid       = "EnqueueDocumentJobs"
    actions   = ["sqs:SendMessage"]
    resources = [local.api_queue_arn]
  }

  statement {
    sid = "ReadAndWriteDemoObjects"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
    ]
    resources = [
      "arn:aws:s3:::${local.bucket_names.artifacts}/*",
      "arn:aws:s3:::${local.bucket_names.documents}/*",
    ]
  }

  dynamic "statement" {
    for_each = var.enable_bedrock ? [local.bedrock_model_arn] : []

    content {
      sid       = "InvokeSelectedBedrockModel"
      actions   = ["bedrock:InvokeModel"]
      resources = [statement.value]
    }
  }
}

data "aws_iam_policy_document" "worker_task" {
  statement {
    sid = "ConsumeDocumentJobs"
    actions = [
      "sqs:ChangeMessageVisibility",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ReceiveMessage",
    ]
    resources = [local.api_queue_arn]
  }

  statement {
    sid = "ReadVersionedDocuments"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]
    resources = ["arn:aws:s3:::${local.bucket_names.documents}/*"]
  }

  statement {
    sid       = "WriteDerivedArtifacts"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${local.bucket_names.artifacts}/*"]
  }
}

resource "aws_iam_role" "api_execution" {
  name                 = "${local.name}-api-execution"
  description          = "Pull da imagem imutável e escrita nos logs da task da API."
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_assume_role.json
  max_session_duration = 3600

  tags = {
    Name    = "${local.name}-api-execution"
    Purpose = "api-execution"
  }
}

resource "aws_iam_role_policy" "api_execution" {
  name   = "least-privilege"
  role   = aws_iam_role.api_execution.id
  policy = data.aws_iam_policy_document.api_execution.json
}

resource "aws_iam_role" "api_task" {
  name                 = "${local.name}-api-task"
  description          = "Ações de dados explícitas do contrato da API no perfil demo."
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_assume_role.json
  max_session_duration = 3600

  tags = {
    Name    = "${local.name}-api-task"
    Purpose = "api-runtime"
  }
}

resource "aws_iam_role_policy" "api_task" {
  name   = "least-privilege"
  role   = aws_iam_role.api_task.id
  policy = data.aws_iam_policy_document.api_task.json
}

resource "aws_iam_role" "worker_task" {
  name                 = "${local.name}-worker-task"
  description          = "Contrato IAM mínimo para um worker futuro consumir a fila sem definir compute inexistente."
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_assume_role.json
  max_session_duration = 3600

  tags = {
    Name    = "${local.name}-worker-task"
    Purpose = "worker-contract"
  }
}

resource "aws_iam_role_policy" "worker_task" {
  name   = "least-privilege"
  role   = aws_iam_role.worker_task.id
  policy = data.aws_iam_policy_document.worker_task.json
}

resource "aws_cognito_user_pool" "demo" {
  name                = local.name
  deletion_protection = "INACTIVE"
  mfa_configuration   = "OFF"
  user_pool_tier      = "LITE"
  username_attributes = ["email"]

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 1
  }

  schema {
    attribute_data_type = "String"
    mutable             = true
    name                = "email"
    required            = true

    string_attribute_constraints {
      max_length = 254
      min_length = 5
    }
  }

  tags = {
    Name    = local.name
    Purpose = "api-authentication"
  }

  depends_on = [aws_budgets_budget.demo]
}

resource "aws_cognito_user_pool_client" "demo" {
  name         = "${local.name}-public-client"
  user_pool_id = aws_cognito_user_pool.demo.id

  access_token_validity                         = 1
  auth_session_validity                         = 3
  enable_propagate_additional_user_context_data = false
  enable_token_revocation                       = true
  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]
  generate_secret               = false
  id_token_validity             = 1
  prevent_user_existence_errors = "ENABLED"
  refresh_token_validity        = 1

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}
