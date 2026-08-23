resource "aws_budgets_budget" "demo" {
  name         = "${local.name}-monthly-cost"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit             = true
    include_discount           = true
    include_other_subscription = true
    include_recurring          = true
    include_refund             = true
    include_subscription       = true
    include_support            = true
    include_tax                = true
    include_upfront            = true
    use_amortized              = false
    use_blended                = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
  }

  lifecycle {
    precondition {
      condition = (
        var.offline_validation ||
        (
          var.aws_account_id != local.placeholder_account_id &&
          data.aws_caller_identity.current[0].account_id == var.aws_account_id &&
          !endswith(nonsensitive(var.budget_alert_email), ".invalid")
        )
      )
      error_message = "Um apply exige conta e e-mail reais; os placeholders são aceitos somente com offline_validation=true."
    }

    precondition {
      condition     = !var.enable_bedrock || var.bedrock_model_id != null
      error_message = "enable_bedrock=true exige bedrock_model_id explícito."
    }
  }
}
