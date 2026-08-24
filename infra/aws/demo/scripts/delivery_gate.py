"""Fail-closed value, identity, and action gates for AWS demo delivery."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, NoReturn, cast

from plan_audit import EXPECTED_CONFIGURATION_RESOURCES, EXPECTED_RESOURCE_CHANGES

EXPECTED_MANAGED = {
    address: resource_type
    for address, (resource_type, _) in EXPECTED_RESOURCE_CHANGES.items()
    if not address.startswith("data.")
}
EXPECTED_DATA_PREFIXES = {
    address
    for address, (_, mode) in EXPECTED_CONFIGURATION_RESOURCES.items()
    if mode == "data"
}
ALLOWED_TASK_REPLACEMENTS = {("create", "delete"), ("delete", "create")}
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")
REGION_PATTERN = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
NAME_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,19}$")

REQUIRED_TAGS = {
    "Environment": "demo",
    "Lifecycle": "ephemeral-demo",
    "ManagedBy": "Terraform",
    "Owner": "demo-team",
    "Profile": "aws-demo",
    "Project": "prescriptive-maintenance",
    "Ticket": "SEN-67",
}
TAGGED_ADDRESSES = {
    address
    for address in EXPECTED_MANAGED
    if address.startswith(
        (
            "aws_apigatewayv2_api.",
            "aws_apigatewayv2_stage.",
            "aws_apigatewayv2_vpc_link.",
            "aws_cloudfront_distribution.",
            "aws_cloudwatch_log_group.",
            "aws_cloudwatch_metric_alarm.",
            "aws_cognito_user_pool.",
            "aws_ecr_repository.",
            "aws_ecs_cluster.",
            "aws_ecs_service.",
            "aws_ecs_task_definition.",
            "aws_iam_role.",
            "aws_route_table.",
            "aws_s3_bucket.storage",
            "aws_security_group.",
            "aws_service_discovery_",
            "aws_sqs_queue.",
            "aws_subnet.",
            "aws_vpc.demo",
            "aws_vpc_endpoint.",
        )
    )
}

STATE_TOP_LEVEL_KEYS = {
    "check_results",
    "lineage",
    "outputs",
    "resources",
    "serial",
    "terraform_version",
    "version",
}
STATE_RESOURCE_KEYS = {"instances", "mode", "module", "name", "provider", "type"}
STATE_INSTANCE_KEYS = {
    "attributes",
    "create_before_destroy",
    "dependencies",
    "identity",
    "identity_schema_version",
    "index_key",
    "private",
    "schema_version",
    "sensitive_attributes",
    "status",
}
EXPECTED_STATE_OUTPUT_TYPES = {
    "api_base_url": "string",
    "api_image_reference": "string",
    "artifact_bucket_name": "string",
    "bedrock_enabled": "bool",
    "cognito_client_id": "string",
    "cognito_hosted_ui_origin": "string",
    "cognito_user_pool_id": "string",
    "cors_allowed_origin": "string",
    "document_bucket_name": "string",
    "ecr_repository_url": "string",
    "frontend_bucket_name": "string",
    "frontend_distribution_domain_name": "string",
    "frontend_distribution_id": "string",
    "frontend_url": "string",
    "ingestion_dead_letter_queue_url": "string",
    "ingestion_queue_url": "string",
    "worker_task_role_arn": "string",
}
PLAN_CHANGE_KEYS = {
    "actions",
    "after",
    "after_identity",
    "after_sensitive",
    "after_unknown",
    "before",
    "before_identity",
    "before_sensitive",
    "generated_config",
    "importing",
    "replace_paths",
}


class DeliveryGateError(RuntimeError):
    """Raised without plan values, state values, or resource identifiers."""


def fail(message: str) -> NoReturn:
    raise DeliveryGateError(message)


def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            fail("JSON de entrega possui chave duplicada.")
        result[key] = value
    return result


def reject_nonfinite(value: str) -> NoReturn:
    del value
    fail("JSON de entrega possui número não finito.")


def mapping(value: object, *, context: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        fail(f"{context} não é um objeto base.")
    return cast(dict[str, Any], value)


def sequence(value: object, *, context: str) -> list[object]:
    if type(value) is not list:
        fail(f"{context} não é uma lista base.")
    return cast(list[object], value)


def load_plan(path: Path) -> Mapping[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
        if len(content) > 50_000_000:
            fail("Plano JSON excede o limite operacional.")
        parsed = json.loads(
            content,
            object_pairs_hook=object_pairs,
            parse_constant=reject_nonfinite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise DeliveryGateError("Plano JSON real não é UTF-8 válido.") from None
    return mapping(parsed, context="plano")


def identity_configuration(value: object) -> Mapping[str, str]:
    identity = mapping(value, context="identidade AWS")
    expected_keys = {"account_id", "frontend_domain", "name_prefix", "region"}
    if set(identity) != expected_keys:
        fail("Identidade AWS está incompleta.")
    account = identity.get("account_id")
    region = identity.get("region")
    prefix = identity.get("name_prefix")
    domain = identity.get("frontend_domain")
    if (
        type(account) is not str
        or ACCOUNT_PATTERN.fullmatch(account) is None
        or type(region) is not str
        or REGION_PATTERN.fullmatch(region) is None
        or type(prefix) is not str
        or NAME_PREFIX_PATTERN.fullmatch(prefix) is None
        or type(domain) is not str
        or not domain
        or len(domain) > 253
    ):
        fail("Identidade AWS está fora do contrato.")
    return {
        "account_id": account,
        "frontend_domain": domain,
        "name_prefix": prefix,
        "region": region,
    }


def action_tuple(change: Mapping[str, Any]) -> tuple[str, ...]:
    raw_actions = sequence(change.get("actions"), context="ações do plano")
    if any(type(action) is not str for action in raw_actions):
        fail("Plano possui ação de tipo inválido.")
    return tuple(cast(list[str], raw_actions))


def ensure_plan_change_schema(change: Mapping[str, Any]) -> None:
    if not {"actions", "after", "after_unknown", "before"} <= set(change):
        fail("Change do plano omite before, after ou after_unknown.")
    if set(change) - PLAN_CHANGE_KEYS:
        fail("Change do plano possui campo de schema desconhecido.")
    mapping(change.get("after_unknown"), context="after_unknown")


def managed_changes(
    plan: Mapping[str, Any], *, allow_expected_subset: bool
) -> dict[str, tuple[str, tuple[str, ...], Mapping[str, Any]]]:
    changes: dict[str, tuple[str, tuple[str, ...], Mapping[str, Any]]] = {}
    for raw_resource in sequence(
        plan.get("resource_changes"), context="resource_changes"
    ):
        resource = mapping(raw_resource, context="resource_change")
        address = resource.get("address")
        resource_type = resource.get("type")
        mode = resource.get("mode")
        if type(address) is not str or type(resource_type) is not str:
            fail("Plano possui identidade de recurso inválida.")
        if mode == "data":
            prefix = address.split("[", maxsplit=1)[0]
            if prefix not in EXPECTED_DATA_PREFIXES:
                fail("Plano consulta data source fora do contrato.")
            continue
        if mode != "managed" or address in changes:
            fail("Plano possui recurso duplicado ou mode inesperado.")
        change = mapping(resource.get("change"), context="change")
        ensure_plan_change_schema(change)
        changes[address] = (resource_type, action_tuple(change), change)
    addresses = set(changes)
    if allow_expected_subset:
        if not addresses or not addresses <= set(EXPECTED_MANAGED):
            fail("Plano destrutivo diverge do subconjunto gerenciado aprovado.")
    elif addresses != set(EXPECTED_MANAGED):
        fail("Plano real diverge da allowlist exata de recursos gerenciados.")
    for address, (resource_type, _, _) in changes.items():
        if EXPECTED_MANAGED[address] != resource_type:
            fail("Plano real troca o tipo de um recurso aprovado.")
    return changes


def iter_strings(value: object) -> Iterable[str]:
    if type(value) is str:
        yield value
    elif type(value) is list:
        for item in cast(list[object], value):
            yield from iter_strings(item)
    elif type(value) is dict:
        for item in cast(dict[str, object], value).values():
            yield from iter_strings(item)


def validate_arn_scope(values: Mapping[str, Any], identity: Mapping[str, str]) -> None:
    for candidate in iter_strings(values):
        if not candidate.startswith("arn:aws:"):
            continue
        parts = candidate.split(":", maxsplit=5)
        if len(parts) != 6:
            fail("Recurso contém ARN malformado.")
        _, partition, _, arn_region, arn_account, _ = parts
        if partition != "aws":
            fail("Recurso contém ARN fora da partição aprovada.")
        if arn_region and arn_region != identity["region"]:
            fail("Recurso contém ARN de outra região.")
        if arn_account and arn_account != identity["account_id"]:
            fail("Recurso contém ARN de outra conta.")


def expected_names(identity: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    name = f"{identity['name_prefix']}-demo"
    result = {
        "aws_apigatewayv2_api.demo": ("name", name),
        "aws_apigatewayv2_authorizer.cognito": ("name", "cognito"),
        "aws_apigatewayv2_stage.default": ("name", "$default"),
        "aws_apigatewayv2_vpc_link.api": ("name", f"{name}-api"),
        "aws_budgets_budget.demo": ("name", f"{name}-monthly-cost"),
        "aws_cloudfront_cache_policy.frontend": ("name", f"{name}-frontend"),
        "aws_cloudfront_origin_access_control.frontend": (
            "name",
            f"{name}-frontend",
        ),
        "aws_cloudfront_response_headers_policy.frontend": (
            "name",
            f"{name}-frontend-security",
        ),
        "aws_cloudwatch_log_group.api": ("name", f"/aws/ecs/{name}/api"),
        "aws_cloudwatch_log_group.api_gateway": (
            "name",
            f"/aws/apigateway/{name}",
        ),
        "aws_cloudwatch_metric_alarm.api_5xx": ("alarm_name", f"{name}-api-5xx"),
        "aws_cloudwatch_metric_alarm.api_cpu": ("alarm_name", f"{name}-api-cpu"),
        "aws_cloudwatch_metric_alarm.dlq_messages": (
            "alarm_name",
            f"{name}-dlq-messages",
        ),
        "aws_cloudwatch_metric_alarm.queue_age": (
            "alarm_name",
            f"{name}-queue-age",
        ),
        "aws_cognito_user_pool.demo": ("name", name),
        "aws_cognito_user_pool_client.demo": ("name", f"{name}-public-client"),
        "aws_ecr_repository.api": ("name", f"{name}/api"),
        "aws_ecs_cluster.demo": ("name", name),
        "aws_ecs_service.api": ("name", f"{name}-api"),
        "aws_ecs_task_definition.api": ("family", f"{name}-api"),
        "aws_iam_role.api_execution": ("name", f"{name}-api-execution"),
        "aws_iam_role.api_task": ("name", f"{name}-api-task"),
        "aws_iam_role.worker_task": ("name", f"{name}-worker-task"),
        "aws_service_discovery_private_dns_namespace.demo": (
            "name",
            f"{name}.internal",
        ),
        "aws_service_discovery_service.api": ("name", "api"),
        "aws_sqs_queue.ingestion": ("name", f"{name}-ingestion"),
        "aws_sqs_queue.ingestion_dlq": ("name", f"{name}-ingestion-dlq"),
    }
    for role in ("api_execution", "api_task", "worker_task"):
        result[f"aws_iam_role_policy.{role}"] = ("name", "least-privilege")
    for group in ("api", "endpoints", "vpc_link"):
        suffix = group.replace("_", "-")
        result[f"aws_security_group.{group}"] = ("name", f"{name}-{suffix}")
    return result


def expected_buckets(identity: Mapping[str, str]) -> dict[str, str]:
    return {
        kind: (
            f"{identity['name_prefix']}-{kind}-{identity['account_id']}-"
            f"{identity['region']}"
        )
        for kind in ("artifacts", "documents", "frontend")
    }


def expected_cognito_domain(identity: Mapping[str, str]) -> str:
    seed = ":".join(
        (
            identity["name_prefix"],
            identity["frontend_domain"],
            identity["region"],
        )
    )
    return f"spm-{hashlib.sha256(seed.encode()).hexdigest()[:20]}"


def validate_tags(
    address: str, values: Mapping[str, Any], identity: Mapping[str, str]
) -> None:
    if address not in TAGGED_ADDRESSES:
        return
    tags = values.get("tags_all")
    if type(tags) is not dict:
        fail("Recurso tagueável não possui tags_all conhecidas.")
    expected = dict(REQUIRED_TAGS)
    expected["Owner"] = "demo-team"
    actual = cast(dict[str, object], tags)
    if any(actual.get(key) != value for key, value in expected.items()):
        fail("Recurso não pertence às tags canônicas da demo.")
    name_tag = actual.get("Name")
    canonical_name = f"{identity['name_prefix']}-demo"
    if type(name_tag) is not str or not (
        name_tag == canonical_name or name_tag.startswith(f"{canonical_name}-")
    ):
        fail("Recurso não possui nome de tag dentro do prefixo canônico.")
    explicit_tags = values.get("tags")
    if type(explicit_tags) is not dict:
        fail("Recurso tagueável não possui tags explícitas conhecidas.")


def validate_generated_id(resource_type: str, values: Mapping[str, Any]) -> None:
    patterns = {
        "aws_apigatewayv2_api": r"^[a-z0-9]{10}$",
        "aws_apigatewayv2_vpc_link": r"^[a-z0-9]{10}$",
        "aws_cloudfront_distribution": r"^[A-Z0-9]{8,32}$",
        "aws_cognito_user_pool": r"^[a-z]{2}-[a-z]+-\d_[A-Za-z0-9]+$",
        "aws_cognito_user_pool_client": r"^[a-z0-9]{1,128}$",
        "aws_route_table": r"^rtb-[0-9a-f]+$",
        "aws_route_table_association": r"^rtbassoc-[0-9a-f]+$",
        "aws_security_group": r"^sg-[0-9a-f]+$",
        "aws_subnet": r"^subnet-[0-9a-f]+$",
        "aws_vpc": r"^vpc-[0-9a-f]+$",
        "aws_vpc_endpoint": r"^vpce-[0-9a-f]+$",
        "aws_vpc_security_group_egress_rule": r"^sgr-[0-9a-f]+$",
        "aws_vpc_security_group_ingress_rule": r"^sgr-[0-9a-f]+$",
    }
    pattern = patterns.get(resource_type)
    if pattern is None:
        return
    resource_id = values.get("id")
    if type(resource_id) is not str or re.fullmatch(pattern, resource_id) is None:
        fail("Recurso possui ID gerado fora do tipo canônico.")


def validate_resource_identity(
    address: str,
    resource_type: str,
    values: object,
    identity: Mapping[str, str],
    *,
    allow_computed_identity: bool = False,
) -> Mapping[str, Any]:
    attributes = mapping(values, context="atributos do recurso")
    validate_arn_scope(attributes, identity)
    demo_name = f"{identity['name_prefix']}-demo"
    expected = expected_names(identity).get(address)
    if expected is not None:
        field, value = expected
        if attributes.get(field) != value:
            fail("Recurso possui nome ou família fora do prefixo canônico.")
    buckets = expected_buckets(identity)
    bucket_match = re.match(
        r'^aws_s3_bucket(?:_[a-z_]+)?\.storage\["(artifacts|documents|frontend)"\]$',
        address,
    )
    if bucket_match is not None:
        field = "bucket"
        expected_bucket = buckets[bucket_match.group(1)]
        if attributes.get(field) != expected_bucket or (
            address.startswith("aws_s3_bucket.storage")
            and attributes.get("id") != expected_bucket
        ):
            fail("Recurso S3 possui bucket fora da identidade canônica.")
    if (
        address == "aws_s3_bucket_policy.frontend"
        and attributes.get("bucket") != buckets["frontend"]
    ):
        fail("Policy S3 aponta para bucket fora da identidade canônica.")
    if address.startswith("aws_apigatewayv2_") and address not in {
        "aws_apigatewayv2_api.demo",
        "aws_apigatewayv2_vpc_link.api",
    }:
        api_id = attributes.get("api_id")
        if type(api_id) is not str or re.fullmatch(r"[a-z0-9]{10}", api_id) is None:
            fail("Subrecurso API Gateway não possui API ID canônico.")
    if address == "aws_apigatewayv2_api.demo":
        api_id = attributes.get("id")
        if attributes.get("api_endpoint") != (
            f"https://{api_id}.execute-api.{identity['region']}.amazonaws.com"
        ):
            fail("API demo não possui endpoint regional ligado ao próprio ID.")
    if address == "aws_cognito_user_pool_client.demo":
        pool_id = attributes.get("user_pool_id")
        if (
            type(pool_id) is not str
            or re.fullmatch(r"[a-z]{2}-[a-z]+-\d_[A-Za-z0-9]+", pool_id) is None
        ):
            fail("App client não aponta para user pool canônico.")
    if address == "aws_cognito_user_pool_domain.demo":
        domain = expected_cognito_domain(identity)
        pool_id = attributes.get("user_pool_id")
        if (
            attributes.get("domain") != domain
            or attributes.get("id") != domain
            or re.fullmatch(r"spm-[0-9a-f]{20}", domain) is None
            or type(pool_id) is not str
            or re.fullmatch(r"[a-z]{2}-[a-z]+-\d_[A-Za-z0-9]+", pool_id) is None
            or attributes.get("managed_login_version") != 1
        ):
            fail("Domínio Cognito no state diverge da identidade determinística.")
    if (
        address == "aws_ecr_lifecycle_policy.api"
        and attributes.get("repository") != f"{demo_name}/api"
    ):
        fail("Lifecycle ECR aponta para repositório fora do prefixo canônico.")
    role_policy_names = {
        "aws_iam_role_policy.api_execution": f"{demo_name}-api-execution",
        "aws_iam_role_policy.api_task": f"{demo_name}-api-task",
        "aws_iam_role_policy.worker_task": f"{demo_name}-worker-task",
    }
    if (
        address in role_policy_names
        and attributes.get("role") != role_policy_names[address]
    ):
        fail("Inline policy aponta para role fora do prefixo canônico.")
    if address == "aws_ecs_cluster.demo":
        cluster_arn = (
            f"arn:aws:ecs:{identity['region']}:{identity['account_id']}:"
            f"cluster/{demo_name}"
        )
        if attributes.get("id") != cluster_arn or attributes.get("arn") != cluster_arn:
            fail("Cluster ECS não possui ARN canônico.")
    if address == "aws_ecs_service.api":
        cluster_arn = (
            f"arn:aws:ecs:{identity['region']}:{identity['account_id']}:"
            f"cluster/{demo_name}"
        )
        task_pattern = re.compile(
            rf"^arn:aws:ecs:{re.escape(identity['region'])}:"
            rf"{re.escape(identity['account_id'])}:task-definition/"
            rf"{re.escape(demo_name)}-api:[1-9][0-9]*$"
        )
        task_definition = attributes.get("task_definition")
        if attributes.get("cluster") != cluster_arn or (
            not (allow_computed_identity and task_definition is None)
            and (
                type(task_definition) is not str
                or task_pattern.fullmatch(task_definition) is None
            )
        ):
            fail("Service ECS aponta para cluster ou task fora do prefixo canônico.")
    validate_tags(address, attributes, identity)
    if not allow_computed_identity or attributes.get("id") is not None:
        validate_generated_id(resource_type, attributes)
    return attributes


def validate_container_definition(
    raw: object,
    identity: Mapping[str, str],
    *,
    expected_image: str | None,
) -> str:
    if type(raw) is not str or len(raw) > 1_000_000:
        fail("Task definition não possui container_definitions conhecido.")
    try:
        parsed = json.loads(raw, object_pairs_hook=object_pairs)
    except json.JSONDecodeError:
        raise DeliveryGateError("container_definitions não é JSON válido.") from None
    containers = sequence(parsed, context="container_definitions")
    if len(containers) != 1:
        fail("Task definition deve possuir exatamente um container.")
    container = mapping(containers[0], context="container da API")
    repository = (
        f"{identity['account_id']}.dkr.ecr.{identity['region']}.amazonaws.com/"
        f"{identity['name_prefix']}-demo/api"
    )
    image = container.get("image")
    if type(image) is not str or not image.startswith(f"{repository}@sha256:"):
        fail("Task definition referencia imagem fora do ECR aprovado.")
    digest = image.removeprefix(f"{repository}@")
    if DIGEST_PATTERN.fullmatch(digest) is None:
        fail("Task definition não usa digest OCI canônico.")
    if expected_image is not None and image != expected_image:
        fail("Task definition não usa exatamente o digest verificado no ECR.")
    environment = container.get("environment")
    expected_environment = [
        {
            "name": "PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE",
            "value": "synthetic_demo",
        },
        {"name": "PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT", "value": "aws"},
        {
            "name": "PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND",
            "value": "memory",
        },
    ]
    ports = container.get("portMappings")
    expected_ports = [
        {
            "appProtocol": "http",
            "containerPort": 8000,
            "hostPort": 8000,
            "name": "api-http",
            "protocol": "tcp",
        }
    ]
    health = mapping(container.get("healthCheck"), context="healthCheck")
    command = health.get("command")
    if (
        container.get("name") != "api"
        or container.get("essential") is not True
        or container.get("readonlyRootFilesystem") is not True
        or container.get("stopTimeout") != 30
        or container.get("user") != "65532:65532"
        or environment != expected_environment
        or ports != expected_ports
        or container.get("command", []) not in (None, [])
        or container.get("entryPoint", []) not in (None, [])
        or container.get("secrets", []) not in (None, [])
        or type(command) is not list
        or cast(list[object], command)[:1] != ["CMD-SHELL"]
        or len(cast(list[object], command)) != 2
        or "127.0.0.1:8000/health/ready" not in cast(str, command[1])
        or health.get("interval") != 10
        or health.get("retries") != 3
        or health.get("startPeriod") != 10
        or health.get("timeout") != 3
    ):
        fail("Task definition altera atributos imutáveis do runtime.")
    return image


def validate_task_values(
    values: object,
    identity: Mapping[str, str],
    *,
    expected_image: str | None,
) -> Mapping[str, Any]:
    task = validate_resource_identity(
        "aws_ecs_task_definition.api",
        "aws_ecs_task_definition",
        values,
        identity,
        allow_computed_identity=True,
    )
    name = f"{identity['name_prefix']}-demo"
    if (
        str(task.get("cpu")) != "256"
        or str(task.get("memory")) != "512"
        or task.get("network_mode") != "awsvpc"
        or task.get("requires_compatibilities") != ["FARGATE"]
        or task.get("execution_role_arn")
        != f"arn:aws:iam::{identity['account_id']}:role/{name}-api-execution"
        or task.get("task_role_arn")
        != f"arn:aws:iam::{identity['account_id']}:role/{name}-api-task"
    ):
        fail("Task definition altera CPU, memória, rede ou roles imutáveis.")
    platform = sequence(task.get("runtime_platform"), context="runtime_platform")
    if platform != [{"cpu_architecture": "X86_64", "operating_system_family": "LINUX"}]:
        fail("Task definition altera a plataforma imutável.")
    validate_container_definition(
        task.get("container_definitions"),
        identity,
        expected_image=expected_image,
    )
    return task


def unknown_paths(value: object, prefix: str = "") -> set[str]:
    result: set[str] = set()
    if value is True:
        result.add(prefix)
    elif type(value) is dict:
        for key, nested in cast(dict[str, object], value).items():
            child = f"{prefix}.{key}" if prefix else key
            result.update(unknown_paths(nested, child))
    elif type(value) is list:
        for index, nested in enumerate(cast(list[object], value)):
            result.update(unknown_paths(nested, f"{prefix}[{index}]"))
    elif value not in (False, None):
        fail("after_unknown possui valor fora do schema booleano.")
    return result


def validate_runtime_unknowns(address: str, raw_unknowns: object) -> None:
    paths = unknown_paths(raw_unknowns)
    allowed = {
        "aws_ecs_task_definition.api": {
            "arn",
            "arn_without_revision",
            "id",
            "revision",
            "tags_all",
        },
        "aws_ecs_service.api": {"id", "task_definition", "tags_all"},
    }.get(address, set())
    if not all(
        any(path == item or path.startswith(f"{item}.") for item in allowed)
        for path in paths
    ):
        fail("Runtime possui after_unknown fora dos campos computados aprovados.")


def require_service_reference(plan: Mapping[str, Any]) -> None:
    configuration = mapping(plan.get("configuration"), context="configuration")
    root_module = mapping(configuration.get("root_module"), context="root_module")
    resources = sequence(
        root_module.get("resources"), context="configuration.resources"
    )
    matches = [
        mapping(resource, context="recurso de configuração")
        for resource in resources
        if mapping(resource, context="recurso de configuração").get("address")
        == "aws_ecs_service.api"
    ]
    if len(matches) != 1:
        fail("Configuração não contém exatamente o service ECS aprovado.")
    expressions = mapping(matches[0].get("expressions"), context="expressions")
    task_expression = mapping(
        expressions.get("task_definition"), context="expressão task_definition"
    )
    if task_expression.get("references") != ["aws_ecs_task_definition.api.arn"]:
        fail("Service ECS não referencia exatamente a nova task revision.")


def validate_noop_change(change: Mapping[str, Any]) -> None:
    if change.get("before") != change.get("after") or unknown_paths(
        change.get("after_unknown")
    ):
        fail("No-op possui valores divergentes ou unknowns residuais.")


def validate_runtime(
    plan: Mapping[str, Any],
    changes: Mapping[str, tuple[str, tuple[str, ...], Mapping[str, Any]]],
    identity: Mapping[str, str],
    expected_image: str,
) -> None:
    repository = (
        f"{identity['account_id']}.dkr.ecr.{identity['region']}.amazonaws.com/"
        f"{identity['name_prefix']}-demo/api"
    )
    if (
        not expected_image.startswith(f"{repository}@")
        or DIGEST_PATTERN.fullmatch(expected_image.removeprefix(f"{repository}@"))
        is None
    ):
        fail("Digest esperado do runtime não pertence ao ECR aprovado.")
    task_change = changes["aws_ecs_task_definition.api"][2]
    service_change = changes["aws_ecs_service.api"][2]
    task_actions = action_tuple(task_change)
    service_actions = action_tuple(service_change)
    if task_actions == ("no-op",) and service_actions == ("no-op",):
        for _, actions, change in changes.values():
            if actions != ("no-op",):
                fail("Runtime idempotente não pode alterar recursos.")
            validate_noop_change(change)
        validate_task_values(
            task_change.get("after"), identity, expected_image=expected_image
        )
        service = validate_resource_identity(
            "aws_ecs_service.api",
            "aws_ecs_service",
            service_change.get("after"),
            identity,
        )
        task = mapping(task_change.get("after"), context="task ECS")
        if service.get("task_definition") != task.get("arn"):
            fail("State planejado não liga o service à task revision exata.")
        return
    if task_actions not in ALLOWED_TASK_REPLACEMENTS or service_actions != ("update",):
        fail("Runtime deve trocar somente task definition e service ECS.")
    for address, (_, actions, change) in changes.items():
        if address in {"aws_ecs_service.api", "aws_ecs_task_definition.api"}:
            continue
        if actions != ("no-op",):
            fail("Runtime tenta alterar recurso fora da troca OCI.")
        validate_noop_change(change)
    validate_runtime_unknowns(
        "aws_ecs_task_definition.api", task_change["after_unknown"]
    )
    validate_runtime_unknowns("aws_ecs_service.api", service_change["after_unknown"])
    before_task = validate_task_values(
        task_change.get("before"), identity, expected_image=None
    )
    after_task = validate_task_values(
        task_change.get("after"), identity, expected_image=expected_image
    )
    immutable_task_fields = {
        "container_definitions",
        "cpu",
        "execution_role_arn",
        "family",
        "memory",
        "network_mode",
        "requires_compatibilities",
        "runtime_platform",
        "task_role_arn",
    }
    for field in immutable_task_fields - {"container_definitions"}:
        if before_task.get(field) != after_task.get(field):
            fail("Task definition altera atributo imutável além da imagem.")
    before_service = validate_resource_identity(
        "aws_ecs_service.api",
        "aws_ecs_service",
        service_change.get("before"),
        identity,
    )
    if before_service.get("task_definition") != before_task.get("arn"):
        fail("Service anterior não aponta para a task revision anterior.")
    after_service = validate_resource_identity(
        "aws_ecs_service.api",
        "aws_ecs_service",
        service_change.get("after"),
        identity,
        allow_computed_identity=True,
    )
    allowed_service_differences = {"desired_count", "task_definition"}
    for key in set(before_service) | set(after_service):
        if key not in allowed_service_differences and before_service.get(
            key
        ) != after_service.get(key):
            fail("Service ECS altera atributo fora da nova task revision.")
    if after_service.get("desired_count") != 1:
        fail("Service ECS deve executar exatamente uma task no runtime.")
    task_unknowns = unknown_paths(task_change.get("after_unknown"))
    service_unknowns = unknown_paths(service_change.get("after_unknown"))
    task_arn_unknown = "arn" in task_unknowns
    service_task_unknown = "task_definition" in service_unknowns
    if task_arn_unknown != service_task_unknown:
        fail("Task e service divergem sobre a revisão computada.")
    if task_arn_unknown:
        if (
            after_task.get("arn") is not None
            or after_service.get("task_definition") is not None
        ):
            fail("Valor conhecido não pode também ser marcado como unknown.")
    elif type(after_task.get("arn")) is not str or after_service.get(
        "task_definition"
    ) != after_task.get("arn"):
        fail("Service planejado não aponta para a task revision exata.")
    require_service_reference(plan)


def audit_plan(
    plan: Mapping[str, Any],
    *,
    mode: str,
    phase: str | None,
    identity: Mapping[str, str] | None = None,
    expected_image: str | None = None,
) -> None:
    if (
        type(plan.get("format_version")) is not str
        or type(plan.get("terraform_version")) is not str
    ):
        fail("Arquivo não possui metadados de plano Terraform.")
    changes = managed_changes(plan, allow_expected_subset=mode == "destroy")
    actions = {address: value[1] for address, value in changes.items()}
    if mode == "destroy":
        if phase is not None or expected_image is not None:
            fail("Plano destrutivo recebeu contexto incompatível.")
        scope = identity_configuration(identity)
        before_values: dict[str, Mapping[str, Any]] = {}
        for address, (resource_type, change_actions, change) in changes.items():
            if change_actions != ("delete",) or change.get("after") is not None:
                fail("Plano de teardown deve conter somente exclusões exatas.")
            if unknown_paths(change.get("after_unknown")):
                fail("Plano destrutivo não aceita after_unknown.")
            before_values[address] = validate_resource_identity(
                address, resource_type, change.get("before"), scope
            )
        validate_relationships(before_values)
        task = before_values.get("aws_ecs_task_definition.api")
        if task is not None:
            validate_task_values(task, scope, expected_image=None)
        return

    allowed_common = {("no-op",), ("create",), ("update",)}
    for address, value in actions.items():
        if value in allowed_common:
            continue
        if (
            address == "aws_ecs_task_definition.api"
            and value in ALLOWED_TASK_REPLACEMENTS
        ):
            continue
        fail("Plano de plan/deploy contém exclusão ou replacement não aprovado.")
    if mode == "review":
        if phase is not None or expected_image is not None:
            fail("Plan review não aceita fase ou digest de deploy.")
        for _, action, change in changes.values():
            if action == ("no-op",):
                validate_noop_change(change)
        return
    if mode != "deploy" or phase not in {"foundation", "foundation-ready", "runtime"}:
        fail("Modo ou fase do gate de deploy é inválido.")
    if phase == "foundation":
        if expected_image is not None or any(
            value != ("create",) for value in actions.values()
        ):
            fail("Fundação inicial deve criar integralmente o perfil vazio.")
        for _, _, change in changes.values():
            if change.get("before") is not None:
                fail("Fundação não pode sobrescrever valor anterior.")
        return
    if phase == "foundation-ready":
        if expected_image is not None or any(
            value != ("no-op",) for value in actions.values()
        ):
            fail("Fundação existente deve permanecer sem alteração.")
        for _, _, change in changes.values():
            validate_noop_change(change)
        return
    if identity is None or expected_image is None:
        fail("Runtime exige identidade AWS e digest ECR verificado.")
    validate_runtime(plan, changes, identity_configuration(identity), expected_image)


def state_addresses(path: Path) -> set[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise DeliveryGateError("Inventário do state não é UTF-8 válido.") from None
    if len(content) > 1_000_000:
        fail("Inventário do state excede o limite operacional.")
    addresses: set[str] = set()
    for line in content.splitlines():
        address = line.strip()
        if not address or address in addresses:
            fail("Inventário do state possui linha vazia ou duplicada.")
        if any(character.isspace() or ord(character) < 33 for character in address):
            fail("Inventário do state possui endereço inválido.")
        addresses.add(address)
    return addresses


def audit_state(path: Path, *, mode: str) -> None:
    addresses = state_addresses(path)
    if mode in {"fresh", "destroyed"}:
        if addresses:
            fail("State deveria estar vazio para esta fase.")
        return
    if mode not in {"destroyable", "existing"}:
        fail("Modo do gate de state é inválido.")
    managed = {address for address in addresses if not address.startswith("data.")}
    data = addresses - managed
    if mode == "destroyable":
        if not managed or not managed <= set(EXPECTED_MANAGED):
            fail("State destrutível está vazio ou fora da allowlist.")
    elif managed != set(EXPECTED_MANAGED):
        fail("State existente está parcial ou fora da allowlist.")
    if any(
        address.split("[", maxsplit=1)[0] not in EXPECTED_DATA_PREFIXES
        for address in data
    ):
        fail("State existente contém data source fora da allowlist.")


def state_instance_address(
    resource: Mapping[str, Any], instance: Mapping[str, Any]
) -> str:
    module = resource.get("module")
    if module is not None:
        fail("State não aceita recurso em módulo inesperado.")
    resource_type = resource.get("type")
    name = resource.get("name")
    if type(resource_type) is not str or type(name) is not str:
        fail("State possui identidade de recurso inválida.")
    address = f"{resource_type}.{name}"
    if "index_key" in instance:
        index = instance.get("index_key")
        if type(index) not in {str, int}:
            fail("State possui index_key inválida.")
        address += f"[{json.dumps(index, ensure_ascii=True)}]"
    return address


def validate_state_instance_identity(
    instance: Mapping[str, Any],
    attributes: Mapping[str, Any],
    scope: Mapping[str, str],
) -> None:
    if "identity_schema_version" in instance:
        version = instance.get("identity_schema_version")
        if type(version) is not int or not 0 <= version <= 100:
            fail("Instância do state possui identity_schema_version desconhecida.")
    if "identity" not in instance:
        return
    if "identity_schema_version" not in instance:
        fail("Identidade da instância não declara sua versão de schema.")
    resource_identity = mapping(
        instance.get("identity"), context="identidade da instância do state"
    )
    if not resource_identity:
        fail("Identidade da instância do state está vazia.")
    for key, value in resource_identity.items():
        if (
            re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None
            or type(value) is not str
            or not value
        ):
            fail("Identidade da instância possui formato inválido.")
    if resource_identity.get("account_id", scope["account_id"]) != scope["account_id"]:
        fail("Identidade da instância pertence a outra conta.")
    if resource_identity.get("region", scope["region"]) != scope["region"]:
        fail("Identidade da instância pertence a outra região.")
    if any(
        key != "account_id" and attributes.get(key) != value
        for key, value in resource_identity.items()
    ):
        fail("Identidade da instância diverge dos atributos do state.")


def state_managed_values(
    snapshot: object, *, identity: Mapping[str, str]
) -> dict[str, tuple[str, Mapping[str, Any]]]:
    document = mapping(snapshot, context="snapshot do state")
    if set(document) - STATE_TOP_LEVEL_KEYS or not {
        "lineage",
        "outputs",
        "resources",
        "serial",
        "version",
    } <= set(document):
        fail("Snapshot do state possui schema de topo desconhecido.")
    if (
        document.get("version") != 4
        or type(document.get("serial")) is not int
        or type(document.get("lineage")) is not str
        or type(document.get("outputs")) is not dict
    ):
        fail("Snapshot do state possui metadados inválidos.")
    result: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for raw_resource in sequence(document.get("resources"), context="state.resources"):
        resource = mapping(raw_resource, context="recurso do state")
        if set(resource) - STATE_RESOURCE_KEYS:
            fail("Recurso do state possui campo de schema desconhecido.")
        mode = resource.get("mode")
        if mode == "data":
            continue
        if mode != "managed" or type(resource.get("provider")) is not str:
            fail("State possui mode ou provider inválido.")
        resource_type = resource.get("type")
        if type(resource_type) is not str:
            fail("State possui tipo de recurso inválido.")
        instances = sequence(resource.get("instances"), context="instances")
        if not instances:
            fail("State possui recurso sem instância concreta.")
        for raw_instance in instances:
            instance = mapping(raw_instance, context="instância do state")
            if set(instance) - STATE_INSTANCE_KEYS:
                fail("Instância do state possui campo de schema desconhecido.")
            schema_version = instance.get("schema_version")
            if type(schema_version) is not int or not 0 <= schema_version <= 100:
                fail("Instância do state possui schema_version desconhecida.")
            attributes = mapping(
                instance.get("attributes"), context="atributos do state"
            )
            validate_state_instance_identity(instance, attributes, identity)
            address = state_instance_address(resource, instance)
            if address in result:
                fail("State possui endereço gerenciado duplicado.")
            result[address] = (
                resource_type,
                attributes,
            )
    return result


def validate_relationships(values: Mapping[str, Mapping[str, Any]]) -> None:
    api = values.get("aws_apigatewayv2_api.demo")
    if api is not None:
        api_id = api.get("id")
        for address, attributes in values.items():
            if (
                address.startswith("aws_apigatewayv2_")
                and address
                not in {
                    "aws_apigatewayv2_api.demo",
                    "aws_apigatewayv2_vpc_link.api",
                }
                and attributes.get("api_id") != api_id
            ):
                fail("Subrecurso API Gateway aponta para outra API.")
    user_pool = values.get("aws_cognito_user_pool.demo")
    user_pool_client = values.get("aws_cognito_user_pool_client.demo")
    user_pool_domain = values.get("aws_cognito_user_pool_domain.demo")
    if (
        user_pool is not None
        and user_pool_client is not None
        and user_pool_client.get("user_pool_id") != user_pool.get("id")
    ):
        fail("App client aponta para outro user pool.")
    if (
        user_pool is not None
        and user_pool_domain is not None
        and user_pool_domain.get("user_pool_id") != user_pool.get("id")
    ):
        fail("Domínio Cognito aponta para outro user pool.")
    ecr = values.get("aws_ecr_repository.api")
    ecr_lifecycle = values.get("aws_ecr_lifecycle_policy.api")
    if (
        ecr is not None
        and ecr_lifecycle is not None
        and ecr_lifecycle.get("repository") != ecr.get("name")
    ):
        fail("Lifecycle ECR aponta para outro repositório.")
    cluster = values.get("aws_ecs_cluster.demo")
    service = values.get("aws_ecs_service.api")
    task = values.get("aws_ecs_task_definition.api")
    if (
        cluster is not None
        and service is not None
        and service.get("cluster") != cluster.get("id")
    ):
        fail("Service ECS aponta para outro cluster.")
    if (
        service is not None
        and task is not None
        and service.get("task_definition") != task.get("arn")
    ):
        fail("Service ECS aponta para outra task revision.")
    vpc = values.get("aws_vpc.demo")
    if vpc is None:
        return
    vpc_id = vpc.get("id")
    for address, attributes in values.items():
        if (
            address.startswith(
                (
                    "aws_route_table.private",
                    "aws_security_group.",
                    "aws_subnet.private",
                    "aws_vpc_endpoint.",
                )
            )
            and "vpc_id" in attributes
            and attributes.get("vpc_id") != vpc_id
        ):
            fail("Recurso de rede aponta para VPC fora do state aprovado.")


def state_output_values(snapshot: object) -> Mapping[str, object]:
    document = mapping(snapshot, context="snapshot do state")
    raw_outputs = mapping(document.get("outputs"), context="state.outputs")
    values: dict[str, object] = {}
    for name, raw_output in raw_outputs.items():
        if type(name) is not str or name not in EXPECTED_STATE_OUTPUT_TYPES:
            fail("State possui nome de output inválido.")
        output = mapping(raw_output, context=f"state.outputs.{name}")
        if not {"type", "value"} <= set(output) or set(output) - {
            "sensitive",
            "type",
            "value",
        }:
            fail("State possui schema de output desconhecido.")
        if output.get("type") != EXPECTED_STATE_OUTPUT_TYPES[name] or output.get(
            "sensitive"
        ) not in {
            None,
            False,
        }:
            fail("Output do state diverge do tipo público canônico.")
        values[name] = output.get("value")
    return values


def validate_operational_outputs(
    snapshot: object,
    resources: Mapping[str, Mapping[str, Any]],
    identity: Mapping[str, str],
    *,
    required: bool,
) -> None:
    outputs = state_output_values(snapshot)
    required_names = {
        "api_base_url",
        "cognito_client_id",
        "cognito_hosted_ui_origin",
        "frontend_bucket_name",
        "frontend_distribution_id",
    }
    if required and set(outputs) != set(EXPECTED_STATE_OUTPUT_TYPES):
        fail("State existente diverge da lista exata de outputs públicos.")
    if not required:
        return
    if not set(outputs) & required_names:
        return
    api = resources.get("aws_apigatewayv2_api.demo")
    client = resources.get("aws_cognito_user_pool_client.demo")
    domain = resources.get("aws_cognito_user_pool_domain.demo")
    bucket = resources.get('aws_s3_bucket.storage["frontend"]')
    distribution = resources.get("aws_cloudfront_distribution.frontend")
    if any(item is None for item in (api, client, domain, bucket, distribution)):
        fail("Outputs operacionais não possuem todos os recursos correspondentes.")
    expected = {
        "api_base_url": cast(Mapping[str, Any], api).get("api_endpoint"),
        "cognito_client_id": cast(Mapping[str, Any], client).get("id"),
        "cognito_hosted_ui_origin": (
            f"https://{expected_cognito_domain(identity)}.auth."
            f"{identity['region']}.amazoncognito.com"
        ),
        "frontend_bucket_name": cast(Mapping[str, Any], bucket).get("id"),
        "frontend_distribution_id": cast(Mapping[str, Any], distribution).get("id"),
    }
    if any(
        type(expected_value) is not str
        or not expected_value
        or outputs.get(name) != expected_value
        for name, expected_value in expected.items()
    ):
        fail("Output operacional não aponta para o recurso do mesmo state.")
    domain_values = cast(Mapping[str, Any], domain)
    if domain_values.get("domain") != expected_cognito_domain(identity):
        fail("Hosted UI não corresponde ao domínio Cognito do mesmo state.")


def audit_state_snapshot(
    snapshot: object,
    *,
    mode: str,
    identity: Mapping[str, str],
    expected_image: str | None = None,
) -> None:
    scope = identity_configuration(identity)
    resources = state_managed_values(snapshot, identity=scope)
    addresses = set(resources)
    if mode in {"fresh", "destroyed"}:
        if addresses or state_output_values(snapshot):
            fail("Snapshot do state deveria estar vazio para esta fase.")
        return
    if mode == "existing":
        if addresses != set(EXPECTED_MANAGED):
            fail("Snapshot existente diverge da allowlist exata.")
    elif mode == "destroyable":
        if not addresses or not addresses <= set(EXPECTED_MANAGED):
            fail("Snapshot destrutível diverge do subconjunto aprovado.")
    else:
        fail("Modo do gate de snapshot é inválido.")
    values: dict[str, Mapping[str, Any]] = {}
    for address, (resource_type, attributes) in resources.items():
        if EXPECTED_MANAGED.get(address) != resource_type:
            fail("Snapshot troca o tipo de recurso aprovado.")
        values[address] = validate_resource_identity(
            address, resource_type, attributes, scope
        )
    validate_relationships(values)
    validate_operational_outputs(
        snapshot,
        values,
        scope,
        required=mode == "existing",
    )
    task_values = values.get("aws_ecs_task_definition.api")
    if task_values is not None:
        task = validate_task_values(
            task_values,
            scope,
            expected_image=expected_image,
        )
        service = values.get("aws_ecs_service.api")
        if service is not None and service.get("task_definition") != task.get("arn"):
            fail("Service em state não aponta para a task revision verificada.")
    elif expected_image is not None:
        fail("State não contém a task revision verificada.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita plano real ou inventário sanitizado da entrega AWS."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("plan_json", type=Path)
    plan_parser.add_argument(
        "--mode", choices=("review", "deploy", "destroy"), required=True
    )
    plan_parser.add_argument(
        "--phase", choices=("foundation", "foundation-ready", "runtime")
    )
    state_parser = subparsers.add_parser("state")
    state_parser.add_argument("state_list", type=Path)
    state_parser.add_argument(
        "--mode",
        choices=("fresh", "existing", "destroyable", "destroyed"),
        required=True,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "plan":
        plan = load_plan(args.plan_json.resolve(strict=True))
        audit_plan(plan, mode=args.mode, phase=args.phase)
        print("Plano real aprovado pela allowlist de ações sanitizada.")
    else:
        audit_state(args.state_list.resolve(strict=True), mode=args.mode)
        print("State aprovado pela allowlist de recursos sanitizada.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeliveryGateError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("Gate da entrega falhou com segurança.", file=sys.stderr)
        raise SystemExit(1) from None
