"""Execute guarded AWS demo plan, deploy, or teardown without raw output."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, NoReturn, cast

from delivery_gate import (
    DeliveryGateError,
    audit_plan,
    audit_state,
    audit_state_snapshot,
    load_plan,
)
from frontend_delivery import (
    FrontendDeliveryError,
    add_runtime_config,
    assert_final_profile,
    publish_frontend,
    runtime_config,
    stage_frontend,
)
from orphan_inventory import (
    OrphanInventoryError,
    command_runner,
    inventory_queries,
    scan_inventory,
)
from published_smoke import PublishedSmokeError, run_published_smoke
from remote_smoke import (
    RemoteSmokeError,
    run_smoke,
    validate_endpoint,
    validate_token,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
MODULE_ROOT = REPOSITORY_ROOT / "infra/aws/demo"
PLACEHOLDER_DIGEST = "sha256:" + "a" * 64
SEN46_BASELINE_SHA = "d45bcabfb6de89c6bac2ec2aa6180bce353be7c1"
STATE_KEY = "demo/sen-67/terraform.tfstate"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_TAG_PATTERN = re.compile(r"^sha-[0-9a-f]{40}$")
ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")
REGION_PATTERN = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,19}$")
RUN_NUMBER_PATTERN = re.compile(r"^[1-9][0-9]{0,19}$")
BUILDX_BUILDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ALLOWED_TF_VARIABLES = {
    "TF_VAR_api_desired_count",
    "TF_VAR_api_image_digest",
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
}
ALLOWED_TF_CONTROLLER_VARIABLES = {
    "TF_STATE_BUCKET",
    "TF_STATE_KEY",
}
ALLOWED_TF_RUNTIME_VARIABLES = {"TF_IN_AUTOMATION", "TF_INPUT"}
ALLOWED_HOST_VARIABLES = {"PATH"}
DEFAULT_CAPTURE_BYTES = 1_000_000
MAX_CAPTURE_BYTES = 50_000_000
SESSION_SAFETY_SECONDS = 300


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


class AwsDeliveryError(RuntimeError):
    """Raised without raw command, plan, state, endpoint, or credential values."""


class DeliveryEnvironment(dict[str, str]):
    """Environment carrier whose representation never exposes values."""

    def __repr__(self) -> str:
        return "DeliveryEnvironment(<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()


def fail(message: str) -> NoReturn:
    raise AwsDeliveryError(message)


def required_text(
    environment: Mapping[str, str],
    name: str,
    pattern: re.Pattern[str],
) -> str:
    value = environment.get(name)
    if type(value) is not str or pattern.fullmatch(value) is None:
        fail("Configuração obrigatória da entrega está ausente ou inválida.")
    return value


def require_final_profile(*, region: object, domain: object) -> None:
    if type(region) is not str or type(domain) is not str:
        fail("Perfil final da entrega está ausente ou inválido.")
    try:
        assert_final_profile(region=region, domain=domain)
    except FrontendDeliveryError:
        fail("Perfil final da entrega diverge da região ou domínio aprovados.")


def validated_configuration(environment: Mapping[str, str]) -> dict[str, str]:
    account_id = required_text(environment, "AWS_DEMO_ACCOUNT_ID", ACCOUNT_PATTERN)
    region = required_text(environment, "AWS_REGION", REGION_PATTERN)
    source_sha = required_text(environment, "AWS_DEMO_SOURCE_SHA", SHA_PATTERN)
    expiration_text = environment.get("AWS_DEMO_SESSION_EXPIRATION")
    name_prefix = required_text(environment, "TF_VAR_name_prefix", NAME_PATTERN)
    frontend_domain = environment.get("TF_VAR_frontend_domain_name")
    state_bucket = environment.get("TF_STATE_BUCKET")
    state_key = environment.get("TF_STATE_KEY")
    try:
        if type(expiration_text) is not str or not expiration_text.endswith("Z"):
            fail("Expiração da sessão AWS está ausente ou inválida.")
        expiration = datetime.fromisoformat(expiration_text.replace("Z", "+00:00"))
        if expiration.tzinfo is None or expiration.utcoffset() != UTC.utcoffset(None):
            fail("Expiração da sessão AWS está ausente ou inválida.")
        expiration_epoch = int(expiration.timestamp())
    except (OverflowError, ValueError):
        raise AwsDeliveryError(
            "Expiração da sessão AWS está ausente ou inválida."
        ) from None
    remaining = expiration_epoch - int(time.time())
    if not 0 < remaining <= 7_300:
        fail("Expiração da sessão AWS está fora da janela OIDC aprovada.")
    if (
        type(state_bucket) is not str
        or re.fullmatch(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", state_bucket) is None
        or state_key != STATE_KEY
        or type(frontend_domain) is not str
        or not frontend_domain
    ):
        fail("Backend remoto da entrega está ausente ou inválido.")
    require_final_profile(region=region, domain=frontend_domain)
    temporary_credentials = (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    )
    if environment.get("AWS_DEFAULT_REGION") != region or any(
        type(environment.get(name)) is not str or not environment.get(name)
        for name in temporary_credentials
    ):
        fail("Sessão AWS temporária da entrega está ausente ou inconsistente.")
    expected = {
        "TF_VAR_aws_account_id": account_id,
        "TF_VAR_aws_region": region,
        "TF_VAR_enable_bedrock": "false",
    }
    if any(environment.get(key) != value for key, value in expected.items()):
        fail("Variáveis Terraform divergem da identidade AWS aprovada.")
    required_variables = ALLOWED_TF_VARIABLES - {
        "TF_VAR_api_desired_count",
        "TF_VAR_api_image_digest",
    }
    if any(not environment.get(key) for key in required_variables):
        fail("Variáveis Terraform reais estão incompletas.")
    unexpected_tf = {
        key
        for key in environment
        if key.upper().startswith("TF_")
        and key not in ALLOWED_TF_VARIABLES
        and key not in ALLOWED_TF_CONTROLLER_VARIABLES
        and key not in ALLOWED_TF_RUNTIME_VARIABLES
    }
    if unexpected_tf:
        fail("Ambiente contém override Terraform fora da allowlist.")
    return {
        "account_id": account_id,
        "frontend_domain": frontend_domain,
        "name_prefix": name_prefix,
        "region": region,
        "session_expiration_epoch": str(expiration_epoch),
        "source_sha": source_sha,
        "state_bucket": state_bucket,
        "state_key": state_key,
    }


def require_session_window(
    configuration: Mapping[str, str], required_seconds: int
) -> None:
    raw_expiration = configuration.get("session_expiration_epoch")
    if (
        type(required_seconds) is not int
        or required_seconds <= 0
        or type(raw_expiration) is not str
        or not raw_expiration.isascii()
        or not raw_expiration.isdigit()
    ):
        fail("Janela operacional da sessão AWS está ausente ou inválida.")
    remaining = int(raw_expiration) - int(time.time())
    if remaining < required_seconds + SESSION_SAFETY_SECONDS:
        fail("Sessão AWS não cobre a próxima operação com margem segura.")


def validated_buildx_configuration(
    environment: Mapping[str, str],
) -> tuple[str, Path]:
    run_id = required_text(environment, "GITHUB_RUN_ID", RUN_NUMBER_PATTERN)
    run_attempt = required_text(environment, "GITHUB_RUN_ATTEMPT", RUN_NUMBER_PATTERN)
    builder = required_text(
        environment, "AWS_DEMO_BUILDX_BUILDER", BUILDX_BUILDER_PATTERN
    )
    expected_builder = f"sen68-{run_id}-{run_attempt}"
    runner_temp_text = environment.get("RUNNER_TEMP")
    docker_config_text = environment.get("AWS_DEMO_BUILDX_DOCKER_CONFIG")
    unexpected = {
        key
        for key in environment
        if (
            key.startswith("BUILDX_")
            or key == "DOCKER_CONFIG"
            or key.startswith("AWS_DEMO_BUILDX_")
        )
        and key
        not in {
            "AWS_DEMO_BUILDX_BUILDER",
            "AWS_DEMO_BUILDX_DOCKER_CONFIG",
        }
    }
    if (
        builder != expected_builder
        or type(runner_temp_text) is not str
        or not runner_temp_text
        or type(docker_config_text) is not str
        or not docker_config_text
        or unexpected
    ):
        fail("Contexto Buildx isolado está ausente ou inválido.")
    try:
        runner_temp = Path(runner_temp_text)
        docker_config = Path(docker_config_text)
        if (
            not runner_temp.is_absolute()
            or not docker_config.is_absolute()
            or docker_config.is_symlink()
        ):
            fail("Contexto Buildx isolado está ausente ou inválido.")
        resolved_runner_temp = runner_temp.resolve(strict=True)
        resolved_docker_config = docker_config.resolve(strict=True)
    except OSError:
        raise AwsDeliveryError(
            "Contexto Buildx isolado está ausente ou inválido."
        ) from None
    if (
        not resolved_runner_temp.is_dir()
        or not resolved_docker_config.is_dir()
        or resolved_docker_config.parent != resolved_runner_temp
        or resolved_docker_config.name != f"sen68-buildx-{run_id}-{run_attempt}"
    ):
        fail("Contexto Buildx isolado está ausente ou inválido.")
    return builder, resolved_docker_config


def child_environment(
    host_environment: Mapping[str, str],
    *,
    digest: str,
    desired_count: int,
    isolation_root: Path,
    buildx_builder: str | None = None,
    docker_config: Path | None = None,
) -> DeliveryEnvironment:
    terraform_data = isolation_root / "terraform-data"
    terraform_data.mkdir(parents=True, exist_ok=True)
    cli_config = isolation_root / "terraform.rc"
    cli_config.write_text("disable_checkpoint = true\n", encoding="utf-8", newline="\n")
    home = isolation_root / "home"
    isolated_docker_config = isolation_root / "docker"
    cache = isolation_root / "cache"
    config = isolation_root / "config"
    temporary = isolation_root / "tmp"
    for directory in (home, cache, config, temporary):
        directory.mkdir(parents=True, exist_ok=True)
    if (buildx_builder is None) != (docker_config is None):
        fail("Contexto Buildx sanitizado está incompleto.")
    if buildx_builder is None:
        isolated_docker_config.mkdir(parents=True, exist_ok=True)
    elif (
        BUILDX_BUILDER_PATTERN.fullmatch(buildx_builder) is None
        or docker_config is None
        or not docker_config.is_absolute()
        or not docker_config.is_dir()
    ):
        fail("Contexto Buildx sanitizado está inválido.")
    child = DeliveryEnvironment(
        {
            key: value
            for key, value in host_environment.items()
            if key in ALLOWED_HOST_VARIABLES and type(value) is str and value
        }
    )
    if "PATH" not in child:
        fail("PATH do ambiente de entrega está ausente ou inválido.")
    allowed_aws = {
        "AWS_ACCESS_KEY_ID",
        "AWS_DEFAULT_REGION",
        "AWS_REGION",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    }
    child.update(
        {
            key: value
            for key, value in host_environment.items()
            if key in allowed_aws and type(value) is str and value
        }
    )
    child.update(
        {
            key: value
            for key, value in host_environment.items()
            if key in ALLOWED_TF_VARIABLES and type(value) is str and value
        }
    )
    child.update(
        {
            "AWS_CLI_AUTO_PROMPT": "off",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_PAGER": "",
            "DOCKER_CONFIG": str(
                docker_config if docker_config is not None else isolated_docker_config
            ),
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": str(temporary),
            "TF_IN_AUTOMATION": "true",
            "TF_INPUT": "false",
            "TF_CLI_CONFIG_FILE": str(cli_config),
            "TF_DATA_DIR": str(terraform_data),
            "TF_VAR_api_desired_count": str(desired_count),
            "TF_VAR_api_image_digest": digest,
            "XDG_CACHE_HOME": str(cache),
            "XDG_CONFIG_HOME": str(config),
        }
    )
    if buildx_builder is not None:
        child["BUILDX_BUILDER"] = buildx_builder
    return child


def executable(name: str) -> Path:
    if name not in {"aws", "docker", "terraform"}:
        fail("Executável não pertence à allowlist da entrega.")
    candidate = shutil.which(name)
    if candidate is None:
        fail("Ferramenta obrigatória da entrega não está disponível.")
    try:
        resolved = Path(candidate).resolve(strict=True)
    except OSError:
        raise AwsDeliveryError(
            "Ferramenta da entrega não pôde ser resolvida."
        ) from None
    if not resolved.is_file():
        fail("Ferramenta da entrega não é um arquivo executável.")
    return resolved


def run_silent(
    name: str,
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str],
    timeout_seconds: int,
    cwd: Path = REPOSITORY_ROOT,
) -> None:
    resolved = executable(name)
    try:
        result = subprocess.run(  # noqa: S603 - executable is resolved explicitly.
            [str(resolved), *arguments],
            check=False,
            cwd=cwd,
            env=dict(environment),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        raise AwsDeliveryError("Uma etapa externa da entrega falhou.") from None
    if result.returncode != 0:
        fail("Uma etapa externa da entrega retornou falha sanitizada.")


def capture_silent(
    name: str,
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str],
    timeout_seconds: int,
    cwd: Path = REPOSITORY_ROOT,
    input_bytes: bytes | None = None,
    max_bytes: int = DEFAULT_CAPTURE_BYTES,
) -> bytes:
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_CAPTURE_BYTES:
        fail("Limite de captura externa está fora da política.")
    if input_bytes is not None and len(input_bytes) > max_bytes:
        fail("Entrada de consulta externa excede o limite operacional.")
    resolved = executable(name)
    process: subprocess.Popen[bytes] | None = None
    stdout_capture = _BoundedCapture()
    stderr_capture = _BoundedCapture()
    try:
        process = subprocess.Popen(  # noqa: S603 - executable is resolved explicitly.
            [str(resolved), *arguments],
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            fail("Captura externa não pôde ser inicializada.")
        stdout_thread = threading.Thread(
            target=_drain_bounded,
            args=(process.stdout, stdout_capture, max_bytes),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_drain_bounded,
            args=(process.stderr, stderr_capture, max_bytes),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        if input_bytes is not None:
            if process.stdin is None:
                fail("Entrada externa não pôde ser inicializada.")
            try:
                process.stdin.write(input_bytes)
                process.stdin.flush()
            finally:
                process.stdin.close()
        return_code = process.wait(timeout=timeout_seconds)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            fail("Captura externa não terminou dentro do limite.")
    except (OSError, subprocess.SubprocessError):
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise AwsDeliveryError("Uma consulta externa da entrega falhou.") from None
    except BaseException:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise
    if return_code != 0 or stdout_capture.is_invalid() or stderr_capture.is_invalid():
        fail("Uma consulta externa da entrega retornou falha sanitizada.")
    return bytes(stdout_capture.content)


def terraform_arguments(*arguments: str) -> list[str]:
    return [f"-chdir={MODULE_ROOT}", *arguments]


def terraform_init(
    configuration: Mapping[str, str],
    environment: Mapping[str, str],
) -> None:
    run_silent(
        "terraform",
        terraform_arguments(
            "init",
            "-input=false",
            "-lockfile=readonly",
            "-no-color",
            "-reconfigure",
            f"-backend-config=bucket={configuration['state_bucket']}",
            f"-backend-config=key={configuration['state_key']}",
            f"-backend-config=region={configuration['region']}",
            "-backend-config=encrypt=true",
            "-backend-config=use_lockfile=true",
        ),
        environment=environment,
        timeout_seconds=600,
    )


def terraform_state_list(
    environment: Mapping[str, str],
    temporary: Path,
    *,
    suffix: str,
    allow_missing: bool = False,
) -> tuple[Path, Mapping[str, object] | None]:
    output_path = temporary / f"state-{suffix}.txt"
    pulled = capture_silent(
        "terraform",
        terraform_arguments("state", "pull"),
        environment=environment,
        timeout_seconds=300,
        max_bytes=MAX_CAPTURE_BYTES,
    )
    if not pulled.strip():
        if not allow_missing:
            fail("State remoto esperado não existe.")
        output_path.write_bytes(b"")
        return output_path, None
    document = strict_json(pulled, context="Snapshot privado do state")
    if (
        type(document) is not dict
        or type(document.get("version")) is not int
        or type(document.get("serial")) is not int
        or type(document.get("lineage")) is not str
        or type(document.get("outputs")) is not dict
        or type(document.get("resources")) is not list
    ):
        fail("Snapshot privado do state diverge da estrutura esperada.")
    content = capture_silent(
        "terraform",
        terraform_arguments("state", "list"),
        environment=environment,
        timeout_seconds=300,
        max_bytes=DEFAULT_CAPTURE_BYTES,
    )
    output_path.write_bytes(content)
    return output_path, cast(dict[str, object], document)


def audit_pulled_state(
    path: Path,
    snapshot: Mapping[str, object] | None,
    *,
    mode: str,
    configuration: Mapping[str, str],
    expected_image: str | None = None,
) -> None:
    audit_state(path, mode=mode)
    if mode in {"fresh", "destroyed"}:
        if snapshot is not None:
            audit_state_snapshot(
                snapshot,
                mode=mode,
                identity=configuration,
                expected_image=expected_image,
            )
        return
    if snapshot is None:
        fail("Snapshot privado do state está ausente.")
    audit_state_snapshot(
        snapshot,
        mode=mode,
        identity=configuration,
        expected_image=expected_image,
    )


def terraform_output(
    environment: Mapping[str, str], name: str, *, max_bytes: int = 4096
) -> str:
    if name not in {
        "api_base_url",
        "api_image_reference",
        "cognito_client_id",
        "cognito_hosted_ui_origin",
        "ecr_repository_url",
        "frontend_bucket_name",
        "frontend_distribution_id",
        "frontend_url",
    }:
        fail("Output Terraform não pertence à allowlist da entrega.")
    raw = capture_silent(
        "terraform",
        terraform_arguments("output", "-raw", name),
        environment=environment,
        timeout_seconds=300,
        max_bytes=max_bytes,
    )
    try:
        return raw.decode("utf-8", errors="strict").strip()
    except UnicodeError:
        raise AwsDeliveryError("Output Terraform não é UTF-8 válido.") from None


def validated_frontend_bucket_output(
    configuration: Mapping[str, str], value: str
) -> str:
    expected = (
        f"{configuration['name_prefix']}-frontend-{configuration['account_id']}"
        f"-{configuration['region']}"
    )
    if value != expected:
        fail("Output do bucket frontend diverge do destino exato aprovado.")
    return value


def existing_digest(
    configuration: Mapping[str, str], environment: Mapping[str, str]
) -> str:
    reference = terraform_output(environment, "api_image_reference")
    repository = expected_repository(configuration)
    prefix = f"{repository}@"
    if not reference.startswith(prefix):
        fail("State referencia imagem fora do ECR aprovado.")
    digest = reference.removeprefix(prefix)
    if DIGEST_PATTERN.fullmatch(digest) is None:
        fail("State não contém digest OCI canônico.")
    return digest


def expected_repository(configuration: Mapping[str, str]) -> str:
    return (
        f"{configuration['account_id']}.dkr.ecr.{configuration['region']}"
        f".amazonaws.com/{configuration['name_prefix']}-demo/api"
    )


def terraform_plan(
    environment: Mapping[str, str],
    temporary: Path,
    *,
    label: str,
    destroy: bool = False,
) -> tuple[Path, Path]:
    plan_path = temporary / f"{label}.tfplan"
    json_path = temporary / f"{label}.json"
    arguments = [
        "plan",
        "-input=false",
        "-lock-timeout=5m",
        "-no-color",
        f"-out={plan_path}",
    ]
    if destroy:
        arguments.append("-destroy")
    run_silent(
        "terraform",
        terraform_arguments(*arguments),
        environment=environment,
        timeout_seconds=1800,
    )
    plan_json = capture_silent(
        "terraform",
        terraform_arguments("show", "-json", str(plan_path)),
        environment=environment,
        timeout_seconds=600,
        max_bytes=MAX_CAPTURE_BYTES,
    )
    json_path.write_bytes(plan_json)
    return plan_path, json_path


def terraform_apply(
    environment: Mapping[str, str],
    plan_path: Path,
) -> None:
    run_silent(
        "terraform",
        terraform_arguments(
            "apply",
            "-input=false",
            "-lock-timeout=5m",
            "-no-color",
            str(plan_path),
        ),
        environment=environment,
        timeout_seconds=3600,
    )


def plan_operation(configuration: Mapping[str, str], temporary: Path) -> None:
    require_final_profile(
        region=configuration.get("region"),
        domain=configuration.get("frontend_domain"),
    )
    base_environment = child_environment(
        os.environ,
        digest=PLACEHOLDER_DIGEST,
        desired_count=0,
        isolation_root=temporary / "isolation",
    )
    terraform_init(configuration, base_environment)
    state_path, state_snapshot = terraform_state_list(
        base_environment, temporary, suffix="plan"
    )
    audit_pulled_state(
        state_path,
        state_snapshot,
        mode="existing",
        configuration=configuration,
    )
    digest = existing_digest(configuration, base_environment)
    desired_count = 0 if digest == PLACEHOLDER_DIGEST else 1
    environment = child_environment(
        os.environ,
        digest=digest,
        desired_count=desired_count,
        isolation_root=temporary / "isolation",
    )
    require_session_window(configuration, 1_800)
    _, json_path = terraform_plan(environment, temporary, label="review")
    audit_plan(load_plan(json_path), mode="review", phase=None)
    print(
        "Plan real concluído sem apply ou mutação de infraestrutura; o lock "
        "S3 nativo foi usado sem publicar plano, state ou outputs."
    )


def strict_json(content: str | bytes, *, context: str) -> object:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                fail(f"{context} possui chave JSON duplicada.")
            document[key] = value
        return document

    def reject_nonfinite(value: str) -> NoReturn:
        del value
        fail(f"{context} possui número não finito.")

    try:
        if type(content) is bytes:
            content = content.decode("utf-8", errors="strict")
        elif type(content) is not str:
            fail(f"{context} não usa o tipo de texto canônico.")
        return json.loads(
            content,
            object_pairs_hook=object_pairs,
            parse_constant=reject_nonfinite,
        )
    except (UnicodeError, json.JSONDecodeError):
        raise AwsDeliveryError(f"{context} não é JSON UTF-8 válido.") from None


def parse_ecr_lookup(
    content: str | bytes,
    *,
    account_id: str,
    repository_name: str,
    image_tag: str,
) -> str | None:
    if (
        ACCOUNT_PATTERN.fullmatch(account_id) is None
        or re.fullmatch(r"^[a-z][a-z0-9-]{2,19}-demo/api$", repository_name) is None
        or IMAGE_TAG_PATTERN.fullmatch(image_tag) is None
    ):
        fail("Identidade da consulta ECR está fora do contrato.")
    raw_document = strict_json(content, context="Consulta da tag ECR")
    if type(raw_document) is not dict:
        fail("Consulta da tag ECR possui tipo inválido.")
    document = cast(dict[str, object], raw_document)
    if set(document) != {"failures", "images"}:
        fail("Consulta da tag ECR diverge da estrutura fechada.")
    raw_images = document.get("images")
    raw_failures = document.get("failures")
    if type(raw_images) is not list or type(raw_failures) is not list:
        fail("Consulta da tag ECR não possui coleções canônicas.")
    images = cast(list[object], raw_images)
    failures = cast(list[object], raw_failures)

    if len(images) == 1 and not failures:
        if type(images[0]) is not dict:
            fail("Imagem ECR encontrada possui tipo inválido.")
        image = cast(dict[str, object], images[0])
        if set(image) != {"imageId", "registryId", "repositoryName"}:
            fail("Imagem ECR encontrada diverge da estrutura fechada.")
        image_id = image.get("imageId")
        if type(image_id) is not dict:
            fail("Imagem ECR encontrada não possui identidade canônica.")
        identity = cast(dict[str, object], image_id)
        digest = identity.get("imageDigest")
        if (
            set(identity) != {"imageDigest", "imageTag"}
            or image.get("registryId") != account_id
            or image.get("repositoryName") != repository_name
            or identity.get("imageTag") != image_tag
            or type(digest) is not str
            or DIGEST_PATTERN.fullmatch(digest) is None
        ):
            fail("Imagem ECR encontrada não corresponde à identidade aprovada.")
        return digest

    if not images and len(failures) == 1:
        if type(failures[0]) is not dict:
            fail("Ausência ECR possui tipo inválido.")
        failure = cast(dict[str, object], failures[0])
        image_id = failure.get("imageId")
        if type(image_id) is not dict:
            fail("Ausência ECR não possui identidade canônica.")
        identity = cast(dict[str, object], image_id)
        if (
            set(failure) != {"failureCode", "imageId"}
            or failure.get("failureCode") != "ImageNotFound"
            or set(identity) != {"imageTag"}
            or identity.get("imageTag") != image_tag
        ):
            fail("Falha ECR não comprova ausência canônica da tag.")
        return None
    fail("Consulta da tag ECR retornou estado ambíguo.")


def published_image_digest(
    configuration: Mapping[str, str],
    environment: Mapping[str, str],
    *,
    image_tag: str,
) -> str | None:
    repository_name = f"{configuration['name_prefix']}-demo/api"
    result = capture_silent(
        "aws",
        (
            "ecr",
            "batch-get-image",
            "--repository-name",
            repository_name,
            "--image-ids",
            f"imageTag={image_tag}",
            "--region",
            configuration["region"],
            "--query",
            "{images:images[].{imageId:imageId,registryId:registryId,"
            "repositoryName:repositoryName},failures:failures[].{failureCode:"
            "failureCode,imageId:imageId}}",
            "--output",
            "json",
        ),
        environment=environment,
        timeout_seconds=60,
        max_bytes=100_000,
    )
    return parse_ecr_lookup(
        result,
        account_id=configuration["account_id"],
        repository_name=repository_name,
        image_tag=image_tag,
    )


def parse_build_digest(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8")
        if len(content) > 1_000_000:
            fail("Metadata do build excede o limite operacional.")
        raw_document = strict_json(content, context="Metadata OCI do build")
    except (OSError, UnicodeError):
        raise AwsDeliveryError("Metadata OCI do build é inválida.") from None
    if type(raw_document) is not dict:
        fail("Metadata OCI do build possui tipo inválido.")
    document = cast(dict[str, object], raw_document)
    digest = document.get("containerimage.digest")
    if type(digest) is not str or DIGEST_PATTERN.fullmatch(digest) is None:
        fail("Build não devolveu digest OCI canônico.")
    return digest


def login_and_build(
    configuration: Mapping[str, str],
    environment: Mapping[str, str],
    temporary: Path,
) -> str:
    builder = environment.get("BUILDX_BUILDER")
    docker_config = environment.get("DOCKER_CONFIG")
    if (
        type(builder) is not str
        or BUILDX_BUILDER_PATTERN.fullmatch(builder) is None
        or type(docker_config) is not str
        or not docker_config
        or not Path(docker_config).is_absolute()
        or not Path(docker_config).is_dir()
    ):
        fail("Build exige o builder e o DOCKER_CONFIG isolados aprovados.")
    repository = terraform_output(environment, "ecr_repository_url")
    if repository != expected_repository(configuration):
        fail("Output ECR diverge do repositório aprovado.")
    registry = repository.split("/", maxsplit=1)[0]
    external_environment = DeliveryEnvironment(
        {key: value for key, value in environment.items() if not key.startswith("TF_")}
    )
    image_tag = f"sha-{configuration['source_sha']}"
    existing = published_image_digest(
        configuration,
        external_environment,
        image_tag=image_tag,
    )
    if existing is not None:
        return existing
    require_session_window(configuration, 2_400)
    password = capture_silent(
        "aws",
        ("ecr", "get-login-password", "--region", configuration["region"]),
        environment=external_environment,
        timeout_seconds=60,
        max_bytes=4096,
    )
    if not password.strip() or len(password) > 4096:
        fail("ECR não devolveu credencial efêmera válida.")
    capture_silent(
        "docker",
        ("login", "--username", "AWS", "--password-stdin", registry),
        environment=external_environment,
        input_bytes=password,
        timeout_seconds=60,
        max_bytes=100_000,
    )
    metadata_path = temporary / "build-metadata.json"
    image_reference = f"{repository}:{image_tag}"
    run_silent(
        "docker",
        (
            "buildx",
            "build",
            "--builder",
            builder,
            "--file",
            "apps/api/Dockerfile",
            "--metadata-file",
            str(metadata_path),
            "--platform",
            "linux/amd64",
            "--provenance=false",
            "--push",
            "--sbom=false",
            "--tag",
            image_reference,
            ".",
        ),
        environment=external_environment,
        timeout_seconds=2400,
    )
    digest = parse_build_digest(metadata_path)
    remote_digest = published_image_digest(
        configuration,
        external_environment,
        image_tag=image_tag,
    )
    if remote_digest != digest:
        fail("Digest OCI publicado não foi confirmado pelo ECR.")
    return digest


def require_sen46_baseline(configuration: Mapping[str, str]) -> None:
    resolved_git = shutil.which("git")
    if resolved_git is None:
        fail("Git não está disponível para validar a baseline SEN-46.")
    path = os.environ.get("PATH")
    if type(path) is not str or not path:
        fail("PATH não está disponível para validar a baseline SEN-46.")
    git_environment = DeliveryEnvironment(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": path,
        }
    )
    for name in ("SYSTEMROOT", "WINDIR"):
        value = os.environ.get(name)
        if type(value) is str and value:
            git_environment[name] = value
    try:
        result = subprocess.run(  # noqa: S603 - git is resolved and arguments validated.
            [
                str(Path(resolved_git).resolve(strict=True)),
                "merge-base",
                "--is-ancestor",
                SEN46_BASELINE_SHA,
                configuration["source_sha"],
            ],
            check=False,
            cwd=REPOSITORY_ROOT,
            env=git_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        raise AwsDeliveryError("Validação da baseline SEN-46 falhou.") from None
    if result.returncode != 0:
        fail("SHA de deploy não contém a baseline SEN-46 aprovada.")


def foundation_operation(configuration: Mapping[str, str], temporary: Path) -> None:
    require_final_profile(
        region=configuration.get("region"),
        domain=configuration.get("frontend_domain"),
    )
    base_environment = child_environment(
        os.environ,
        digest=PLACEHOLDER_DIGEST,
        desired_count=0,
        isolation_root=temporary / "isolation",
    )
    terraform_init(configuration, base_environment)
    state_path, state_snapshot = terraform_state_list(
        base_environment,
        temporary,
        suffix="before-foundation",
        allow_missing=True,
    )
    if state_path.stat().st_size == 0:
        audit_pulled_state(
            state_path,
            state_snapshot,
            mode="fresh",
            configuration=configuration,
        )
        require_session_window(configuration, 1_800)
        foundation_plan, foundation_json = terraform_plan(
            base_environment, temporary, label="foundation"
        )
        audit_plan(
            load_plan(foundation_json),
            mode="deploy",
            phase="foundation",
        )
        require_session_window(configuration, 3_600)
        terraform_apply(base_environment, foundation_plan)
        state_path, state_snapshot = terraform_state_list(
            base_environment, temporary, suffix="after-foundation"
        )
    audit_pulled_state(
        state_path,
        state_snapshot,
        mode="existing",
        configuration=configuration,
    )
    if existing_digest(configuration, base_environment) != PLACEHOLDER_DIGEST:
        fail("Fundação não pode alterar um runtime já implantado.")
    require_session_window(configuration, 1_800)
    _, ready_json = terraform_plan(
        base_environment, temporary, label="foundation-ready"
    )
    audit_plan(
        load_plan(ready_json),
        mode="deploy",
        phase="foundation-ready",
    )
    print(
        "Fundação AWS concluída sem runtime: state completo, digest placeholder e "
        "desired count zero aprovados; usuário e token Cognito permanecem externos."
    )


def deploy_operation(configuration: Mapping[str, str], temporary: Path) -> None:
    require_final_profile(
        region=configuration.get("region"),
        domain=configuration.get("frontend_domain"),
    )
    require_sen46_baseline(configuration)
    token = validate_token(os.environ.get("AWS_DEMO_SMOKE_BEARER_TOKEN"))
    buildx_builder, docker_config = validated_buildx_configuration(os.environ)
    base_environment = child_environment(
        os.environ,
        digest=PLACEHOLDER_DIGEST,
        desired_count=0,
        isolation_root=temporary / "isolation",
        buildx_builder=buildx_builder,
        docker_config=docker_config,
    )
    terraform_init(configuration, base_environment)
    state_path, state_snapshot = terraform_state_list(
        base_environment,
        temporary,
        suffix="before-deploy",
        allow_missing=True,
    )
    if state_path.stat().st_size == 0:
        fail("Deploy de runtime exige a fundação AWS concluída previamente.")
    audit_pulled_state(
        state_path,
        state_snapshot,
        mode="existing",
        configuration=configuration,
    )

    digest = login_and_build(configuration, base_environment, temporary)
    runtime_environment = child_environment(
        os.environ,
        digest=digest,
        desired_count=1,
        isolation_root=temporary / "isolation",
    )
    require_session_window(configuration, 1_800)
    runtime_plan, runtime_json = terraform_plan(
        runtime_environment, temporary, label="runtime"
    )
    image_reference = f"{expected_repository(configuration)}@{digest}"
    audit_plan(
        load_plan(runtime_json),
        mode="deploy",
        phase="runtime",
        identity=configuration,
        expected_image=image_reference,
    )
    require_session_window(configuration, 3_600)
    terraform_apply(runtime_environment, runtime_plan)

    deployed_state, deployed_snapshot = terraform_state_list(
        runtime_environment,
        temporary,
        suffix="after-deploy",
    )
    audit_pulled_state(
        deployed_state,
        deployed_snapshot,
        mode="existing",
        configuration=configuration,
        expected_image=image_reference,
    )

    endpoint = validate_endpoint(
        terraform_output(runtime_environment, "api_base_url"),
        configuration["region"],
    )
    frontend_bucket = validated_frontend_bucket_output(
        configuration,
        terraform_output(runtime_environment, "frontend_bucket_name"),
    )
    frontend_origin = terraform_output(runtime_environment, "frontend_url")
    if frontend_origin != f"https://{configuration['frontend_domain']}":
        fail("Output do frontend diverge da origem final aprovada.")
    distribution_id = terraform_output(runtime_environment, "frontend_distribution_id")
    cognito_client_id = terraform_output(runtime_environment, "cognito_client_id")
    cognito_origin = terraform_output(runtime_environment, "cognito_hosted_ui_origin")
    stage_root = temporary / "frontend-stage"
    staged = stage_frontend(
        REPOSITORY_ROOT / "apps/web/src",
        stage_root,
        configuration["source_sha"],
    )
    public_config = runtime_config(
        api_origin=endpoint,
        client_id=cognito_client_id,
        cognito_origin=cognito_origin,
    )
    staged = add_runtime_config(
        staged,
        stage_root,
        api_origin=endpoint,
        client_id=cognito_client_id,
        cognito_origin=cognito_origin,
    )
    require_session_window(configuration, 1_800)
    publish_frontend(
        staged,
        bucket=frontend_bucket,
        distribution_id=distribution_id,
        runner=command_runner,
    )
    run_smoke(endpoint, token)
    run_published_smoke(
        frontend_origin=frontend_origin,
        api_origin=endpoint,
        cognito_origin=cognito_origin,
        runtime_config=public_config,
        staged=staged,
        bearer_token=token,
    )
    print(
        "Deploy concluído por digest, frontend allowlisted e smokes autenticados "
        "aprovados; nenhum plano, state ou output sensível foi publicado."
    )


def teardown_operation(configuration: Mapping[str, str], temporary: Path) -> None:
    require_final_profile(
        region=configuration.get("region"),
        domain=configuration.get("frontend_domain"),
    )
    base_environment = child_environment(
        os.environ,
        digest=PLACEHOLDER_DIGEST,
        desired_count=0,
        isolation_root=temporary / "isolation",
    )
    terraform_init(configuration, base_environment)
    state_path, state_snapshot = terraform_state_list(
        base_environment,
        temporary,
        suffix="before-destroy",
        allow_missing=True,
    )
    if state_path.stat().st_size == 0:
        audit_pulled_state(
            state_path,
            state_snapshot,
            mode="destroyed",
            configuration=configuration,
        )
        empty_state = state_path
        empty_snapshot = state_snapshot
    else:
        audit_pulled_state(
            state_path,
            state_snapshot,
            mode="destroyable",
            configuration=configuration,
        )
        require_session_window(configuration, 1_800)
        destroy_plan, destroy_json = terraform_plan(
            base_environment,
            temporary,
            label="destroy",
            destroy=True,
        )
        audit_plan(
            load_plan(destroy_json),
            mode="destroy",
            phase=None,
            identity=configuration,
        )
        require_session_window(configuration, 3_600)
        terraform_apply(base_environment, destroy_plan)
        empty_state, empty_snapshot = terraform_state_list(
            base_environment, temporary, suffix="after-destroy"
        )
    audit_pulled_state(
        empty_state,
        empty_snapshot,
        mode="destroyed",
        configuration=configuration,
    )
    queries = inventory_queries(
        account_id=configuration["account_id"],
        region=configuration["region"],
        name_prefix=configuration["name_prefix"],
        frontend_domain=configuration["frontend_domain"],
    )
    scan_inventory(queries, command_runner)
    print(
        "Teardown aprovado: plano destrutivo exato aplicado, state vazio e "
        "inventário AWS sem órfãos no escopo."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa uma operação manual protegida do perfil AWS demo."
    )
    parser.add_argument(
        "operation", choices=("plan", "foundation", "deploy", "teardown")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configuration = validated_configuration(os.environ)
    with tempfile.TemporaryDirectory(prefix="sen68-delivery-") as temporary_name:
        temporary = Path(temporary_name)
        if args.operation == "plan":
            plan_operation(configuration, temporary)
        elif args.operation == "foundation":
            foundation_operation(configuration, temporary)
        elif args.operation == "deploy":
            deploy_operation(configuration, temporary)
        else:
            teardown_operation(configuration, temporary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AwsDeliveryError,
        DeliveryGateError,
        FrontendDeliveryError,
        OrphanInventoryError,
        PublishedSmokeError,
        RemoteSmokeError,
    ) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("Operação AWS falhou com segurança.", file=sys.stderr)
        raise SystemExit(1) from None
