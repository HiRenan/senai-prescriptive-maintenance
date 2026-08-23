"""Audit the offline security contract for AWS demo delivery workflows."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, NoReturn, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONTRACT = REPOSITORY_ROOT / "infra/aws/demo/delivery/delivery-contract.v1.json"
WORKFLOW_CONTRACT = {
    "aws-demo-validate.yml": (None, False, None, None, None),
    "aws-demo-plan.yml": (
        "aws-demo-plan",
        True,
        "AWS_DEMO_PLAN_ROLE_ARN",
        "PLAN-AWS-DEMO",
        "plan",
    ),
    "aws-demo-deploy.yml": (
        "aws-demo-deploy",
        True,
        "AWS_DEMO_DEPLOY_ROLE_ARN",
        "DEPLOY-AWS-DEMO",
        "deploy",
    ),
    "aws-demo-teardown.yml": (
        "aws-demo-teardown",
        True,
        "AWS_DEMO_TEARDOWN_ROLE_ARN",
        "TEARDOWN-AWS-DEMO",
        "teardown",
    ),
}
EXPECTED_REPOSITORY = "HiRenan/senai-prescriptive-maintenance"
EXPECTED_OWNER_ID = "107653306"
EXPECTED_REPOSITORY_ID = "1342357031"
EXPECTED_REF = "refs/heads/main"
EXPECTED_SEN46_BASELINE = "d45bcabfb6de89c6bac2ec2aa6180bce353be7c1"
EXPECTED_ROLE_NAMES = ("deploy", "plan", "teardown")
OIDC_AUDIENCE = "sts.amazonaws.com"
OIDC_ISSUER = "token.actions.githubusercontent.com"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ACTION_REFERENCE_PATTERN = re.compile(
    r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{40})$"
)
ACTION_USE_PATTERN = re.compile(
    r"^\s*uses:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{40})\s*(?:#.*)?$",
    re.MULTILINE,
)
EXPECTED_ACTIONS = (
    "actions/checkout",
    "actions/setup-python",
    "aws-actions/configure-aws-credentials",
    "docker/setup-buildx-action",
    "hashicorp/setup-terraform",
)
COMMON_OPERATION_ENV_KEYS = (
    "AWS_DEMO_ACCOUNT_ID",
    "AWS_DEMO_SESSION_EXPIRATION",
    "AWS_DEMO_SOURCE_SHA",
    "AWS_DEFAULT_REGION",
    "AWS_REGION",
    "TF_STATE_BUCKET",
    "TF_STATE_KEY",
    "TF_VAR_availability_zone",
    "TF_VAR_aws_account_id",
    "TF_VAR_aws_region",
    "TF_VAR_budget_alert_email",
    "TF_VAR_enable_bedrock",
    "TF_VAR_frontend_certificate_arn",
    "TF_VAR_frontend_domain_name",
    "TF_VAR_monthly_budget_usd",
    "TF_VAR_name_prefix",
    "TF_VAR_owner",
)
BUILDX_CONFIG_EXPRESSION = (
    "${{ runner.temp }}/sen68-buildx-${{ github.run_id }}-${{ github.run_attempt }}"
)
BUILDX_NAME_EXPRESSION = "sen68-${{ github.run_id }}-${{ github.run_attempt }}"
BUILDX_OUTPUT_EXPRESSION = "${{ steps.buildx.outputs.name }}"
PROTECTED_MAIN_REVALIDATION = "Revalidate the current main revision after approval"
EXPECTED_PERMISSION_POLICY_SHA256 = {
    "deploy": "c64facd3774a48fc1fb5260c1ca5a64baed92887ec92e5c0b14857da14d39738",
    "plan": "7c1c66b51df21c1ca326c6ca750c4dff3d11e7165f9efc4d727215e4c4d552b8",
    "teardown": "830a12da473e08ace8f70224c7988a97bbec3fd57a57eb9259468137d23d56c7",
}
EXPECTED_PERMISSION_SIDS = {
    "deploy": (
        "AlarmCreate",
        "ApiCreate",
        "ApiSlr",
        "ApiWrites",
        "Buckets",
        "BudgetRW",
        "CfPolicyNew",
        "CfTagCreate",
        "CloudMapNew",
        "CognitoRW",
        "CognitoTag",
        "Ec2Create",
        "EcrAuth",
        "EcrCreate",
        "EcrReads",
        "EcrWrites",
        "EcsCreate",
        "EcsSlr",
        "GlobalNew",
        "GlobalReads",
        "IamCreate",
        "IamWrites",
        "LogCreate",
        "ManageStateLockOnly",
        "ManageStateObject",
        "PassEcs",
        "QueueCreate",
        "ReadDemoIamRoles",
        "ReadStateBucket",
        "S3Locs",
        "TaggedRW",
        "TaskNew",
    ),
    "plan": (
        "ManageStateLockOnly",
        "ReadDemoBucketLocations",
        "ReadDemoBuckets",
        "ReadDemoEcrRepository",
        "ReadDemoIamRoles",
        "ReadDemoResources",
        "ReadStateBucket",
        "ReadStateObject",
    ),
    "teardown": (
        "DestroyDemoApiGateway",
        "DestroyDemoBucketObjects",
        "DestroyDemoBuckets",
        "DestroyDemoBudget",
        "DestroyDemoCognitoClient",
        "DestroyDemoEcrRepository",
        "DestroyDemoIamRoles",
        "DestroyTaggedDemoResources",
        "DestroyUntaggableCloudFrontPolicies",
        "InventoryDemoResourcesGlobal",
        "ManageStateLockOnly",
        "ManageStateObject",
        "ReadDemoBucketLocations",
        "ReadDemoIamRoles",
        "ReadStateBucket",
    ),
}
EXPECTED_WILDCARD_SIDS = {
    "deploy": {
        "CfPolicyNew",
        "EcrAuth",
        "GlobalNew",
        "GlobalReads",
    },
    "plan": {"ReadDemoResources"},
    "teardown": {"InventoryDemoResourcesGlobal"},
}
REQUIRED_PROVIDER_ACTIONS = {
    "deploy": {
        "ec2:DescribeSecurityGroupRules",
        "ec2:DescribeVpcAttribute",
        "iam:ListAttachedRolePolicies",
    },
    "plan": {
        "ec2:DescribeSecurityGroupRules",
        "ec2:DescribeVpcAttribute",
        "iam:ListAttachedRolePolicies",
    },
    "teardown": {
        "ec2:DescribeSecurityGroupRules",
        "ec2:DescribeVpcAttribute",
        "iam:ListAttachedRolePolicies",
        "iam:ListInstanceProfilesForRole",
    },
}
EXPECTED_SERVICE_LINKED_ROLES = {
    "ApiSlr": (
        "ops.apigateway.amazonaws.com",
        "arn:aws:iam::${AWS_ACCOUNT_ID}:role/aws-service-role/"
        "ops.apigateway.amazonaws.com/AWSServiceRoleForAPIGateway*",
    ),
    "EcsSlr": (
        "ecs.amazonaws.com",
        "arn:aws:iam::${AWS_ACCOUNT_ID}:role/aws-service-role/"
        "ecs.amazonaws.com/AWSServiceRoleForECS*",
    ),
}
EXPECTED_DEPLOY_MODES = {
    "foundation": {
        "command": "foundation",
        "confirmation": "FOUNDATION-AWS-DEMO",
        "requires_buildx_context": False,
        "requires_sen46_baseline": False,
        "requires_smoke_token": False,
    },
    "runtime": {
        "command": "deploy",
        "confirmation": "DEPLOY-AWS-DEMO",
        "requires_buildx_context": True,
        "requires_sen46_baseline": True,
        "requires_smoke_token": True,
    },
}
FORBIDDEN_S3_PERMISSION_ALIASES = {
    "s3:DeleteBucketLifecycle",
    "s3:DeleteBucketTagging",
    "s3:GetBucketLifecycleConfiguration",
    "s3:PutBucketLifecycleConfiguration",
}
REQUIRED_S3_PERMISSIONS = {
    "plan": {"s3:GetLifecycleConfiguration"},
    "deploy": {
        "s3:GetLifecycleConfiguration",
        "s3:PutLifecycleConfiguration",
    },
    "teardown": {
        "s3:GetLifecycleConfiguration",
        "s3:PutBucketOwnershipControls",
        "s3:PutBucketPublicAccessBlock",
        "s3:PutBucketTagging",
        "s3:PutEncryptionConfiguration",
        "s3:PutLifecycleConfiguration",
    },
}


class DeliveryPolicyError(RuntimeError):
    """Raised with a content-free explanation when a policy gate fails."""


def fail(message: str) -> NoReturn:
    raise DeliveryPolicyError(message)


def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            fail("Contrato JSON possui uma chave duplicada.")
        result[key] = value
    return result


def reject_nonfinite(value: str) -> NoReturn:
    del value
    fail("Contrato JSON possui um número não finito.")


def load_json(path: Path) -> Mapping[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
        parsed = json.loads(
            content,
            object_pairs_hook=object_pairs,
            parse_constant=reject_nonfinite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise DeliveryPolicyError(
            "Contrato de entrega não é um JSON UTF-8 válido."
        ) from None
    return mapping(parsed, context="contrato")


def mapping(value: object, *, context: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        fail(f"{context} deve ser um objeto exato.")
    return cast(dict[str, Any], value)


def sequence(value: object, *, context: str) -> list[object]:
    if type(value) is not list:
        fail(f"{context} deve ser uma lista exata.")
    return cast(list[object], value)


def exact_keys(
    value: Mapping[str, Any], expected: Iterable[str], *, context: str
) -> None:
    if set(value) != set(expected):
        fail(f"{context} diverge da estrutura fechada.")


def exact_text(value: object, expected: str, *, context: str) -> None:
    if type(value) is not str or value != expected:
        fail(f"{context} diverge do valor exato.")


def text_list(value: object, *, context: str) -> list[str]:
    values = sequence(value, context=context)
    if any(type(item) is not str or not item for item in values):
        fail(f"{context} deve conter somente textos base não vazios.")
    result = cast(list[str], values)
    if result != sorted(result) or len(result) != len(set(result)):
        fail(f"{context} deve ser ordenada e não pode repetir valores.")
    return result


def normalized_actions(value: object, *, context: str) -> list[str]:
    actions = [value] if type(value) is str else text_list(value, context=context)
    for action in actions:
        if action == "*" or action.endswith(":*") or ":" not in action:
            fail(f"{context} possui ação ampla ou inválida.")
    return actions


def normalized_resources(value: object, *, context: str) -> list[str]:
    resources = [value] if type(value) is str else text_list(value, context=context)
    if any(not resource for resource in resources):
        fail(f"{context} possui recurso vazio.")
    return resources


def expected_subject(environment: str) -> str:
    return (
        "repo:HiRenan@107653306/"
        "senai-prescriptive-maintenance@1342357031:environment:"
        f"{environment}"
    )


def audit_trust_policy(
    raw_policy: object,
    *,
    role_name: str,
    expected_environment: str,
) -> None:
    policy = mapping(raw_policy, context=f"trust {role_name}")
    exact_keys(policy, ("Statement", "Version"), context=f"trust {role_name}")
    exact_text(policy.get("Version"), "2012-10-17", context="versão da trust")
    statements = sequence(policy.get("Statement"), context=f"trust {role_name}")
    if len(statements) != 1:
        fail(f"Trust {role_name} deve possuir uma única statement.")
    statement = mapping(statements[0], context=f"trust {role_name}.statement")
    exact_keys(
        statement,
        ("Action", "Condition", "Effect", "Principal", "Sid"),
        context=f"trust {role_name}.statement",
    )
    exact_text(statement.get("Effect"), "Allow", context=f"trust {role_name}.effect")
    exact_text(
        statement.get("Action"),
        "sts:AssumeRoleWithWebIdentity",
        context=f"trust {role_name}.action",
    )
    exact_text(
        statement.get("Sid"),
        f"AllowGitHubActions{role_name.title()}",
        context=f"trust {role_name}.sid",
    )
    principal = mapping(statement.get("Principal"), context="principal federado")
    exact_keys(principal, ("Federated",), context="principal federado")
    exact_text(
        principal.get("Federated"),
        "arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/"
        "token.actions.githubusercontent.com",
        context="principal federado",
    )
    condition = mapping(statement.get("Condition"), context="condição OIDC")
    exact_keys(condition, ("StringEquals",), context="condição OIDC")
    equals = mapping(condition.get("StringEquals"), context="condição OIDC exata")
    exact_keys(
        equals,
        (
            f"{OIDC_ISSUER}:aud",
            f"{OIDC_ISSUER}:sub",
        ),
        context="condição OIDC exata",
    )
    exact_text(
        equals.get(f"{OIDC_ISSUER}:aud"),
        OIDC_AUDIENCE,
        context="audience OIDC",
    )
    exact_text(
        equals.get(f"{OIDC_ISSUER}:sub"),
        expected_subject(expected_environment),
        context="subject OIDC",
    )


def audit_permission_policy(raw_policy: object, *, role_name: str) -> None:
    policy = mapping(raw_policy, context=f"policy {role_name}")
    exact_keys(policy, ("Statement", "Version"), context=f"policy {role_name}")
    exact_text(policy.get("Version"), "2012-10-17", context="versão da policy")
    statements = sequence(policy.get("Statement"), context=f"policy {role_name}")
    if not statements:
        fail(f"Policy {role_name} não pode ser vazia.")
    seen_sids: set[str] = set()
    all_actions: set[str] = set()
    for raw_statement in statements:
        statement = mapping(raw_statement, context=f"policy {role_name}.statement")
        sid = statement.get("Sid")
        if type(sid) is not str or not sid or sid in seen_sids:
            fail(f"Policy {role_name} possui Sid inválido ou duplicado.")
        service_linked_role = EXPECTED_SERVICE_LINKED_ROLES.get(sid)
        has_condition = "Condition" in statement
        condition: Mapping[str, Any] | None = None
        exact_keys(
            statement,
            (
                ("Action", "Condition", "Effect", "Resource", "Sid")
                if has_condition
                else ("Action", "Effect", "Resource", "Sid")
            ),
            context=f"policy {role_name}.statement",
        )
        exact_text(
            statement.get("Effect"),
            "Allow",
            context=f"policy {role_name}.effect",
        )
        seen_sids.add(sid)
        actions = normalized_actions(
            statement.get("Action"), context=f"policy {role_name}.{sid}.actions"
        )
        resources = normalized_resources(
            statement.get("Resource"), context=f"policy {role_name}.{sid}.resources"
        )
        all_actions.update(actions)
        if any("?" in resource for resource in resources):
            fail(f"Policy {role_name} possui wildcard ambíguo em Resource.")
        if (resources == ["*"]) != (sid in EXPECTED_WILDCARD_SIDS[role_name]):
            fail(f"Policy {role_name} diverge dos wildcards residuais aprovados.")
        if "*" in resources and resources != ["*"]:
            fail(f"Policy {role_name} mistura wildcard global com ARN.")
        if has_condition:
            condition = mapping(
                statement.get("Condition"),
                context=f"policy {role_name}.{sid}.condition",
            )
            exact_keys(
                condition,
                ("StringEquals",),
                context=f"policy {role_name}.{sid}.condition",
            )
            equals = mapping(
                condition.get("StringEquals"),
                context=f"policy {role_name}.{sid}.condition.equals",
            )
            allowed_condition_keys = {
                "aws:RequestTag/Profile",
                "aws:ResourceTag/Profile",
                "ecs:task-cpu",
                "ecs:task-memory",
                "iam:AWSServiceName",
                "iam:PassedToService",
            }
            if (
                not equals
                or not set(equals) <= allowed_condition_keys
                or any(type(value) is not str or not value for value in equals.values())
            ):
                fail(f"Policy {role_name} usa condição fora da matriz oficial.")
        if service_linked_role is not None:
            if role_name != "deploy":
                fail("Criação de service-linked role pertence somente ao deploy.")
            service_name, expected_resource = service_linked_role
            if actions != ["iam:CreateServiceLinkedRole"] or resources != [
                expected_resource
            ]:
                fail("Service-linked role diverge da ação ou ARN estritos.")
            if condition is None:
                fail("Service-linked role perdeu a condição obrigatória.")
            equals = mapping(
                condition.get("StringEquals"),
                context=f"policy {role_name}.{sid}.condition.equals",
            )
            exact_keys(
                equals,
                ("iam:AWSServiceName",),
                context=f"policy {role_name}.{sid}.condition.equals",
            )
            exact_text(
                equals.get("iam:AWSServiceName"),
                service_name,
                context=f"policy {role_name}.{sid}.service",
            )

    if tuple(sorted(seen_sids)) != EXPECTED_PERMISSION_SIDS[role_name]:
        fail(f"Policy {role_name} diverge dos Sids exatos aprovados.")
    canonical = json.dumps(
        policy,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(canonical) > 10_240:
        fail(f"Policy {role_name} excede o limite agregado de inline policies da role.")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    if fingerprint != EXPECTED_PERMISSION_POLICY_SHA256[role_name]:
        fail(f"Policy {role_name} diverge dos pares exatos de Sid, ações e recursos.")
    if all_actions & FORBIDDEN_S3_PERMISSION_ALIASES:
        fail(f"Policy {role_name} usa alias inválido de permissão S3.")
    if not REQUIRED_S3_PERMISSIONS[role_name] <= all_actions:
        fail(f"Policy {role_name} não cobre o ciclo de vida S3 aprovado.")
    if not REQUIRED_PROVIDER_ACTIONS[role_name] <= all_actions:
        fail(f"Policy {role_name} não cobre as leituras exigidas pelo provider AWS.")

    if role_name == "plan":
        mutating = {
            action
            for action in all_actions
            if action.startswith(
                (
                    "apigateway:POST",
                    "apigateway:PATCH",
                    "budgets:Modify",
                    "cloudfront:Create",
                    "cloudfront:Delete",
                    "cloudfront:Update",
                    "cloudwatch:Put",
                    "cognito-idp:Create",
                    "cognito-idp:Delete",
                    "cognito-idp:Update",
                    "ec2:Create",
                    "ec2:Delete",
                    "ec2:Modify",
                    "ecr:Create",
                    "ecr:Delete",
                    "ecr:Put",
                    "ecs:Create",
                    "ecs:Delete",
                    "ecs:Register",
                    "ecs:Update",
                    "iam:Create",
                    "iam:Delete",
                    "iam:Pass",
                    "iam:Put",
                    "iam:Update",
                    "logs:Create",
                    "logs:Delete",
                    "logs:Put",
                    "sqs:Create",
                    "sqs:Delete",
                    "sqs:Set",
                )
            )
        }
        if mutating:
            fail("Role de plan possui mutação de infraestrutura.")
    elif role_name == "deploy":
        forbidden = {
            action
            for action in all_actions
            if ":Delete" in action and action != "s3:DeleteObject"
        }
        if forbidden:
            fail("Role de deploy possui exclusão fora do lock de state.")
        if "ecs:DeregisterTaskDefinition" not in all_actions:
            fail("Role de deploy não cobre a troca segura por digest.")
    else:
        forbidden = {
            action
            for action in all_actions
            if ":Create" in action or action in {"ecr:PutImage", "sqs:SendMessage"}
        }
        if forbidden:
            fail("Role de teardown possui criação ou publicação.")
        if "resourcegroupstaggingapi:GetResources" not in all_actions:
            fail("Role de teardown não cobre o inventário por tags.")


def audit_contract(contract: Mapping[str, Any]) -> dict[str, str]:
    exact_keys(
        contract,
        (
            "actions",
            "deploy_modes",
            "repository",
            "roles",
            "schema_version",
            "sen46_baseline_sha",
            "terraform_backend",
        ),
        context="contrato",
    )
    exact_text(
        contract.get("schema_version"),
        "aws-demo-delivery.v1",
        context="schema_version",
    )
    exact_text(
        contract.get("sen46_baseline_sha"),
        EXPECTED_SEN46_BASELINE,
        context="baseline SEN-46",
    )
    repository = mapping(contract.get("repository"), context="repository")
    exact_keys(
        repository,
        (
            "deployment_ref",
            "full_name",
            "immutable_subject_required",
            "owner_id",
            "repository_id",
        ),
        context="repository",
    )
    exact_text(repository.get("full_name"), EXPECTED_REPOSITORY, context="repositório")
    exact_text(repository.get("owner_id"), EXPECTED_OWNER_ID, context="owner_id")
    exact_text(
        repository.get("repository_id"),
        EXPECTED_REPOSITORY_ID,
        context="repository_id",
    )
    exact_text(repository.get("deployment_ref"), EXPECTED_REF, context="ref")
    if repository.get("immutable_subject_required") is not True:
        fail("O contrato deve exigir subject OIDC imutável.")

    deploy_modes = mapping(contract.get("deploy_modes"), context="deploy_modes")
    if set(deploy_modes) != set(EXPECTED_DEPLOY_MODES):
        fail("Contrato deve separar exatamente fundação e runtime.")
    for mode_name, expected_mode in EXPECTED_DEPLOY_MODES.items():
        mode = mapping(deploy_modes.get(mode_name), context=f"modo {mode_name}")
        exact_keys(mode, expected_mode, context=f"modo {mode_name}")
        if mode != expected_mode:
            fail(f"Modo {mode_name} diverge do contrato operacional exato.")

    backend = mapping(contract.get("terraform_backend"), context="backend")
    exact_keys(
        backend,
        (
            "lock_suffix",
            "plan_requires_initialized_state",
            "state_key",
            "use_lockfile",
        ),
        context="backend",
    )
    exact_text(
        backend.get("state_key"),
        "demo/sen-67/terraform.tfstate",
        context="state key",
    )
    exact_text(backend.get("lock_suffix"), ".tflock", context="lock suffix")
    if backend.get("use_lockfile") is not True:
        fail("Backend real deve usar lockfile nativo do S3.")
    if backend.get("plan_requires_initialized_state") is not True:
        fail("Plan protegido deve exigir state inicializado pela fundação.")

    raw_actions = sequence(contract.get("actions"), context="actions")
    action_pins: dict[str, str] = {}
    for reference in raw_actions:
        if type(reference) is not str:
            fail("Referências de actions devem ser strings exatas.")
        match = ACTION_REFERENCE_PATTERN.fullmatch(reference)
        if match is None:
            fail("Actions devem usar referências completas fixadas por SHA.")
        action, sha = match.groups()
        if action in action_pins:
            fail("Contrato não pode repetir uma action.")
        action_pins[action] = sha
    if tuple(sorted(action_pins)) != EXPECTED_ACTIONS:
        fail("Contrato deve fixar exatamente as cinco actions aprovadas.")

    roles = mapping(contract.get("roles"), context="roles")
    if tuple(sorted(roles)) != EXPECTED_ROLE_NAMES:
        fail("Contrato deve separar exatamente plan, deploy e teardown.")
    seen_environments: set[str] = set()
    seen_subjects: set[str] = set()
    for role_name in EXPECTED_ROLE_NAMES:
        role = mapping(roles.get(role_name), context=f"role {role_name}")
        exact_keys(
            role,
            (
                "environment",
                "permission_policy",
                "role_variable",
                "subject",
                "trust_policy",
            ),
            context=f"role {role_name}",
        )
        environment = f"aws-demo-{role_name}"
        exact_text(role.get("environment"), environment, context="environment")
        exact_text(
            role.get("role_variable"),
            f"AWS_DEMO_{role_name.upper()}_ROLE_ARN",
            context="role variable",
        )
        subject = expected_subject(environment)
        exact_text(role.get("subject"), subject, context="subject")
        if environment in seen_environments or subject in seen_subjects:
            fail("Roles de entrega não podem compartilhar environment ou subject.")
        seen_environments.add(environment)
        seen_subjects.add(subject)
        audit_trust_policy(
            role.get("trust_policy"),
            role_name=role_name,
            expected_environment=environment,
        )
        audit_permission_policy(role.get("permission_policy"), role_name=role_name)
    return action_pins


def run_blocks(workflow: str) -> list[str]:
    lines = workflow.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = re.match(r"^(\s*)run:\s*\|\s*$", line)
        if match is None:
            index += 1
            continue
        indentation = len(match.group(1))
        collected: list[str] = []
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if (
                candidate.strip()
                and len(candidate) - len(candidate.lstrip()) <= indentation
            ):
                break
            collected.append(candidate)
            index += 1
        blocks.append("\n".join(collected))
    return blocks


def audit_workflow(
    path: Path,
    *,
    expected_environment: str | None,
    uses_oidc: bool,
    action_pins: Mapping[str, str],
    expected_role_variable: str | None,
    expected_confirmation: str | None,
    expected_operation: str | None,
) -> None:
    try:
        workflow = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise DeliveryPolicyError("Workflow de entrega não é UTF-8 válido.") from None
    if "\r" in workflow or not workflow.endswith("\n"):
        fail("Workflow deve usar LF e newline final.")
    if workflow.count("permissions: {}") != 1:
        fail("Workflow deve negar permissões no topo.")
    forbidden = (
        "pull_request_target:",
        "upload-artifact",
        "download-artifact",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "terraform output -json",
        "terraform show -no-color",
        "persist-credentials: true",
        "merge-base --is-ancestor",
        "AWS_DEMO_SEN46_BASELINE_SHA",
    )
    if any(value in workflow for value in forbidden):
        fail("Workflow contém evento, credencial, artifact ou saída proibida.")
    run_declarations = re.findall(
        r"(?<![A-Za-z0-9_])(?:run|'run'|\"run\")[ \t]*:", workflow
    )
    literal_runs = re.findall(r"^[ \t]*run:[ \t]*\|[ \t]*$", workflow, re.MULTILINE)
    if len(run_declarations) != len(literal_runs):
        fail("Todo run deve usar bloco literal aprovado, sem forma inline ou folded.")
    for block in run_blocks(workflow):
        if "${{" in block:
            fail("Expressões GitHub não podem ser interpoladas dentro de shell.")
    uses = ACTION_USE_PATTERN.findall(workflow)
    if not uses:
        fail("Workflow não fixa nenhuma action aprovada.")
    for action, sha in uses:
        if action_pins.get(action) != sha:
            fail("Workflow usa action ausente do contrato ou SHA divergente.")
    raw_uses = re.findall(r"^\s*uses:\s*([^\s]+)", workflow, flags=re.MULTILINE)
    if len(raw_uses) != len(uses):
        fail("Toda action deve ser fixada por SHA completo.")

    if expected_environment is None:
        if any(
            value is not None
            for value in (
                expected_role_variable,
                expected_confirmation,
                expected_operation,
            )
        ):
            fail("Validação offline não aceita identidade de operação AWS.")
        if "pull_request:" not in workflow or "workflow_dispatch:" in workflow:
            fail("Validação offline deve rodar somente no contexto de revisão.")
        if "id-token: write" in workflow or "configure-aws-credentials" in workflow:
            fail("Validação offline não pode solicitar OIDC ou AWS.")
        if "environment:" in workflow:
            fail("Validação offline não usa environment protegido.")
        if "offline" not in workflow.lower():
            fail("Validação deve tornar a fronteira offline explícita.")
        return

    if (
        expected_role_variable is None
        or expected_confirmation is None
        or expected_operation is None
    ):
        fail("Workflow AWS não possui identidade operacional completa.")

    if "cancel-in-progress: true" in workflow:
        fail("Operações de state não podem cancelar execução em andamento.")
    if "pull_request:" in workflow or "push:" in workflow or "schedule:" in workflow:
        fail("Operação AWS deve ser estritamente manual.")
    if workflow.count("workflow_dispatch:") != 1:
        fail("Operação AWS deve expor um único workflow_dispatch.")
    if workflow.count("id-token: write") != 1 or not uses_oidc:
        fail("OIDC deve existir apenas no job protegido da operação.")
    if f"environment: {expected_environment}" not in workflow:
        fail("Workflow usa environment diferente do contrato.")
    expected_role = f"role-to-assume: ${{{{ vars.{expected_role_variable} }}}}"
    expected_command = (
        f"python infra/aws/demo/scripts/aws_delivery.py {expected_operation}"
    )
    foundation_command: str | None = None
    if expected_operation == "deploy":
        foundation_confirmation = "FOUNDATION-AWS-DEMO"
        foundation_command = "python infra/aws/demo/scripts/aws_delivery.py foundation"
        combined_guard = (
            '[[ "$CONFIRMATION" == "FOUNDATION-AWS-DEMO" || '
            '"$CONFIRMATION" == "DEPLOY-AWS-DEMO" ]]'
        )
        foundation_condition = "if: ${{ inputs.confirmation == 'FOUNDATION-AWS-DEMO' }}"
        runtime_condition = "if: ${{ inputs.confirmation == 'DEPLOY-AWS-DEMO' }}"
        if (
            workflow.count(expected_role) != 1
            or workflow.count(foundation_confirmation) != 3
            or workflow.count(expected_confirmation) != 5
            or workflow.count(combined_guard) != 1
            or workflow.count(foundation_condition) != 1
            or workflow.count(runtime_condition) != 3
            or workflow.count(foundation_command) != 1
            or workflow.count(expected_command) != 1
        ):
            fail("Workflow de deploy diverge dos dois modos manuais exatos.")
    else:
        expected_guard = f'[[ "$CONFIRMATION" == "{expected_confirmation}" ]]'
        if (
            workflow.count(expected_role) != 1
            or workflow.count(expected_confirmation) != 2
            or workflow.count(expected_guard) != 1
            or workflow.count(expected_command) != 1
        ):
            fail("Workflow diverge de role, confirmação ou operação exatas.")
    credential_reference = (
        "uses: aws-actions/configure-aws-credentials@"
        f"{action_pins['aws-actions/configure-aws-credentials']}"
    )
    credential_offset = workflow.find(credential_reference)
    command_offset = workflow.find(expected_command)
    source_checkout_offset = workflow.find("ref: ${{ inputs.source_sha }}")
    revalidation_offset = workflow.find(PROTECTED_MAIN_REVALIDATION)
    protected_fetch = "git fetch --force --no-tags --no-recurse-submodules --depth=1"
    protected_head_check = '[[ "$(git rev-parse --verify HEAD)" == "$SOURCE_SHA" ]]'
    protected_main_check = (
        '[[ "$(git rev-parse --verify refs/remotes/origin/main)" == "$SOURCE_SHA" ]]'
    )
    if (
        workflow.count(PROTECTED_MAIN_REVALIDATION) != 1
        or workflow.count(protected_fetch) != 1
        or workflow.count("origin refs/heads/main:refs/remotes/origin/main") != 1
        or workflow.count(protected_head_check) != 1
        or workflow.count(protected_main_check) != 1
        or workflow.count("GIT_CONFIG_GLOBAL: /dev/null") != 1
        or workflow.count('GIT_CONFIG_NOSYSTEM: "1"') != 1
        or workflow.count('GIT_NO_REPLACE_OBJECTS: "1"') != 1
        or workflow.count('GIT_TERMINAL_PROMPT: "0"') != 1
        or not 0
        <= source_checkout_offset
        < revalidation_offset
        < credential_offset
        < command_offset
    ):
        fail("Job protegido não revalida o HEAD atual de main antes do OIDC.")
    operation_env_keys: list[str] = list(COMMON_OPERATION_ENV_KEYS)
    if expected_operation == "deploy":
        if foundation_command is None:
            fail("Workflow de deploy perdeu o comando de fundação.")
        foundation_offset = workflow.find(foundation_command)
        for key in operation_env_keys:
            declarations = list(
                re.finditer(rf"^[ \t]+{re.escape(key)}[ \t]*:", workflow, re.MULTILINE)
            )
            if (
                len(declarations) != 2
                or not credential_offset
                < declarations[0].start()
                < foundation_offset
                < declarations[1].start()
                < command_offset
            ):
                fail("Ambiente comum deve ficar nos dois steps protegidos de deploy.")
        for key in ("AWS_DEMO_SMOKE_BEARER_TOKEN",):
            declarations = list(
                re.finditer(rf"^[ \t]+{re.escape(key)}[ \t]*:", workflow, re.MULTILINE)
            )
            if (
                len(declarations) != 1
                or not foundation_offset < declarations[0].start() < command_offset
            ):
                fail("Token temporário pertence somente ao runtime protegido.")
        buildx_action = (
            "uses: docker/setup-buildx-action@"
            f"{action_pins['docker/setup-buildx-action']}"
        )
        buildx_prepare_offset = workflow.find(
            "Prepare the isolated Buildx configuration"
        )
        buildx_action_offset = workflow.find(buildx_action)
        buildx_builder_declaration = (
            f"AWS_DEMO_BUILDX_BUILDER: {BUILDX_OUTPUT_EXPRESSION}"
        )
        buildx_config_declaration = (
            f"AWS_DEMO_BUILDX_DOCKER_CONFIG: {BUILDX_CONFIG_EXPRESSION}"
        )
        if (
            workflow.count("id: buildx") != 1
            or workflow.count(BUILDX_CONFIG_EXPRESSION) != 3
            or workflow.count(f"\n          DOCKER_CONFIG: {BUILDX_CONFIG_EXPRESSION}")
            != 1
            or workflow.count(f"name: {BUILDX_NAME_EXPRESSION}") != 1
            or workflow.count(buildx_builder_declaration) != 1
            or workflow.count(buildx_config_declaration) != 1
            or workflow.count('install -d -m 0700 -- "$BUILDX_CONFIG_PATH"') != 1
            or not revalidation_offset
            < buildx_prepare_offset
            < buildx_action_offset
            < credential_offset
            < foundation_offset
            < workflow.find(buildx_builder_declaration)
            < command_offset
        ):
            fail("Workflow de runtime perdeu o contexto Buildx isolado e explícito.")
    else:
        for key in operation_env_keys:
            declarations = list(
                re.finditer(rf"^[ \t]+{re.escape(key)}[ \t]*:", workflow, re.MULTILINE)
            )
            if (
                len(declarations) != 1
                or not credential_offset < declarations[0].start() < command_offset
            ):
                fail(
                    "Ambiente operacional deve existir somente no último step "
                    "protegido."
                )
    if credential_offset < 0 or "${{ secrets." in workflow[:credential_offset]:
        fail("Segredos não podem alcançar actions de preparação ou OIDC.")
    expected_duration = "3600" if expected_operation == "plan" else "7200"
    expected_timeout = "45" if expected_operation == "plan" else "90"
    expected_expiration_references = 2 if expected_operation == "deploy" else 1
    environment_gate = (
        "python infra/aws/demo/scripts/github_environment_gate.py "
        f"{expected_environment}"
    )
    if (
        workflow.count("actions: read") != 1
        or workflow.count("GITHUB_TOKEN: ${{ github.token }}") != 1
        or workflow.count(environment_gate) != 1
        or workflow.find(environment_gate) > credential_offset
        or workflow.count("id: aws-creds") != 1
        or workflow.count("output-credentials: true") != 1
        or workflow.count(f"role-duration-seconds: {expected_duration}") != 1
        or workflow.count("timeout-minutes: 10") != 1
        or workflow.count(f"timeout-minutes: {expected_timeout}") != 1
        or workflow.count("steps.aws-creds.outputs.aws-expiration")
        != expected_expiration_references
    ):
        fail("Workflow não prova environment ou validade da sessão antes do OIDC.")
    foreign_values = {
        value
        for (
            environment,
            _,
            role_variable,
            confirmation,
            operation,
        ) in WORKFLOW_CONTRACT.values()
        for value in (environment, role_variable, confirmation, operation)
        if value is not None
    } - {
        expected_environment,
        expected_role_variable,
        expected_confirmation,
        expected_operation,
    }
    if any(
        (
            f"vars.{value}" in workflow
            if value.endswith("_ROLE_ARN")
            else f'"{value}"' in workflow
        )
        for value in foreign_values
    ):
        fail("Workflow mistura identidade de outra operação AWS.")
    required_fragments = (
        "group: aws-demo-state",
        "GITHUB_REPOSITORY",
        EXPECTED_REPOSITORY,
        "GITHUB_REF",
        f"EXPECTED_REF: {EXPECTED_REF}",
        EXPECTED_REF,
        "persist-credentials: false",
        "fetch-depth: 0",
        "source_sha",
        "^[0-9a-f]{40}$",
        '[[ "$SOURCE_SHA" == "$GITHUB_SHA" ]]',
        '[[ "$(git rev-parse HEAD)" == "$SOURCE_SHA" ]]',
        "allowed-account-ids:",
        "mask-aws-account-id: true",
        "unset-current-credentials: true",
    )
    if any(fragment not in workflow for fragment in required_fragments):
        fail("Workflow manual não contém todos os guards fechados.")


def audit_workflows(action_pins: Mapping[str, str], root: Path) -> None:
    workflow_root = root / ".github/workflows"
    for filename, (
        environment,
        uses_oidc,
        role_variable,
        confirmation,
        operation,
    ) in WORKFLOW_CONTRACT.items():
        audit_workflow(
            workflow_root / filename,
            expected_environment=environment,
            uses_oidc=uses_oidc,
            action_pins=action_pins,
            expected_role_variable=role_variable,
            expected_confirmation=confirmation,
            expected_operation=operation,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita o contrato OIDC/IAM e os workflows AWS da demo."
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract = load_json(args.contract.resolve(strict=True))
    action_pins = audit_contract(contract)
    audit_workflows(action_pins, args.repository_root.resolve(strict=True))
    print(
        "Política de entrega aprovada: três subjects imutáveis, permissions "
        "fechadas, actions por SHA e operações AWS exclusivamente manuais."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeliveryPolicyError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("Política de entrega falhou com segurança.", file=sys.stderr)
        raise SystemExit(1) from None
