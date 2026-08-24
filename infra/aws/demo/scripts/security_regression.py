"""Prove that the AWS demo audit rejects exact security regressions."""

from __future__ import annotations

import argparse
import copy
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from plan_audit import AuditError, audit_plan, parse_json_text
from static_plan import (
    ISOLATED_TERRAFORM_KEYS,
    StaticPlanError,
    isolated_environment,
    require_public_var_files_without_offline_controls,
)

Plan = dict[str, Any]
Mutation = Callable[[Plan], None]


class RegressionError(RuntimeError):
    """Raised when a negative security regression is not rejected."""


def object_dict(value: object, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegressionError(f"{context} não é um objeto JSON.")
    return cast(dict[str, Any], value)


def object_list(value: object, *, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise RegressionError(f"{context} não é uma lista JSON.")
    return cast(list[Any], value)


def change_by_address(plan: Plan, address: str) -> dict[str, Any]:
    changes = object_list(plan.get("resource_changes"), context="resource_changes")
    matches = [
        object_dict(change, context="resource_change")
        for change in changes
        if object_dict(change, context="resource_change").get("address") == address
    ]
    if len(matches) != 1:
        raise RegressionError(f"Plano baseline não possui exatamente {address}.")
    return matches[0]


def configured_by_address(plan: Plan, address: str) -> dict[str, Any]:
    configuration = object_dict(plan.get("configuration"), context="configuration")
    root = object_dict(configuration.get("root_module"), context="root_module")
    resources = object_list(root.get("resources"), context="configured resources")
    matches = [
        object_dict(resource, context="configured resource")
        for resource in resources
        if object_dict(resource, context="configured resource").get("address")
        == address
    ]
    if len(matches) != 1:
        raise RegressionError(f"Configuração baseline não possui exatamente {address}.")
    return matches[0]


def set_ingress_cidr(plan: Plan, *, key: str, value: str) -> None:
    address = "aws_vpc_security_group_ingress_rule.api_from_vpc_link"
    resource = change_by_address(plan, address)
    change = object_dict(resource.get("change"), context=f"{address}.change")
    after = object_dict(change.get("after"), context=f"{address}.after")
    after[key] = value
    expressions = object_dict(
        configured_by_address(plan, address).get("expressions"),
        context=f"{address}.expressions",
    )
    expressions.pop("referenced_security_group_id")
    expressions[key] = {"constant_value": value}


def public_ipv4_ingress(plan: Plan) -> None:
    set_ingress_cidr(plan, key="cidr_ipv4", value="0.0.0.0/0")


def public_ipv6_ingress(plan: Plan) -> None:
    set_ingress_cidr(plan, key="cidr_ipv6", value="::/0")


def extra_resource(plan: Plan) -> None:
    object_list(plan.get("resource_changes"), context="resource_changes").append(
        {
            "address": "aws_instance.unexpected",
            "change": {"actions": ["create"], "after": {}},
            "mode": "managed",
            "type": "aws_instance",
        }
    )
    configuration = object_dict(plan.get("configuration"), context="configuration")
    root = object_dict(configuration.get("root_module"), context="root_module")
    object_list(root.get("resources"), context="configured resources").append(
        {
            "address": "aws_instance.unexpected",
            "expressions": {},
            "mode": "managed",
            "name": "unexpected",
            "type": "aws_instance",
        }
    )


def unexpected_type(plan: Plan) -> None:
    change_by_address(plan, "aws_vpc.demo")["type"] = "aws_instance"
    configured_by_address(plan, "aws_vpc.demo")["type"] = "aws_instance"


def unexpected_address(plan: Plan) -> None:
    change_by_address(plan, "aws_vpc.demo")["address"] = "aws_vpc.unexpected"
    configured_by_address(plan, "aws_vpc.demo")["address"] = "aws_vpc.unexpected"


def policy_statement(plan: Plan, *, address: str, sid: str) -> dict[str, Any]:
    resource = change_by_address(plan, address)
    change = object_dict(resource.get("change"), context=f"{address}.change")
    after = object_dict(change.get("after"), context=f"{address}.after")
    policy_text = after.get("policy")
    if not isinstance(policy_text, str):
        raise RegressionError(f"{address}.policy não é conhecida na baseline.")
    policy = object_dict(json.loads(policy_text), context=f"{address}.policy")
    raw_statements = object_list(policy.get("Statement"), context="policy.Statement")
    matches = [
        object_dict(statement, context="policy statement")
        for statement in raw_statements
        if object_dict(statement, context="policy statement").get("Sid") == sid
    ]
    if len(matches) != 1:
        raise RegressionError(f"{address} não possui exatamente a statement {sid}.")
    after["policy"] = policy
    return matches[0]


def serialize_mutated_policy(plan: Plan, *, address: str) -> None:
    resource = change_by_address(plan, address)
    change = object_dict(resource.get("change"), context=f"{address}.change")
    after = object_dict(change.get("after"), context=f"{address}.after")
    policy = after.get("policy")
    if not isinstance(policy, dict):
        raise RegressionError(f"{address}.policy mutada não é um objeto.")
    after["policy"] = json.dumps(policy, separators=(",", ":"), sort_keys=True)


def out_of_scope_s3_arn(plan: Plan) -> None:
    address = "aws_iam_role_policy.api_task"
    statement = policy_statement(plan, address=address, sid="ReadAndWriteDemoObjects")
    statement["Resource"] = [
        "arn:aws:s3:::senai-pm-documents-000000000000-us-east-1/*",
        "arn:aws:s3:::outside-demo-profile/*",
    ]
    serialize_mutated_policy(plan, address=address)


def altered_action(plan: Plan) -> None:
    address = "aws_iam_role_policy.worker_task"
    statement = policy_statement(plan, address=address, sid="WriteDerivedArtifacts")
    statement["Action"] = ["s3:PutObject", "s3:DeleteObject"]
    serialize_mutated_policy(plan, address=address)


def altered_trust(plan: Plan) -> None:
    address = "aws_iam_role.api_task"
    resource = change_by_address(plan, address)
    change = object_dict(resource.get("change"), context=f"{address}.change")
    after = object_dict(change.get("after"), context=f"{address}.after")
    trust_text = after.get("assume_role_policy")
    if not isinstance(trust_text, str):
        raise RegressionError("Trust policy baseline não é conhecida.")
    trust = object_dict(json.loads(trust_text), context="trust policy")
    statements = object_list(trust.get("Statement"), context="trust Statement")
    statement = object_dict(statements[0], context="trust statement")
    statement["Principal"] = {"Service": "lambda.amazonaws.com"}
    after["assume_role_policy"] = json.dumps(
        trust, separators=(",", ":"), sort_keys=True
    )


def output_maps(plan: Plan) -> tuple[dict[str, Any], dict[str, Any]]:
    planned = object_dict(plan.get("output_changes"), context="output_changes")
    configuration = object_dict(plan.get("configuration"), context="configuration")
    root = object_dict(configuration.get("root_module"), context="root_module")
    configured = object_dict(root.get("outputs"), context="configured outputs")
    return planned, configured


def extra_output(plan: Plan) -> None:
    planned, configured = output_maps(plan)
    planned["debug_endpoint"] = {
        "actions": ["create"],
        "after": "https://debug.example.invalid",
        "after_sensitive": False,
        "after_unknown": False,
        "before": None,
        "before_sensitive": False,
    }
    configured["debug_endpoint"] = {
        "description": "Output hostil adicional.",
        "expression": {"constant_value": "https://debug.example.invalid"},
    }


def missing_output(plan: Plan) -> None:
    planned, configured = output_maps(plan)
    planned.pop("frontend_url")
    configured.pop("frontend_url")


def altered_output_reference(plan: Plan) -> None:
    _, configured = output_maps(plan)
    worker_output = object_dict(
        configured.get("worker_task_role_arn"), context="worker_task_role_arn"
    )
    worker_output["expression"] = {
        "references": ["aws_iam_role.api_task.arn", "aws_iam_role.api_task"]
    }


def default_cloudfront_certificate(plan: Plan) -> None:
    address = "aws_cloudfront_distribution.frontend"
    resource = change_by_address(plan, address)
    change = object_dict(resource.get("change"), context=f"{address}.change")
    after = object_dict(change.get("after"), context=f"{address}.after")
    after["aliases"] = []
    after["viewer_certificate"] = [
        {
            "acm_certificate_arn": None,
            "cloudfront_default_certificate": True,
            "iam_certificate_id": None,
            "minimum_protocol_version": "TLSv1",
            "ssl_support_method": None,
        }
    ]
    expressions = object_dict(
        configured_by_address(plan, address).get("expressions"),
        context=f"{address}.expressions",
    )
    expressions.pop("aliases")
    expressions["viewer_certificate"] = [
        {"cloudfront_default_certificate": {"constant_value": True}}
    ]


def remove_frontend_response_policy_attachment(plan: Plan) -> None:
    address = "aws_cloudfront_distribution.frontend"
    expressions = object_dict(
        configured_by_address(plan, address).get("expressions"),
        context=f"{address}.expressions",
    )
    behavior = object_dict(
        object_list(
            expressions.get("default_cache_behavior"),
            context="default_cache_behavior",
        )[0],
        context="default_cache_behavior[0]",
    )
    behavior.pop("response_headers_policy_id")


def expand_frontend_methods(plan: Plan) -> None:
    address = "aws_cloudfront_distribution.frontend"
    change = object_dict(
        change_by_address(plan, address).get("change"), context=f"{address}.change"
    )
    after = object_dict(change.get("after"), context=f"{address}.after")
    behavior = object_dict(
        object_list(after.get("default_cache_behavior"), context="default behavior")[0],
        context="default behavior[0]",
    )
    behavior["allowed_methods"] = ["GET", "HEAD", "POST"]


def response_policy_parts(plan: Plan) -> tuple[dict[str, Any], dict[str, Any]]:
    address = "aws_cloudfront_response_headers_policy.frontend"
    change = object_dict(
        change_by_address(plan, address).get("change"), context=f"{address}.change"
    )
    after = object_dict(change.get("after"), context=f"{address}.after")
    expressions = object_dict(
        configured_by_address(plan, address).get("expressions"),
        context=f"{address}.expressions",
    )
    return after, expressions


def unsafe_frontend_csp(plan: Plan) -> None:
    after, _ = response_policy_parts(plan)
    security = object_dict(
        object_list(after.get("security_headers_config"), context="security headers")[
            0
        ],
        context="security headers[0]",
    )
    csp = object_dict(
        object_list(security.get("content_security_policy"), context="CSP")[0],
        context="CSP[0]",
    )
    csp["content_security_policy"] = "default-src *; script-src 'unsafe-inline'"


def foreign_permissions_policy(plan: Plan) -> None:
    after, _ = response_policy_parts(plan)
    custom = object_dict(
        object_list(
            object_dict(
                object_list(
                    after.get("custom_headers_config"), context="custom headers"
                )[0],
                context="custom headers[0]",
            ).get("items"),
            context="custom header items",
        )[0],
        context="custom header item",
    )
    custom["value"] = "camera=*"


def relaxed_referrer_policy(plan: Plan) -> None:
    after, _ = response_policy_parts(plan)
    security = object_dict(
        object_list(after.get("security_headers_config"), context="security headers")[
            0
        ],
        context="security headers[0]",
    )
    referrer = object_dict(
        object_list(security.get("referrer_policy"), context="referrer policy")[0],
        context="referrer policy[0]",
    )
    referrer["referrer_policy"] = "origin"


def weaken_hsts(plan: Plan) -> None:
    after, _ = response_policy_parts(plan)
    security = object_dict(
        object_list(after.get("security_headers_config"), context="security headers")[
            0
        ],
        context="security headers[0]",
    )
    hsts = object_dict(
        object_list(
            security.get("strict_transport_security"), context="strict transport"
        )[0],
        context="strict transport[0]",
    )
    hsts["access_control_max_age_sec"] = 60
    hsts["include_subdomains"] = False


def wrong_cognito_managed_login_version(plan: Plan) -> None:
    address = "aws_cognito_user_pool_domain.demo"
    change = object_dict(
        change_by_address(plan, address).get("change"), context=f"{address}.change"
    )
    object_dict(change.get("after"), context=f"{address}.after")[
        "managed_login_version"
    ] = 2


def wrong_cognito_domain(plan: Plan) -> None:
    address = "aws_cognito_user_pool_domain.demo"
    change = object_dict(
        change_by_address(plan, address).get("change"), context=f"{address}.change"
    )
    object_dict(change.get("after"), context=f"{address}.after")["domain"] = (
        "spm-00000000000000000000"
    )


def frontend_cache_min_ttl(plan: Plan) -> None:
    address = "aws_cloudfront_cache_policy.frontend"
    change = object_dict(
        change_by_address(plan, address).get("change"), context=f"{address}.change"
    )
    object_dict(change.get("after"), context=f"{address}.after")["min_ttl"] = 60


def frontend_cache_forwards_headers(plan: Plan) -> None:
    address = "aws_cloudfront_cache_policy.frontend"
    change = object_dict(
        change_by_address(plan, address).get("change"), context=f"{address}.change"
    )
    after = object_dict(change.get("after"), context=f"{address}.after")
    parameters = object_dict(
        object_list(
            after.get("parameters_in_cache_key_and_forwarded_to_origin"),
            context="cache parameters",
        )[0],
        context="cache parameters[0]",
    )
    headers = object_dict(
        object_list(parameters.get("headers_config"), context="headers config")[0],
        context="headers config[0]",
    )
    headers["header_behavior"] = "whitelist"
    headers["headers"] = ["authorization"]


def missing_security_group_description(plan: Plan) -> None:
    address = "aws_vpc_security_group_ingress_rule.api_from_vpc_link"
    resource = change_by_address(plan, address)
    change = object_dict(resource.get("change"), context=f"{address}.change")
    after = object_dict(change.get("after"), context=f"{address}.after")
    after["description"] = None
    expressions = object_dict(
        configured_by_address(plan, address).get("expressions"),
        context=f"{address}.expressions",
    )
    expressions.pop("description")


def mutate_task_container(
    plan: Plan, mutation: Callable[[dict[str, Any]], None]
) -> None:
    address = "aws_ecs_task_definition.api"
    resource = change_by_address(plan, address)
    change = object_dict(resource.get("change"), context=f"{address}.change")
    after = object_dict(change.get("after"), context=f"{address}.after")
    container_text = after.get("container_definitions")
    if not isinstance(container_text, str):
        raise RegressionError("container_definitions baseline não é conhecido.")
    containers = object_list(
        json.loads(container_text), context="container_definitions"
    )
    if len(containers) != 1:
        raise RegressionError("Baseline não possui exatamente um container.")
    container = object_dict(containers[0], context="container")
    mutation(container)
    after["container_definitions"] = json.dumps(
        containers, separators=(",", ":"), sort_keys=True
    )


def set_task_environment(plan: Plan, *, name: str, value: str) -> None:
    def mutate(container: dict[str, Any]) -> None:
        environment = object_list(container.get("environment"), context="environment")
        matches = [
            object_dict(entry, context="environment entry")
            for entry in environment
            if object_dict(entry, context="environment entry").get("name") == name
        ]
        if len(matches) != 1:
            raise RegressionError(f"Environment baseline não possui exatamente {name}.")
        matches[0]["value"] = value

    mutate_task_container(plan, mutate)


def wrong_application_environment(plan: Plan) -> None:
    set_task_environment(
        plan,
        name="PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT",
        value="local",
    )


def wrong_persistence_backend(plan: Plan) -> None:
    set_task_environment(
        plan,
        name="PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND",
        value="postgres",
    )


def wrong_analysis_mode(plan: Plan) -> None:
    set_task_environment(
        plan,
        name="PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE",
        value="artifacts",
    )


def injected_database_url(plan: Plan) -> None:
    def mutate(container: dict[str, Any]) -> None:
        environment = object_list(container.get("environment"), context="environment")
        environment.append(
            {
                "name": "PRESCRIPTIVE_MAINTENANCE_DATABASE_URL",
                "value": "postgresql://synthetic:synthetic@example.invalid/demo",
            }
        )

    mutate_task_container(plan, mutate)


def replace_health_command(plan: Plan, *, old: str, new: str) -> None:
    def mutate(container: dict[str, Any]) -> None:
        health = object_dict(container.get("healthCheck"), context="healthCheck")
        command = object_list(health.get("command"), context="healthCheck.command")
        if len(command) != 2 or not isinstance(command[1], str):
            raise RegressionError("Healthcheck baseline não possui comando canônico.")
        if command[1].count(old) != 1:
            raise RegressionError("Healthcheck baseline diverge do trecho esperado.")
        command[1] = command[1].replace(old, new, 1)

    mutate_task_container(plan, mutate)


def liveness_used_for_traffic(plan: Plan) -> None:
    replace_health_command(plan, old="/health/ready", new="/health/live")


def wrong_readiness_body(plan: Plan) -> None:
    replace_health_command(
        plan,
        old='b\'{\\"status\\":\\"ready\\"}\'',
        new='b\'{\\"status\\":\\"ok\\"}\'',
    )


def sensitive_container_definition(plan: Plan) -> None:
    address = "aws_ecs_task_definition.api"
    resource = change_by_address(plan, address)
    change = object_dict(resource.get("change"), context=f"{address}.change")
    sensitive = object_dict(
        change.get("after_sensitive"), context=f"{address}.after_sensitive"
    )
    sensitive["container_definitions"] = True


def unexpected_container_reference(plan: Plan) -> None:
    address = "aws_ecs_task_definition.api"
    expressions = object_dict(
        configured_by_address(plan, address).get("expressions"),
        context=f"{address}.expressions",
    )
    container_expression = object_dict(
        expressions.get("container_definitions"),
        context=f"{address}.container_definitions",
    )
    references = object_list(
        container_expression.get("references"), context="container references"
    )
    references.append("var.budget_alert_email")


def disabled_offline_validation(plan: Plan) -> None:
    variables = object_dict(plan.get("variables"), context="variables")
    offline_validation = object_dict(
        variables.get("offline_validation"), context="offline_validation"
    )
    offline_validation["value"] = "false"


def persisted_offline_nonce(plan: Plan) -> None:
    variables = object_dict(plan.get("variables"), context="variables")
    offline_nonce = object_dict(
        variables.get("offline_plan_nonce"), context="offline_plan_nonce"
    )
    offline_nonce["value"] = "synthetic-persisted-value"


MUTATIONS: dict[str, tuple[Mutation, str]] = {
    "disabled_offline_validation": (
        disabled_offline_validation,
        "Plano não comprova o modo offline isolado do harness",
    ),
    "persisted_offline_nonce": (
        persisted_offline_nonce,
        "Plano persiste o nonce efêmero do harness",
    ),
    "public_ipv4_ingress": (public_ipv4_ingress, ".cidr_ipv4 inclui uma origem"),
    "public_ipv6_ingress": (public_ipv6_ingress, ".cidr_ipv6 inclui uma origem"),
    "extra_resource": (extra_resource, "Configuração diverge da allowlist exata"),
    "unexpected_type": (unexpected_type, "diverge em type/mode"),
    "unexpected_address": (
        unexpected_address,
        "Configuração diverge da allowlist exata",
    ),
    "out_of_scope_s3_arn": (
        out_of_scope_s3_arn,
        "diverge integralmente da policy IAM mínima",
    ),
    "altered_iam_action": (
        altered_action,
        "diverge integralmente da policy IAM mínima",
    ),
    "altered_role_trust": (
        altered_trust,
        "diverge da trust policy exclusiva do ECS Tasks",
    ),
    "extra_output": (extra_output, "Configuração de outputs diverge"),
    "missing_output": (missing_output, "Configuração de outputs diverge"),
    "altered_output_reference": (
        altered_output_reference,
        "configured output worker_task_role_arn.expression diverge",
    ),
    "default_cloudfront_certificate": (
        default_cloudfront_certificate,
        "CloudFront não publica exclusivamente o domínio próprio esperado",
    ),
    "missing_frontend_response_policy": (
        remove_frontend_response_policy_attachment,
        "default_cache_behavior possui campos fora do contrato exato",
    ),
    "expanded_frontend_methods": (
        expand_frontend_methods,
        "CloudFront permite métodos além de GET e HEAD",
    ),
    "unsafe_frontend_csp": (
        unsafe_frontend_csp,
        "Headers de segurança do frontend divergem do contrato exato",
    ),
    "foreign_permissions_policy": (
        foreign_permissions_policy,
        "Permissions-Policy do frontend diverge do valor exato aprovado",
    ),
    "relaxed_referrer_policy": (
        relaxed_referrer_policy,
        "Headers de segurança do frontend divergem do contrato exato",
    ),
    "weakened_hsts": (
        weaken_hsts,
        "Headers de segurança do frontend divergem do contrato exato",
    ),
    "wrong_cognito_managed_login_version": (
        wrong_cognito_managed_login_version,
        "Domínio Cognito não é determinístico",
    ),
    "wrong_cognito_domain": (
        wrong_cognito_domain,
        "Domínio Cognito não é determinístico",
    ),
    "frontend_cache_min_ttl": (
        frontend_cache_min_ttl,
        "Cache policy do frontend diverge dos TTLs e forwarding exatos",
    ),
    "frontend_cache_forwards_headers": (
        frontend_cache_forwards_headers,
        "Cache policy do frontend diverge dos TTLs e forwarding exatos",
    ),
    "missing_security_group_description": (
        missing_security_group_description,
        ".description diverge da descrição operacional exata",
    ),
    "wrong_application_environment": (
        wrong_application_environment,
        "A task diverge do contrato exato de imagem, runtime, ambiente ou readiness",
    ),
    "wrong_persistence_backend": (
        wrong_persistence_backend,
        "A task diverge do contrato exato de imagem, runtime, ambiente ou readiness",
    ),
    "wrong_analysis_mode": (
        wrong_analysis_mode,
        "A task diverge do contrato exato de imagem, runtime, ambiente ou readiness",
    ),
    "injected_database_url": (
        injected_database_url,
        "A task diverge do contrato exato de imagem, runtime, ambiente ou readiness",
    ),
    "liveness_used_for_traffic": (
        liveness_used_for_traffic,
        "A task diverge do contrato exato de imagem, runtime, ambiente ou readiness",
    ),
    "wrong_readiness_body": (
        wrong_readiness_body,
        "A task diverge do contrato exato de imagem, runtime, ambiente ou readiness",
    ),
    "sensitive_container_definition": (
        sensitive_container_definition,
        "A task não pode ocultar container_definitions como valor sensível",
    ),
    "unexpected_container_reference": (
        unexpected_container_reference,
        "A task diverge das referências exatas do container aprovado",
    ),
}


def prove_duplicate_output_rejected(plan: Plan) -> None:
    serialized = json.dumps(plan, separators=(",", ":"), sort_keys=True)
    marker = '"output_changes":{'
    if serialized.count(marker) != 1:
        raise RegressionError("Plano baseline não expõe output_changes canônico.")
    duplicated = serialized.replace(
        marker,
        marker + '"api_base_url":{},',
        1,
    )
    try:
        parse_json_text(duplicated, context="duplicate output plan")
    except AuditError as error:
        if "Chave JSON duplicada rejeitada: api_base_url" not in str(error):
            raise RegressionError(
                "A duplicação de output acionou um gate inesperado."
            ) from error
    else:
        raise RegressionError("Um output JSON duplicado foi aceito.")


def prove_nonfinite_json_constants_rejected() -> None:
    expected_error = "JSON contém constante numérica não finita."
    for constant in ("NaN", "Infinity", "-Infinity"):
        try:
            parse_json_text(f'{{"value":{constant}}}', context="nonfinite plan")
        except AuditError as error:
            if str(error) != expected_error or constant in str(error):
                raise RegressionError(
                    "Uma constante JSON não finita acionou um erro inseguro."
                ) from error
        else:
            raise RegressionError("Uma constante JSON não finita foi aceita.")


def prove_public_var_files_have_no_offline_controls() -> None:
    module_root = Path(__file__).resolve().parents[1]
    public_files = (
        module_root / "examples" / "demo.tfvars.example",
        module_root / "tests" / "invalid.tfvars.example",
    )
    try:
        require_public_var_files_without_offline_controls(*public_files)
    except StaticPlanError as error:
        raise RegressionError(
            "Um arquivo público habilita controles do modo offline."
        ) from error

    for identifier in ("offline_validation", "offline_plan_nonce"):
        with tempfile.TemporaryDirectory(prefix="sen67-public-vars-") as temporary:
            hostile_file = Path(temporary) / "hostile.tfvars"
            hostile_file.write_text(
                f'{identifier} = "hostile"\n', encoding="utf-8", newline="\n"
            )
            try:
                require_public_var_files_without_offline_controls(hostile_file)
            except StaticPlanError as error:
                if "não podem declarar controles offline" not in str(error):
                    raise RegressionError(
                        "O controle offline público acionou um gate inesperado."
                    ) from error
            else:
                raise RegressionError(
                    "Um arquivo público com controle offline foi aceito."
                )


def prove_hostile_environment_isolation() -> None:
    expected_nonce = "a" * 64
    hostile_environment = {
        "APPDATA": "C:/hostile/appdata",
        "AWS_PROFILE": "production",
        "HOME": "C:/hostile/home",
        "LOCALAPPDATA": "C:/hostile/localappdata",
        "TF_CLI_ARGS": "-destroy",
        "TF_CLI_ARGS_plan": "-var api_desired_count=1",
        "TF_CLI_CONFIG_FILE": "C:/hostile/dev-overrides.tfrc",
        "TF_DATA_DIR": "C:/hostile/data",
        "TF_LOG": "TRACE",
        "TF_LOG_CORE": "TRACE",
        "TF_LOG_PATH": "C:/hostile/terraform.log",
        "TF_LOG_PROVIDER": "TRACE",
        "TF_PLUGIN_CACHE_DIR": "C:/hostile/plugins",
        "TF_REATTACH_PROVIDERS": '{"hostile":true}',
        "TF_VAR_api_desired_count": "1",
        "TF_VAR_offline_plan_nonce": "hostile",
        "TF_VAR_offline_validation": "false",
        "TF_WORKSPACE": "production",
        "USERPROFILE": "C:/hostile/profile",
        "tf_cli_args_apply": "-auto-approve",
    }
    with tempfile.TemporaryDirectory(prefix="sen67-environment-") as temporary:
        isolation_root = Path(temporary) / "isolated"
        child = isolated_environment(
            hostile_environment,
            isolation_root=isolation_root,
            offline_plan_nonce=expected_nonce,
        )
        terraform_keys = {key.upper() for key in child if key.upper().startswith("TF_")}
        if terraform_keys != ISOLATED_TERRAFORM_KEYS:
            raise RegressionError(
                "Variáveis Terraform hostis alcançaram o subprocesso."
            )
        if child.get("TF_IN_AUTOMATION") != "true" or child.get("TF_INPUT") != "false":
            raise RegressionError("Controles Terraform isolados foram adulterados.")
        if (
            child.get("TF_VAR_offline_validation") != "true"
            or child.get("TF_VAR_offline_plan_nonce") != expected_nonce
        ):
            raise RegressionError("Controles offline isolados foram adulterados.")
        for key in ("TF_CLI_CONFIG_FILE", "TF_DATA_DIR", "HOME", "USERPROFILE"):
            value = child.get(key)
            if value is None or not Path(value).is_relative_to(isolation_root):
                raise RegressionError(f"{key} não aponta para o isolamento temporário.")
        cli_config = Path(child["TF_CLI_CONFIG_FILE"])
        if cli_config.read_text(encoding="utf-8") != "disable_checkpoint = true\n":
            raise RegressionError("Configuração Terraform isolada permite override.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa mutações negativas exatas contra um plano AWS demo real."
    )
    parser.add_argument("plan_json", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        plan_text = args.plan_json.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RegressionError(
            "Não foi possível ler um plano JSON UTF-8 válido."
        ) from error
    raw_plan = parse_json_text(plan_text, context="plan")
    baseline = object_dict(raw_plan, context="plan")
    audit_plan(baseline)

    rejected: list[str] = []
    for name, (mutate, expected_error) in MUTATIONS.items():
        candidate = copy.deepcopy(baseline)
        mutate(candidate)
        try:
            audit_plan(candidate)
        except AuditError as error:
            if expected_error not in str(error):
                raise RegressionError(
                    f"A mutação {name} acionou um gate inesperado."
                ) from error
            rejected.append(name)
        else:
            raise RegressionError(f"A mutação {name} foi aceita pelo auditor.")

    prove_duplicate_output_rejected(baseline)
    prove_nonfinite_json_constants_rejected()
    prove_public_var_files_have_no_offline_controls()
    prove_hostile_environment_isolation()
    print(
        f"Regressões aprovadas: baseline aceita, {len(rejected)} mutações e uma "
        "duplicação de output rejeitadas, três constantes não finitas "
        "recusadas, arquivos públicos sem bypass e ambiente Terraform hostil isolado."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
