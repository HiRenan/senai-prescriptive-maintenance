"""Fail-closed AWS inventory after the demo Terraform destroy."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, NoReturn, cast

MAX_AWS_OUTPUT_BYTES = 10_000_000
MAX_AWS_ERROR_BYTES = 65_536


class OrphanInventoryError(RuntimeError):
    """Raised without AWS identifiers, paths, credentials, or raw CLI output."""


class InventoryEnvironment(dict[str, str]):
    """Environment carrier whose representation never exposes values."""

    def __repr__(self) -> str:
        return "InventoryEnvironment(<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()


@dataclass(slots=True)
class _BoundedCapture:
    content: bytearray = field(default_factory=bytearray, repr=False)
    overflowed: bool = False
    failed: bool = False

    def is_invalid(self) -> bool:
        return self.overflowed or self.failed


def _drain_bounded(stream: BinaryIO, capture: _BoundedCapture, limit: int) -> None:
    try:
        while chunk := stream.read(65_536):
            remaining = limit + 1 - len(capture.content)
            if remaining > 0:
                capture.content.extend(chunk[:remaining])
            if len(chunk) > remaining or len(capture.content) > limit:
                capture.overflowed = True
    except BaseException:
        capture.failed = True
    finally:
        try:
            stream.close()
        except BaseException:
            capture.failed = True


def fail(message: str) -> NoReturn:
    raise OrphanInventoryError(message)


def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            fail("Inventário AWS possui chave JSON duplicada.")
        result[key] = value
    return result


def reject_nonfinite(value: str) -> NoReturn:
    del value
    fail("Inventário AWS possui número não finito.")


def mapping(value: object, *, context: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        fail(f"{context} não é um objeto JSON base.")
    return cast(dict[str, Any], value)


def sequence(value: object, *, context: str) -> list[object]:
    if type(value) is not list:
        fail(f"{context} não é uma lista JSON base.")
    return cast(list[object], value)


def parse_json(content: str) -> Mapping[str, Any]:
    if len(content.encode("utf-8")) > MAX_AWS_OUTPUT_BYTES:
        fail("Inventário AWS excede o limite operacional.")
    try:
        parsed = json.loads(
            content,
            object_pairs_hook=object_pairs,
            parse_constant=reject_nonfinite,
        )
    except json.JSONDecodeError:
        raise OrphanInventoryError("AWS CLI não devolveu JSON válido.") from None
    return mapping(parsed, context="resposta AWS")


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = field(repr=False)
    stderr: str = field(repr=False)
    missing: bool = False


Runner = Callable[[tuple[str, ...]], CommandResult]
Predicate = Callable[[object], bool]


@dataclass(frozen=True, slots=True)
class InventoryQuery:
    name: str
    arguments: tuple[str, ...]
    collection_path: tuple[str, ...]
    predicate: Predicate = field(repr=False)
    allow_missing_collection: bool = False
    single_object: bool = False
    allow_not_found: bool = False
    allow_empty_output: bool = False
    allow_empty_parent: bool = False


def safe_environment(host_environment: Mapping[str, str]) -> InventoryEnvironment:
    allowed_exact = {
        "AWS_ACCESS_KEY_ID",
        "AWS_DEFAULT_REGION",
        "AWS_REGION",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
    }
    child = InventoryEnvironment(
        {
            key: value
            for key, value in host_environment.items()
            if key.upper() in allowed_exact and type(value) is str and value
        }
    )
    child.update(
        {
            "AWS_CLI_AUTO_PROMPT": "off",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_PAGER": "",
        }
    )
    return child


def is_canonical_domain_not_found(
    arguments: Sequence[str], returncode: int, stderr: str
) -> bool:
    return (
        returncode != 0
        and tuple(arguments[:2]) == ("cognito-idp", "describe-user-pool-domain")
        and re.fullmatch(
            r"\s*An error occurred \(ResourceNotFoundException\) when calling the "
            r"DescribeUserPoolDomain operation: [^\r\n]{1,2048}\s*",
            stderr,
        )
        is not None
    )


def command_runner(arguments: tuple[str, ...]) -> CommandResult:
    candidate = shutil.which("aws")
    if candidate is None:
        fail("AWS CLI não está disponível para o inventário.")
    process: subprocess.Popen[bytes] | None = None
    stdout_capture = _BoundedCapture()
    stderr_capture = _BoundedCapture()
    try:
        resolved = Path(candidate).resolve(strict=True)
        process = subprocess.Popen(  # noqa: S603 - executable is resolved explicitly.
            [str(resolved), *arguments, "--no-cli-pager", "--output", "json"],
            env=safe_environment(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            fail("Captura do inventário AWS não pôde ser inicializada.")
        stdout_thread = threading.Thread(
            target=_drain_bounded,
            args=(process.stdout, stdout_capture, MAX_AWS_OUTPUT_BYTES),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_drain_bounded,
            args=(process.stderr, stderr_capture, MAX_AWS_ERROR_BYTES),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        returncode = process.wait(timeout=90)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            fail("Captura do inventário AWS não terminou dentro do limite.")
        stdout = bytes(stdout_capture.content).decode("utf-8", errors="strict")
        stderr = bytes(stderr_capture.content).decode("utf-8", errors="strict")
    except (OSError, subprocess.SubprocessError, UnicodeError):
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise OrphanInventoryError("Consulta de inventário AWS falhou.") from None
    except BaseException:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise
    if stdout_capture.is_invalid() or stderr_capture.is_invalid():
        fail("Consulta de inventário AWS excedeu o limite operacional.")
    return CommandResult(
        returncode=returncode,
        stdout=stdout,
        stderr="",
        missing=is_canonical_domain_not_found(arguments, returncode, stderr),
    )


def nested_collection(
    document: Mapping[str, Any],
    path: Sequence[str],
    *,
    allow_missing_final: bool,
    allow_empty_parent: bool,
) -> list[object]:
    if not path:
        fail("Consulta de inventário não definiu uma coleção.")
    value: object = document
    for index, key in enumerate(path):
        current = mapping(value, context="estrutura do inventário")
        if key not in current or current[key] is None:
            if allow_missing_final and (
                index == len(path) - 1 or (allow_empty_parent and not current)
            ):
                return []
            fail("Resposta AWS omitiu uma coleção obrigatória do inventário.")
        value = current[key]
    return sequence(value, context="coleção do inventário")


def nested_object(
    document: Mapping[str, Any],
    path: Sequence[str],
    *,
    allow_missing_final: bool,
) -> object | None:
    if not path:
        fail("Consulta de inventário não definiu um objeto.")
    value: object = document
    for index, key in enumerate(path):
        current = mapping(value, context="estrutura do inventário")
        if key not in current or current[key] is None:
            if allow_missing_final and index == len(path) - 1:
                return None
            fail("Resposta AWS omitiu um objeto obrigatório do inventário.")
        value = current[key]
    return value


def base_text(value: object, key: str) -> str:
    if type(value) is not dict:
        fail("Item do inventário AWS possui tipo inválido.")
    candidate = cast(dict[str, object], value).get(key)
    if type(candidate) is not str or not candidate:
        fail("Item do inventário AWS omitiu sua identidade textual.")
    return candidate


def exact_name(key: str, expected: set[str]) -> Predicate:
    return lambda item: base_text(item, key) in expected


def tagged_resource_outside(delegated_arns: set[str]) -> Predicate:
    return lambda item: base_text(item, "ResourceARN") not in delegated_arns


def ecs_cluster_is_residual(expected_arn: str) -> Predicate:
    def matches(item: object) -> bool:
        cluster = mapping(item, context="descrição do cluster ECS")
        cluster_arn = cluster.get("clusterArn")
        status = cluster.get("status")
        if cluster_arn != expected_arn or type(status) is not str or not status:
            fail("Descrição do cluster ECS está ausente ou inválida.")
        return status != "INACTIVE"

    return matches


def text_suffix(expected: str) -> Predicate:
    def matches(item: object) -> bool:
        if type(item) is not str or not item:
            fail("Item do inventário AWS possui identidade inválida.")
        return item.endswith(expected)

    return matches


def final_segment(expected: set[str]) -> Predicate:
    def matches(item: object) -> bool:
        if (
            type(item) is not str
            or not item
            or any(character.isspace() for character in item)
        ):
            fail("Item do inventário AWS possui URL inválida.")
        return item.rsplit("/", maxsplit=1)[-1] in expected

    return matches


def distribution_matches(domain: str) -> Predicate:
    def matches(item: object) -> bool:
        if type(item) is not dict:
            fail("Distribuição CloudFront possui tipo inválido.")
        aliases = cast(dict[str, object], item).get("Aliases")
        if type(aliases) is not dict:
            fail("Distribuição CloudFront omitiu aliases.")
        alias_document = cast(dict[str, object], aliases)
        quantity = alias_document.get("Quantity")
        values = alias_document.get("Items")
        if type(quantity) is not int or quantity < 0:
            fail("Distribuição CloudFront possui quantidade de aliases inválida.")
        if values is None:
            if quantity != 0:
                fail("Distribuição CloudFront omitiu aliases declarados.")
            return False
        if (
            type(values) is not list
            or len(values) != quantity
            or any(type(value) is not str or not value for value in values)
        ):
            fail("Distribuição CloudFront possui aliases inválidos.")
        return domain in cast(list[str], values)

    return matches


def nested_name(*keys: str, expected: str) -> Predicate:
    def matches(item: object) -> bool:
        value = item
        for key in keys:
            if type(value) is not dict:
                fail("Item aninhado do inventário AWS possui tipo inválido.")
            current = cast(dict[str, object], value)
            if key not in current:
                fail("Item aninhado do inventário AWS omitiu sua identidade.")
            value = current[key]
        if type(value) is not str or not value:
            fail("Item aninhado do inventário AWS possui identidade inválida.")
        return value == expected

    return matches


def cognito_domain_matches(expected: str) -> Predicate:
    def matches(item: object) -> bool:
        description = mapping(item, context="descrição do domínio Cognito")
        if not description:
            return False
        domain = description.get("Domain")
        if type(domain) is not str or domain != expected:
            fail("Descrição Cognito não corresponde ao domínio consultado.")
        return True

    return matches


def inventory_queries(
    *,
    account_id: str,
    region: str,
    name_prefix: str,
    frontend_domain: str,
) -> tuple[InventoryQuery, ...]:
    name = f"{name_prefix}-demo"
    buckets = {
        f"{name_prefix}-artifacts-{account_id}-{region}",
        f"{name_prefix}-documents-{account_id}-{region}",
        f"{name_prefix}-frontend-{account_id}-{region}",
    }
    role_names = {
        f"{name}-api-execution",
        f"{name}-api-task",
        f"{name}-worker-task",
    }
    log_groups = {f"/aws/apigateway/{name}", f"/aws/ecs/{name}/api"}
    queue_names = {f"{name}-ingestion", f"{name}-ingestion-dlq"}
    alarm_names = {
        f"{name}-api-5xx",
        f"{name}-api-cpu",
        f"{name}-dlq-messages",
        f"{name}-queue-age",
    }
    domain_seed = f"{name_prefix}:{frontend_domain}:{region}"
    cognito_domain = f"spm-{hashlib.sha256(domain_seed.encode()).hexdigest()[:20]}"
    ecs_cluster_arn = f"arn:aws:ecs:{region}:{account_id}:cluster/{name}"
    tag_filters = (
        "Key=Environment,Values=demo",
        "Key=Profile,Values=aws-demo",
        "Key=Project,Values=prescriptive-maintenance",
    )
    ec2_filters = (
        "Name=tag:Environment,Values=demo",
        "Name=tag:Profile,Values=aws-demo",
        "Name=tag:Project,Values=prescriptive-maintenance",
    )
    return (
        InventoryQuery(
            "tagged resources",
            (
                "resourcegroupstaggingapi",
                "get-resources",
                "--tag-filters",
                *tag_filters,
            ),
            ("ResourceTagMappingList",),
            tagged_resource_outside({ecs_cluster_arn}),
        ),
        InventoryQuery(
            "S3 buckets",
            ("s3api", "list-buckets"),
            ("Buckets",),
            exact_name("Name", buckets),
        ),
        InventoryQuery(
            "ECR repositories",
            ("ecr", "describe-repositories", "--region", region),
            ("repositories",),
            exact_name("repositoryName", {f"{name}/api"}),
        ),
        InventoryQuery(
            "ECS clusters",
            ("ecs", "list-clusters", "--region", region),
            ("clusterArns",),
            text_suffix(f"cluster/{name}"),
        ),
        InventoryQuery(
            "ECS canonical cluster status",
            (
                "ecs",
                "describe-clusters",
                "--clusters",
                ecs_cluster_arn,
                "--region",
                region,
            ),
            ("clusters",),
            ecs_cluster_is_residual(ecs_cluster_arn),
        ),
        InventoryQuery(
            "CloudWatch log groups",
            (
                "logs",
                "describe-log-groups",
                "--log-group-name-prefix",
                "/aws/",
                "--region",
                region,
            ),
            ("logGroups",),
            exact_name("logGroupName", log_groups),
        ),
        InventoryQuery(
            "SQS queues",
            ("sqs", "list-queues", "--queue-name-prefix", name, "--region", region),
            ("QueueUrls",),
            final_segment(queue_names),
            allow_missing_collection=True,
            allow_empty_output=True,
        ),
        InventoryQuery(
            "IAM roles",
            ("iam", "list-roles"),
            ("Roles",),
            exact_name("RoleName", role_names),
        ),
        InventoryQuery(
            "Cognito user pools",
            (
                "cognito-idp",
                "list-user-pools",
                "--max-results",
                "60",
                "--region",
                region,
            ),
            ("UserPools",),
            exact_name("Name", {name}),
        ),
        InventoryQuery(
            "Cognito hosted UI domain",
            (
                "cognito-idp",
                "describe-user-pool-domain",
                "--domain",
                cognito_domain,
                "--region",
                region,
            ),
            ("DomainDescription",),
            cognito_domain_matches(cognito_domain),
            allow_missing_collection=True,
            single_object=True,
            allow_not_found=True,
        ),
        InventoryQuery(
            "API Gateway APIs",
            ("apigatewayv2", "get-apis", "--region", region),
            ("Items",),
            exact_name("Name", {name}),
        ),
        InventoryQuery(
            "Cloud Map namespaces",
            ("servicediscovery", "list-namespaces", "--region", region),
            ("Namespaces",),
            exact_name("Name", {f"{name}.internal"}),
        ),
        InventoryQuery(
            "AWS Budgets",
            ("budgets", "describe-budgets", "--account-id", account_id),
            ("Budgets",),
            exact_name("BudgetName", {f"{name}-monthly-cost"}),
            allow_missing_collection=True,
            allow_empty_output=True,
        ),
        InventoryQuery(
            "CloudFront distributions",
            ("cloudfront", "list-distributions"),
            ("DistributionList", "Items"),
            distribution_matches(frontend_domain),
            allow_missing_collection=True,
            allow_empty_output=True,
            allow_empty_parent=True,
        ),
        InventoryQuery(
            "CloudFront origin access controls",
            ("cloudfront", "list-origin-access-controls"),
            ("OriginAccessControlList", "Items"),
            exact_name("Name", {f"{name}-frontend"}),
            allow_missing_collection=True,
            allow_empty_output=True,
            allow_empty_parent=True,
        ),
        InventoryQuery(
            "CloudFront cache policies",
            ("cloudfront", "list-cache-policies", "--type", "custom"),
            ("CachePolicyList", "Items"),
            nested_name(
                "CachePolicy", "CachePolicyConfig", "Name", expected=f"{name}-frontend"
            ),
            allow_missing_collection=True,
        ),
        InventoryQuery(
            "CloudFront response headers policies",
            ("cloudfront", "list-response-headers-policies", "--type", "custom"),
            ("ResponseHeadersPolicyList", "Items"),
            nested_name(
                "ResponseHeadersPolicy",
                "ResponseHeadersPolicyConfig",
                "Name",
                expected=f"{name}-frontend-security",
            ),
            allow_missing_collection=True,
        ),
        InventoryQuery(
            "EC2 VPCs",
            ("ec2", "describe-vpcs", "--filters", *ec2_filters, "--region", region),
            ("Vpcs",),
            lambda item: True,
        ),
        InventoryQuery(
            "EC2 subnets",
            ("ec2", "describe-subnets", "--filters", *ec2_filters, "--region", region),
            ("Subnets",),
            lambda item: True,
        ),
        InventoryQuery(
            "EC2 route tables",
            (
                "ec2",
                "describe-route-tables",
                "--filters",
                *ec2_filters,
                "--region",
                region,
            ),
            ("RouteTables",),
            lambda item: True,
        ),
        InventoryQuery(
            "EC2 security groups",
            (
                "ec2",
                "describe-security-groups",
                "--filters",
                *ec2_filters,
                "--region",
                region,
            ),
            ("SecurityGroups",),
            lambda item: True,
        ),
        InventoryQuery(
            "EC2 VPC endpoints",
            (
                "ec2",
                "describe-vpc-endpoints",
                "--filters",
                *ec2_filters,
                "--region",
                region,
            ),
            ("VpcEndpoints",),
            lambda item: True,
        ),
        InventoryQuery(
            "CloudWatch alarms",
            (
                "cloudwatch",
                "describe-alarms",
                "--alarm-name-prefix",
                name,
                "--region",
                region,
            ),
            ("MetricAlarms",),
            exact_name("AlarmName", alarm_names),
        ),
    )


def scan_inventory(queries: Sequence[InventoryQuery], runner: Runner) -> int:
    residual_count = 0
    for query in queries:
        result = runner(query.arguments)
        if type(result) is not CommandResult:
            fail("Uma consulta obrigatória do inventário AWS falhou.")
        if result.returncode != 0:
            if query.allow_not_found and result.missing:
                continue
            fail("Uma consulta obrigatória do inventário AWS falhou.")
        if result.missing:
            fail("Consulta bem-sucedida não pode declarar recurso ausente.")
        if not result.stdout.strip():
            if not query.allow_empty_output or not query.allow_missing_collection:
                fail("AWS CLI não devolveu JSON válido.")
            document: Mapping[str, Any] = {}
        else:
            document = parse_json(result.stdout)
        if query.single_object:
            item = nested_object(
                document,
                query.collection_path,
                allow_missing_final=query.allow_missing_collection,
            )
            if item is not None and query.predicate(item):
                residual_count += 1
            continue
        items = nested_collection(
            document,
            query.collection_path,
            allow_missing_final=query.allow_missing_collection,
            allow_empty_parent=query.allow_empty_parent,
        )
        residual_count += sum(1 for item in items if query.predicate(item))
    if residual_count:
        fail("Inventário AWS encontrou recursos residuais no escopo da demo.")
    return residual_count


def required_environment() -> tuple[str, str, str, str]:
    variable_names = {
        "account": "AWS_DEMO_ACCOUNT_ID",
        "domain": "TF_VAR_frontend_domain_name",
        "prefix": "TF_VAR_name_prefix",
        "region": "AWS_REGION",
    }
    account_id = os.environ.get(variable_names["account"])
    region = os.environ.get(variable_names["region"])
    name_prefix = os.environ.get(variable_names["prefix"])
    frontend_domain = os.environ.get(variable_names["domain"])
    if (
        type(account_id) is not str
        or re.fullmatch(r"^[0-9]{12}$", account_id) is None
        or type(region) is not str
        or re.fullmatch(r"^[a-z]{2}-[a-z]+-\d$", region) is None
        or type(name_prefix) is not str
        or re.fullmatch(r"^[a-z][a-z0-9-]{2,19}$", name_prefix) is None
        or type(frontend_domain) is not str
        or len(frontend_domain) > 253
        or re.fullmatch(
            r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
            frontend_domain,
        )
        is None
    ):
        fail("Configuração do inventário AWS está ausente ou inválida.")
    return account_id, region, name_prefix, frontend_domain


def main() -> int:
    account_id, region, name_prefix, frontend_domain = required_environment()
    queries = inventory_queries(
        account_id=account_id,
        region=region,
        name_prefix=name_prefix,
        frontend_domain=frontend_domain,
    )
    scan_inventory(queries, command_runner)
    print(
        "Inventário pós-teardown aprovado: state e scans AWS do escopo não "
        "encontraram recursos residuais."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OrphanInventoryError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("Inventário AWS falhou com segurança.", file=sys.stderr)
        raise SystemExit(1) from None
