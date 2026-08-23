variable "name_prefix" {
  description = "Prefixo curto, em inglês, usado nos nomes dos recursos do perfil demo."
  type        = string
  default     = "senai-pm"
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.name_prefix))
    error_message = "name_prefix deve ter de 3 a 20 caracteres minúsculos, dígitos ou hífens e começar por letra."
  }
}

variable "owner" {
  description = "Responsável operacional registrado nas tags; não é uma credencial."
  type        = string
  default     = "demo-team"
  nullable    = false

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._@-]{2,63}$", var.owner))
    error_message = "owner deve ser um identificador auditável de 3 a 64 caracteres."
  }
}

variable "aws_account_id" {
  description = "ID da conta AWS de destino, informado explicitamente para planos determinísticos."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id deve conter exatamente 12 dígitos."
  }
}

variable "aws_region" {
  description = "Região AWS única do perfil demo."
  type        = string
  default     = "us-east-1"
  nullable    = false

  validation {
    condition     = can(regex("^us-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region deve ser uma região comercial dos Estados Unidos, como us-east-1."
  }
}

variable "availability_zone" {
  description = "Zona de disponibilidade única usada pelo perfil sem alta disponibilidade."
  type        = string
  default     = "us-east-1a"
  nullable    = false

  validation {
    condition     = startswith(var.availability_zone, var.aws_region) && can(regex("[a-z]$", var.availability_zone))
    error_message = "availability_zone deve pertencer a aws_region."
  }
}

variable "frontend_domain_name" {
  description = "Domínio DNS próprio coberto pelo certificado ACM e usado pelo frontend e pelo CORS."
  type        = string
  nullable    = false

  validation {
    condition = (
      length(var.frontend_domain_name) <= 253 &&
      can(regex(
        "^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$",
        var.frontend_domain_name,
      ))
    )
    error_message = "frontend_domain_name deve ser um hostname DNS minúsculo completo, sem protocolo ou caminho."
  }
}

variable "frontend_certificate_arn" {
  description = "ARN de certificado ACM já validado em us-east-1 e que cobre frontend_domain_name."
  type        = string
  nullable    = false

  validation {
    condition = can(regex(
      "^arn:aws:acm:us-east-1:[0-9]{12}:certificate/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
      var.frontend_certificate_arn,
    ))
    error_message = "frontend_certificate_arn deve identificar um certificado ACM de us-east-1."
  }
}

variable "api_image_digest" {
  description = "Digest OCI imutável da imagem da API já enviada ao ECR criado por este perfil."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.api_image_digest))
    error_message = "api_image_digest deve usar o formato sha256 seguido por 64 caracteres hexadecimais minúsculos."
  }
}

variable "api_desired_count" {
  description = "Quantidade de tasks da API: 0 durante o bootstrap do ECR ou 1 durante a demo."
  type        = number
  default     = 0
  nullable    = false

  validation {
    condition     = contains([0, 1], var.api_desired_count)
    error_message = "api_desired_count deve ser 0 ou 1; alta disponibilidade não pertence ao perfil demo."
  }
}

variable "budget_alert_email" {
  description = "E-mail que recebe alertas do AWS Budget; tratado como dado sensível no plano."
  type        = string
  sensitive   = true
  nullable    = false

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_alert_email))
    error_message = "budget_alert_email deve ter formato de e-mail válido."
  }
}

variable "monthly_budget_usd" {
  description = "Teto mensal conservador em USD; o máximo de USD 16 mantém margem para R$ 100 a R$ 6/USD."
  type        = number
  default     = 15
  nullable    = false

  validation {
    condition     = var.monthly_budget_usd >= 1 && var.monthly_budget_usd <= 16
    error_message = "monthly_budget_usd deve ficar entre USD 1 e USD 16."
  }
}

variable "log_retention_days" {
  description = "Retenção curta dos logs CloudWatch do perfil removível."
  type        = number
  default     = 7
  nullable    = false

  validation {
    condition     = contains([1, 3, 5, 7, 14], var.log_retention_days)
    error_message = "log_retention_days deve ser 1, 3, 5, 7 ou 14."
  }
}

variable "enable_bedrock" {
  description = "Cria a permissão e o endpoint privado mínimos para Bedrock; permanece falso por padrão."
  type        = bool
  default     = false
  nullable    = false
}

variable "bedrock_model_id" {
  description = "ID exato do foundation model permitido quando enable_bedrock for verdadeiro."
  type        = string
  default     = null

  validation {
    condition = (
      var.bedrock_model_id == null ||
      can(regex("^[A-Za-z0-9][A-Za-z0-9._:-]{2,255}$", var.bedrock_model_id))
    )
    error_message = "bedrock_model_id deve ser nulo ou um identificador Bedrock seguro e explícito."
  }
}

variable "offline_validation" {
  description = "Desabilita chamadas de descoberta do provider somente para o plano estático fictício documentado."
  type        = bool
  default     = false
  nullable    = false

  validation {
    condition = var.offline_validation ? (
      var.offline_plan_nonce != null &&
      can(regex("^[0-9a-f]{64}$", nonsensitive(var.offline_plan_nonce)))
    ) : var.offline_plan_nonce == null
    error_message = "offline_validation=true exige o nonce efêmero do harness; execução real proíbe esse nonce."
  }
}

variable "offline_plan_nonce" {
  description = "Nonce efêmero e sensível gerado exclusivamente pelo harness de validação estática."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
  ephemeral   = true
}
