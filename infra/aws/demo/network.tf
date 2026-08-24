locals {
  interface_endpoint_services = merge(
    {
      ecr_api = "ecr.api"
      ecr_dkr = "ecr.dkr"
      logs    = "logs"
      sqs     = "sqs"
    },
    var.enable_bedrock ? { bedrock_runtime = "bedrock-runtime" } : {},
  )
}

resource "aws_vpc" "demo" {
  cidr_block           = "10.67.0.0/24"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = local.name
  }
}

resource "aws_subnet" "private" {
  vpc_id                  = aws_vpc.demo.id
  cidr_block              = "10.67.0.0/26"
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.name}-private-${var.availability_zone}"
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.demo.id

  tags = {
    Name = "${local.name}-private"
  }
}

resource "aws_route_table_association" "private" {
  route_table_id = aws_route_table.private.id
  subnet_id      = aws_subnet.private.id
}

resource "aws_security_group" "api" {
  name        = "${local.name}-api"
  description = "Entrada somente do VPC Link e saída somente para serviços AWS privados."
  vpc_id      = aws_vpc.demo.id

  tags = {
    Name = "${local.name}-api"
  }
}

resource "aws_security_group" "vpc_link" {
  name        = "${local.name}-vpc-link"
  description = "Tráfego do API Gateway VPC Link para a API registrada no Cloud Map."
  vpc_id      = aws_vpc.demo.id

  tags = {
    Name = "${local.name}-vpc-link"
  }
}

resource "aws_security_group" "endpoints" {
  name        = "${local.name}-endpoints"
  description = "HTTPS privado das tasks para endpoints AWS PrivateLink."
  vpc_id      = aws_vpc.demo.id

  tags = {
    Name = "${local.name}-endpoints"
  }
}

resource "aws_vpc_security_group_ingress_rule" "api_from_vpc_link" {
  description                  = "TCP da integração VPC Link exclusivamente para a API."
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = aws_security_group.vpc_link.id
  from_port                    = local.api_container_port
  ip_protocol                  = "tcp"
  to_port                      = local.api_container_port
}

resource "aws_vpc_security_group_egress_rule" "vpc_link_to_api" {
  description                  = "TCP do VPC Link exclusivamente para o security group da API."
  security_group_id            = aws_security_group.vpc_link.id
  referenced_security_group_id = aws_security_group.api.id
  from_port                    = local.api_container_port
  ip_protocol                  = "tcp"
  to_port                      = local.api_container_port
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_api" {
  description                  = "HTTPS da API exclusivamente para endpoints Interface privados."
  security_group_id            = aws_security_group.endpoints.id
  referenced_security_group_id = aws_security_group.api.id
  from_port                    = 443
  ip_protocol                  = "tcp"
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "api_to_interface_endpoints" {
  description                  = "HTTPS da API exclusivamente para o security group dos endpoints."
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = aws_security_group.endpoints.id
  from_port                    = 443
  ip_protocol                  = "tcp"
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "api_to_s3_gateway" {
  security_group_id = aws_security_group.api.id
  from_port         = 443
  ip_protocol       = "tcp"
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  to_port           = 443

  description = "HTTPS somente para o prefix list regional do gateway endpoint S3."
}

resource "aws_vpc_security_group_egress_rule" "api_dns_udp" {
  description       = "DNS UDP da API restrito ao CIDR privado da VPC."
  security_group_id = aws_security_group.api.id
  cidr_ipv4         = aws_vpc.demo.cidr_block
  from_port         = 53
  ip_protocol       = "udp"
  to_port           = 53
}

resource "aws_vpc_security_group_egress_rule" "api_dns_tcp" {
  description       = "DNS TCP da API restrito ao CIDR privado da VPC."
  security_group_id = aws_security_group.api.id
  cidr_ipv4         = aws_vpc.demo.cidr_block
  from_port         = 53
  ip_protocol       = "tcp"
  to_port           = 53
}

resource "aws_vpc_endpoint" "interface" {
  for_each = local.interface_endpoint_services

  vpc_id              = aws_vpc.demo.id
  service_name        = "com.amazonaws.${var.aws_region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  security_group_ids  = [aws_security_group.endpoints.id]
  subnet_ids          = [aws_subnet.private.id]

  tags = {
    Name = "${local.name}-${replace(each.key, "_", "-")}"
  }

  depends_on = [aws_budgets_budget.demo]
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.demo.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name = "${local.name}-s3"
  }
}
