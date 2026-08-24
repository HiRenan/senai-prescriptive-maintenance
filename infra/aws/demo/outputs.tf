output "api_base_url" {
  description = "Endpoint HTTPS da API Gateway; rotas de negócio exigem JWT Cognito."
  value       = aws_apigatewayv2_api.demo.api_endpoint
}

output "api_image_reference" {
  description = "Referência OCI imutável usada pela task definition."
  value       = "${aws_ecr_repository.api.repository_url}@${var.api_image_digest}"
}

output "artifact_bucket_name" {
  description = "Bucket privado e versionado de artefatos derivados."
  value       = aws_s3_bucket.storage["artifacts"].id
}

output "bedrock_enabled" {
  description = "Indica se IAM e conectividade privada do Bedrock foram incluídos."
  value       = var.enable_bedrock
}

output "cognito_client_id" {
  description = "ID público do cliente Cognito sem client secret."
  value       = aws_cognito_user_pool_client.demo.id
}

output "cognito_user_pool_id" {
  description = "ID público do user pool que emite os JWTs aceitos pela API."
  value       = aws_cognito_user_pool.demo.id
}

output "cognito_hosted_ui_origin" {
  description = "Origem pública do Hosted UI Cognito usada somente por OAuth Code com PKCE."
  value       = local.cognito_hosted_ui
}

output "cors_allowed_origin" {
  description = "Única origem autorizada pelo CORS da API."
  value       = "https://${var.frontend_domain_name}"
}

output "document_bucket_name" {
  description = "Bucket privado e versionado de documentos da demo."
  value       = aws_s3_bucket.storage["documents"].id
}

output "ecr_repository_url" {
  description = "Destino privado para publicar a imagem real produzida pela SEN-49."
  value       = aws_ecr_repository.api.repository_url
}

output "frontend_url" {
  description = "URL HTTPS pública do CloudFront cuja origem S3 permanece privada."
  value       = "https://${var.frontend_domain_name}"
}

output "frontend_distribution_domain_name" {
  description = "Alvo DNS público da distribuição para configurar o domínio próprio fora deste perfil."
  value       = aws_cloudfront_distribution.frontend.domain_name
}

output "frontend_bucket_name" {
  description = "Bucket privado exato que recebe somente a allowlist publicada do frontend."
  value       = aws_s3_bucket.storage["frontend"].id
}

output "frontend_distribution_id" {
  description = "ID público da distribuição usado para invalidação e espera após a publicação."
  value       = aws_cloudfront_distribution.frontend.id
}

output "ingestion_dead_letter_queue_url" {
  description = "URL não secreta da DLQ do contrato de worker."
  value       = aws_sqs_queue.ingestion_dlq.url
}

output "ingestion_queue_url" {
  description = "URL não secreta da fila principal do contrato de worker."
  value       = aws_sqs_queue.ingestion.url
}

output "worker_task_role_arn" {
  description = "Role mínima que um worker real poderá assumir sem wildcard de ações."
  value       = aws_iam_role.worker_task.arn
}
