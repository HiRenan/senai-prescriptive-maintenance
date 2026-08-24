"""Adversarial offline regressions for the AWS demo delivery boundaries."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from base64 import urlsafe_b64encode
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, cast

import aws_delivery
import orphan_inventory
import remote_smoke
from aws_delivery import (
    AwsDeliveryError,
    capture_silent,
    child_environment,
    login_and_build,
    parse_ecr_lookup,
    strict_json,
    validated_buildx_configuration,
    validated_configuration,
)
from delivery_gate import (
    EXPECTED_MANAGED,
    EXPECTED_STATE_OUTPUT_TYPES,
    REQUIRED_TAGS,
    TAGGED_ADDRESSES,
    DeliveryGateError,
    audit_plan,
    audit_state,
    audit_state_snapshot,
    expected_buckets,
    expected_cognito_domain,
    expected_names,
)
from delivery_policy import (
    DEFAULT_CONTRACT,
    REPOSITORY_ROOT,
    DeliveryPolicyError,
    audit_contract,
    audit_workflow,
    load_json,
)
from github_environment_gate import (
    GitHubEnvironmentGateError,
    audit_environment,
)
from orphan_inventory import (
    CommandResult,
    InventoryQuery,
    OrphanInventoryError,
    inventory_queries,
    safe_environment,
    scan_inventory,
)
from remote_smoke import (
    RESULT_FIELDS,
    RemoteSmokeError,
    require_authentication,
    validate_endpoint,
    validate_readiness,
    validate_scenario,
    validate_token,
)

SENSITIVE_MARKER = "SENSITIVE_SYNTHETIC_MARKER"


class DeliveryRegressionError(RuntimeError):
    """Raised when an adversarial mutation is not rejected safely."""


class HostileStream:
    def __init__(self, *, fail_read: bool, fail_close: bool) -> None:
        self._fail_read = fail_read
        self._fail_close = fail_close
        self._returned_partial = False

    def read(self, size: int = -1) -> bytes:
        del size
        if self._fail_read:
            if not self._returned_partial:
                self._returned_partial = True
                return b"partial"
            raise RuntimeError(SENSITIVE_MARKER)
        return b""

    def close(self) -> None:
        if self._fail_close:
            raise RuntimeError(SENSITIVE_MARKER)


def expect_failure(
    operation: Callable[[], object],
    expected: type[Exception] | tuple[type[Exception], ...],
) -> None:
    try:
        operation()
    except expected as error:
        if SENSITIVE_MARKER in str(error) or SENSITIVE_MARKER in repr(error):
            raise DeliveryRegressionError(
                "Uma falha sanitizada expôs conteúdo."
            ) from None
    else:
        raise DeliveryRegressionError("Uma mutação adversarial foi aceita.")


def mutable_contract() -> dict[str, Any]:
    return cast(dict[str, Any], copy.deepcopy(load_json(DEFAULT_CONTRACT)))


def role(contract: dict[str, Any], name: str) -> dict[str, Any]:
    return cast(dict[str, Any], cast(dict[str, Any], contract["roles"])[name])


def trust_equals(contract: dict[str, Any], name: str) -> dict[str, str]:
    policy = cast(dict[str, Any], role(contract, name)["trust_policy"])
    statement = cast(list[dict[str, Any]], policy["Statement"])[0]
    condition = cast(dict[str, Any], statement["Condition"])
    return cast(dict[str, str], condition["StringEquals"])


def permission_statement(
    contract: dict[str, Any], role_name: str, sid: str
) -> dict[str, Any]:
    policy = cast(dict[str, Any], role(contract, role_name)["permission_policy"])
    statements = cast(list[dict[str, Any]], policy["Statement"])
    matches = [statement for statement in statements if statement.get("Sid") == sid]
    if len(matches) != 1:
        raise DeliveryRegressionError("Contrato sintético não possui Sid único.")
    return matches[0]


def remove_permission_statement(
    contract: dict[str, Any], role_name: str, sid: str
) -> None:
    policy = cast(dict[str, Any], role(contract, role_name)["permission_policy"])
    statements = cast(list[dict[str, Any]], policy["Statement"])
    policy["Statement"] = [
        statement for statement in statements if statement.get("Sid") != sid
    ]


def prove_contract_mutations_rejected() -> int:
    mutations: list[Callable[[dict[str, Any]], None]] = []

    def nominal_subject(contract: dict[str, Any]) -> None:
        value = "repo:HiRenan/senai-prescriptive-maintenance:environment:aws-demo-plan"
        role(contract, "plan")["subject"] = value
        trust_equals(contract, "plan")["token.actions.githubusercontent.com:sub"] = (
            value
        )

    mutations.append(nominal_subject)

    def wildcard_subject(contract: dict[str, Any]) -> None:
        trust_equals(contract, "deploy")["token.actions.githubusercontent.com:sub"] = (
            "repo:*"
        )

    mutations.append(wildcard_subject)

    def wrong_audience(contract: dict[str, Any]) -> None:
        trust_equals(contract, "teardown")[
            "token.actions.githubusercontent.com:aud"
        ] = "hostile.invalid"

    mutations.append(wrong_audience)

    def action_wildcard(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "TaggedRW")
        cast(list[str], statement["Action"]).append("s3:*")

    mutations.append(action_wildcard)

    def plan_mutation(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "plan", "ReadDemoResources")
        cast(list[str], statement["Action"]).append("ec2:CreateVpc")
        cast(list[str], statement["Action"]).sort()

    mutations.append(plan_mutation)

    def missing_batch_get_image(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "EcrReads")
        cast(list[str], statement["Action"]).remove("ecr:BatchGetImage")

    mutations.append(missing_batch_get_image)

    def unused_repository_policy(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "EcrWrites")
        actions = cast(list[str], statement["Action"])
        actions.append("ecr:SetRepositoryPolicy")
        actions.sort()

    mutations.append(unused_repository_policy)

    def crossed_service_linked_role(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "EcsSlr")
        condition = cast(dict[str, Any], statement["Condition"])
        equals = cast(dict[str, str], condition["StringEquals"])
        equals["iam:AWSServiceName"] = "ops.apigateway.amazonaws.com"

    mutations.append(crossed_service_linked_role)

    def wildcard_service_linked_role(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "ApiSlr")
        statement["Resource"] = "*"

    mutations.append(wildcard_service_linked_role)

    def widened_service_linked_role_action(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "EcsSlr")
        statement["Action"] = ["iam:CreateServiceLinkedRole", "iam:PassRole"]

    mutations.append(widened_service_linked_role_action)

    def budget_service_linked_role(contract: dict[str, Any]) -> None:
        policy = cast(dict[str, Any], role(contract, "deploy")["permission_policy"])
        statements = cast(list[dict[str, Any]], policy["Statement"])
        statements.append(
            {
                "Action": "iam:CreateServiceLinkedRole",
                "Condition": {
                    "StringEquals": {"iam:AWSServiceName": "budgets.amazonaws.com"}
                },
                "Effect": "Allow",
                "Resource": "*",
                "Sid": "CreateBudgetsServiceLinkedRole",
            }
        )

    mutations.append(budget_service_linked_role)

    def deletable_state_object(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "ManageStateObject")
        actions = cast(list[str], statement["Action"])
        actions.append("s3:DeleteObject")
        actions.sort()

    mutations.append(deletable_state_object)

    def wildcard_iam_roles(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "IamWrites")
        statement["Resource"] = "*"

    mutations.append(wildcard_iam_roles)

    def wildcard_bucket_objects(contract: dict[str, Any]) -> None:
        statement = permission_statement(
            contract, "teardown", "DestroyDemoBucketObjects"
        )
        statement["Resource"] = "*"

    mutations.append(wildcard_bucket_objects)

    def invalid_s3_lifecycle_alias(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "teardown", "DestroyDemoBuckets")
        actions = cast(list[str], statement["Action"])
        actions.remove("s3:PutLifecycleConfiguration")
        actions.append("s3:DeleteBucketLifecycle")
        actions.sort()

    mutations.append(invalid_s3_lifecycle_alias)

    def missing_global_ecr_inventory(contract: dict[str, Any]) -> None:
        statement = permission_statement(
            contract, "teardown", "InventoryDemoResourcesGlobal"
        )
        cast(list[str], statement["Action"]).remove("ecr:DescribeRepositories")

    mutations.append(missing_global_ecr_inventory)

    def wildcard_ecr_delete(contract: dict[str, Any]) -> None:
        statement = permission_statement(
            contract, "teardown", "InventoryDemoResourcesGlobal"
        )
        actions = cast(list[str], statement["Action"])
        actions.append("ecr:DeleteRepository")
        actions.sort()

    mutations.append(wildcard_ecr_delete)

    def widened_state_resource(contract: dict[str, Any]) -> None:
        policy = cast(dict[str, Any], role(contract, "plan")["permission_policy"])
        statements = cast(list[dict[str, Any]], policy["Statement"])
        statements[1]["Resource"] = "*"

    mutations.append(widened_state_resource)

    def crossed_sid_resources(contract: dict[str, Any]) -> None:
        policy = cast(dict[str, Any], role(contract, "plan")["permission_policy"])
        statements = cast(list[dict[str, Any]], policy["Statement"])
        statements[0]["Resource"], statements[1]["Resource"] = (
            statements[1]["Resource"],
            statements[0]["Resource"],
        )

    mutations.append(crossed_sid_resources)

    def shared_environment(contract: dict[str, Any]) -> None:
        role(contract, "teardown")["environment"] = "aws-demo-deploy"

    mutations.append(shared_environment)

    def legacy_role_variable(contract: dict[str, Any]) -> None:
        plan_role = role(contract, "plan")
        plan_role["role_variable"] = plan_role.pop("role_secret")

    mutations.append(legacy_role_variable)

    def unpinned_action(contract: dict[str, Any]) -> None:
        cast(list[str], contract["actions"])[0] = "actions/checkout@v7"

    mutations.append(unpinned_action)

    def duplicated_action(contract: dict[str, Any]) -> None:
        actions = cast(list[str], contract["actions"])
        actions[1] = actions[0]

    mutations.append(duplicated_action)

    def disabled_lock(contract: dict[str, Any]) -> None:
        cast(dict[str, Any], contract["terraform_backend"])["use_lockfile"] = False

    mutations.append(disabled_lock)

    def plan_without_foundation(contract: dict[str, Any]) -> None:
        backend = cast(dict[str, Any], contract["terraform_backend"])
        backend["plan_requires_initialized_state"] = False

    mutations.append(plan_without_foundation)

    def foundation_requires_runtime_secret(contract: dict[str, Any]) -> None:
        modes = cast(dict[str, Any], contract["deploy_modes"])
        foundation = cast(dict[str, Any], modes["foundation"])
        foundation["requires_smoke_token"] = True

    mutations.append(foundation_requires_runtime_secret)

    def runtime_without_buildx(contract: dict[str, Any]) -> None:
        modes = cast(dict[str, Any], contract["deploy_modes"])
        runtime = cast(dict[str, Any], modes["runtime"])
        runtime["requires_buildx_context"] = False

    mutations.append(runtime_without_buildx)

    def crossed_deploy_command(contract: dict[str, Any]) -> None:
        modes = cast(dict[str, Any], contract["deploy_modes"])
        foundation = cast(dict[str, Any], modes["foundation"])
        foundation["command"] = "deploy"

    mutations.append(crossed_deploy_command)

    def changed_sen46_baseline(contract: dict[str, Any]) -> None:
        contract["sen46_baseline_sha"] = "a" * 40

    mutations.append(changed_sen46_baseline)

    def wildcard_tagged_mutations(contract: dict[str, Any]) -> None:
        permission_statement(contract, "deploy", "TaggedRW")["Resource"] = "*"

    mutations.append(wildcard_tagged_mutations)

    def invalid_tag_condition(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "GlobalNew")
        condition = cast(dict[str, Any], statement["Condition"])
        equals = cast(dict[str, str], condition["StringEquals"])
        equals["aws:PrincipalTag/Profile"] = equals.pop("aws:RequestTag/Profile")

    mutations.append(invalid_tag_condition)

    def conditioned_deploy_task_deregister(contract: dict[str, Any]) -> None:
        remove_permission_statement(contract, "deploy", "TaskDeregister")
        statement = permission_statement(contract, "deploy", "TaggedRW")
        actions = cast(list[str], statement["Action"])
        actions.append("ecs:DeregisterTaskDefinition")
        actions.sort()

    mutations.append(conditioned_deploy_task_deregister)

    def conditioned_teardown_task_deregister(contract: dict[str, Any]) -> None:
        remove_permission_statement(contract, "teardown", "DestroyTaskDefinitions")
        statement = permission_statement(
            contract, "teardown", "DestroyTaggedDemoResources"
        )
        actions = cast(list[str], statement["Action"])
        actions.append("ecs:DeregisterTaskDefinition")
        actions.sort()

    mutations.append(conditioned_teardown_task_deregister)

    def unsupported_cloud_map_resource_tag(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "CloudMapTag")
        statement["Condition"] = {
            "StringEquals": {"aws:ResourceTag/Profile": "aws-demo"}
        }

    mutations.append(unsupported_cloud_map_resource_tag)

    def unsupported_cloud_map_resource_arn(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "CloudMapTag")
        statement["Resource"] = [
            "arn:aws:servicediscovery:${AWS_REGION}:${AWS_ACCOUNT_ID}:namespace/*",
            "arn:aws:servicediscovery:${AWS_REGION}:${AWS_ACCOUNT_ID}:service/*",
        ]

    mutations.append(unsupported_cloud_map_resource_arn)

    def string_task_dimensions(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "TaskNew")
        statement["Condition"] = {
            "StringEquals": {
                "aws:RequestTag/Profile": "aws-demo",
                "ecs:task-cpu": "256",
                "ecs:task-memory": "512",
            }
        }

    mutations.append(string_task_dimensions)

    def invalid_tag_inventory_prefix(contract: dict[str, Any]) -> None:
        statement = permission_statement(
            contract, "teardown", "InventoryDemoResourcesGlobal"
        )
        actions = cast(list[str], statement["Action"])
        actions.remove("tag:GetResources")
        actions.append("resourcegroupstaggingapi:GetResources")
        actions.sort()

    mutations.append(invalid_tag_inventory_prefix)

    def inline_deploy_publication(contract: dict[str, Any]) -> None:
        publication = cast(
            dict[str, Any], role(contract, "deploy")["permission_policy_publication"]
        )
        publication["mode"] = "inline"

    mutations.append(inline_deploy_publication)

    def unversioned_managed_policy(contract: dict[str, Any]) -> None:
        publication = cast(
            dict[str, Any], role(contract, "deploy")["permission_policy_publication"]
        )
        documents = cast(list[dict[str, Any]], publication["documents"])
        documents[0]["name"] = "${NAME_PREFIX}-demo-deploy-core"

    mutations.append(unversioned_managed_policy)

    def incomplete_policy_publication(contract: dict[str, Any]) -> None:
        publication = cast(
            dict[str, Any], role(contract, "teardown")["permission_policy_publication"]
        )
        documents = cast(list[dict[str, Any]], publication["documents"])
        cast(list[str], documents[0]["sids"]).remove("DestroyTaskDefinitions")

    mutations.append(incomplete_policy_publication)

    def untagged_ecr_creation(contract: dict[str, Any]) -> None:
        permission_statement(contract, "deploy", "EcrCreate").pop("Condition")

    mutations.append(untagged_ecr_creation)

    def untagged_ecr_write(contract: dict[str, Any]) -> None:
        permission_statement(contract, "deploy", "EcrWrites").pop("Condition")

    mutations.append(untagged_ecr_write)

    def untagged_cloudfront_creation(contract: dict[str, Any]) -> None:
        permission_statement(contract, "deploy", "CfTagCreate").pop("Condition")

    mutations.append(untagged_cloudfront_creation)

    def untagged_api_creation(contract: dict[str, Any]) -> None:
        permission_statement(contract, "deploy", "ApiCreate").pop("Condition")

    mutations.append(untagged_api_creation)

    def widened_api_creation_path(contract: dict[str, Any]) -> None:
        permission_statement(contract, "deploy", "ApiCreate")["Resource"] = (
            "arn:aws:apigateway:${AWS_REGION}::/apis*"
        )

    mutations.append(widened_api_creation_path)

    def untagged_api_writes(contract: dict[str, Any]) -> None:
        permission_statement(contract, "deploy", "ApiWrites").pop("Condition")

    mutations.append(untagged_api_writes)

    def untagged_api_destroy(contract: dict[str, Any]) -> None:
        permission_statement(contract, "teardown", "DestroyDemoApiGateway").pop(
            "Condition"
        )

    mutations.append(untagged_api_destroy)

    def scoped_cognito_domain_describe(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "CognitoDomainRW")
        statement["Action"] = [
            "cognito-idp:CreateUserPoolDomain",
            "cognito-idp:DescribeUserPoolDomain",
        ]

    mutations.append(scoped_cognito_domain_describe)

    def widened_frontend_delete(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "FrontendObjectDelete")
        statement["Resource"] = (
            "arn:aws:s3:::${NAME_PREFIX}-frontend-${AWS_ACCOUNT_ID}-${AWS_REGION}/*"
        )

    mutations.append(widened_frontend_delete)

    def widened_frontend_put(contract: dict[str, Any]) -> None:
        statement = permission_statement(contract, "deploy", "FrontendObjectPut")
        statement["Resource"] = (
            "arn:aws:s3:::${NAME_PREFIX}-frontend-${AWS_ACCOUNT_ID}-${AWS_REGION}/*"
        )

    mutations.append(widened_frontend_put)

    def unencrypted_frontend_put(contract: dict[str, Any]) -> None:
        permission_statement(contract, "deploy", "FrontendObjectPut").pop("Condition")

    mutations.append(unencrypted_frontend_put)

    def without_provider_action(
        role_name: str, sid: str, action: str
    ) -> Callable[[dict[str, Any]], None]:
        def mutate(contract: dict[str, Any]) -> None:
            statement = permission_statement(contract, role_name, sid)
            cast(list[str], statement["Action"]).remove(action)

        return mutate

    mutations.extend(
        (
            without_provider_action(
                "plan", "ReadDemoIamRoles", "iam:ListAttachedRolePolicies"
            ),
            without_provider_action(
                "plan", "ReadDemoResources", "ec2:DescribeVpcAttribute"
            ),
            without_provider_action(
                "plan", "ReadDemoResources", "ec2:DescribeSecurityGroupRules"
            ),
            without_provider_action(
                "deploy", "ReadDemoIamRoles", "iam:ListAttachedRolePolicies"
            ),
            without_provider_action(
                "deploy", "GlobalReads", "ec2:DescribeVpcAttribute"
            ),
            without_provider_action(
                "deploy", "GlobalReads", "ec2:DescribeSecurityGroupRules"
            ),
            without_provider_action(
                "teardown", "ReadDemoIamRoles", "iam:ListAttachedRolePolicies"
            ),
            without_provider_action(
                "teardown",
                "ReadDemoIamRoles",
                "iam:ListInstanceProfilesForRole",
            ),
            without_provider_action(
                "teardown", "InventoryDemoResourcesGlobal", "ec2:DescribeVpcAttribute"
            ),
            without_provider_action(
                "teardown",
                "InventoryDemoResourcesGlobal",
                "ec2:DescribeSecurityGroupRules",
            ),
        )
    )

    for mutate in mutations:
        candidate = mutable_contract()
        mutate(candidate)
        expect_failure(
            lambda candidate=candidate: audit_contract(candidate), DeliveryPolicyError
        )
    return len(mutations)


def prove_workflow_mutations_rejected(action_pins: Mapping[str, str]) -> int:
    path = REPOSITORY_ROOT / ".github/workflows/aws-demo-deploy.yml"
    source = path.read_text(encoding="utf-8")
    revalidation_start = source.index(
        "      - name: Revalidate the current main revision after approval"
    )
    revalidation_end = source.index(
        "\n      - name: Set up Python 3.13", revalidation_start
    )
    revalidation_block = source[revalidation_start:revalidation_end]
    without_revalidation = source[:revalidation_start] + source[revalidation_end + 1 :]
    revalidation_after_runtime = (
        without_revalidation.rstrip() + "\n\n" + revalidation_block + "\n"
    )
    mutations = (
        without_revalidation,
        revalidation_after_runtime,
        source.replace(
            "  workflow_dispatch:", "  pull_request:\n  workflow_dispatch:", 1
        ),
        source.replace("refs/heads/main", "refs/heads/develop", 1),
        source.replace(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/checkout@v7",
            1,
        ),
        source.replace(
            "python infra/aws/demo/scripts/aws_delivery.py deploy",
            "echo ${{ inputs.source_sha }}",
            1,
        ),
        source.replace("environment: aws-demo-deploy", "environment: aws-demo-plan", 1),
        source.replace(
            "unset-current-credentials: true",
            "unset-current-credentials: true\n      - uses: actions/upload-artifact@v4",
            1,
        ),
        source.replace("group: aws-demo-state", "group: hostile", 1),
        source.replace(
            "python infra/aws/demo/scripts/github_environment_gate.py aws-demo-deploy",
            "python -c \"print('hostile')\"",
            1,
        ),
        source.replace("actions: read", "actions: write", 1),
        source.replace("id-token: write", "id-token: read", 1),
        source.replace("role-duration-seconds: 7200", "role-duration-seconds: 3600", 1),
        source.replace("timeout-minutes: 90", "timeout-minutes: 150", 1),
        source.replace(
            "${{ steps.aws-creds.outputs.aws-expiration }}",
            "2099-01-01T00:00:00Z",
            1,
        ),
        source.replace(
            "allowed-account-ids: ${{ secrets.AWS_DEMO_ACCOUNT_ID }}",
            "allowed-account-ids: ${{ vars.AWS_DEMO_ACCOUNT_ID }}",
            1,
        ),
        source.replace(
            "allowed-account-ids: ${{ secrets.AWS_DEMO_ACCOUNT_ID }}",
            "allowed-account-ids: ${{ secrets.AWS_DEMO_ACCOUNT_ID }}-hostile",
            1,
        ),
        source.replace(
            "          action-timeout-s: 120",
            "          action-timeout-s: 120\n"
            "          external-id: ${{ secrets['HOSTILE_EXTERNAL_ID'] }}",
            1,
        ),
        source.replace(
            "role-to-assume: ${{ secrets.AWS_DEMO_DEPLOY_ROLE_ARN }}",
            "role-to-assume: ${{ vars.AWS_DEMO_DEPLOY_ROLE_ARN }}",
            1,
        ),
        source.replace(
            "TF_STATE_BUCKET: ${{ secrets.AWS_DEMO_STATE_BUCKET }}",
            "TF_STATE_BUCKET: ${{ vars.AWS_DEMO_STATE_BUCKET }}",
            1,
        ),
        source.replace(
            "TF_VAR_frontend_certificate_arn: "
            "${{ secrets.AWS_DEMO_FRONTEND_CERTIFICATE_ARN }}",
            "TF_VAR_frontend_certificate_arn: "
            "${{ vars.AWS_DEMO_FRONTEND_CERTIFICATE_ARN }}",
            1,
        ),
        source.replace(
            "TF_STATE_BUCKET: ${{ secrets.AWS_DEMO_STATE_BUCKET }}",
            "TF_STATE_BUCKET: ${{ secrets.HOSTILE_STATE_BUCKET }}",
            1,
        ),
        source.replace(
            "          TF_STATE_BUCKET: ${{ secrets.AWS_DEMO_STATE_BUCKET }}",
            "          HOSTILE_INPUT: ${{ secrets['HOSTILE_INPUT'] }}\n"
            "          TF_STATE_BUCKET: ${{ secrets.AWS_DEMO_STATE_BUCKET }}",
            1,
        ),
        source.replace("run: |", "run: echo '${{ inputs.source_sha }}'", 1),
        source.replace("run: |", "run: >", 1),
        source.replace("AWS_DEMO_DEPLOY_ROLE_ARN", "AWS_DEMO_PLAN_ROLE_ARN", 1),
        source.replace("DEPLOY-AWS-DEMO", "PLAN-AWS-DEMO"),
        source.replace("aws_delivery.py deploy", "aws_delivery.py teardown", 1),
        source.replace(
            '[[ "$SOURCE_SHA" == "$GITHUB_SHA" ]]',
            'git merge-base --is-ancestor "$SOURCE_SHA" "$GITHUB_SHA"',
            1,
        ),
        source.replace(
            "      id-token: write\n    steps:",
            "      id-token: write\n    env:\n"
            "      AWS_DEMO_SMOKE_BEARER_TOKEN: "
            "${{ secrets.AWS_DEMO_SMOKE_BEARER_TOKEN }}\n    steps:",
            1,
        ),
        source.replace(
            "python infra/aws/demo/scripts/aws_delivery.py foundation",
            "python infra/aws/demo/scripts/aws_delivery.py plan",
            1,
        ),
        source.replace(
            "          AWS_DEMO_SOURCE_SHA: ${{ inputs.source_sha }}",
            "          AWS_DEMO_SEN46_BASELINE_SHA: "
            "${{ vars.AWS_DEMO_SEN46_BASELINE_SHA }}\n"
            "          AWS_DEMO_SOURCE_SHA: ${{ inputs.source_sha }}",
            1,
        ),
        source.replace("        id: buildx\n", "", 1),
        source.replace(
            "          name: sen68-${{ github.run_id }}-${{ github.run_attempt }}",
            "          name: default",
            1,
        ),
        source.replace(
            "AWS_DEMO_BUILDX_DOCKER_CONFIG: "
            "${{ runner.temp }}/sen68-buildx-${{ github.run_id }}-"
            "${{ github.run_attempt }}",
            "AWS_DEMO_BUILDX_DOCKER_CONFIG: ${{ env.DOCKER_CONFIG }}",
            1,
        ),
    )
    with tempfile.TemporaryDirectory(prefix="sen68-workflow-regression-") as temporary:
        for index, candidate in enumerate(mutations):
            candidate_path = Path(temporary) / f"candidate-{index}.yml"
            candidate_path.write_text(candidate, encoding="utf-8", newline="\n")
            expect_failure(
                lambda candidate_path=candidate_path: audit_workflow(
                    candidate_path,
                    expected_environment="aws-demo-deploy",
                    uses_oidc=True,
                    action_pins=action_pins,
                    expected_role_name="AWS_DEMO_DEPLOY_ROLE_ARN",
                    expected_confirmation="DEPLOY-AWS-DEMO",
                    expected_operation="deploy",
                ),
                DeliveryPolicyError,
            )
        validate_source = (
            REPOSITORY_ROOT / ".github/workflows/aws-demo-validate.yml"
        ).read_text(encoding="utf-8")
        validate_mutations = (
            validate_source.replace('      - "apps/web/**"\n', "", 1),
            validate_source.replace(
                "          python infra/aws/demo/scripts/"
                "frontend_delivery_regression.py\n",
                "",
                1,
            ),
        )
        for index, candidate in enumerate(validate_mutations):
            candidate_path = Path(temporary) / f"validate-{index}.yml"
            candidate_path.write_text(candidate, encoding="utf-8", newline="\n")
            expect_failure(
                lambda candidate_path=candidate_path: audit_workflow(
                    candidate_path,
                    expected_environment=None,
                    uses_oidc=False,
                    action_pins=action_pins,
                    expected_role_name=None,
                    expected_confirmation=None,
                    expected_operation=None,
                ),
                DeliveryPolicyError,
            )
    return len(mutations) + len(validate_mutations)


def prove_github_environment_gate() -> int:
    environment = {
        "can_admins_bypass": False,
        "deployment_branch_policy": {
            "custom_branch_policies": True,
            "protected_branches": False,
        },
        "name": "aws-demo-deploy",
        "protection_rules": [
            {
                "prevent_self_review": False,
                "reviewers": [
                    {
                        "reviewer": {"id": 107653306, "login": "HiRenan"},
                        "type": "User",
                    }
                ],
                "type": "required_reviewers",
            },
            {"type": "branch_policy"},
        ],
    }
    policies = {
        "branch_policies": [{"id": 1, "name": "main", "type": "branch"}],
        "total_count": 1,
    }
    audit_environment(
        environment,
        policies,
        expected_environment="aws-demo-deploy",
    )
    mutations: list[tuple[object, object]] = []
    missing_reviewers = copy.deepcopy(environment)
    cast(list[object], missing_reviewers["protection_rules"]).clear()
    mutations.append((missing_reviewers, policies))
    blocked_self_review = copy.deepcopy(environment)
    reviewer_rule = cast(
        dict[str, Any], cast(list[object], blocked_self_review["protection_rules"])[0]
    )
    reviewer_rule["prevent_self_review"] = True
    mutations.append((blocked_self_review, policies))
    admin_bypass = copy.deepcopy(environment)
    admin_bypass["can_admins_bypass"] = True
    mutations.append((admin_bypass, policies))
    missing_admin_bypass = copy.deepcopy(environment)
    missing_admin_bypass.pop("can_admins_bypass")
    mutations.append((missing_admin_bypass, policies))
    no_reviewer = copy.deepcopy(environment)
    no_reviewer_rule = cast(
        dict[str, Any], cast(list[object], no_reviewer["protection_rules"])[0]
    )
    no_reviewer_rule["reviewers"] = []
    mutations.append((no_reviewer, policies))
    wrong_reviewer_login = copy.deepcopy(environment)
    login_rule = cast(
        dict[str, Any],
        cast(list[object], wrong_reviewer_login["protection_rules"])[0],
    )
    login_reviewer = cast(list[dict[str, Any]], login_rule["reviewers"])[0]
    cast(dict[str, Any], login_reviewer["reviewer"])["login"] = "Other"
    mutations.append((wrong_reviewer_login, policies))
    wrong_reviewer_id = copy.deepcopy(environment)
    id_rule = cast(
        dict[str, Any], cast(list[object], wrong_reviewer_id["protection_rules"])[0]
    )
    id_reviewer = cast(list[dict[str, Any]], id_rule["reviewers"])[0]
    cast(dict[str, Any], id_reviewer["reviewer"])["id"] = 1
    mutations.append((wrong_reviewer_id, policies))
    extra_reviewer = copy.deepcopy(environment)
    extra_rule = cast(
        dict[str, Any], cast(list[object], extra_reviewer["protection_rules"])[0]
    )
    cast(list[object], extra_rule["reviewers"]).append(
        {"reviewer": {"id": 1, "login": "Other"}, "type": "User"}
    )
    mutations.append((extra_reviewer, policies))
    extra_protection_rule = copy.deepcopy(environment)
    cast(list[object], extra_protection_rule["protection_rules"]).append(
        {"type": "wait_timer", "wait_timer": 1}
    )
    mutations.append((extra_protection_rule, policies))
    protected_branches = copy.deepcopy(environment)
    protected_branches["deployment_branch_policy"] = {
        "custom_branch_policies": False,
        "protected_branches": True,
    }
    mutations.append((protected_branches, policies))
    broad_policy = copy.deepcopy(policies)
    cast(list[object], broad_policy["branch_policies"]).append(
        {"id": 2, "name": "release/*", "type": "branch"}
    )
    broad_policy["total_count"] = 2
    mutations.append((environment, broad_policy))
    tag_policy = copy.deepcopy(policies)
    cast(dict[str, Any], cast(list[object], tag_policy["branch_policies"])[0])[
        "type"
    ] = "tag"
    mutations.append((environment, tag_policy))
    for candidate_environment, candidate_policies in mutations:
        expect_failure(
            partial(
                audit_environment,
                candidate_environment,
                candidate_policies,
                expected_environment="aws-demo-deploy",
            ),
            GitHubEnvironmentGateError,
        )
    return 1 + len(mutations)


def prove_cognito_runbook_is_sanitized() -> int:
    identity = (REPOSITORY_ROOT / "infra/aws/demo/identity.tf").read_text(
        encoding="utf-8"
    )
    runbook = (REPOSITORY_ROOT / "infra/aws/demo/delivery/README.md").read_text(
        encoding="utf-8"
    )
    exact_flows = """explicit_auth_flows = [
    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]"""
    if (
        exact_flows not in identity
        or "ALLOW_USER_PASSWORD_AUTH" in identity
        or "ALLOW_USER_SRP_AUTH" in identity
        or identity.count("ALLOW_ADMIN_USER_PASSWORD_AUTH") != 1
        or re.search(r"(?m)^\s*access_token_validity\s*=\s*2\s*$", identity) is None
        or re.search(r"(?m)^\s*id_token_validity\s*=\s*2\s*$", identity) is None
    ):
        raise DeliveryRegressionError(
            "Cliente Cognito ampliou fluxo ou validade fora do contrato."
        )
    required_runbook = (
        "set +x",
        "set +o history",
        "export HISTFILE=/dev/null",
        "aws cognito-idp admin-create-user",
        "aws cognito-idp admin-set-user-password",
        "aws cognito-idp admin-initiate-auth",
        "aws cognito-idp admin-delete-user",
        "--query AuthenticationResult.AccessToken",
        "gh secret set AWS_DEMO_SMOKE_BEARER_TOKEN --env aws-demo-deploy",
        "gh secret delete AWS_DEMO_SMOKE_BEARER_TOKEN --env aws-demo-deploy",
        "unset AWS_DEMO_SMOKE_BEARER_TOKEN AWS_DEMO_SMOKE_PASSWORD",
    )
    if any(fragment not in runbook for fragment in required_runbook):
        raise DeliveryRegressionError(
            "Runbook Cognito perdeu uma fronteira sanitizada."
        )
    if runbook.count("--cli-input-json file:///dev/stdin") != 4:
        raise DeliveryRegressionError(
            "Runbook Cognito não entrega quatro payloads por stdin."
        )
    forbidden = (
        r"(?i)--password(?:\s|=)",
        r"(?i)--temporary-password(?:\s|=)",
        r"(?i)--auth-parameters(?:\s|=)",
        r"(?i)--body(?:\s|=)",
        r"(?im)^\s*echo\b.*(?:TOKEN|PASSWORD)",
        r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.",
    )
    if any(re.search(pattern, runbook) for pattern in forbidden):
        raise DeliveryRegressionError("Runbook Cognito persiste ou imprime segredo.")
    return 1 + len(required_runbook) + len(forbidden)


def prove_wildcard_ledger_is_complete() -> int:
    contract = mutable_contract()
    documentation = (REPOSITORY_ROOT / "infra/aws/demo/delivery/README.md").read_text(
        encoding="utf-8"
    )
    ledger = documentation.split("### Wildcards residuais, ação por ação", 1)[1]
    ledger = ledger.split("As matrizes oficiais", 1)[0]
    labels = {"deploy": "Deploy", "plan": "Plan", "teardown": "Teardown"}
    checked = 0
    for role_name, label in labels.items():
        policy = cast(dict[str, Any], role(contract, role_name)["permission_policy"])
        statements = cast(list[dict[str, Any]], policy["Statement"])
        for statement in statements:
            raw_resources = statement.get("Resource")
            resources = (
                [raw_resources]
                if type(raw_resources) is str
                else cast(list[str], raw_resources)
            )
            is_global = resources == ["*"]
            has_scoped_wildcard = any("*" in resource for resource in resources)
            if not is_global and not has_scoped_wildcard:
                continue
            sid = cast(str, statement["Sid"])
            suffix = "" if is_global else " (ARN)"
            marker = f"- {label}, `{sid}`{suffix}:"
            if ledger.count(marker) != 1:
                raise DeliveryRegressionError(
                    "Ledger de wildcard perdeu phase ou Sid residual."
                )
            segment = ledger.split(marker, 1)[1].split("\n- ", 1)[0]
            documented_actions = set(
                re.findall(r"`([a-z0-9-]+:[A-Za-z0-9*]+)`", segment)
            )
            raw_actions = statement["Action"]
            expected_actions = (
                {raw_actions}
                if type(raw_actions) is str
                else set(cast(list[str], raw_actions))
            )
            if not expected_actions <= documented_actions:
                raise DeliveryRegressionError(
                    f"Ledger de wildcard diverge em {role_name}.{sid}."
                )
            checked += len(expected_actions)
    return checked


SYNTHETIC_IDENTITY = {
    "account_id": "000000000000",
    "frontend_domain": "demo.example.invalid",
    "name_prefix": "senai-pm",
    "region": "us-east-1",
}
OLD_IMAGE = (
    "000000000000.dkr.ecr.us-east-1.amazonaws.com/senai-pm-demo/api@sha256:" + "b" * 64
)
EXPECTED_IMAGE = (
    "000000000000.dkr.ecr.us-east-1.amazonaws.com/senai-pm-demo/api@sha256:" + "c" * 64
)


def synthetic_container(image: str) -> str:
    return json.dumps(
        [
            {
                "command": [],
                "entryPoint": [],
                "environment": [
                    {
                        "name": "PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE",
                        "value": "synthetic_demo",
                    },
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
                    "command": [
                        "CMD-SHELL",
                        "synthetic http://127.0.0.1:8000/health/ready",
                    ],
                    "interval": 10,
                    "retries": 3,
                    "startPeriod": 10,
                    "timeout": 3,
                },
                "image": image,
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
                "secrets": [],
                "stopTimeout": 30,
                "user": "65532:65532",
            }
        ],
        separators=(",", ":"),
        sort_keys=True,
    )


def synthetic_attributes(
    address: str,
    *,
    image: str = OLD_IMAGE,
    revision: int = 1,
) -> dict[str, object]:
    resource_type = EXPECTED_MANAGED[address]
    values: dict[str, object] = {"id": f"synthetic-{address.replace('.', '-')}"}
    generated_ids = {
        "aws_apigatewayv2_api": "abcdefghij",
        "aws_apigatewayv2_vpc_link": "klmnopqrst",
        "aws_cognito_user_pool": "us-east-1_Synthetic01",
        "aws_route_table": "rtb-a1b2c3d4",
        "aws_route_table_association": "rtbassoc-a1b2c3d4",
        "aws_security_group": "sg-a1b2c3d4",
        "aws_subnet": "subnet-a1b2c3d4",
        "aws_vpc": "vpc-a1b2c3d4",
        "aws_vpc_endpoint": "vpce-a1b2c3d4",
        "aws_vpc_security_group_egress_rule": "sgr-a1b2c3d4",
        "aws_vpc_security_group_ingress_rule": "sgr-b1c2d3e4",
    }
    if resource_type in generated_ids:
        values["id"] = generated_ids[resource_type]
    if address == "aws_apigatewayv2_api.demo":
        values["api_endpoint"] = (
            "https://abcdefghij.execute-api.us-east-1.amazonaws.com"
        )
    if address == "aws_cloudfront_distribution.frontend":
        values["id"] = "E1SYNTHETIC01"
    if address.startswith("aws_apigatewayv2_") and address not in {
        "aws_apigatewayv2_api.demo",
        "aws_apigatewayv2_vpc_link.api",
    }:
        values["api_id"] = "abcdefghij"
    named = expected_names(SYNTHETIC_IDENTITY).get(address)
    if named is not None:
        values[named[0]] = named[1]
    if address in TAGGED_ADDRESSES:
        tags = dict(REQUIRED_TAGS)
        tag_suffix = address.split(".", maxsplit=1)[1]
        tag_suffix = tag_suffix.split("[", maxsplit=1)[0].replace("_", "-")
        tags["Name"] = f"senai-pm-demo-{tag_suffix}"
        values["tags"] = dict(tags)
        values["tags_all"] = dict(tags)
    bucket_match = re.match(
        r'^aws_s3_bucket(?:_[a-z_]+)?\.storage\["(artifacts|documents|frontend)"\]$',
        address,
    )
    if bucket_match is not None:
        bucket = expected_buckets(SYNTHETIC_IDENTITY)[bucket_match.group(1)]
        values["bucket"] = bucket
        if address.startswith("aws_s3_bucket.storage"):
            values["id"] = bucket
    if address == "aws_s3_bucket_policy.frontend":
        values["bucket"] = expected_buckets(SYNTHETIC_IDENTITY)["frontend"]
    if address == "aws_cognito_user_pool_client.demo":
        values["id"] = "syntheticclient01"
        values["user_pool_id"] = "us-east-1_Synthetic01"
    if address == "aws_cognito_user_pool_domain.demo":
        domain = expected_cognito_domain(SYNTHETIC_IDENTITY)
        values.update(
            {
                "domain": domain,
                "id": domain,
                "managed_login_version": 1,
                "user_pool_id": "us-east-1_Synthetic01",
            }
        )
    if address == "aws_ecr_lifecycle_policy.api":
        values["repository"] = "senai-pm-demo/api"
    role_policy_roles = {
        "aws_iam_role_policy.api_execution": "senai-pm-demo-api-execution",
        "aws_iam_role_policy.api_task": "senai-pm-demo-api-task",
        "aws_iam_role_policy.worker_task": "senai-pm-demo-worker-task",
    }
    if address in role_policy_roles:
        values["role"] = role_policy_roles[address]
    if address == "aws_ecs_cluster.demo":
        cluster_arn = "arn:aws:ecs:us-east-1:000000000000:cluster/senai-pm-demo"
        values["arn"] = cluster_arn
        values["id"] = cluster_arn
    if address.startswith(
        (
            "aws_route_table.private",
            "aws_security_group.",
            "aws_subnet.private",
            "aws_vpc_endpoint.",
        )
    ):
        values["vpc_id"] = "vpc-a1b2c3d4"
    if address == "aws_ecs_task_definition.api":
        values.update(
            {
                "arn": (
                    "arn:aws:ecs:us-east-1:000000000000:task-definition/"
                    f"senai-pm-demo-api:{revision}"
                ),
                "container_definitions": synthetic_container(image),
                "cpu": "256",
                "execution_role_arn": (
                    "arn:aws:iam::000000000000:role/senai-pm-demo-api-execution"
                ),
                "family": "senai-pm-demo-api",
                "memory": "512",
                "network_mode": "awsvpc",
                "requires_compatibilities": ["FARGATE"],
                "revision": revision,
                "runtime_platform": [
                    {
                        "cpu_architecture": "X86_64",
                        "operating_system_family": "LINUX",
                    }
                ],
                "task_role_arn": (
                    "arn:aws:iam::000000000000:role/senai-pm-demo-api-task"
                ),
            }
        )
    if address == "aws_ecs_service.api":
        values.update(
            {
                "cluster": ("arn:aws:ecs:us-east-1:000000000000:cluster/senai-pm-demo"),
                "desired_count": 1,
                "launch_type": "FARGATE",
                "platform_version": "1.4.0",
                "task_definition": (
                    "arn:aws:ecs:us-east-1:000000000000:task-definition/"
                    f"senai-pm-demo-api:{revision}"
                ),
            }
        )
    return values


def synthetic_plan(actions: Mapping[str, tuple[str, ...]]) -> dict[str, object]:
    resources: list[dict[str, object]] = []
    for address in sorted(EXPECTED_MANAGED):
        action = actions[address]
        before: object = synthetic_attributes(address)
        after: object = copy.deepcopy(before)
        unknown: dict[str, object] = {}
        if action == ("create",):
            before = None
        elif action == ("delete",):
            after = None
        elif address == "aws_ecs_task_definition.api" and action in {
            ("create", "delete"),
            ("delete", "create"),
        }:
            after = synthetic_attributes(address, image=EXPECTED_IMAGE, revision=2)
            cast(dict[str, object], after)["arn"] = None
            cast(dict[str, object], after)["id"] = None
            cast(dict[str, object], after)["revision"] = None
            unknown = {"arn": True, "id": True, "revision": True}
        elif address == "aws_ecs_service.api" and action == ("update",):
            after = synthetic_attributes(address, revision=2)
            cast(dict[str, object], after)["task_definition"] = None
            unknown = {"task_definition": True}
        resources.append(
            {
                "address": address,
                "mode": "managed",
                "type": EXPECTED_MANAGED[address],
                "change": {
                    "actions": list(action),
                    "after": after,
                    "after_unknown": unknown,
                    "before": before,
                },
            }
        )
    return {
        "configuration": {
            "root_module": {
                "resources": [
                    {
                        "address": "aws_ecs_service.api",
                        "expressions": {
                            "task_definition": {
                                "references": ["aws_ecs_task_definition.api.arn"]
                            }
                        },
                    }
                ]
            }
        },
        "format_version": "1.2",
        "terraform_version": "1.15.9",
        "resource_changes": resources,
    }


def synthetic_state(addresses: Iterable[str]) -> dict[str, object]:
    address_set = set(addresses)
    resources: list[dict[str, object]] = []
    attributes_by_address: dict[str, dict[str, object]] = {}
    for address in sorted(address_set):
        base = address.split("[", maxsplit=1)[0]
        resource_type, resource_name = base.split(".", maxsplit=1)
        attributes = synthetic_attributes(address, image=EXPECTED_IMAGE, revision=2)
        attributes_by_address[address] = attributes
        instance: dict[str, object] = {
            "attributes": attributes,
            "schema_version": 0,
            "sensitive_attributes": [],
        }
        if "[" in address:
            instance["index_key"] = json.loads(address[address.index("[") + 1 : -1])
        resources.append(
            {
                "instances": [instance],
                "mode": "managed",
                "name": resource_name,
                "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
                "type": resource_type,
            }
        )
    outputs: dict[str, object] = {}
    operational_addresses = {
        "aws_apigatewayv2_api.demo",
        "aws_cloudfront_distribution.frontend",
        "aws_cognito_user_pool_client.demo",
        "aws_cognito_user_pool_domain.demo",
        'aws_s3_bucket.storage["frontend"]',
    }
    if operational_addresses <= address_set:
        domain = expected_cognito_domain(SYNTHETIC_IDENTITY)
        output_values = {
            "api_base_url": attributes_by_address["aws_apigatewayv2_api.demo"][
                "api_endpoint"
            ],
            "cognito_client_id": attributes_by_address[
                "aws_cognito_user_pool_client.demo"
            ]["id"],
            "cognito_hosted_ui_origin": (
                f"https://{domain}.auth.us-east-1.amazoncognito.com"
            ),
            "frontend_bucket_name": attributes_by_address[
                'aws_s3_bucket.storage["frontend"]'
            ]["id"],
            "frontend_distribution_id": attributes_by_address[
                "aws_cloudfront_distribution.frontend"
            ]["id"],
            "api_image_reference": EXPECTED_IMAGE,
            "artifact_bucket_name": expected_buckets(SYNTHETIC_IDENTITY)["artifacts"],
            "bedrock_enabled": False,
            "cognito_user_pool_id": "us-east-1_Synthetic01",
            "cors_allowed_origin": "https://senai.maib.com.br",
            "document_bucket_name": expected_buckets(SYNTHETIC_IDENTITY)["documents"],
            "ecr_repository_url": (
                "000000000000.dkr.ecr.us-east-1.amazonaws.com/senai-pm-demo/api"
            ),
            "frontend_distribution_domain_name": "synthetic.cloudfront.net",
            "frontend_url": "https://senai.maib.com.br",
            "ingestion_dead_letter_queue_url": "https://sqs.us-east-1.amazonaws.com/synthetic-dlq",
            "ingestion_queue_url": "https://sqs.us-east-1.amazonaws.com/synthetic",
            "worker_task_role_arn": (
                "arn:aws:iam::000000000000:role/senai-pm-demo-worker-task"
            ),
        }
        if set(output_values) != set(EXPECTED_STATE_OUTPUT_TYPES):
            raise DeliveryRegressionError(
                "Fixture de outputs do state está incompleta."
            )
        outputs = {
            name: {
                "sensitive": False,
                "type": EXPECTED_STATE_OUTPUT_TYPES[name],
                "value": value,
            }
            for name, value in output_values.items()
        }
    return {
        "lineage": "00000000-0000-4000-8000-000000000000",
        "outputs": outputs,
        "resources": resources,
        "serial": 1,
        "terraform_version": "1.15.9",
        "version": 4,
    }


def state_attributes(snapshot: Mapping[str, object], address: str) -> dict[str, Any]:
    base = address.split("[", maxsplit=1)[0]
    resource_type, resource_name = base.split(".", maxsplit=1)
    matches = [
        cast(dict[str, Any], resource)
        for resource in cast(list[object], snapshot["resources"])
        if cast(dict[str, Any], resource).get("type") == resource_type
        and cast(dict[str, Any], resource).get("name") == resource_name
    ]
    if len(matches) != 1:
        raise DeliveryRegressionError("Fixture do state não possui recurso único.")
    instances = cast(list[dict[str, Any]], matches[0]["instances"])
    if "[" in address:
        expected_index = json.loads(address[address.index("[") + 1 : -1])
        instances = [
            instance
            for instance in instances
            if instance.get("index_key") == expected_index
        ]
    if len(instances) != 1:
        raise DeliveryRegressionError("Fixture do state não possui instância única.")
    return cast(dict[str, Any], instances[0]["attributes"])


def action_map(default: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    return {address: default for address in EXPECTED_MANAGED}


def prove_plan_and_state_gates() -> int:
    review = action_map(("no-op",))
    foundation = action_map(("create",))
    runtime = action_map(("no-op",))
    runtime["aws_ecs_task_definition.api"] = ("create", "delete")
    runtime["aws_ecs_service.api"] = ("update",)
    destroy = action_map(("delete",))
    audit_plan(synthetic_plan(review), mode="review", phase=None)
    audit_plan(synthetic_plan(foundation), mode="deploy", phase="foundation")
    audit_plan(synthetic_plan(review), mode="deploy", phase="foundation-ready")
    audit_plan(
        synthetic_plan(runtime),
        mode="deploy",
        phase="runtime",
        identity=SYNTHETIC_IDENTITY,
        expected_image=EXPECTED_IMAGE,
    )
    audit_plan(
        synthetic_plan(review),
        mode="deploy",
        phase="runtime",
        identity=SYNTHETIC_IDENTITY,
        expected_image=OLD_IMAGE,
    )
    audit_plan(
        synthetic_plan(destroy),
        mode="destroy",
        phase=None,
        identity=SYNTHETIC_IDENTITY,
    )
    partial_destroy = synthetic_plan(destroy)
    partial_destroy["resource_changes"] = cast(
        list[object], partial_destroy["resource_changes"]
    )[:3]
    audit_plan(
        partial_destroy,
        mode="destroy",
        phase=None,
        identity=SYNTHETIC_IDENTITY,
    )
    empty_destroy = synthetic_plan(destroy)
    empty_destroy["resource_changes"] = []
    expect_failure(
        lambda: audit_plan(
            empty_destroy,
            mode="destroy",
            phase=None,
            identity=SYNTHETIC_IDENTITY,
        ),
        DeliveryGateError,
    )

    hostile_maps: list[tuple[dict[str, tuple[str, ...]], str, str | None]] = []
    hostile_review = dict(review)
    hostile_review["aws_vpc.demo"] = ("delete",)
    hostile_maps.append((hostile_review, "review", None))
    hostile_replace = dict(review)
    hostile_replace['aws_s3_bucket.storage["frontend"]'] = ("create", "delete")
    hostile_maps.append((hostile_replace, "review", None))
    hostile_runtime = dict(runtime)
    hostile_runtime["aws_ecr_repository.api"] = ("create",)
    hostile_maps.append((hostile_runtime, "deploy", "runtime"))
    widened_runtime = dict(runtime)
    widened_runtime["aws_vpc.demo"] = ("update",)
    hostile_maps.append((widened_runtime, "deploy", "runtime"))
    hostile_foundation = dict(review)
    hostile_foundation["aws_vpc.demo"] = ("update",)
    hostile_maps.append((hostile_foundation, "deploy", "foundation-ready"))
    hostile_destroy = dict(destroy)
    hostile_destroy["aws_vpc.demo"] = ("update",)
    hostile_maps.append((hostile_destroy, "destroy", None))
    for actions, mode, phase in hostile_maps:
        expect_failure(
            lambda actions=actions, mode=mode, phase=phase: audit_plan(
                synthetic_plan(actions),
                mode=mode,
                phase=phase,
                identity=SYNTHETIC_IDENTITY if mode in {"deploy", "destroy"} else None,
                expected_image=EXPECTED_IMAGE
                if mode == "deploy" and phase == "runtime"
                else None,
            ),
            DeliveryGateError,
        )

    hostile_container = synthetic_plan(runtime)
    task_change = next(
        resource
        for resource in cast(
            list[dict[str, Any]], hostile_container["resource_changes"]
        )
        if resource["address"] == "aws_ecs_task_definition.api"
    )
    cast(dict[str, Any], task_change["change"])["after"] = synthetic_attributes(
        "aws_ecs_task_definition.api",
        image=(
            "000000000000.dkr.ecr.us-east-1.amazonaws.com/hostile/api@sha256:"
            + "d" * 64
        ),
        revision=2,
    )
    expect_failure(
        lambda: audit_plan(
            hostile_container,
            mode="deploy",
            phase="runtime",
            identity=SYNTHETIC_IDENTITY,
            expected_image=EXPECTED_IMAGE,
        ),
        DeliveryGateError,
    )
    malformed_container = synthetic_plan(runtime)
    malformed_task = next(
        resource
        for resource in cast(
            list[dict[str, Any]], malformed_container["resource_changes"]
        )
        if resource["address"] == "aws_ecs_task_definition.api"
    )
    cast(dict[str, Any], cast(dict[str, Any], malformed_task["change"])["after"])[
        "container_definitions"
    ] = "hostile"
    expect_failure(
        lambda: audit_plan(
            malformed_container,
            mode="deploy",
            phase="runtime",
            identity=SYNTHETIC_IDENTITY,
            expected_image=EXPECTED_IMAGE,
        ),
        DeliveryGateError,
    )
    wrong_digest = synthetic_plan(runtime)
    wrong_digest_task = next(
        resource
        for resource in cast(list[dict[str, Any]], wrong_digest["resource_changes"])
        if resource["address"] == "aws_ecs_task_definition.api"
    )
    cast(dict[str, Any], wrong_digest_task["change"])["after"] = synthetic_attributes(
        "aws_ecs_task_definition.api",
        image=OLD_IMAGE,
        revision=2,
    )
    expect_failure(
        lambda: audit_plan(
            wrong_digest,
            mode="deploy",
            phase="runtime",
            identity=SYNTHETIC_IDENTITY,
            expected_image=EXPECTED_IMAGE,
        ),
        DeliveryGateError,
    )
    hostile_cpu = synthetic_plan(runtime)
    cpu_change = next(
        resource
        for resource in cast(list[dict[str, Any]], hostile_cpu["resource_changes"])
        if resource["address"] == "aws_ecs_task_definition.api"
    )
    cast(dict[str, Any], cast(dict[str, Any], cpu_change["change"])["after"])["cpu"] = (
        "16384"
    )
    expect_failure(
        lambda: audit_plan(
            hostile_cpu,
            mode="deploy",
            phase="runtime",
            identity=SYNTHETIC_IDENTITY,
            expected_image=EXPECTED_IMAGE,
        ),
        DeliveryGateError,
    )
    hostile_unknown = synthetic_plan(runtime)
    unknown_change = next(
        resource
        for resource in cast(list[dict[str, Any]], hostile_unknown["resource_changes"])
        if resource["address"] == "aws_ecs_task_definition.api"
    )
    cast(dict[str, Any], unknown_change["change"])["after_unknown"] = {
        "container_definitions": True
    }
    expect_failure(
        lambda: audit_plan(
            hostile_unknown,
            mode="deploy",
            phase="runtime",
            identity=SYNTHETIC_IDENTITY,
            expected_image=EXPECTED_IMAGE,
        ),
        DeliveryGateError,
    )

    hostile_service = synthetic_plan(runtime)
    hostile_service_change = next(
        resource
        for resource in cast(list[dict[str, Any]], hostile_service["resource_changes"])
        if resource["address"] == "aws_ecs_service.api"
    )
    hostile_service_after = cast(
        dict[str, Any], cast(dict[str, Any], hostile_service_change["change"])["after"]
    )
    hostile_service_after["task_definition"] = (
        "arn:aws:ecs:us-east-1:000000000000:task-definition/hostile:99"
    )
    cast(dict[str, Any], hostile_service_change["change"])["after_unknown"] = {}
    expect_failure(
        lambda: audit_plan(
            hostile_service,
            mode="deploy",
            phase="runtime",
            identity=SYNTHETIC_IDENTITY,
            expected_image=EXPECTED_IMAGE,
        ),
        DeliveryGateError,
    )

    hostile_previous_service = synthetic_plan(runtime)
    previous_service_change = next(
        resource
        for resource in cast(
            list[dict[str, Any]], hostile_previous_service["resource_changes"]
        )
        if resource["address"] == "aws_ecs_service.api"
    )
    previous_service_before = cast(
        dict[str, Any],
        cast(dict[str, Any], previous_service_change["change"])["before"],
    )
    previous_service_before["task_definition"] = (
        "arn:aws:ecs:us-east-1:000000000000:task-definition/hostile:1"
    )
    expect_failure(
        lambda: audit_plan(
            hostile_previous_service,
            mode="deploy",
            phase="runtime",
            identity=SYNTHETIC_IDENTITY,
            expected_image=EXPECTED_IMAGE,
        ),
        DeliveryGateError,
    )

    with tempfile.TemporaryDirectory(prefix="sen68-state-regression-") as temporary:
        root = Path(temporary)
        empty = root / "empty.txt"
        empty.write_text("", encoding="utf-8", newline="\n")
        audit_state(empty, mode="fresh")
        audit_state(empty, mode="destroyed")
        complete = root / "complete.txt"
        complete.write_text(
            "\n".join(sorted(EXPECTED_MANAGED)) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        audit_state(complete, mode="existing")
        partial = root / "partial.txt"
        partial.write_text(
            "\n".join(sorted(EXPECTED_MANAGED)[1:]) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        audit_state(partial, mode="destroyable")
        expect_failure(lambda: audit_state(partial, mode="existing"), DeliveryGateError)
        extra = root / "extra.txt"
        extra.write_text(
            complete.read_text(encoding="utf-8") + "aws_instance.hostile\n",
            encoding="utf-8",
            newline="\n",
        )
        expect_failure(lambda: audit_state(extra, mode="existing"), DeliveryGateError)
        expect_failure(
            lambda: audit_state(extra, mode="destroyable"), DeliveryGateError
        )
    complete_snapshot = synthetic_state(EXPECTED_MANAGED)
    if cast(dict[str, Any], complete_snapshot["outputs"])["bedrock_enabled"] != {
        "sensitive": False,
        "type": "bool",
        "value": False,
    }:
        raise DeliveryRegressionError("Fixture perdeu o output booleano do Bedrock.")
    audit_state_snapshot(
        complete_snapshot,
        mode="existing",
        identity=SYNTHETIC_IDENTITY,
        expected_image=EXPECTED_IMAGE,
    )
    for field, hostile in (
        ("domain", "spm-00000000000000000000"),
        ("id", "spm-00000000000000000000"),
        ("user_pool_id", "us-east-1_Foreign01"),
    ):
        hostile_domain = copy.deepcopy(complete_snapshot)
        state_attributes(hostile_domain, "aws_cognito_user_pool_domain.demo")[field] = (
            hostile
        )
        expect_failure(
            lambda hostile_domain=hostile_domain: audit_state_snapshot(
                hostile_domain,
                mode="existing",
                identity=SYNTHETIC_IDENTITY,
            ),
            DeliveryGateError,
        )
    hostile_outputs = {
        "api_base_url": "https://zzzzzzzzzz.execute-api.us-east-1.amazonaws.com",
        "cognito_client_id": "foreignclient01",
        "cognito_hosted_ui_origin": (
            "https://spm-00000000000000000000.auth.us-east-1.amazoncognito.com"
        ),
        "frontend_bucket_name": "senai-pm-frontend-111111111111-us-east-1",
        "frontend_distribution_id": "E9SYNTHETIC99",
    }
    for name, hostile in hostile_outputs.items():
        poisoned = copy.deepcopy(complete_snapshot)
        output = cast(dict[str, Any], cast(dict[str, Any], poisoned["outputs"])[name])
        output["value"] = hostile
        expect_failure(
            lambda poisoned=poisoned: audit_state_snapshot(
                poisoned,
                mode="existing",
                identity=SYNTHETIC_IDENTITY,
            ),
            DeliveryGateError,
        )
    hostile_state_schema = copy.deepcopy(complete_snapshot)
    hostile_state_schema["foreign"] = True
    expect_failure(
        lambda: audit_state_snapshot(
            hostile_state_schema,
            mode="existing",
            identity=SYNTHETIC_IDENTITY,
        ),
        DeliveryGateError,
    )
    hostile_instance_schema = copy.deepcopy(complete_snapshot)
    hostile_resources = cast(list[dict[str, Any]], hostile_instance_schema["resources"])
    hostile_instances = cast(list[dict[str, Any]], hostile_resources[0]["instances"])
    hostile_instances[0]["foreign"] = True
    expect_failure(
        lambda: audit_state_snapshot(
            hostile_instance_schema,
            mode="existing",
            identity=SYNTHETIC_IDENTITY,
        ),
        DeliveryGateError,
    )
    unknown_destroy = synthetic_plan(destroy)
    unknown_destroy_resource = cast(
        list[dict[str, Any]], unknown_destroy["resource_changes"]
    )[0]
    cast(dict[str, Any], unknown_destroy_resource["change"])["after_unknown"] = {
        "id": True
    }
    expect_failure(
        lambda: audit_plan(
            unknown_destroy,
            mode="destroy",
            phase=None,
            identity=SYNTHETIC_IDENTITY,
        ),
        DeliveryGateError,
    )
    hostile_identity = dict(SYNTHETIC_IDENTITY)
    hostile_identity["self_attested"] = "true"
    expect_failure(
        lambda: audit_state_snapshot(
            complete_snapshot,
            mode="existing",
            identity=hostile_identity,
        ),
        DeliveryGateError,
    )
    foreign_snapshot = copy.deepcopy(complete_snapshot)
    foreign_resource = cast(
        dict[str, Any], cast(list[object], foreign_snapshot["resources"])[0]
    )
    foreign_instance = cast(
        dict[str, Any], cast(list[object], foreign_resource["instances"])[0]
    )
    foreign_attributes = cast(dict[str, Any], foreign_instance["attributes"])
    if "tags_all" not in foreign_attributes:
        tagged_resource = next(
            cast(dict[str, Any], resource)
            for resource in cast(list[object], foreign_snapshot["resources"])
            if "tags_all"
            in cast(
                dict[str, Any],
                cast(list[dict[str, Any]], cast(dict[str, Any], resource)["instances"])[
                    0
                ]["attributes"],
            )
        )
        foreign_attributes = cast(
            dict[str, Any],
            cast(list[dict[str, Any]], tagged_resource["instances"])[0]["attributes"],
        )
    cast(dict[str, Any], foreign_attributes["tags_all"])["Profile"] = "production"
    expect_failure(
        lambda: audit_state_snapshot(
            foreign_snapshot,
            mode="destroyable",
            identity=SYNTHETIC_IDENTITY,
        ),
        DeliveryGateError,
    )
    foreign_api_snapshot = copy.deepcopy(complete_snapshot)
    foreign_api_resource = next(
        cast(dict[str, Any], resource)
        for resource in cast(list[object], foreign_api_snapshot["resources"])
        if cast(dict[str, Any], resource).get("type") == "aws_apigatewayv2_authorizer"
    )
    foreign_api_attributes = cast(
        dict[str, Any],
        cast(list[dict[str, Any]], foreign_api_resource["instances"])[0]["attributes"],
    )
    foreign_api_attributes["api_id"] = "production"
    expect_failure(
        lambda: audit_state_snapshot(
            foreign_api_snapshot,
            mode="destroyable",
            identity=SYNTHETIC_IDENTITY,
        ),
        DeliveryGateError,
    )
    foreign_destroy = synthetic_plan(destroy)
    foreign_destroy_resource = next(
        resource
        for resource in cast(list[dict[str, Any]], foreign_destroy["resource_changes"])
        if resource["address"] == "aws_vpc.demo"
    )
    foreign_before = cast(
        dict[str, Any],
        cast(dict[str, Any], foreign_destroy_resource["change"])["before"],
    )
    cast(dict[str, Any], foreign_before["tags_all"])["Profile"] = "production"
    expect_failure(
        lambda: audit_plan(
            foreign_destroy,
            mode="destroy",
            phase=None,
            identity=SYNTHETIC_IDENTITY,
        ),
        DeliveryGateError,
    )
    foreign_api_destroy = synthetic_plan(destroy)
    foreign_api_change = next(
        resource
        for resource in cast(
            list[dict[str, Any]], foreign_api_destroy["resource_changes"]
        )
        if resource["address"] == "aws_apigatewayv2_authorizer.cognito"
    )
    foreign_api_before = cast(
        dict[str, Any],
        cast(dict[str, Any], foreign_api_change["change"])["before"],
    )
    foreign_api_before["api_id"] = "production"
    expect_failure(
        lambda: audit_plan(
            foreign_api_destroy,
            mode="destroy",
            phase=None,
            identity=SYNTHETIC_IDENTITY,
        ),
        DeliveryGateError,
    )
    return 39


def synthetic_result(name: str) -> dict[str, object]:
    result: dict[str, object] = {field: None for field in RESULT_FIELDS}
    result.update(
        {
            "abstention": None,
            "analysis_id": f"ana_synthetic_{name}",
            "citations": [],
            "diagnosis": {"code": f"synthetic_{name}", "summary": "Sintético."},
            "model_id": "model_synthetic_v1",
            "neighbors": [{"neighbor_ref": "neighbor_synthetic_01"}],
            "outcome": name,
            "prescription": None,
            "support": {"level": "sufficient", "support_score": 0.9},
            "warnings": [],
        }
    )
    if name == "documented_fault":
        result["citations"] = [{"chunk": "chunk_synthetic_01"}]
        result["prescription"] = {"summary": "Ação sintética."}
    elif name == "undocumented_fault":
        result["abstention"] = {
            "reason": "undocumented_fault",
            "message": "Recusa sintética.",
        }
        result["warnings"] = [{"code": "synthetic_warning"}]
    return result


def prove_remote_smoke_boundaries() -> int:
    endpoint = "https://abcdefghij.execute-api.us-east-1.amazonaws.com"
    validate_endpoint(endpoint, "us-east-1")
    now_epoch = int(time.time())

    def jwt(expiration: int, *, use: str = "access") -> str:
        def encode(value: Mapping[str, object]) -> str:
            return (
                urlsafe_b64encode(
                    json.dumps(value, separators=(",", ":")).encode("utf-8")
                )
                .decode("ascii")
                .rstrip("=")
            )

        return (
            encode({"alg": "RS256", "typ": "JWT"})
            + "."
            + encode(
                {
                    "exp": expiration,
                    "iat": now_epoch,
                    "token_use": use,
                }
            )
            + "."
            + "c" * 64
        )

    valid_token = jwt(now_epoch + 7_000)
    validate_token(valid_token, now_epoch=now_epoch)
    validate_readiness({"status": "ready"})
    for name in ("normal", "documented_fault", "undocumented_fault"):
        validate_scenario(name, synthetic_result(name))

    invalid_endpoints = (
        "http://abcdefghij.execute-api.us-east-1.amazonaws.com",
        "https://user@abcdefghij.execute-api.us-east-1.amazonaws.com",
        "https://abcdefghij.execute-api.us-east-1.amazonaws.com/hostile",
        "https://example.invalid",
    )
    for candidate in invalid_endpoints:
        expect_failure(
            lambda candidate=candidate: validate_endpoint(candidate, "us-east-1"),
            RemoteSmokeError,
        )
    expect_failure(lambda: validate_token(SENSITIVE_MARKER), RemoteSmokeError)
    expect_failure(
        lambda: validate_token(jwt(now_epoch + 5_999), now_epoch=now_epoch),
        RemoteSmokeError,
    )
    expect_failure(
        lambda: validate_token(jwt(now_epoch + 7_000, use="id"), now_epoch=now_epoch),
        RemoteSmokeError,
    )
    expect_failure(
        lambda: validate_readiness({"status": SENSITIVE_MARKER}), RemoteSmokeError
    )
    crossed = synthetic_result("normal")
    crossed["prescription"] = {"summary": SENSITIVE_MARKER}
    expect_failure(lambda: validate_scenario("normal", crossed), RemoteSmokeError)

    def unauthenticated_case(
        status: object,
        *,
        should_pass: bool,
    ) -> dict[str, object]:
        observed: dict[str, object] = {
            "connection_closed": 0,
            "requests": [],
            "response_closed": 0,
        }

        class FakeResponse:
            def __init__(self) -> None:
                self.status = status

            def read(self, size: int) -> object:
                del size
                observed["read_called"] = True
                raise OSError(SENSITIVE_MARKER)

            def close(self) -> None:
                observed["response_closed"] = cast(int, observed["response_closed"]) + 1

        class FakeConnection:
            def __init__(self, *args: object, **kwargs: object) -> None:
                observed["connection_args"] = args
                observed["connection_kwargs"] = kwargs

            def request(self, *args: object, **kwargs: object) -> None:
                cast(list[object], observed["requests"]).append((args, kwargs))

            def getresponse(self) -> FakeResponse:
                return FakeResponse()

            def close(self) -> None:
                observed["connection_closed"] = (
                    cast(int, observed["connection_closed"]) + 1
                )

        original_connection = remote_smoke.HTTPSConnection
        remote_smoke.HTTPSConnection = cast(Any, FakeConnection)
        try:
            if should_pass:
                require_authentication(endpoint)
            else:
                expect_failure(
                    lambda: require_authentication(endpoint), RemoteSmokeError
                )
        finally:
            remote_smoke.HTTPSConnection = original_connection
        return observed

    for status in (401, 403):
        observed = unauthenticated_case(status, should_pass=True)
        requests = cast(
            list[tuple[tuple[object, ...], dict[str, object]]], observed["requests"]
        )
        first_args, first_kwargs = requests[0]
        second_args, second_kwargs = requests[1]
        first_headers = cast(dict[str, str], first_kwargs["headers"])
        second_headers = cast(dict[str, str], second_kwargs["headers"])
        if (
            first_args[:2] != ("GET", "/health/ready")
            or second_args[:2] != ("POST", "/analysis")
            or first_kwargs.get("body") is not None
            or type(second_kwargs.get("body")) is not bytes
            or "Authorization" in first_headers
            or "Authorization" in second_headers
            or "read_called" in observed
            or observed.get("response_closed") != 2
            or observed.get("connection_closed") != 2
        ):
            raise DeliveryRegressionError(
                "Preflight anônimo enviou credencial, leu body ou omitiu POST."
            )
    for status in (200, 302, 500):
        unauthenticated_case(status, should_pass=False)
    return 15


def prove_inventory_is_fail_closed() -> int:
    query = InventoryQuery(
        name="synthetic",
        arguments=("synthetic",),
        collection_path=("Items",),
        predicate=lambda item: item == "residual",
    )

    def empty_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(0, '{"Items":[]}', "")

    scan_inventory((query,), empty_runner)

    def missing_collection_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(0, "{}", "")

    expect_failure(
        lambda: scan_inventory((query,), missing_collection_runner),
        OrphanInventoryError,
    )

    optional_query = InventoryQuery(
        name="synthetic optional",
        arguments=("synthetic",),
        collection_path=("Container", "Items"),
        predicate=lambda item: item == "residual",
        allow_missing_collection=True,
    )

    def optional_empty_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(0, '{"Container":{}}', "")

    scan_inventory((optional_query,), optional_empty_runner)
    expect_failure(
        lambda: scan_inventory((optional_query,), missing_collection_runner),
        OrphanInventoryError,
    )

    blank_output_query = InventoryQuery(
        name="synthetic optional blank",
        arguments=("synthetic",),
        collection_path=("Items",),
        predicate=lambda item: item == "residual",
        allow_missing_collection=True,
        allow_empty_output=True,
    )

    def blank_output_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(0, " \n", "")

    scan_inventory((blank_output_query,), blank_output_runner)
    expect_failure(
        lambda: scan_inventory((query,), blank_output_runner),
        OrphanInventoryError,
    )

    invalid_blank_query = InventoryQuery(
        name="synthetic invalid blank",
        arguments=("synthetic",),
        collection_path=("Items",),
        predicate=lambda item: item == "residual",
        allow_empty_output=True,
    )
    expect_failure(
        lambda: scan_inventory((invalid_blank_query,), blank_output_runner),
        OrphanInventoryError,
    )

    def residual_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(0, '{"Items":["residual"]}', SENSITIVE_MARKER)

    expect_failure(
        lambda: scan_inventory((query,), residual_runner), OrphanInventoryError
    )

    def failed_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(1, SENSITIVE_MARKER, SENSITIVE_MARKER)

    result = failed_runner(("synthetic",))
    if SENSITIVE_MARKER in repr(result):
        raise DeliveryRegressionError("CommandResult expõe stdout ou stderr em repr.")
    expect_failure(
        lambda: scan_inventory((query,), failed_runner), OrphanInventoryError
    )

    actual_queries = inventory_queries(
        account_id="000000000000",
        region="us-east-1",
        name_prefix="senai-pm",
        frontend_domain="demo.example.invalid",
    )
    route_queries = tuple(
        candidate
        for candidate in actual_queries
        if candidate.name == "EC2 route tables"
    )
    if (
        len(route_queries) != 1
        or "describe-route-tables" not in route_queries[0].arguments
    ):
        raise DeliveryRegressionError("Inventário não fixa scan de route tables.")

    bucket_queries = tuple(
        candidate for candidate in actual_queries if candidate.name == "S3 buckets"
    )
    if len(bucket_queries) != 1:
        raise DeliveryRegressionError("Inventário não fixa scan de buckets S3.")

    blank_list_names = {
        "SQS queues",
        "AWS Budgets",
        "CloudFront distributions",
        "CloudFront origin access controls",
    }
    blank_list_queries = tuple(
        candidate for candidate in actual_queries if candidate.allow_empty_output
    )
    if {
        candidate.name for candidate in blank_list_queries
    } != blank_list_names or not all(
        candidate.allow_missing_collection and candidate.allow_empty_output
        for candidate in blank_list_queries
    ):
        raise DeliveryRegressionError(
            "Inventário não limita respostas vazias às listas AWS aprovadas."
        )
    origin_access_queries = tuple(
        candidate
        for candidate in actual_queries
        if candidate.name == "CloudFront origin access controls"
    )
    if len(origin_access_queries) != 1 or origin_access_queries[0].arguments != (
        "cloudfront",
        "list-origin-access-controls",
    ):
        raise DeliveryRegressionError(
            "Inventário não usa a operação CloudFront canônica."
        )

    domain_queries = tuple(
        candidate
        for candidate in actual_queries
        if candidate.name == "Cognito hosted UI domain"
    )
    expected_domain = expected_cognito_domain(
        {
            "account_id": "000000000000",
            "frontend_domain": "demo.example.invalid",
            "name_prefix": "senai-pm",
            "region": "us-east-1",
        }
    )
    if (
        len(domain_queries) != 1
        or domain_queries[0].arguments
        != (
            "cognito-idp",
            "describe-user-pool-domain",
            "--domain",
            expected_domain,
            "--region",
            "us-east-1",
        )
        or not domain_queries[0].single_object
        or not domain_queries[0].allow_not_found
    ):
        raise DeliveryRegressionError("Inventário não fixa o domínio Cognito opaco.")

    def absent_domain_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(255, "", "", missing=True)

    scan_inventory(domain_queries, absent_domain_runner)

    if not orphan_inventory.is_canonical_domain_not_found(
        domain_queries[0].arguments,
        255,
        "An error occurred (ResourceNotFoundException) when calling the "
        "DescribeUserPoolDomain operation: synthetic domain not found.",
    ) or orphan_inventory.is_canonical_domain_not_found(
        domain_queries[0].arguments,
        255,
        "An error occurred (AccessDeniedException) when calling the "
        "DescribeUserPoolDomain operation: denied.",
    ):
        raise DeliveryRegressionError("Classificação Cognito não separa not-found.")

    def residual_domain_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(
            0,
            json.dumps({"DomainDescription": {"Domain": expected_domain}}),
            "",
        )

    expect_failure(
        lambda: scan_inventory(domain_queries, residual_domain_runner),
        OrphanInventoryError,
    )

    def malformed_domain_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(0, '{"DomainDescription":[]}', "")

    expect_failure(
        lambda: scan_inventory(domain_queries, malformed_domain_runner),
        OrphanInventoryError,
    )

    def denied_domain_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(255, SENSITIVE_MARKER, "", missing=False)

    expect_failure(
        lambda: scan_inventory(domain_queries, denied_domain_runner),
        OrphanInventoryError,
    )

    def malformed_bucket_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(0, '{"Buckets":[{}]}', "")

    expect_failure(
        lambda: scan_inventory(bucket_queries, malformed_bucket_runner),
        OrphanInventoryError,
    )

    def route_residual_runner(arguments: tuple[str, ...]) -> CommandResult:
        del arguments
        return CommandResult(0, '{"RouteTables":[{}]}', "")

    expect_failure(
        lambda: scan_inventory(route_queries, route_residual_runner),
        OrphanInventoryError,
    )

    inventory_environment = safe_environment(
        {
            "AWS_ACCESS_KEY_ID": "synthetic-access-key",
            "AWS_SECRET_ACCESS_KEY": SENSITIVE_MARKER,
            "HTTPS_PROXY": SENSITIVE_MARKER,
            "PATH": os.environ["PATH"],
            "SSL_CERT_FILE": SENSITIVE_MARKER,
        }
    )
    if (
        "HTTPS_PROXY" in inventory_environment
        or "SSL_CERT_FILE" in inventory_environment
    ):
        raise DeliveryRegressionError("Inventário herdou proxy ou trust root hostil.")
    if SENSITIVE_MARKER in repr(inventory_environment):
        raise DeliveryRegressionError("Ambiente do inventário expõe credencial.")
    return 15


def prove_inventory_capture_is_bounded() -> int:
    def synthetic_which(name: str) -> str:
        del name
        return sys.executable

    original_which = orphan_inventory.shutil.which
    original_output_limit = orphan_inventory.MAX_AWS_OUTPUT_BYTES
    original_error_limit = orphan_inventory.MAX_AWS_ERROR_BYTES
    orphan_inventory.shutil.which = synthetic_which
    orphan_inventory.MAX_AWS_OUTPUT_BYTES = 64
    orphan_inventory.MAX_AWS_ERROR_BYTES = 16
    try:
        result = orphan_inventory.command_runner(
            ("-c", 'print(\'{"Items":[]}\', end="")')
        )
        if result.returncode != 0 or result.stdout != '{"Items":[]}':
            raise DeliveryRegressionError("Captura do inventário alterou JSON válido.")
        expect_failure(
            lambda: orphan_inventory.command_runner(("-c", 'print("x" * 128, end="")')),
            OrphanInventoryError,
        )
        expect_failure(
            lambda: orphan_inventory.command_runner(
                (
                    "-c",
                    "import sys;sys.stderr.write('SENSITIVE_SYNTHETIC_MARKER'*4)",
                )
            ),
            OrphanInventoryError,
        )
    finally:
        orphan_inventory.shutil.which = original_which
        orphan_inventory.MAX_AWS_OUTPUT_BYTES = original_output_limit
        orphan_inventory.MAX_AWS_ERROR_BYTES = original_error_limit
    return 3


def synthetic_environment() -> dict[str, str]:
    return {
        "AWS_DEMO_ACCOUNT_ID": "000000000000",
        "AWS_DEMO_SESSION_EXPIRATION": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 7_000)
        ),
        "AWS_DEMO_SOURCE_SHA": "a" * 40,
        "AWS_ACCESS_KEY_ID": "synthetic-access-key",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
        "AWS_SECRET_ACCESS_KEY": "synthetic-secret-key",
        "AWS_SESSION_TOKEN": "synthetic-session-token",
        "TF_STATE_BUCKET": "synthetic-state-bucket",
        "TF_STATE_KEY": "demo/sen-67/terraform.tfstate",
        "TF_VAR_availability_zone": "us-east-1a",
        "TF_VAR_aws_account_id": "000000000000",
        "TF_VAR_aws_region": "us-east-1",
        "TF_VAR_budget_alert_email": "synthetic@example.invalid",
        "TF_VAR_enable_bedrock": "false",
        "TF_VAR_frontend_certificate_arn": (
            "arn:aws:acm:us-east-1:000000000000:certificate/"
            "00000000-0000-4000-8000-000000000000"
        ),
        "TF_VAR_frontend_domain_name": "senai.maib.com.br",
        "TF_VAR_monthly_budget_usd": "15",
        "TF_VAR_name_prefix": "senai-pm",
        "TF_VAR_owner": "demo-team",
        "PATH": os.environ["PATH"],
    }


def prove_hostile_environment_rejected() -> int:
    base_environment = synthetic_environment()
    approved = validated_configuration(base_environment)
    if (
        approved.get("state_bucket") != "synthetic-state-bucket"
        or approved.get("state_key") != "demo/sen-67/terraform.tfstate"
    ):
        raise DeliveryRegressionError("Controller vars do state foram alteradas.")
    raw_expiration = base_environment["AWS_DEMO_SESSION_EXPIRATION"]
    json_string_environment = dict(base_environment)
    json_string_environment["AWS_DEMO_SESSION_EXPIRATION"] = json.dumps(raw_expiration)
    json_string_approved = validated_configuration(json_string_environment)
    if json_string_approved.get("session_expiration_epoch") != approved.get(
        "session_expiration_epoch"
    ):
        raise DeliveryRegressionError(
            "Expiração JSON-string alterou a sessão OIDC aprovada."
        )

    hostile_expirations: tuple[object, ...] = (
        json.dumps(raw_expiration).replace("Z", r"\u005a"),
        f" {json.dumps(raw_expiration)}",
        json.dumps({"expiration": raw_expiration}),
        json.dumps([raw_expiration]),
        "null",
        json.dumps(7_000),
        json.dumps(json.dumps(raw_expiration)),
        f'"{raw_expiration}',
        json.dumps(SENSITIVE_MARKER),
        json.dumps("2000-01-01T00:00:00Z"),
        json.dumps("2099-01-01T00:00:00Z"),
        json.dumps(raw_expiration.replace("Z", "+00:00")),
        7_000,
    )
    for hostile_expiration in hostile_expirations:
        candidate = cast(dict[str, Any], synthetic_environment())
        candidate["AWS_DEMO_SESSION_EXPIRATION"] = hostile_expiration
        expect_failure(
            lambda candidate=candidate: validated_configuration(
                cast(Mapping[str, str], candidate)
            ),
            AwsDeliveryError,
        )
    near_controller = synthetic_environment()
    near_controller["TF_STATE_BUCKET_EXTRA"] = SENSITIVE_MARKER
    expect_failure(lambda: validated_configuration(near_controller), AwsDeliveryError)
    permanent_credentials = synthetic_environment()
    permanent_credentials.pop("AWS_SESSION_TOKEN")
    expect_failure(
        lambda: validated_configuration(permanent_credentials), AwsDeliveryError
    )
    crossed_region = synthetic_environment()
    crossed_region["AWS_DEFAULT_REGION"] = "us-west-2"
    expect_failure(lambda: validated_configuration(crossed_region), AwsDeliveryError)
    wrong_region = synthetic_environment()
    wrong_region.update(
        {
            "AWS_DEFAULT_REGION": "us-west-2",
            "AWS_REGION": "us-west-2",
            "TF_VAR_availability_zone": "us-west-2a",
            "TF_VAR_aws_region": "us-west-2",
        }
    )
    expect_failure(lambda: validated_configuration(wrong_region), AwsDeliveryError)
    wrong_domain = synthetic_environment()
    wrong_domain["TF_VAR_frontend_domain_name"] = "other.example.invalid"
    expect_failure(lambda: validated_configuration(wrong_domain), AwsDeliveryError)
    expired_session = synthetic_environment()
    expired_session["AWS_DEMO_SESSION_EXPIRATION"] = "2000-01-01T00:00:00Z"
    expect_failure(lambda: validated_configuration(expired_session), AwsDeliveryError)
    self_attested_session = synthetic_environment()
    self_attested_session["AWS_DEMO_SESSION_EXPIRATION"] = "2099-01-01T00:00:00Z"
    expect_failure(
        lambda: validated_configuration(self_attested_session), AwsDeliveryError
    )

    environment = synthetic_environment()
    environment["TF_CLI_ARGS"] = SENSITIVE_MARKER
    expect_failure(lambda: validated_configuration(environment), AwsDeliveryError)

    host = synthetic_environment()
    host.update(
        {
            "AWS_ACCESS_KEY_ID": "synthetic-access-key",
            "AWS_SECRET_ACCESS_KEY": SENSITIVE_MARKER,
            "AWS_SESSION_TOKEN": "synthetic-session-token",
            "BASH_ENV": SENSITIVE_MARKER,
            "CURL_CA_BUNDLE": SENSITIVE_MARKER,
            "DOCKER_CONFIG": SENSITIVE_MARKER,
            "DOCKER_HOST": SENSITIVE_MARKER,
            "GIT_CONFIG_SYSTEM": SENSITIVE_MARKER,
            "LD_PRELOAD": SENSITIVE_MARKER,
            "PYTHONPATH": SENSITIVE_MARKER,
            "REQUESTS_CA_BUNDLE": SENSITIVE_MARKER,
            "SSL_CERT_DIR": SENSITIVE_MARKER,
            "SSL_CERT_FILE": SENSITIVE_MARKER,
        }
    )
    with tempfile.TemporaryDirectory(prefix="sen68-env-regression-") as temporary:
        child = child_environment(
            host,
            digest="sha256:" + "a" * 64,
            desired_count=0,
            isolation_root=Path(temporary),
        )
        forbidden = {
            "BASH_ENV",
            "CURL_CA_BUNDLE",
            "DOCKER_HOST",
            "GIT_CONFIG_SYSTEM",
            "LD_PRELOAD",
            "PYTHONPATH",
            "REQUESTS_CA_BUNDLE",
            "SSL_CERT_DIR",
            "SSL_CERT_FILE",
        }
        if forbidden & set(child) or child["DOCKER_CONFIG"] == SENSITIVE_MARKER:
            raise DeliveryRegressionError("Ambiente filho herdou um runtime hostil.")
        if SENSITIVE_MARKER in repr(child):
            raise DeliveryRegressionError("Ambiente filho expõe credencial em repr.")
    tls_previous = {
        name: os.environ.get(name) for name in remote_smoke.FORBIDDEN_TLS_ENVIRONMENT
    }
    try:
        for name in remote_smoke.FORBIDDEN_TLS_ENVIRONMENT:
            for candidate in remote_smoke.FORBIDDEN_TLS_ENVIRONMENT:
                os.environ.pop(candidate, None)
            os.environ[name] = SENSITIVE_MARKER
            expect_failure(remote_smoke.verified_tls_context, RemoteSmokeError)
    finally:
        for name, value in tls_previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return 10 + len(hostile_expirations) + len(remote_smoke.FORBIDDEN_TLS_ENVIRONMENT)


def prove_final_profile_precedes_operations() -> int:
    approved = validated_configuration(synthetic_environment())
    hostile_configurations = []
    wrong_region = dict(approved)
    wrong_region["region"] = "us-west-2"
    hostile_configurations.append(wrong_region)
    wrong_domain = dict(approved)
    wrong_domain["frontend_domain"] = "other.example.invalid"
    hostile_configurations.append(wrong_domain)

    observed_external_calls: list[str] = []

    def forbidden_external(*args: object, **kwargs: object) -> None:
        del args, kwargs
        observed_external_calls.append("external")
        raise DeliveryRegressionError("Gate final ocorreu depois de efeito externo.")

    boundaries = {
        "capture_silent": aws_delivery.capture_silent,
        "child_environment": aws_delivery.child_environment,
        "command_runner": aws_delivery.command_runner,
        "publish_frontend": aws_delivery.publish_frontend,
        "require_sen46_baseline": aws_delivery.require_sen46_baseline,
        "run_published_smoke": aws_delivery.run_published_smoke,
        "run_silent": aws_delivery.run_silent,
        "run_smoke": aws_delivery.run_smoke,
        "terraform_apply": aws_delivery.terraform_apply,
        "terraform_init": aws_delivery.terraform_init,
        "terraform_plan": aws_delivery.terraform_plan,
    }
    operations = (
        aws_delivery.plan_operation,
        aws_delivery.foundation_operation,
        aws_delivery.deploy_operation,
        aws_delivery.teardown_operation,
    )
    try:
        for name in boundaries:
            aws_delivery.__dict__[name] = forbidden_external
        with tempfile.TemporaryDirectory(
            prefix="sen75-final-profile-regression-"
        ) as temporary:
            root = Path(temporary)
            baseline = tuple(root.iterdir())
            for operation in operations:
                for configuration in hostile_configurations:
                    expect_failure(
                        lambda operation=operation, configuration=configuration: (
                            operation(configuration, root)
                        ),
                        AwsDeliveryError,
                    )
                    if observed_external_calls or tuple(root.iterdir()) != baseline:
                        raise DeliveryRegressionError(
                            "Perfil final hostil causou efeito externo ou mutação."
                        )
    finally:
        for name, original in boundaries.items():
            aws_delivery.__dict__[name] = original
    return len(operations) * len(hostile_configurations)


def prove_buildx_configuration_isolated() -> int:
    with tempfile.TemporaryDirectory(prefix="sen68-buildx-regression-") as temporary:
        runner_temp = Path(temporary).resolve()
        docker_config = runner_temp / "sen68-buildx-123456789-2"
        docker_config.mkdir()
        approved = synthetic_environment()
        approved.update(
            {
                "AWS_DEMO_BUILDX_BUILDER": "sen68-123456789-2",
                "AWS_DEMO_BUILDX_DOCKER_CONFIG": str(docker_config),
                "GITHUB_RUN_ATTEMPT": "2",
                "GITHUB_RUN_ID": "123456789",
                "RUNNER_TEMP": str(runner_temp),
            }
        )
        builder, validated_config = validated_buildx_configuration(approved)
        child = child_environment(
            approved,
            digest="sha256:" + "a" * 64,
            desired_count=0,
            isolation_root=runner_temp / "child",
            buildx_builder=builder,
            docker_config=validated_config,
        )
        if child.get("BUILDX_BUILDER") != "sen68-123456789-2" or child.get(
            "DOCKER_CONFIG"
        ) != str(docker_config):
            raise DeliveryRegressionError("Ambiente filho perdeu o Buildx aprovado.")

        hostile_cases: list[dict[str, str]] = []
        wrong_builder = dict(approved)
        wrong_builder["AWS_DEMO_BUILDX_BUILDER"] = "default"
        hostile_cases.append(wrong_builder)
        traversing_builder = dict(approved)
        traversing_builder["AWS_DEMO_BUILDX_BUILDER"] = "../../hostile"
        hostile_cases.append(traversing_builder)
        wrong_config = dict(approved)
        sibling = runner_temp / "sen68-buildx-123456789-3"
        sibling.mkdir()
        wrong_config["AWS_DEMO_BUILDX_DOCKER_CONFIG"] = str(sibling)
        hostile_cases.append(wrong_config)
        ambient_builder = dict(approved)
        ambient_builder["BUILDX_BUILDER"] = SENSITIVE_MARKER
        hostile_cases.append(ambient_builder)
        ambient_config = dict(approved)
        ambient_config["DOCKER_CONFIG"] = SENSITIVE_MARKER
        hostile_cases.append(ambient_config)
        near_name = dict(approved)
        near_name["AWS_DEMO_BUILDX_BUILDER_EXTRA"] = SENSITIVE_MARKER
        hostile_cases.append(near_name)
        for candidate in hostile_cases:
            expect_failure(
                lambda candidate=candidate: validated_buildx_configuration(candidate),
                AwsDeliveryError,
            )
    return 8


def prove_git_baseline_environment_isolated() -> int:
    tracked_names = (
        "AWS_DEMO_SEN46_BASELINE_SHA",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_OBJECT_DIRECTORY",
        "GIT_REPLACE_REF_BASE",
    )
    previous = {name: os.environ.get(name) for name in tracked_names}
    observed: dict[str, object] = {}
    original_which = aws_delivery.shutil.which
    original_run = aws_delivery.subprocess.run

    def fake_which(name: str) -> str:
        del name
        return sys.executable

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["arguments"] = args[0]
        observed["environment"] = kwargs.get("env")
        return subprocess.CompletedProcess([b"git"], 0)

    try:
        os.environ["AWS_DEMO_SEN46_BASELINE_SHA"] = "b" * 40
        for name in tracked_names[1:]:
            os.environ[name] = SENSITIVE_MARKER
        aws_delivery.shutil.which = fake_which
        aws_delivery.subprocess.run = fake_run
        aws_delivery.require_sen46_baseline({"source_sha": "a" * 40})
    finally:
        aws_delivery.shutil.which = original_which
        aws_delivery.subprocess.run = original_run
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    environment = observed.get("environment")
    arguments = cast(list[str], observed.get("arguments"))
    if (
        type(environment) is not aws_delivery.DeliveryEnvironment
        or SENSITIVE_MARKER in cast(Mapping[str, str], environment).values()
        or SENSITIVE_MARKER in repr(environment)
        or aws_delivery.SEN46_BASELINE_SHA not in arguments
        or "b" * 40 in arguments
    ):
        raise DeliveryRegressionError(
            "Validação Git herdou configuração hostil ou baseline autoatestada."
        )
    return 1


def prove_terraform_init_is_readonly() -> int:
    observed: dict[str, object] = {}
    original_run_silent = aws_delivery.run_silent

    def fake_run_silent(
        name: str,
        arguments: Sequence[str],
        **kwargs: object,
    ) -> None:
        observed["name"] = name
        observed["arguments"] = tuple(arguments)
        observed["environment"] = kwargs.get("environment")

    configuration = {
        "region": "us-east-1",
        "state_bucket": "synthetic-state-bucket",
        "state_key": "demo/sen-67/terraform.tfstate",
    }
    environment = {"PATH": os.environ["PATH"]}
    try:
        aws_delivery.run_silent = fake_run_silent
        aws_delivery.terraform_init(configuration, environment)
    finally:
        aws_delivery.run_silent = original_run_silent
    arguments = cast(tuple[str, ...], observed.get("arguments"))
    if (
        observed.get("name") != "terraform"
        or observed.get("environment") is not environment
        or arguments.count("-lockfile=readonly") != 1
        or "-backend-config=use_lockfile=true" not in arguments
        or "-reconfigure" not in arguments
    ):
        raise DeliveryRegressionError("Init Terraform não preserva o lock aprovado.")
    return 1


def prove_missing_state_is_canonical() -> int:
    original_capture_silent = aws_delivery.capture_silent
    configuration = {
        "account_id": "000000000000",
        "state_bucket": "synthetic-state-bucket",
        "state_key": aws_delivery.STATE_KEY,
    }
    pull_content = b""
    state_present = False
    listing_override: bytes | None = None

    def fake_capture_silent(
        name: str,
        arguments: Sequence[str],
        **kwargs: object,
    ) -> bytes:
        del kwargs
        if name == "aws":
            expected = (
                "s3api",
                "list-objects-v2",
                "--bucket",
                configuration["state_bucket"],
                "--prefix",
                configuration["state_key"],
                "--max-keys",
                "2",
                "--expected-bucket-owner",
                configuration["account_id"],
                "--no-paginate",
                "--output",
                "json",
                "--no-cli-pager",
            )
            if tuple(arguments) != expected:
                raise DeliveryRegressionError(
                    "Consulta de presença do state divergiu do escopo exato."
                )
            if listing_override is not None:
                return listing_override
            contents = [{"Key": configuration["state_key"]}] if state_present else []
            return json.dumps(
                {
                    "Contents": contents,
                    "IsTruncated": False,
                    "KeyCount": len(contents),
                }
            ).encode("utf-8")
        if name != "terraform":
            raise DeliveryRegressionError("Consulta de state usou executável inválido.")
        if arguments[-2:] == ["state", "pull"]:
            return pull_content
        if arguments[-2:] == ["state", "list"]:
            return b"aws_vpc.demo\n"
        raise DeliveryRegressionError("Consulta Terraform inesperada no state.")

    try:
        aws_delivery.capture_silent = fake_capture_silent
        with tempfile.TemporaryDirectory(prefix="sen68-state-pull-") as temporary:
            root = Path(temporary)
            missing, missing_snapshot = aws_delivery.terraform_state_list(
                configuration, {}, root, suffix="missing", allow_missing=True
            )
            if missing.read_bytes() != b"" or missing_snapshot is not None:
                raise DeliveryRegressionError("State ausente não ficou canônico.")
            expect_failure(
                lambda: aws_delivery.terraform_state_list(
                    configuration, {}, root, suffix="required", allow_missing=False
                ),
                AwsDeliveryError,
            )
            listing_override = json.dumps(
                {"Contents": [], "IsTruncated": True, "KeyCount": 0}
            ).encode("utf-8")
            expect_failure(
                lambda: aws_delivery.terraform_state_list(
                    configuration, {}, root, suffix="truncated", allow_missing=True
                ),
                AwsDeliveryError,
            )
            listing_override = json.dumps(
                {
                    "Contents": [{"Key": f"{configuration['state_key']}.tflock"}],
                    "IsTruncated": False,
                    "KeyCount": 1,
                }
            ).encode("utf-8")
            expect_failure(
                lambda: aws_delivery.terraform_state_list(
                    configuration, {}, root, suffix="unexpected", allow_missing=True
                ),
                AwsDeliveryError,
            )
            listing_override = b'{"IsTruncated":false,"KeyCount":'
            expect_failure(
                lambda: aws_delivery.terraform_state_list(
                    configuration, {}, root, suffix="invalid", allow_missing=True
                ),
                AwsDeliveryError,
            )
            listing_override = None
            state_present = True
            pull_content = b""
            expect_failure(
                lambda: aws_delivery.terraform_state_list(
                    configuration, {}, root, suffix="empty", allow_missing=True
                ),
                AwsDeliveryError,
            )
            pull_content = b' {"version":4} '
            expect_failure(
                lambda: aws_delivery.terraform_state_list(
                    configuration, {}, root, suffix="malformed", allow_missing=True
                ),
                AwsDeliveryError,
            )
            pull_content = json.dumps(
                {
                    "lineage": "00000000-0000-4000-8000-000000000000",
                    "outputs": {},
                    "resources": [{"private": SENSITIVE_MARKER}],
                    "serial": 1,
                    "version": 4,
                }
            ).encode("utf-8")
            existing, existing_snapshot = aws_delivery.terraform_state_list(
                configuration, {}, root, suffix="existing", allow_missing=True
            )
            if (
                existing.read_text(encoding="utf-8") != "aws_vpc.demo\n"
                or any(
                    SENSITIVE_MARKER in path.read_text(encoding="utf-8")
                    for path in root.iterdir()
                    if path.is_file()
                )
                or existing_snapshot is None
            ):
                raise DeliveryRegressionError(
                    "Snapshot privado do state foi persistido."
                )
    finally:
        aws_delivery.capture_silent = original_capture_silent
    return 8


def prove_plan_requires_existing_foundation() -> int:
    original_child_environment = aws_delivery.child_environment
    original_terraform_init = aws_delivery.terraform_init
    original_state_list = aws_delivery.terraform_state_list
    original_terraform_plan = aws_delivery.terraform_plan
    observed: dict[str, object] = {"planned": False}

    def fake_child_environment(*args: object, **kwargs: object) -> dict[str, str]:
        del args, kwargs
        return {}

    def fake_terraform_init(*args: object, **kwargs: object) -> None:
        del args, kwargs

    def missing_state(
        configuration: Mapping[str, str],
        environment: Mapping[str, str],
        temporary: Path,
        *,
        suffix: str,
        allow_missing: bool = False,
    ) -> Path:
        del configuration, environment, temporary, suffix
        observed["allow_missing"] = allow_missing
        raise AwsDeliveryError("State sintético ausente.")

    def forbidden_plan(*args: object, **kwargs: object) -> tuple[Path, Path]:
        del args, kwargs
        observed["planned"] = True
        raise DeliveryRegressionError("Plan alcançou state ausente.")

    try:
        aws_delivery.child_environment = fake_child_environment
        aws_delivery.terraform_init = fake_terraform_init
        aws_delivery.terraform_state_list = missing_state
        aws_delivery.terraform_plan = forbidden_plan
        with tempfile.TemporaryDirectory(prefix="sen68-plan-state-") as temporary:
            expect_failure(
                lambda: aws_delivery.plan_operation(
                    {
                        "frontend_domain": "senai.maib.com.br",
                        "region": "us-east-1",
                    },
                    Path(temporary),
                ),
                AwsDeliveryError,
            )
    finally:
        aws_delivery.child_environment = original_child_environment
        aws_delivery.terraform_init = original_terraform_init
        aws_delivery.terraform_state_list = original_state_list
        aws_delivery.terraform_plan = original_terraform_plan
    if observed != {"allow_missing": False, "planned": False}:
        raise DeliveryRegressionError("Plan aceitou state anterior à fundação.")
    return 1


def prove_bounded_capture_and_strict_json() -> int:
    strict_json('{"digest":"synthetic"}', context="Sintético")
    expect_failure(
        lambda: strict_json('{"digest":"a","digest":"b"}', context="Sintético"),
        AwsDeliveryError,
    )
    expect_failure(
        lambda: strict_json('{"value":NaN}', context="Sintético"),
        AwsDeliveryError,
    )

    def synthetic_executable(name: str) -> Path:
        del name
        return Path(sys.executable)

    original_executable = aws_delivery.executable
    aws_delivery.executable = synthetic_executable
    try:
        result = capture_silent(
            "aws",
            ("-c", "print('ok', end='')"),
            environment=os.environ,
            timeout_seconds=10,
            max_bytes=8,
        )
        if result != b"ok":
            raise DeliveryRegressionError("Captura externa alterou a saída aprovada.")
        expect_failure(
            lambda: capture_silent(
                "aws",
                ("-c", "import sys;sys.stdout.write('x'*32)"),
                environment=os.environ,
                timeout_seconds=10,
                max_bytes=8,
            ),
            AwsDeliveryError,
        )
        expect_failure(
            lambda: capture_silent(
                "aws",
                (
                    "-c",
                    "import sys;sys.stderr.write('SENSITIVE_SYNTHETIC_MARKER'*4)",
                ),
                environment=os.environ,
                timeout_seconds=10,
                max_bytes=8,
            ),
            AwsDeliveryError,
        )
    finally:
        aws_delivery.executable = original_executable
    return 5


def prove_immutable_ecr_recovery() -> int:
    account_id = "000000000000"
    repository_name = "senai-pm-demo/api"
    image_tag = "sha-" + "a" * 40
    digest = "sha256:" + "b" * 64
    present = {
        "images": [
            {
                "imageId": {"imageDigest": digest, "imageTag": image_tag},
                "registryId": account_id,
                "repositoryName": repository_name,
            }
        ],
        "failures": [],
    }
    absent = {
        "images": [],
        "failures": [
            {"failureCode": "ImageNotFound", "imageId": {"imageTag": image_tag}}
        ],
    }
    if (
        parse_ecr_lookup(
            json.dumps(present),
            account_id=account_id,
            repository_name=repository_name,
            image_tag=image_tag,
        )
        != digest
        or parse_ecr_lookup(
            json.dumps(absent),
            account_id=account_id,
            repository_name=repository_name,
            image_tag=image_tag,
        )
        is not None
    ):
        raise DeliveryRegressionError("Lookup ECR alterou presença ou ausência.")

    ambiguous = copy.deepcopy(present)
    cast(list[object], ambiguous["failures"]).append(absent["failures"][0])
    expect_failure(
        lambda: parse_ecr_lookup(
            json.dumps(ambiguous),
            account_id=account_id,
            repository_name=repository_name,
            image_tag=image_tag,
        ),
        AwsDeliveryError,
    )
    hostile_failure = copy.deepcopy(absent)
    cast(dict[str, object], cast(list[object], hostile_failure["failures"])[0])[
        "failureCode"
    ] = SENSITIVE_MARKER
    expect_failure(
        lambda: parse_ecr_lookup(
            json.dumps(hostile_failure),
            account_id=account_id,
            repository_name=repository_name,
            image_tag=image_tag,
        ),
        AwsDeliveryError,
    )
    invalid_digest = copy.deepcopy(present)
    invalid_image = cast(
        dict[str, object], cast(list[object], invalid_digest["images"])[0]
    )
    cast(dict[str, object], invalid_image["imageId"])["imageDigest"] = "sha256:bad"
    expect_failure(
        lambda: parse_ecr_lookup(
            json.dumps(invalid_digest),
            account_id=account_id,
            repository_name=repository_name,
            image_tag=image_tag,
        ),
        AwsDeliveryError,
    )

    configuration = {
        "account_id": account_id,
        "name_prefix": "senai-pm",
        "region": "us-east-1",
        "session_expiration_epoch": str(int(time.time()) + 7_000),
        "source_sha": "a" * 40,
    }
    repository = f"{account_id}.dkr.ecr.us-east-1.amazonaws.com/{repository_name}"
    original_output = aws_delivery.terraform_output
    original_lookup = aws_delivery.published_image_digest
    original_capture = aws_delivery.capture_silent
    original_run = aws_delivery.run_silent
    capture_calls: list[tuple[str, ...]] = []
    run_calls: list[tuple[str, ...]] = []

    def fake_output(
        environment: Mapping[str, str], name: str, *, max_bytes: int = 4096
    ) -> str:
        del environment, max_bytes
        if name != "ecr_repository_url":
            raise DeliveryRegressionError("Output ECR inesperado no teste.")
        return repository

    def reuse_lookup(
        configuration: Mapping[str, str],
        environment: Mapping[str, str],
        *,
        image_tag: str,
    ) -> str:
        del configuration, environment
        if image_tag != "sha-" + "a" * 40:
            raise DeliveryRegressionError("Tag ECR não é derivada do commit.")
        return digest

    lookup_count = 0

    def build_lookup(
        configuration: Mapping[str, str],
        environment: Mapping[str, str],
        *,
        image_tag: str,
    ) -> str | None:
        nonlocal lookup_count
        del configuration, environment, image_tag
        lookup_count += 1
        return None if lookup_count == 1 else digest

    def fake_capture(
        name: str,
        arguments: Sequence[str],
        **kwargs: object,
    ) -> bytes:
        capture_calls.append(tuple(arguments))
        external_environment = cast(Mapping[str, str], kwargs.get("environment"))
        if external_environment.get(
            "BUILDX_BUILDER"
        ) != "sen68-123456789-2" or not external_environment.get("DOCKER_CONFIG"):
            raise DeliveryRegressionError("Build perdeu o contexto isolado aprovado.")
        if name == "aws" and tuple(arguments[:2]) == (
            "ecr",
            "get-login-password",
        ):
            return b"synthetic-password"
        if name == "docker" and arguments[:1] == ("login",):
            return b""
        raise DeliveryRegressionError("Consulta externa inesperada no build sintético.")

    def fake_run(
        name: str,
        arguments: Sequence[str],
        **kwargs: object,
    ) -> None:
        del kwargs
        if name != "docker":
            raise DeliveryRegressionError("Executável inesperado no build sintético.")
        run_calls.append(tuple(arguments))
        metadata_index = arguments.index("--metadata-file") + 1
        Path(arguments[metadata_index]).write_text(
            json.dumps({"containerimage.digest": digest}),
            encoding="utf-8",
            newline="\n",
        )

    try:
        aws_delivery.terraform_output = fake_output
        aws_delivery.capture_silent = cast(Any, fake_capture)
        aws_delivery.run_silent = fake_run
        aws_delivery.published_image_digest = reuse_lookup
        with tempfile.TemporaryDirectory(prefix="sen68-ecr-reuse-") as temporary:
            docker_config = Path(temporary) / "docker"
            docker_config.mkdir()
            build_environment = {
                "BUILDX_BUILDER": "sen68-123456789-2",
                "DOCKER_CONFIG": str(docker_config),
            }
            reused = login_and_build(configuration, build_environment, Path(temporary))
            if reused != digest or capture_calls or run_calls:
                raise DeliveryRegressionError(
                    "Reuso ECR tentou republicar a tag imutável."
                )

        aws_delivery.published_image_digest = build_lookup
        with tempfile.TemporaryDirectory(prefix="sen68-ecr-build-") as temporary:
            docker_config = Path(temporary) / "docker"
            docker_config.mkdir()
            build_environment = {
                "BUILDX_BUILDER": "sen68-123456789-2",
                "DOCKER_CONFIG": str(docker_config),
            }
            built = login_and_build(configuration, build_environment, Path(temporary))
            build_arguments = run_calls[0]
            if (
                built != digest
                or capture_calls[0][:2] != ("ecr", "get-login-password")
                or capture_calls[1][:1] != ("login",)
                or build_arguments[:2] != ("buildx", "build")
                or build_arguments[build_arguments.index("--builder") + 1]
                != "sen68-123456789-2"
            ):
                raise DeliveryRegressionError(
                    "Ausência ECR não iniciou o build aprovado."
                )
            hostile_environment = dict(build_environment)
            hostile_environment["BUILDX_BUILDER"] = "../../hostile"
            expect_failure(
                lambda: login_and_build(
                    configuration, hostile_environment, Path(temporary)
                ),
                AwsDeliveryError,
            )
    finally:
        aws_delivery.terraform_output = original_output
        aws_delivery.published_image_digest = original_lookup
        aws_delivery.capture_silent = original_capture
        aws_delivery.run_silent = original_run
    return 9


def prove_hostile_streams_fail_closed() -> int:
    cases = (
        (aws_delivery, True, False),
        (aws_delivery, False, True),
        (orphan_inventory, True, False),
        (orphan_inventory, False, True),
    )
    for module, fail_read, fail_close in cases:
        capture = module._BoundedCapture()  # pyright: ignore[reportPrivateUsage]
        stream = cast(
            BinaryIO,
            HostileStream(fail_read=fail_read, fail_close=fail_close),
        )
        stderr = io.StringIO()
        thread = threading.Thread(
            target=module._drain_bounded,  # pyright: ignore[reportPrivateUsage]
            args=(stream, capture, 64),
        )
        with contextlib.redirect_stderr(stderr):
            thread.start()
            thread.join(timeout=5)
        if (
            thread.is_alive()
            or not capture.is_invalid()
            or SENSITIVE_MARKER in stderr.getvalue()
            or "Traceback" in stderr.getvalue()
        ):
            raise DeliveryRegressionError(
                "Stream hostil gerou traceback ou captura parcial aceitável."
            )
    return len(cases)


def prove_cli_failures_are_sanitized() -> int:
    commands = (
        (
            "delivery_gate.py",
            "plan",
            SENSITIVE_MARKER,
            "--mode",
            "review",
        ),
        (
            "delivery_policy.py",
            "--contract",
            SENSITIVE_MARKER,
        ),
    )
    for arguments in commands:
        completed = subprocess.run(  # noqa: S603 - current Python is explicit.
            [
                sys.executable,
                str(REPOSITORY_ROOT / "infra/aws/demo/scripts" / arguments[0]),
                *arguments[1:],
            ],
            check=False,
            cwd=REPOSITORY_ROOT,
            env={"PATH": os.environ["PATH"]},
            capture_output=True,
            timeout=10,
        )
        output = completed.stdout + completed.stderr
        if (
            completed.returncode == 0
            or SENSITIVE_MARKER.encode("ascii") in output
            or b"Traceback" in output
        ):
            raise DeliveryRegressionError("CLI expôs caminho ou traceback hostil.")
    return len(commands)


def main() -> int:
    contract = mutable_contract()
    action_pins = audit_contract(contract)
    checks = {
        "contract": prove_contract_mutations_rejected(),
        "workflow": prove_workflow_mutations_rejected(action_pins),
        "github_environment": prove_github_environment_gate(),
        "cognito_runbook": prove_cognito_runbook_is_sanitized(),
        "wildcard_ledger": prove_wildcard_ledger_is_complete(),
        "plan_state": prove_plan_and_state_gates(),
        "remote_smoke": prove_remote_smoke_boundaries(),
        "inventory": prove_inventory_is_fail_closed(),
        "inventory_capture": prove_inventory_capture_is_bounded(),
        "environment": prove_hostile_environment_rejected(),
        "final_profile": prove_final_profile_precedes_operations(),
        "buildx_environment": prove_buildx_configuration_isolated(),
        "git_environment": prove_git_baseline_environment_isolated(),
        "terraform_init": prove_terraform_init_is_readonly(),
        "terraform_state": prove_missing_state_is_canonical(),
        "plan_foundation": prove_plan_requires_existing_foundation(),
        "capture_json": prove_bounded_capture_and_strict_json(),
        "immutable_ecr": prove_immutable_ecr_recovery(),
        "hostile_streams": prove_hostile_streams_fail_closed(),
        "cli_sanitization": prove_cli_failures_are_sanitized(),
    }
    total = sum(checks.values())
    print(
        f"Regressões de entrega aprovadas: {total} casos adversariais em "
        "OIDC/IAM, workflows, plano/state, smoke, inventário e ambiente."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AwsDeliveryError,
        DeliveryGateError,
        DeliveryPolicyError,
        DeliveryRegressionError,
        OrphanInventoryError,
        RemoteSmokeError,
    ) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("Regressão de entrega falhou com segurança.", file=sys.stderr)
        raise SystemExit(1) from None
