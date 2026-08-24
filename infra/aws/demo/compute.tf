resource "aws_cloudwatch_log_group" "api" {
  name              = local.api_log_group_name
  log_group_class   = "STANDARD"
  retention_in_days = var.log_retention_days

  tags = {
    Name    = "${local.name}-api"
    Purpose = "api-logs"
  }

  depends_on = [aws_budgets_budget.demo]
}

resource "aws_ecs_cluster" "demo" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = {
    Name    = local.name
    Purpose = "api-compute"
  }
}

resource "aws_service_discovery_private_dns_namespace" "demo" {
  name        = "${local.name}.internal"
  description = "Namespace privado e removível para a integração API Gateway/ECS."
  vpc         = aws_vpc.demo.id

  tags = {
    Name    = "${local.name}.internal"
    Purpose = "api-service-discovery"
  }

  depends_on = [aws_budgets_budget.demo]
}

resource "aws_service_discovery_service" "api" {
  name          = "api"
  force_destroy = true

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.demo.id
    routing_policy = "MULTIVALUE"

    dns_records {
      ttl  = 10
      type = "SRV"
    }
  }

  health_check_custom_config {}

  tags = {
    Name    = "${local.name}-api"
    Purpose = "api-service-discovery"
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.api_execution.arn
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  task_role_arn            = aws_iam_role.api_task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name                   = local.api_container_name
      image                  = "${var.aws_account_id}.dkr.ecr.${var.aws_region}.amazonaws.com/${aws_ecr_repository.api.name}@${var.api_image_digest}"
      essential              = true
      readonlyRootFilesystem = true
      stopTimeout            = 30
      user                   = "65532:65532"

      environment = [
        {
          name  = "PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE"
          value = "synthetic_demo"
        },
        {
          name  = "PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT"
          value = "aws"
        },
        {
          name  = "PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND"
          value = "memory"
        },
      ]

      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"from urllib.request import urlopen; response = urlopen('http://127.0.0.1:8000/health/ready', timeout=2); body = response.read(32); content_type = response.headers.get_content_type(); response.close(); raise SystemExit(0 if response.status == 200 and content_type == 'application/json' and body == b'{\\\"status\\\":\\\"ready\\\"}' else 1)\"",
        ]
        interval    = 10
        retries     = 3
        startPeriod = 10
        timeout     = 3
      }

      linuxParameters = {
        capabilities = {
          drop = ["ALL"]
        }
        initProcessEnabled = true
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-create-group  = "false"
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "api"
        }
      }

      portMappings = [
        {
          appProtocol   = "http"
          containerPort = local.api_container_port
          hostPort      = local.api_container_port
          name          = "api-http"
          protocol      = "tcp"
        }
      ]
    }
  ])

  tags = {
    Name    = "${local.name}-api"
    Purpose = "api-compute"
  }

  depends_on = [aws_iam_role_policy.api_execution]
}

resource "aws_ecs_service" "api" {
  name             = "${local.name}-api"
  cluster          = aws_ecs_cluster.demo.id
  task_definition  = aws_ecs_task_definition.api.arn
  desired_count    = var.api_desired_count
  launch_type      = "FARGATE"
  platform_version = "1.4.0"

  deployment_maximum_percent         = 100
  deployment_minimum_healthy_percent = 0
  enable_ecs_managed_tags            = true
  enable_execute_command             = false
  propagate_tags                     = "SERVICE"
  wait_for_steady_state              = false

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    assign_public_ip = false
    security_groups  = [aws_security_group.api.id]
    subnets          = [aws_subnet.private.id]
  }

  service_registries {
    container_name = local.api_container_name
    container_port = local.api_container_port
    registry_arn   = aws_service_discovery_service.api.arn
  }

  tags = {
    Name    = "${local.name}-api"
    Purpose = "api-compute"
  }

  lifecycle {
    precondition {
      condition = (
        var.api_desired_count == 0 ||
        (
          !var.offline_validation &&
          var.api_image_digest != local.placeholder_image_digest
        )
      )
      error_message = "api_desired_count=1 exige um digest real e offline_validation=false."
    }
  }

  depends_on = [
    aws_budgets_budget.demo,
    aws_iam_role_policy.api_execution,
    aws_vpc_endpoint.interface,
    aws_vpc_endpoint.s3,
  ]
}
