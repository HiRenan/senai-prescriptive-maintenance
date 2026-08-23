"""Fail-closed static audit for the Terraform demo plan JSON."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, NoReturn, cast

REQUIRED_TAGS = {
    "Environment": "demo",
    "Lifecycle": "ephemeral-demo",
    "ManagedBy": "Terraform",
    "Profile": "aws-demo",
    "Project": "prescriptive-maintenance",
    "Ticket": "SEN-67",
}

EXPECTED_RESOURCE_CHANGES = {
    "aws_apigatewayv2_api.demo": ("aws_apigatewayv2_api", ("create",)),
    "aws_apigatewayv2_authorizer.cognito": (
        "aws_apigatewayv2_authorizer",
        ("create",),
    ),
    "aws_apigatewayv2_integration.api": (
        "aws_apigatewayv2_integration",
        ("create",),
    ),
    "aws_apigatewayv2_route.default": ("aws_apigatewayv2_route", ("create",)),
    "aws_apigatewayv2_stage.default": ("aws_apigatewayv2_stage", ("create",)),
    "aws_apigatewayv2_vpc_link.api": (
        "aws_apigatewayv2_vpc_link",
        ("create",),
    ),
    "aws_budgets_budget.demo": ("aws_budgets_budget", ("create",)),
    "aws_cloudfront_cache_policy.frontend": (
        "aws_cloudfront_cache_policy",
        ("create",),
    ),
    "aws_cloudfront_distribution.frontend": (
        "aws_cloudfront_distribution",
        ("create",),
    ),
    "aws_cloudfront_origin_access_control.frontend": (
        "aws_cloudfront_origin_access_control",
        ("create",),
    ),
    "aws_cloudfront_response_headers_policy.frontend": (
        "aws_cloudfront_response_headers_policy",
        ("create",),
    ),
    "aws_cloudwatch_log_group.api": ("aws_cloudwatch_log_group", ("create",)),
    "aws_cloudwatch_log_group.api_gateway": (
        "aws_cloudwatch_log_group",
        ("create",),
    ),
    "aws_cloudwatch_metric_alarm.api_5xx": (
        "aws_cloudwatch_metric_alarm",
        ("create",),
    ),
    "aws_cloudwatch_metric_alarm.api_cpu": (
        "aws_cloudwatch_metric_alarm",
        ("create",),
    ),
    "aws_cloudwatch_metric_alarm.dlq_messages": (
        "aws_cloudwatch_metric_alarm",
        ("create",),
    ),
    "aws_cloudwatch_metric_alarm.queue_age": (
        "aws_cloudwatch_metric_alarm",
        ("create",),
    ),
    "aws_cognito_user_pool.demo": ("aws_cognito_user_pool", ("create",)),
    "aws_cognito_user_pool_client.demo": (
        "aws_cognito_user_pool_client",
        ("create",),
    ),
    "aws_ecr_lifecycle_policy.api": ("aws_ecr_lifecycle_policy", ("create",)),
    "aws_ecr_repository.api": ("aws_ecr_repository", ("create",)),
    "aws_ecs_cluster.demo": ("aws_ecs_cluster", ("create",)),
    "aws_ecs_service.api": ("aws_ecs_service", ("create",)),
    "aws_ecs_task_definition.api": ("aws_ecs_task_definition", ("create",)),
    "aws_iam_role.api_execution": ("aws_iam_role", ("create",)),
    "aws_iam_role.api_task": ("aws_iam_role", ("create",)),
    "aws_iam_role.worker_task": ("aws_iam_role", ("create",)),
    "aws_iam_role_policy.api_execution": ("aws_iam_role_policy", ("create",)),
    "aws_iam_role_policy.api_task": ("aws_iam_role_policy", ("create",)),
    "aws_iam_role_policy.worker_task": ("aws_iam_role_policy", ("create",)),
    "aws_route_table.private": ("aws_route_table", ("create",)),
    "aws_route_table_association.private": (
        "aws_route_table_association",
        ("create",),
    ),
    'aws_s3_bucket.storage["artifacts"]': ("aws_s3_bucket", ("create",)),
    'aws_s3_bucket.storage["documents"]': ("aws_s3_bucket", ("create",)),
    'aws_s3_bucket.storage["frontend"]': ("aws_s3_bucket", ("create",)),
    'aws_s3_bucket_lifecycle_configuration.storage["artifacts"]': (
        "aws_s3_bucket_lifecycle_configuration",
        ("create",),
    ),
    'aws_s3_bucket_lifecycle_configuration.storage["documents"]': (
        "aws_s3_bucket_lifecycle_configuration",
        ("create",),
    ),
    'aws_s3_bucket_lifecycle_configuration.storage["frontend"]': (
        "aws_s3_bucket_lifecycle_configuration",
        ("create",),
    ),
    'aws_s3_bucket_ownership_controls.storage["artifacts"]': (
        "aws_s3_bucket_ownership_controls",
        ("create",),
    ),
    'aws_s3_bucket_ownership_controls.storage["documents"]': (
        "aws_s3_bucket_ownership_controls",
        ("create",),
    ),
    'aws_s3_bucket_ownership_controls.storage["frontend"]': (
        "aws_s3_bucket_ownership_controls",
        ("create",),
    ),
    "aws_s3_bucket_policy.frontend": ("aws_s3_bucket_policy", ("create",)),
    'aws_s3_bucket_public_access_block.storage["artifacts"]': (
        "aws_s3_bucket_public_access_block",
        ("create",),
    ),
    'aws_s3_bucket_public_access_block.storage["documents"]': (
        "aws_s3_bucket_public_access_block",
        ("create",),
    ),
    'aws_s3_bucket_public_access_block.storage["frontend"]': (
        "aws_s3_bucket_public_access_block",
        ("create",),
    ),
    'aws_s3_bucket_server_side_encryption_configuration.storage["artifacts"]': (
        "aws_s3_bucket_server_side_encryption_configuration",
        ("create",),
    ),
    'aws_s3_bucket_server_side_encryption_configuration.storage["documents"]': (
        "aws_s3_bucket_server_side_encryption_configuration",
        ("create",),
    ),
    'aws_s3_bucket_server_side_encryption_configuration.storage["frontend"]': (
        "aws_s3_bucket_server_side_encryption_configuration",
        ("create",),
    ),
    'aws_s3_bucket_versioning.storage["artifacts"]': (
        "aws_s3_bucket_versioning",
        ("create",),
    ),
    'aws_s3_bucket_versioning.storage["documents"]': (
        "aws_s3_bucket_versioning",
        ("create",),
    ),
    'aws_s3_bucket_versioning.storage["frontend"]': (
        "aws_s3_bucket_versioning",
        ("create",),
    ),
    "aws_security_group.api": ("aws_security_group", ("create",)),
    "aws_security_group.endpoints": ("aws_security_group", ("create",)),
    "aws_security_group.vpc_link": ("aws_security_group", ("create",)),
    "aws_service_discovery_private_dns_namespace.demo": (
        "aws_service_discovery_private_dns_namespace",
        ("create",),
    ),
    "aws_service_discovery_service.api": (
        "aws_service_discovery_service",
        ("create",),
    ),
    "aws_sqs_queue.ingestion": ("aws_sqs_queue", ("create",)),
    "aws_sqs_queue.ingestion_dlq": ("aws_sqs_queue", ("create",)),
    "aws_sqs_queue_redrive_allow_policy.ingestion_dlq": (
        "aws_sqs_queue_redrive_allow_policy",
        ("create",),
    ),
    "aws_subnet.private": ("aws_subnet", ("create",)),
    "aws_vpc.demo": ("aws_vpc", ("create",)),
    'aws_vpc_endpoint.interface["ecr_api"]': ("aws_vpc_endpoint", ("create",)),
    'aws_vpc_endpoint.interface["ecr_dkr"]': ("aws_vpc_endpoint", ("create",)),
    'aws_vpc_endpoint.interface["logs"]': ("aws_vpc_endpoint", ("create",)),
    'aws_vpc_endpoint.interface["sqs"]': ("aws_vpc_endpoint", ("create",)),
    "aws_vpc_endpoint.s3": ("aws_vpc_endpoint", ("create",)),
    "aws_vpc_security_group_egress_rule.api_dns_tcp": (
        "aws_vpc_security_group_egress_rule",
        ("create",),
    ),
    "aws_vpc_security_group_egress_rule.api_dns_udp": (
        "aws_vpc_security_group_egress_rule",
        ("create",),
    ),
    "aws_vpc_security_group_egress_rule.api_to_interface_endpoints": (
        "aws_vpc_security_group_egress_rule",
        ("create",),
    ),
    "aws_vpc_security_group_egress_rule.api_to_s3_gateway": (
        "aws_vpc_security_group_egress_rule",
        ("create",),
    ),
    "aws_vpc_security_group_egress_rule.vpc_link_to_api": (
        "aws_vpc_security_group_egress_rule",
        ("create",),
    ),
    "aws_vpc_security_group_ingress_rule.api_from_vpc_link": (
        "aws_vpc_security_group_ingress_rule",
        ("create",),
    ),
    "aws_vpc_security_group_ingress_rule.endpoints_from_api": (
        "aws_vpc_security_group_ingress_rule",
        ("create",),
    ),
    "data.aws_iam_policy_document.frontend_bucket": (
        "aws_iam_policy_document",
        ("read",),
    ),
}

EXPECTED_SECURITY_GROUP_RULES = {
    "aws_vpc_security_group_ingress_rule.api_from_vpc_link": {
        "description": "TCP da integração VPC Link exclusivamente para a API.",
        "protocol": "tcp",
        "from_port": 8000,
        "to_port": 8000,
        "target": ("aws_security_group.api.id", "aws_security_group.api"),
        "source_key": "referenced_security_group_id",
        "source": (
            "aws_security_group.vpc_link.id",
            "aws_security_group.vpc_link",
        ),
    },
    "aws_vpc_security_group_ingress_rule.endpoints_from_api": {
        "description": (
            "HTTPS da API exclusivamente para endpoints Interface privados."
        ),
        "protocol": "tcp",
        "from_port": 443,
        "to_port": 443,
        "target": (
            "aws_security_group.endpoints.id",
            "aws_security_group.endpoints",
        ),
        "source_key": "referenced_security_group_id",
        "source": ("aws_security_group.api.id", "aws_security_group.api"),
    },
    "aws_vpc_security_group_egress_rule.vpc_link_to_api": {
        "description": ("TCP do VPC Link exclusivamente para o security group da API."),
        "protocol": "tcp",
        "from_port": 8000,
        "to_port": 8000,
        "target": (
            "aws_security_group.vpc_link.id",
            "aws_security_group.vpc_link",
        ),
        "source_key": "referenced_security_group_id",
        "source": ("aws_security_group.api.id", "aws_security_group.api"),
    },
    "aws_vpc_security_group_egress_rule.api_to_interface_endpoints": {
        "description": (
            "HTTPS da API exclusivamente para o security group dos endpoints."
        ),
        "protocol": "tcp",
        "from_port": 443,
        "to_port": 443,
        "target": ("aws_security_group.api.id", "aws_security_group.api"),
        "source_key": "referenced_security_group_id",
        "source": (
            "aws_security_group.endpoints.id",
            "aws_security_group.endpoints",
        ),
    },
    "aws_vpc_security_group_egress_rule.api_to_s3_gateway": {
        "description": (
            "HTTPS somente para o prefix list regional do gateway endpoint S3."
        ),
        "protocol": "tcp",
        "from_port": 443,
        "to_port": 443,
        "target": ("aws_security_group.api.id", "aws_security_group.api"),
        "source_key": "prefix_list_id",
        "source": ("aws_vpc_endpoint.s3.prefix_list_id", "aws_vpc_endpoint.s3"),
    },
    "aws_vpc_security_group_egress_rule.api_dns_udp": {
        "description": "DNS UDP da API restrito ao CIDR privado da VPC.",
        "protocol": "udp",
        "from_port": 53,
        "to_port": 53,
        "target": ("aws_security_group.api.id", "aws_security_group.api"),
        "source_key": "cidr_ipv4",
        "source": ("aws_vpc.demo.cidr_block", "aws_vpc.demo"),
    },
    "aws_vpc_security_group_egress_rule.api_dns_tcp": {
        "description": "DNS TCP da API restrito ao CIDR privado da VPC.",
        "protocol": "tcp",
        "from_port": 53,
        "to_port": 53,
        "target": ("aws_security_group.api.id", "aws_security_group.api"),
        "source_key": "cidr_ipv4",
        "source": ("aws_vpc.demo.cidr_block", "aws_vpc.demo"),
    },
}

EXPECTED_CONFIGURATION_RESOURCES = {
    address.split("[", maxsplit=1)[0]: (resource_type, "managed")
    for address, (resource_type, actions) in EXPECTED_RESOURCE_CHANGES.items()
    if actions == ("create",)
}
EXPECTED_CONFIGURATION_RESOURCES.update(
    {
        "data.aws_caller_identity.current": ("aws_caller_identity", "data"),
        "data.aws_iam_policy_document.api_execution": (
            "aws_iam_policy_document",
            "data",
        ),
        "data.aws_iam_policy_document.api_task": (
            "aws_iam_policy_document",
            "data",
        ),
        "data.aws_iam_policy_document.ecs_tasks_assume_role": (
            "aws_iam_policy_document",
            "data",
        ),
        "data.aws_iam_policy_document.frontend_bucket": (
            "aws_iam_policy_document",
            "data",
        ),
        "data.aws_iam_policy_document.worker_task": (
            "aws_iam_policy_document",
            "data",
        ),
    }
)

EXPECTED_OUTPUT_REFERENCES = {
    "api_base_url": (
        "aws_apigatewayv2_api.demo.api_endpoint",
        "aws_apigatewayv2_api.demo",
    ),
    "api_image_reference": (
        "aws_ecr_repository.api.repository_url",
        "aws_ecr_repository.api",
        "var.api_image_digest",
    ),
    "artifact_bucket_name": (
        'aws_s3_bucket.storage["artifacts"].id',
        'aws_s3_bucket.storage["artifacts"]',
        "aws_s3_bucket.storage",
    ),
    "bedrock_enabled": ("var.enable_bedrock",),
    "cognito_client_id": (
        "aws_cognito_user_pool_client.demo.id",
        "aws_cognito_user_pool_client.demo",
    ),
    "cognito_user_pool_id": (
        "aws_cognito_user_pool.demo.id",
        "aws_cognito_user_pool.demo",
    ),
    "cors_allowed_origin": ("var.frontend_domain_name",),
    "document_bucket_name": (
        'aws_s3_bucket.storage["documents"].id',
        'aws_s3_bucket.storage["documents"]',
        "aws_s3_bucket.storage",
    ),
    "ecr_repository_url": (
        "aws_ecr_repository.api.repository_url",
        "aws_ecr_repository.api",
    ),
    "frontend_url": ("var.frontend_domain_name",),
    "frontend_distribution_domain_name": (
        "aws_cloudfront_distribution.frontend.domain_name",
        "aws_cloudfront_distribution.frontend",
    ),
    "ingestion_dead_letter_queue_url": (
        "aws_sqs_queue.ingestion_dlq.url",
        "aws_sqs_queue.ingestion_dlq",
    ),
    "ingestion_queue_url": (
        "aws_sqs_queue.ingestion.url",
        "aws_sqs_queue.ingestion",
    ),
    "worker_task_role_arn": (
        "aws_iam_role.worker_task.arn",
        "aws_iam_role.worker_task",
    ),
}

BUDGET_GATED_RESOURCES = {
    "aws_apigatewayv2_api.demo",
    "aws_cloudfront_distribution.frontend",
    "aws_cloudwatch_log_group.api",
    "aws_cloudwatch_log_group.api_gateway",
    "aws_cloudwatch_metric_alarm.api_5xx",
    "aws_cloudwatch_metric_alarm.api_cpu",
    "aws_cloudwatch_metric_alarm.dlq_messages",
    "aws_cloudwatch_metric_alarm.queue_age",
    "aws_cognito_user_pool.demo",
    "aws_ecr_repository.api",
    "aws_ecs_service.api",
    "aws_s3_bucket.storage",
    "aws_service_discovery_private_dns_namespace.demo",
    "aws_sqs_queue.ingestion",
    "aws_sqs_queue.ingestion_dlq",
    "aws_vpc_endpoint.interface",
}


class AuditError(RuntimeError):
    """Raised when a plan violates the demo profile contract."""


def fail(message: str) -> NoReturn:
    raise AuditError(message)


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"Chave JSON duplicada rejeitada: {key}.")
        result[key] = value
    return result


def reject_nonfinite_json_constant(_: str) -> NoReturn:
    fail("JSON contém constante numérica não finita.")


def parse_json_text(text: str, *, context: str) -> object:
    try:
        return json.loads(
            text,
            object_pairs_hook=unique_json_object,
            parse_constant=reject_nonfinite_json_constant,
        )
    except json.JSONDecodeError as error:
        raise AuditError(f"{context} não contém JSON válido.") from error


def mapping(value: object, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        fail(f"{context} deve ser um objeto JSON.")
    return cast(Mapping[str, Any], value)


def sequence(value: object, *, context: str) -> list[Any]:
    if not isinstance(value, list):
        fail(f"{context} deve ser uma lista JSON.")
    return cast(list[Any], value)


def audit_configuration_scope(plan: Mapping[str, Any]) -> None:
    configuration = mapping(plan.get("configuration"), context="configuration")
    root_module = mapping(configuration.get("root_module"), context="root_module")
    configured = sequence(
        root_module.get("resources"), context="configuration.root_module.resources"
    )
    indexed: dict[str, Mapping[str, Any]] = {}
    for raw_resource in configured:
        resource = mapping(raw_resource, context="configured resource")
        address = resource.get("address")
        if not isinstance(address, str) or not address:
            fail("Todo recurso configurado deve possuir address textual.")
        if address in indexed:
            fail(f"Configuração possui address duplicado: {address}.")
        indexed[address] = resource

    actual_addresses = set(indexed)
    expected_addresses = set(EXPECTED_CONFIGURATION_RESOURCES)
    unexpected = sorted(actual_addresses - expected_addresses)
    missing = sorted(expected_addresses - actual_addresses)
    if unexpected or missing:
        details: list[str] = []
        if unexpected:
            details.append(f"inesperados={unexpected}")
        if missing:
            details.append(f"ausentes={missing}")
        fail("Configuração diverge da allowlist exata: " + "; ".join(details))

    for address, resource in indexed.items():
        expected_type, expected_mode = EXPECTED_CONFIGURATION_RESOURCES[address]
        if (
            resource.get("type") != expected_type
            or resource.get("mode") != expected_mode
        ):
            fail(
                f"{address} diverge em type/mode; esperado "
                f"{expected_type}/{expected_mode}."
            )


def planned_resources(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    audit_configuration_scope(plan)
    changes = sequence(plan.get("resource_changes"), context="resource_changes")
    indexed: dict[str, Mapping[str, Any]] = {}
    for raw_change in changes:
        change = mapping(raw_change, context="resource_change")
        address = change.get("address")
        if not isinstance(address, str) or not address:
            fail("Todo resource_change deve possuir address textual.")
        if address in indexed:
            fail(f"resource_changes possui address duplicado: {address}.")
        indexed[address] = change

    actual_addresses = set(indexed)
    expected_addresses = set(EXPECTED_RESOURCE_CHANGES)
    unexpected = sorted(actual_addresses - expected_addresses)
    missing = sorted(expected_addresses - actual_addresses)
    if unexpected or missing:
        details: list[str] = []
        if unexpected:
            details.append(f"inesperados={unexpected}")
        if missing:
            details.append(f"ausentes={missing}")
        fail("Escopo de recursos diverge da allowlist exata: " + "; ".join(details))

    resources: list[Mapping[str, Any]] = []
    for address, change in indexed.items():
        expected_type, expected_actions = EXPECTED_RESOURCE_CHANGES[address]
        actual_type = change.get("type")
        if actual_type != expected_type:
            fail(
                f"{address} possui tipo inesperado: {actual_type}; "
                f"esperado {expected_type}."
            )
        actions = tuple(
            str(action)
            for action in sequence(
                mapping(change.get("change"), context="change").get("actions"),
                context="change.actions",
            )
        )
        if actions != expected_actions:
            fail(
                f"{address} possui ações inesperadas: {list(actions)}; "
                f"esperado {list(expected_actions)}."
            )
        if actions == ("read",):
            continue
        resources.append(change)
    return resources


def by_type(
    resources: Iterable[Mapping[str, Any]], resource_type: str
) -> list[Mapping[str, Any]]:
    return [resource for resource in resources if resource.get("type") == resource_type]


def after(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    change = mapping(resource.get("change"), context="resource.change")
    return mapping(change.get("after"), context=f"{resource.get('address')}.after")


def require_count(
    resources: Iterable[Mapping[str, Any]], resource_type: str, expected: int
) -> list[Mapping[str, Any]]:
    selected = by_type(resources, resource_type)
    if len(selected) != expected:
        fail(f"{resource_type}: esperado {expected}, encontrado {len(selected)}.")
    return selected


def configured_resource(plan: Mapping[str, Any], *, address: str) -> Mapping[str, Any]:
    configuration = mapping(plan.get("configuration"), context="configuration")
    root_module = mapping(configuration.get("root_module"), context="root_module")
    resources = sequence(root_module.get("resources"), context="configured resources")
    matches = [
        mapping(resource, context="configured resource")
        for resource in resources
        if mapping(resource, context="configured resource").get("address") == address
    ]
    if len(matches) != 1:
        fail(f"Configuração {address}: esperado um recurso, encontrado {len(matches)}.")
    return matches[0]


def resource_by_address(
    resources: Iterable[Mapping[str, Any]], *, address: str
) -> Mapping[str, Any]:
    matches = [resource for resource in resources if resource.get("address") == address]
    if len(matches) != 1:
        fail(f"Plano {address}: esperado um recurso, encontrado {len(matches)}.")
    return matches[0]


def audit_resource_scope(resources: list[Mapping[str, Any]]) -> None:
    expected_creates = sum(
        actions == ("create",) for _, actions in EXPECTED_RESOURCE_CHANGES.values()
    )
    if len(resources) != expected_creates:
        fail(f"Plano possui {len(resources)} criações; esperado {expected_creates}.")


def audit_storage(resources: list[Mapping[str, Any]], plan: Mapping[str, Any]) -> None:
    for bucket in require_count(resources, "aws_s3_bucket", 3):
        if after(bucket).get("force_destroy") is not True:
            fail(f"{bucket.get('address')} não permite teardown completo.")

    for block in require_count(resources, "aws_s3_bucket_public_access_block", 3):
        values = after(block)
        controls = (
            "block_public_acls",
            "block_public_policy",
            "ignore_public_acls",
            "restrict_public_buckets",
        )
        if any(values.get(control) is not True for control in controls):
            fail(f"{block.get('address')} não bloqueia toda exposição pública.")

    for versioning in require_count(resources, "aws_s3_bucket_versioning", 3):
        configuration = sequence(
            after(versioning).get("versioning_configuration"),
            context="versioning_configuration",
        )
        if (
            not configuration
            or mapping(configuration[0], context="versioning_configuration[0]").get(
                "status"
            )
            != "Enabled"
        ):
            fail(f"{versioning.get('address')} não habilita versionamento.")

    for encryption in require_count(
        resources, "aws_s3_bucket_server_side_encryption_configuration", 3
    ):
        rules = sequence(after(encryption).get("rule"), context="encryption.rule")
        defaults = sequence(
            mapping(rules[0], context="encryption.rule[0]").get(
                "apply_server_side_encryption_by_default"
            ),
            context="encryption.default",
        )
        algorithm = mapping(defaults[0], context="encryption.default[0]").get(
            "sse_algorithm"
        )
        if algorithm != "AES256":
            fail(f"{encryption.get('address')} não usa SSE-S3 explícito.")

    repository = after(require_count(resources, "aws_ecr_repository", 1)[0])
    if repository.get("force_delete") is not True:
        fail("O ECR não está configurado para teardown.")
    if repository.get("image_tag_mutability") != "IMMUTABLE":
        fail("O ECR permite tags mutáveis.")

    for lifecycle in require_count(
        resources, "aws_s3_bucket_lifecycle_configuration", 3
    ):
        rules = sequence(after(lifecycle).get("rule"), context="lifecycle.rule")
        noncurrent = sequence(
            mapping(rules[0], context="lifecycle.rule[0]").get(
                "noncurrent_version_expiration"
            ),
            context="lifecycle.noncurrent_version_expiration",
        )
        if (
            not noncurrent
            or mapping(noncurrent[0], context="lifecycle.noncurrent[0]").get(
                "noncurrent_days"
            )
            != 7
        ):
            fail(f"{lifecycle.get('address')} não expira versões antigas em 7 dias.")

    distribution = after(require_count(resources, "aws_cloudfront_distribution", 1)[0])
    if distribution.get("retain_on_delete") is not False:
        fail("CloudFront seria retido no destroy.")
    frontend_domain = variable_string(plan, name="frontend_domain_name")
    certificate_arn = variable_string(plan, name="frontend_certificate_arn")
    account_id = variable_string(plan, name="aws_account_id")
    expected_certificate_prefix = f"arn:aws:acm:us-east-1:{account_id}:certificate/"
    if not certificate_arn.startswith(expected_certificate_prefix):
        fail("O certificado do frontend não pertence à conta em us-east-1.")
    if distribution.get("aliases") != [frontend_domain]:
        fail("CloudFront não publica exclusivamente o domínio próprio esperado.")
    viewer_certificates = sequence(
        distribution.get("viewer_certificate"),
        context="cloudfront.viewer_certificate",
    )
    expected_viewer_certificate = {
        "acm_certificate_arn": certificate_arn,
        "cloudfront_default_certificate": False,
        "iam_certificate_id": None,
        "minimum_protocol_version": "TLSv1.2_2021",
        "ssl_support_method": "sni-only",
    }
    if (
        len(viewer_certificates) != 1
        or mapping(viewer_certificates[0], context="cloudfront.viewer_certificate[0]")
        != expected_viewer_certificate
    ):
        fail("CloudFront não exige ACM/SNI com a política TLSv1.2_2021 exata.")

    configured_distribution = configured_resource(
        plan, address="aws_cloudfront_distribution.frontend"
    )
    distribution_expressions = mapping(
        configured_distribution.get("expressions"),
        context="cloudfront expressions",
    )
    exact_references(
        mapping(distribution_expressions.get("aliases"), context="cloudfront.aliases"),
        expected=("var.frontend_domain_name",),
        context="cloudfront.aliases",
    )
    configured_viewers = sequence(
        distribution_expressions.get("viewer_certificate"),
        context="configured cloudfront.viewer_certificate",
    )
    if len(configured_viewers) != 1:
        fail("CloudFront deve possuir exatamente um viewer_certificate.")
    configured_viewer = mapping(
        configured_viewers[0], context="configured cloudfront.viewer_certificate[0]"
    )
    expected_viewer_keys = {
        "acm_certificate_arn",
        "cloudfront_default_certificate",
        "minimum_protocol_version",
        "ssl_support_method",
    }
    if set(configured_viewer) != expected_viewer_keys:
        fail("CloudFront possui configuração de certificado fora do contrato.")
    exact_references(
        mapping(
            configured_viewer.get("acm_certificate_arn"),
            context="cloudfront.acm_certificate_arn",
        ),
        expected=("var.frontend_certificate_arn",),
        context="cloudfront.acm_certificate_arn",
    )
    for key, expected in (
        ("cloudfront_default_certificate", False),
        ("minimum_protocol_version", "TLSv1.2_2021"),
        ("ssl_support_method", "sni-only"),
    ):
        exact_constant(
            mapping(configured_viewer.get(key), context=f"cloudfront.{key}"),
            expected=expected,
            context=f"cloudfront.{key}",
        )

    oac = after(require_count(resources, "aws_cloudfront_origin_access_control", 1)[0])
    if (
        oac.get("signing_behavior") != "always"
        or oac.get("signing_protocol") != "sigv4"
    ):
        fail("CloudFront OAC não assina todas as requisições com SigV4.")

    discovery_service = after(
        require_count(resources, "aws_service_discovery_service", 1)[0]
    )
    if discovery_service.get("force_destroy") is not True:
        fail("O serviço Cloud Map não permite teardown de instâncias residuais.")


def exact_references(
    expression: Mapping[str, Any], *, expected: tuple[str, ...], context: str
) -> None:
    if set(expression) != {"references"}:
        fail(f"{context} deve conter somente references.")
    references = normalized_strings(
        expression.get("references"), context=f"{context}.references"
    )
    if len(references) != len(set(references)) or set(references) != set(expected):
        fail(f"{context} diverge das referências exatas esperadas.")


def audit_security_group_contract(
    resources: list[Mapping[str, Any]], plan: Mapping[str, Any]
) -> None:
    vpc_cidr = after(resource_by_address(resources, address="aws_vpc.demo")).get(
        "cidr_block"
    )
    if not isinstance(vpc_cidr, str) or vpc_cidr != "10.67.0.0/24":
        fail("A VPC não usa o CIDR privado mínimo esperado.")

    for address in (
        "aws_security_group.api",
        "aws_security_group.endpoints",
        "aws_security_group.vpc_link",
    ):
        values = after(resource_by_address(resources, address=address))
        if values.get("ingress") not in (None, []) or values.get("egress") not in (
            None,
            [],
        ):
            fail(f"{address} contém regras inline fora da allowlist.")
        expressions = mapping(
            configured_resource(plan, address=address).get("expressions"),
            context=f"{address}.expressions",
        )
        exact_references(
            mapping(expressions.get("vpc_id"), context=f"{address}.vpc_id"),
            expected=("aws_vpc.demo.id", "aws_vpc.demo"),
            context=f"{address}.vpc_id",
        )

    source_keys = {
        "cidr_ipv4",
        "cidr_ipv6",
        "prefix_list_id",
        "referenced_security_group_id",
    }
    security_expression_keys = source_keys | {
        "from_port",
        "ip_protocol",
        "security_group_id",
        "to_port",
    }
    for address, contract in EXPECTED_SECURITY_GROUP_RULES.items():
        resource = resource_by_address(resources, address=address)
        values = after(resource)
        expected_values = {
            "ip_protocol": contract["protocol"],
            "from_port": contract["from_port"],
            "to_port": contract["to_port"],
        }
        for key, expected in expected_values.items():
            if values.get(key) != expected:
                fail(f"{address}.{key} diverge do contrato: {values.get(key)}.")
        if values.get("description") != contract["description"]:
            fail(f"{address}.description diverge da descrição operacional exata.")

        source_key = str(contract["source_key"])
        for key in source_keys:
            expected_source = vpc_cidr if key == source_key == "cidr_ipv4" else None
            if values.get(key) != expected_source:
                fail(f"{address}.{key} inclui uma origem fora do contrato.")

        expressions = mapping(
            configured_resource(plan, address=address).get("expressions"),
            context=f"{address}.expressions",
        )
        actual_security_keys = set(expressions) & security_expression_keys
        expected_security_keys = {
            "from_port",
            "ip_protocol",
            "security_group_id",
            source_key,
            "to_port",
        }
        if actual_security_keys != expected_security_keys:
            fail(f"{address} possui atributos de rede fora do contrato exato.")
        exact_constant(
            mapping(expressions.get("description"), context=f"{address}.description"),
            expected=contract["description"],
            context=f"{address}.description",
        )
        exact_references(
            mapping(
                expressions.get("security_group_id"),
                context=f"{address}.security_group_id",
            ),
            expected=cast(tuple[str, ...], contract["target"]),
            context=f"{address}.security_group_id",
        )
        exact_references(
            mapping(expressions.get(source_key), context=f"{address}.{source_key}"),
            expected=cast(tuple[str, ...], contract["source"]),
            context=f"{address}.{source_key}",
        )


def audit_network_and_auth(
    resources: list[Mapping[str, Any]], plan: Mapping[str, Any]
) -> None:
    audit_security_group_contract(resources, plan)

    subnet = after(require_count(resources, "aws_subnet", 1)[0])
    if subnet.get("map_public_ip_on_launch") is not False:
        fail("A subnet atribui IP público.")

    service = after(require_count(resources, "aws_ecs_service", 1)[0])
    network = sequence(
        service.get("network_configuration"), context="ecs.network_configuration"
    )
    if (
        not network
        or mapping(network[0], context="ecs.network_configuration[0]").get(
            "assign_public_ip"
        )
        is not False
    ):
        fail("A task Fargate recebe IP público.")
    if service.get("desired_count") != 0:
        fail(
            "O plano estático padrão deve manter desired_count=0 até o push do digest."
        )

    if service.get("launch_type") != "FARGATE":
        fail("A imagem OCI da API não está associada ao ECS Fargate.")
    if service.get("enable_execute_command") is not False:
        fail("ECS Exec deve permanecer desabilitado no perfil mínimo.")

    task_resource = require_count(resources, "aws_ecs_task_definition", 1)[0]
    task = after(task_resource)
    if task.get("requires_compatibilities") != ["FARGATE"]:
        fail("A task definition não declara compatibilidade Fargate explícita.")
    if (
        task.get("network_mode") != "awsvpc"
        or task.get("cpu") != "256"
        or task.get("memory") != "512"
    ):
        fail("A task definition diverge do compute mínimo documentado.")
    runtime = sequence(task.get("runtime_platform"), context="task.runtime_platform")
    if not runtime or mapping(runtime[0], context="task.runtime_platform[0]") != {
        "cpu_architecture": "X86_64",
        "operating_system_family": "LINUX",
    }:
        fail("A task não preserva a plataforma Linux/x86 da imagem SEN-49.")
    configured_task = configured_resource(plan, address="aws_ecs_task_definition.api")
    task_expressions = mapping(
        configured_task.get("expressions"), context="task expressions"
    )
    container_expression = mapping(
        task_expressions.get("container_definitions"),
        context="task container_definitions",
    )
    container_references = normalized_strings(
        container_expression.get("references"), context="task container references"
    )
    expected_container_references = (
        "local.api_container_name",
        "var.aws_account_id",
        "var.aws_region",
        "aws_ecr_repository.api.name",
        "aws_ecr_repository.api",
        "var.api_image_digest",
        "aws_cloudwatch_log_group.api.name",
        "aws_cloudwatch_log_group.api",
        "var.aws_region",
        "local.api_container_port",
        "local.api_container_port",
    )
    if tuple(container_references) != expected_container_references:
        fail("A task diverge das referências exatas do container aprovado.")

    task_change = mapping(task_resource.get("change"), context="task change")
    sensitive = mapping(
        task_change.get("after_sensitive"), context="task after_sensitive"
    )
    if sensitive.get("container_definitions") not in (None, False):
        fail("A task não pode ocultar container_definitions como valor sensível.")
    container_text = task.get("container_definitions")
    if not isinstance(container_text, str) or not container_text:
        fail("A task deve possuir container_definitions conhecido no plano.")
    containers = sequence(
        parse_json_text(container_text, context="task container_definitions"),
        context="task container_definitions",
    )
    region = variable_string(plan, name="aws_region")
    account_id = variable_string(plan, name="aws_account_id")
    image_digest = variable_string(plan, name="api_image_digest")
    repository_name = resource_string(
        resources, address="aws_ecr_repository.api", attribute="name"
    )
    log_group_name = resource_string(
        resources, address="aws_cloudwatch_log_group.api", attribute="name"
    )
    expected_health_command = (
        'python -c "from urllib.request import urlopen; response = '
        "urlopen('http://127.0.0.1:8000/health/ready', timeout=2); "
        "body = response.read(32); content_type = "
        "response.headers.get_content_type(); response.close(); "
        "raise SystemExit(0 if response.status == 200 and content_type == "
        "'application/json' and body == b'{\\\"status\\\":\\\"ready\\\"}' "
        'else 1)"'
    )
    expected_containers = [
        {
            "environment": [
                {
                    "name": "PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT",
                    "value": "aws",
                },
                {
                    "name": "PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND",
                    "value": "memory",
                },
            ],
            "essential": True,
            "healthCheck": {
                "command": ["CMD-SHELL", expected_health_command],
                "interval": 10,
                "retries": 3,
                "startPeriod": 10,
                "timeout": 3,
            },
            "image": (
                f"{account_id}.dkr.ecr.{region}.amazonaws.com/"
                f"{repository_name}@{image_digest}"
            ),
            "linuxParameters": {
                "capabilities": {"drop": ["ALL"]},
                "initProcessEnabled": True,
            },
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-create-group": "false",
                    "awslogs-group": log_group_name,
                    "awslogs-region": region,
                    "awslogs-stream-prefix": "api",
                },
            },
            "name": "api",
            "portMappings": [
                {
                    "appProtocol": "http",
                    "containerPort": 8000,
                    "hostPort": 8000,
                    "name": "api-http",
                    "protocol": "tcp",
                }
            ],
            "readonlyRootFilesystem": True,
            "stopTimeout": 30,
            "user": "65532:65532",
        }
    ]
    if json.dumps(containers, sort_keys=True, separators=(",", ":")) != json.dumps(
        expected_containers, sort_keys=True, separators=(",", ":")
    ):
        fail(
            "A task diverge do contrato exato de imagem, runtime, ambiente ou "
            "readiness do container."
        )

    endpoints = require_count(resources, "aws_vpc_endpoint", 5)
    endpoint_types = [
        str(after(endpoint).get("vpc_endpoint_type")) for endpoint in endpoints
    ]
    if endpoint_types.count("Interface") != 4 or endpoint_types.count("Gateway") != 1:
        fail("O plano padrão deve ter quatro endpoints Interface e um S3 Gateway.")
    for endpoint in endpoints:
        values = after(endpoint)
        if (
            values.get("vpc_endpoint_type") == "Interface"
            and values.get("private_dns_enabled") is not True
        ):
            fail(f"{endpoint.get('address')} não habilita DNS privado.")

    api = after(require_count(resources, "aws_apigatewayv2_api", 1)[0])
    cors = sequence(api.get("cors_configuration"), context="api.cors_configuration")
    if not cors:
        fail("A API não declara CORS.")
    cors_values = mapping(cors[0], context="api.cors_configuration[0]")
    if cors_values.get("allow_credentials") is not False:
        fail("CORS permite credenciais de navegador sem necessidade.")
    frontend_origin = f"https://{variable_string(plan, name='frontend_domain_name')}"
    if cors_values.get("allow_origins") != [frontend_origin]:
        fail("CORS não restringe a origem ao domínio próprio HTTPS esperado.")
    if (
        cors_values.get("allow_headers") != ["authorization", "content-type"]
        or set(
            normalized_strings(
                cors_values.get("allow_methods"), context="CORS allow_methods"
            )
        )
        != {"GET", "OPTIONS", "POST"}
        or cors_values.get("expose_headers") is not None
        or cors_values.get("max_age") != 300
    ):
        fail("CORS diverge do contrato mínimo de headers, métodos ou cache.")
    configured_api = configured_resource(plan, address="aws_apigatewayv2_api.demo")
    api_expressions = mapping(
        configured_api.get("expressions"), context="api expressions"
    )
    configured_cors = sequence(
        api_expressions.get("cors_configuration"),
        context="configured cors_configuration",
    )
    if len(configured_cors) != 1:
        fail("A API deve possuir exatamente uma configuração CORS.")
    configured_cors_values = mapping(configured_cors[0], context="configured CORS")
    expected_cors_keys = {
        "allow_credentials",
        "allow_headers",
        "allow_methods",
        "allow_origins",
        "max_age",
    }
    if set(configured_cors_values) != expected_cors_keys:
        fail("A configuração CORS possui campos fora do contrato.")
    origin_expression = mapping(
        configured_cors_values.get("allow_origins"),
        context="configured CORS origins",
    )
    exact_references(
        origin_expression,
        expected=("var.frontend_domain_name",),
        context="configured CORS origins",
    )
    for key, expected in (
        ("allow_credentials", False),
        ("allow_headers", ["authorization", "content-type"]),
        ("allow_methods", ["GET", "POST", "OPTIONS"]),
        ("max_age", 300),
    ):
        exact_constant(
            mapping(configured_cors_values.get(key), context=f"CORS {key}"),
            expected=expected,
            context=f"CORS {key}",
        )

    route = after(require_count(resources, "aws_apigatewayv2_route", 1)[0])
    if route.get("authorization_type") != "JWT":
        fail("A rota default da API não exige JWT.")

    client = after(require_count(resources, "aws_cognito_user_pool_client", 1)[0])
    if client.get("generate_secret") is not False:
        fail("O cliente Cognito gera credencial permanente.")

    user_pool = after(require_count(resources, "aws_cognito_user_pool", 1)[0])
    if user_pool.get("deletion_protection") != "INACTIVE":
        fail("Cognito impediria o teardown por deletion protection.")
    if user_pool.get("user_pool_tier") != "LITE":
        fail("Cognito deve usar o tier mínimo LITE.")
    admin_config = sequence(
        user_pool.get("admin_create_user_config"), context="cognito.admin_create_user"
    )
    if (
        not admin_config
        or mapping(admin_config[0], context="cognito.admin_create_user[0]").get(
            "allow_admin_create_user_only"
        )
        is not True
    ):
        fail("Cognito permite cadastro público fora do perfil demo.")

    bedrock_endpoints = [
        resource
        for resource in by_type(resources, "aws_vpc_endpoint")
        if "bedrock" in str(resource.get("address"))
    ]
    if bedrock_endpoints:
        fail("Bedrock deve permanecer desabilitado no plano padrão.")


def audit_queue_contract(
    resources: list[Mapping[str, Any]], plan: Mapping[str, Any]
) -> None:
    retention_by_address = {
        "aws_sqs_queue.ingestion": 86400,
        "aws_sqs_queue.ingestion_dlq": 345600,
    }
    for address, expected_retention in retention_by_address.items():
        queue = after(resource_by_address(resources, address=address))
        if queue.get("sqs_managed_sse_enabled") is not True:
            fail(f"{address} não usa criptografia SQS gerenciada.")
        if queue.get("message_retention_seconds") != expected_retention:
            fail(f"{address} não possui a retenção efêmera esperada.")

    ingestion_config = configured_resource(plan, address="aws_sqs_queue.ingestion")
    redrive = mapping(
        mapping(
            ingestion_config.get("expressions"), context="ingestion expressions"
        ).get("redrive_policy"),
        context="ingestion redrive policy",
    )
    redrive_references = normalized_strings(
        redrive.get("references"), context="ingestion redrive references"
    )
    if "aws_sqs_queue.ingestion_dlq.arn" not in redrive_references:
        fail("A fila de ingestão não referencia a ARN da DLQ.")

    allow_config = configured_resource(
        plan, address="aws_sqs_queue_redrive_allow_policy.ingestion_dlq"
    )
    allow_policy = mapping(
        mapping(allow_config.get("expressions"), context="DLQ expressions").get(
            "redrive_allow_policy"
        ),
        context="DLQ redrive allow policy",
    )
    allow_references = normalized_strings(
        allow_policy.get("references"), context="DLQ allow references"
    )
    if "aws_sqs_queue.ingestion.arn" not in allow_references:
        fail("A DLQ não restringe redrive à fila de ingestão.")


def statements(policy: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = policy.get("Statement")
    if isinstance(raw, list):
        return [
            mapping(item, context="policy.Statement")
            for item in cast(list[object], raw)
        ]
    return [mapping(raw, context="policy.Statement")]


def normalized_strings(value: object, *, context: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    items = sequence(value, context=context)
    if any(not isinstance(item, str) for item in items):
        fail(f"{context} deve conter somente strings.")
    return cast(list[str], items)


def exact_constant(
    expression: Mapping[str, Any], *, expected: object, context: str
) -> None:
    if set(expression) != {"constant_value"}:
        fail(f"{context} deve conter somente constant_value.")
    if expression.get("constant_value") != expected:
        fail(f"{context} diverge do valor exato esperado.")


def json_document(policy_text: str, *, context: str) -> Mapping[str, Any]:
    raw_policy = parse_json_text(policy_text, context=context)
    return mapping(raw_policy, context=context)


def canonical_role_policy(
    policy_text: str, *, address: str
) -> dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]]:
    policy = json_document(policy_text, context=f"{address}.policy")
    if set(policy) != {"Statement", "Version"} or policy.get("Version") != "2012-10-17":
        fail(f"{address} diverge da estrutura IAM versionada esperada.")

    canonical: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {}
    for statement in statements(policy):
        if set(statement) != {"Action", "Effect", "Resource", "Sid"}:
            fail(f"{address} possui campos de statement fora do contrato.")
        sid = statement.get("Sid")
        effect = statement.get("Effect")
        if not isinstance(sid, str) or not isinstance(effect, str):
            fail(f"{address} possui Sid/Effect inválido.")
        if sid in canonical:
            fail(f"{address} repete a statement {sid}.")
        actions = normalized_strings(
            statement.get("Action"), context=f"{address}.{sid}.Action"
        )
        statement_resources = normalized_strings(
            statement.get("Resource"), context=f"{address}.{sid}.Resource"
        )
        if len(actions) != len(set(actions)) or len(statement_resources) != len(
            set(statement_resources)
        ):
            fail(f"{address}.{sid} contém permissões duplicadas.")
        canonical[sid] = (
            effect,
            tuple(sorted(actions)),
            tuple(sorted(statement_resources)),
        )
    return canonical


def variable_string(plan: Mapping[str, Any], *, name: str) -> str:
    variables = mapping(plan.get("variables"), context="variables")
    variable = mapping(variables.get(name), context=f"variables.{name}")
    value = variable.get("value")
    if not isinstance(value, str) or not value:
        fail(f"variables.{name}.value deve ser uma string não vazia.")
    return value


def audit_offline_plan_controls(plan: Mapping[str, Any]) -> None:
    variables = mapping(plan.get("variables"), context="variables")
    offline_validation = mapping(
        variables.get("offline_validation"), context="variables.offline_validation"
    )
    if (
        set(offline_validation) != {"value"}
        or offline_validation.get("value") != "true"
    ):
        fail("Plano não comprova o modo offline isolado do harness.")

    offline_nonce = mapping(
        variables.get("offline_plan_nonce"), context="variables.offline_plan_nonce"
    )
    if set(offline_nonce) != {"value"} or offline_nonce.get("value") is not None:
        fail("Plano persiste o nonce efêmero do harness.")


def resource_string(
    resources: list[Mapping[str, Any]], *, address: str, attribute: str
) -> str:
    value = after(resource_by_address(resources, address=address)).get(attribute)
    if not isinstance(value, str) or not value:
        fail(f"{address}.{attribute} deve ser uma string conhecida no plano.")
    return value


def expected_role_policies(
    resources: list[Mapping[str, Any]], plan: Mapping[str, Any]
) -> dict[str, dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]]]:
    region = variable_string(plan, name="aws_region")
    account_id = variable_string(plan, name="aws_account_id")
    repository_name = resource_string(
        resources, address="aws_ecr_repository.api", attribute="name"
    )
    log_group_name = resource_string(
        resources, address="aws_cloudwatch_log_group.api", attribute="name"
    )
    queue_name = resource_string(
        resources, address="aws_sqs_queue.ingestion", attribute="name"
    )
    artifacts_bucket = resource_string(
        resources, address='aws_s3_bucket.storage["artifacts"]', attribute="bucket"
    )
    documents_bucket = resource_string(
        resources, address='aws_s3_bucket.storage["documents"]', attribute="bucket"
    )
    ecr_arn = f"arn:aws:ecr:{region}:{account_id}:repository/{repository_name}"
    log_stream_arn = (
        f"arn:aws:logs:{region}:{account_id}:log-group:{log_group_name}:log-stream:*"
    )
    queue_arn = f"arn:aws:sqs:{region}:{account_id}:{queue_name}"
    artifacts_arn = f"arn:aws:s3:::{artifacts_bucket}/*"
    documents_arn = f"arn:aws:s3:::{documents_bucket}/*"
    return {
        "aws_iam_role_policy.api_execution": {
            "AuthenticateToEcr": (
                "Allow",
                ("ecr:GetAuthorizationToken",),
                ("*",),
            ),
            "PullApiImage": (
                "Allow",
                tuple(
                    sorted(
                        (
                            "ecr:BatchCheckLayerAvailability",
                            "ecr:BatchGetImage",
                            "ecr:GetDownloadUrlForLayer",
                        )
                    )
                ),
                (ecr_arn,),
            ),
            "WriteApiLogs": (
                "Allow",
                tuple(sorted(("logs:CreateLogStream", "logs:PutLogEvents"))),
                (log_stream_arn,),
            ),
        },
        "aws_iam_role_policy.api_task": {
            "EnqueueDocumentJobs": ("Allow", ("sqs:SendMessage",), (queue_arn,)),
            "ReadAndWriteDemoObjects": (
                "Allow",
                tuple(sorted(("s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"))),
                tuple(sorted((artifacts_arn, documents_arn))),
            ),
        },
        "aws_iam_role_policy.worker_task": {
            "ConsumeDocumentJobs": (
                "Allow",
                tuple(
                    sorted(
                        (
                            "sqs:ChangeMessageVisibility",
                            "sqs:DeleteMessage",
                            "sqs:GetQueueAttributes",
                            "sqs:ReceiveMessage",
                        )
                    )
                ),
                (queue_arn,),
            ),
            "ReadVersionedDocuments": (
                "Allow",
                tuple(sorted(("s3:GetObject", "s3:GetObjectVersion"))),
                (documents_arn,),
            ),
            "WriteDerivedArtifacts": (
                "Allow",
                ("s3:PutObject",),
                (artifacts_arn,),
            ),
        },
    }


def audit_role_trust_and_attachments(
    resources: list[Mapping[str, Any]], plan: Mapping[str, Any]
) -> None:
    expected_trust = {
        "Statement": [
            {
                "Action": "sts:AssumeRole",
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Sid": "AllowEcsTasks",
            }
        ],
        "Version": "2012-10-17",
    }
    roles = ("api_execution", "api_task", "worker_task")
    for role in roles:
        role_address = f"aws_iam_role.{role}"
        trust_text = after(resource_by_address(resources, address=role_address)).get(
            "assume_role_policy"
        )
        if not isinstance(trust_text, str):
            fail(f"{role_address} não expõe trust policy revisável.")
        if (
            json_document(trust_text, context=f"{role_address}.assume_role_policy")
            != expected_trust
        ):
            fail(f"{role_address} diverge da trust policy exclusiva do ECS Tasks.")
        role_expressions = mapping(
            configured_resource(plan, address=role_address).get("expressions"),
            context=f"{role_address}.expressions",
        )
        exact_references(
            mapping(
                role_expressions.get("assume_role_policy"),
                context=f"{role_address}.assume_role_policy",
            ),
            expected=(
                "data.aws_iam_policy_document.ecs_tasks_assume_role.json",
                "data.aws_iam_policy_document.ecs_tasks_assume_role",
            ),
            context=f"{role_address}.assume_role_policy",
        )

        policy_address = f"aws_iam_role_policy.{role}"
        policy_expressions = mapping(
            configured_resource(plan, address=policy_address).get("expressions"),
            context=f"{policy_address}.expressions",
        )
        exact_constant(
            mapping(policy_expressions.get("name"), context=f"{policy_address}.name"),
            expected="least-privilege",
            context=f"{policy_address}.name",
        )
        exact_references(
            mapping(policy_expressions.get("role"), context=f"{policy_address}.role"),
            expected=(f"aws_iam_role.{role}.id", f"aws_iam_role.{role}"),
            context=f"{policy_address}.role",
        )
        exact_references(
            mapping(
                policy_expressions.get("policy"), context=f"{policy_address}.policy"
            ),
            expected=(
                f"data.aws_iam_policy_document.{role}.json",
                f"data.aws_iam_policy_document.{role}",
            ),
            context=f"{policy_address}.policy",
        )


def audit_frontend_bucket_policy(plan: Mapping[str, Any]) -> None:
    configured_policy = configured_resource(
        plan, address="data.aws_iam_policy_document.frontend_bucket"
    )
    expressions = mapping(
        configured_policy.get("expressions"), context="frontend policy expressions"
    )
    if set(expressions) != {"statement"}:
        fail("A policy do frontend possui blocos fora da statement esperada.")
    configured_statements = sequence(
        expressions.get("statement"), context="frontend policy statements"
    )
    if len(configured_statements) != 1:
        fail("A policy do frontend deve possuir uma única statement.")
    statement = mapping(configured_statements[0], context="frontend policy statement")
    if set(statement) != {"actions", "condition", "principals", "resources", "sid"}:
        fail("A statement do frontend possui campos fora do contrato.")
    exact_constant(
        mapping(statement.get("sid"), context="frontend policy sid"),
        expected="AllowCloudFrontOACReadOnly",
        context="frontend policy sid",
    )
    exact_constant(
        mapping(statement.get("actions"), context="frontend policy actions"),
        expected=["s3:GetObject"],
        context="frontend policy actions",
    )
    exact_references(
        mapping(statement.get("resources"), context="frontend policy resources"),
        expected=(
            'aws_s3_bucket.storage["frontend"].arn',
            'aws_s3_bucket.storage["frontend"]',
            "aws_s3_bucket.storage",
        ),
        context="frontend policy resources",
    )

    principals = sequence(
        statement.get("principals"), context="frontend policy principals"
    )
    if len(principals) != 1:
        fail("A policy do frontend deve possuir um único principal.")
    principal = mapping(principals[0], context="frontend policy principal")
    if set(principal) != {"identifiers", "type"}:
        fail("O principal da policy do frontend possui campos inesperados.")
    exact_constant(
        mapping(principal.get("identifiers"), context="frontend principal identifiers"),
        expected=["cloudfront.amazonaws.com"],
        context="frontend principal identifiers",
    )
    exact_constant(
        mapping(principal.get("type"), context="frontend principal type"),
        expected="Service",
        context="frontend principal type",
    )

    conditions = sequence(
        statement.get("condition"), context="frontend policy conditions"
    )
    if len(conditions) != 2:
        fail("A policy do frontend deve possuir duas condições exatas.")
    expected_conditions = {
        "AWS:SourceAccount": ("var.aws_account_id",),
        "AWS:SourceArn": (
            "aws_cloudfront_distribution.frontend.arn",
            "aws_cloudfront_distribution.frontend",
        ),
    }
    seen_conditions: set[str] = set()
    for raw_condition in conditions:
        condition = mapping(raw_condition, context="frontend policy condition")
        if set(condition) != {"test", "values", "variable"}:
            fail("Uma condição da policy do frontend possui campos inesperados.")
        variable_expression = mapping(
            condition.get("variable"), context="frontend condition variable"
        )
        variable = variable_expression.get("constant_value")
        if not isinstance(variable, str) or variable not in expected_conditions:
            fail("A policy do frontend possui condição inesperada.")
        if variable in seen_conditions:
            fail(f"A policy do frontend repete a condição {variable}.")
        seen_conditions.add(variable)
        exact_constant(
            variable_expression,
            expected=variable,
            context=f"frontend condition {variable}.variable",
        )
        exact_constant(
            mapping(condition.get("test"), context=f"{variable}.test"),
            expected="StringEquals",
            context=f"frontend condition {variable}.test",
        )
        exact_references(
            mapping(condition.get("values"), context=f"{variable}.values"),
            expected=expected_conditions[variable],
            context=f"frontend condition {variable}.values",
        )
    if seen_conditions != set(expected_conditions):
        fail("A policy do frontend não condiciona conta e distribuição exatas.")

    bucket_policy = configured_resource(plan, address="aws_s3_bucket_policy.frontend")
    bucket_expressions = mapping(
        bucket_policy.get("expressions"), context="frontend bucket policy expressions"
    )
    if set(bucket_expressions) != {"bucket", "policy"}:
        fail("O vínculo da policy ao bucket frontend possui campos inesperados.")
    exact_references(
        mapping(bucket_expressions.get("bucket"), context="frontend policy bucket"),
        expected=(
            'aws_s3_bucket.storage["frontend"].id',
            'aws_s3_bucket.storage["frontend"]',
            "aws_s3_bucket.storage",
        ),
        context="frontend policy bucket",
    )
    exact_references(
        mapping(bucket_expressions.get("policy"), context="frontend policy document"),
        expected=(
            "data.aws_iam_policy_document.frontend_bucket.json",
            "data.aws_iam_policy_document.frontend_bucket",
        ),
        context="frontend policy document",
    )


def audit_iam(resources: list[Mapping[str, Any]], plan: Mapping[str, Any]) -> None:
    audit_role_trust_and_attachments(resources, plan)
    expected_policies = expected_role_policies(resources, plan)
    for address, expected in expected_policies.items():
        policy_text = after(resource_by_address(resources, address=address)).get(
            "policy"
        )
        if not isinstance(policy_text, str):
            fail(f"{address} não expõe policy revisável no plano.")
        actual = canonical_role_policy(policy_text, address=address)
        if actual != expected:
            fail(f"{address} diverge integralmente da policy IAM mínima esperada.")

    bucket_policy_value = after(
        resource_by_address(resources, address="aws_s3_bucket_policy.frontend")
    ).get("policy")
    if bucket_policy_value is not None:
        fail(
            "A policy calculada do frontend deveria permanecer unknown no plano limpo."
        )
    audit_frontend_bucket_policy(plan)


def audit_budget_observability_and_tags(
    resources: list[Mapping[str, Any]], plan: Mapping[str, Any]
) -> None:
    budget_resource = require_count(resources, "aws_budgets_budget", 1)[0]
    budget = after(budget_resource)
    budget_change = mapping(budget_resource.get("change"), context="budget.change")
    budget_sensitive = mapping(
        budget_change.get("after_sensitive"), context="budget.after_sensitive"
    )
    if budget_sensitive.get("notification") is not True:
        fail("O destinatário do Budget não está protegido como sensitive.")
    if budget.get("limit_unit") != "USD":
        fail("Budget deve usar USD, a unidade suportada pela estimativa.")
    try:
        limit = float(str(budget.get("limit_amount")))
    except ValueError as error:
        raise AuditError("Budget possui limite não numérico.") from error
    if limit > 16:
        fail("Budget excede a margem documentada para R$ 100.")

    if budget.get("budget_type") != "COST" or budget.get("time_unit") != "MONTHLY":
        fail("Budget deve controlar custo mensal.")
    notifications = sequence(budget.get("notification"), context="budget.notification")
    notification_contract = {
        (
            str(notification.get("notification_type")),
            float(str(notification.get("threshold"))),
        )
        for notification in (
            mapping(item, context="budget.notification[]") for item in notifications
        )
    }
    if notification_contract != {("ACTUAL", 80.0), ("FORECASTED", 100.0)}:
        fail("Budget não possui os alertas ACTUAL 80% e FORECASTED 100%.")
    for item in notifications:
        notification = mapping(item, context="budget.notification[]")
        recipients = sequence(
            notification.get("subscriber_email_addresses"),
            context="budget subscriber emails",
        )
        if len(recipients) != 1:
            fail("Cada alerta do Budget deve possuir exatamente um destinatário.")

    for address in BUDGET_GATED_RESOURCES:
        configured = configured_resource(plan, address=address)
        dependencies = normalized_strings(
            configured.get("depends_on"), context=f"{address}.depends_on"
        )
        if "aws_budgets_budget.demo" not in dependencies:
            fail(f"{address} pode ser criado antes do AWS Budget.")

    configuration = mapping(plan.get("configuration"), context="configuration")
    root_module = mapping(configuration.get("root_module"), context="root_module")
    variables = mapping(root_module.get("variables"), context="configured variables")
    budget_email = mapping(
        variables.get("budget_alert_email"), context="budget_alert_email variable"
    )
    if budget_email.get("sensitive") is not True:
        fail("budget_alert_email deve permanecer sensitive no plano.")

    alarms = require_count(resources, "aws_cloudwatch_metric_alarm", 4)
    metrics: set[str] = set()
    for alarm in alarms:
        values = after(alarm)
        metrics.add(str(values.get("metric_name")))
        if values.get("treat_missing_data") != "notBreaching":
            fail(f"{alarm.get('address')} trata ausência de dados como alarme.")
        for action_key in (
            "alarm_actions",
            "insufficient_data_actions",
            "ok_actions",
        ):
            if values.get(action_key) not in (None, []):
                fail(f"{alarm.get('address')} possui ação automática não documentada.")
    if metrics != {
        "5xx",
        "ApproximateAgeOfOldestMessage",
        "ApproximateNumberOfMessagesVisible",
        "CPUUtilization",
    }:
        fail(f"Métricas dos quatro alarmes divergem do contrato: {sorted(metrics)}")

    for log_group in require_count(resources, "aws_cloudwatch_log_group", 2):
        values = after(log_group)
        retention = values.get("retention_in_days")
        if not isinstance(retention, int) or not 1 <= retention <= 14:
            fail(f"{log_group.get('address')} possui retenção incompatível com demo.")
        if values.get("skip_destroy") is not False:
            fail(f"{log_group.get('address')} seria retido no teardown.")

    tagged_resources = 0
    for resource in resources:
        values = after(resource)
        tags = values.get("tags_all")
        if tags is None:
            continue
        tagged_resources += 1
        tag_map = mapping(tags, context=f"{resource.get('address')}.tags_all")
        for key, expected in REQUIRED_TAGS.items():
            if tag_map.get(key) != expected:
                fail(f"{resource.get('address')} não possui tag {key}={expected}.")
    if tagged_resources < 20:
        fail("Poucos recursos taggable foram reconhecidos no plano.")


def audit_outputs(plan: Mapping[str, Any]) -> None:
    expected_names = set(EXPECTED_OUTPUT_REFERENCES)
    frontend_url = f"https://{variable_string(plan, name='frontend_domain_name')}"
    known_output_values: dict[str, object] = {
        "bedrock_enabled": False,
        "cors_allowed_origin": frontend_url,
        "frontend_url": frontend_url,
    }
    configuration = mapping(plan.get("configuration"), context="configuration")
    root_module = mapping(configuration.get("root_module"), context="root_module")
    configured_outputs = mapping(
        root_module.get("outputs"), context="configuration.root_module.outputs"
    )
    planned_outputs = mapping(plan.get("output_changes"), context="output_changes")
    for context, actual_names in (
        ("Configuração de outputs", set(configured_outputs)),
        ("Plano de outputs", set(planned_outputs)),
    ):
        unexpected = sorted(actual_names - expected_names)
        missing = sorted(expected_names - actual_names)
        if unexpected or missing:
            details: list[str] = []
            if unexpected:
                details.append(f"inesperados={unexpected}")
            if missing:
                details.append(f"ausentes={missing}")
            fail(f"{context} diverge da allowlist exata: " + "; ".join(details))

    for name, expected_references in EXPECTED_OUTPUT_REFERENCES.items():
        configured_output = mapping(
            configured_outputs.get(name), context=f"configured output {name}"
        )
        if set(configured_output) != {"description", "expression"}:
            fail(f"Output {name} possui configuração fora do contrato.")
        description = configured_output.get("description")
        if not isinstance(description, str) or not description.strip():
            fail(f"Output {name} deve possuir descrição não vazia.")
        exact_references(
            mapping(
                configured_output.get("expression"),
                context=f"configured output {name}.expression",
            ),
            expected=expected_references,
            context=f"configured output {name}.expression",
        )

        planned_output = mapping(
            planned_outputs.get(name), context=f"output_changes.{name}"
        )
        common_keys: set[str] = {
            "actions",
            "after_sensitive",
            "after_unknown",
            "before",
            "before_sensitive",
        }
        conditional_keys: set[str] = {"after"} if name in known_output_values else set()
        expected_keys: set[str] = common_keys | conditional_keys
        if set(planned_output) != expected_keys:
            fail(f"Output {name} possui campos planejados fora do contrato.")
        if sequence(
            planned_output.get("actions"), context=f"output_changes.{name}.actions"
        ) != ["create"]:
            fail(f"Output {name} não é uma criação limpa.")
        if (
            planned_output.get("before") is not None
            or planned_output.get("before_sensitive") is not False
            or planned_output.get("after_sensitive") is not False
        ):
            fail(f"Output {name} diverge do contrato público não sensível.")
        if name in known_output_values:
            if (
                planned_output.get("after") != known_output_values[name]
                or planned_output.get("after_unknown") is not False
            ):
                fail(f"Output {name} diverge do valor público determinístico esperado.")
        elif planned_output.get("after_unknown") is not True:
            fail(f"Output {name} deveria permanecer calculado pelo provider.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita invariantes do plano JSON do perfil AWS demo."
    )
    parser.add_argument("plan_json", type=Path)
    return parser.parse_args()


def audit_plan(raw_plan: object) -> None:
    plan = mapping(raw_plan, context="plan")
    if plan.get("format_version") is None or plan.get("terraform_version") is None:
        fail("Arquivo não parece ser a saída de terraform show -json.")

    audit_offline_plan_controls(plan)
    resources = planned_resources(plan)
    audit_resource_scope(resources)
    audit_storage(resources, plan)
    audit_network_and_auth(resources, plan)
    audit_queue_contract(resources, plan)
    audit_iam(resources, plan)
    audit_budget_observability_and_tags(resources, plan)
    audit_outputs(plan)


def main() -> int:
    args = parse_args()
    try:
        plan_text = args.plan_json.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise AuditError("Não foi possível ler um plano JSON UTF-8 válido.") from error
    raw_plan = parse_json_text(plan_text, context="plan")
    audit_plan(raw_plan)

    print(
        "Plano aprovado: allowlist exata, rede privada, IAM integral, "
        "outputs públicos, budget/alarmes, tags e teardown verificados."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
