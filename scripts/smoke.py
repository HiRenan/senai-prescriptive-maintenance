"""Cross-platform smoke checks for the local development foundation."""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI
from pydantic import ValidationError

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
ENV_EXAMPLE: Final = REPOSITORY_ROOT / ".env.example"
OPENAPI_SNAPSHOT: Final = REPOSITORY_ROOT / "apps" / "api" / "openapi" / "v1.json"
LOOPBACK_HOST: Final = "127.0.0.1"
EXPECTED_LIVENESS_BODY: Final = b'{"status":"ok"}'
EXPECTED_READINESS_BODY: Final = b'{"status":"ready"}'
ANALYSIS_MODE_HEADER: Final = "X-Analysis-Mode"
CORRELATION_ID_HEADER: Final = "X-Correlation-ID"
CORRELATION_ID_PATTERN: Final = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,62}[A-Za-z0-9])?"
)
APPLICATION_ENVIRONMENT_PREFIX: Final = "PRESCRIPTIVE_MAINTENANCE_"
STARTUP_TIMEOUT_SECONDS: Final = 15.0
REQUEST_TIMEOUT_SECONDS: Final = 1.0
SHUTDOWN_TIMEOUT_SECONDS: Final = 10.0
MAX_OPENAPI_RESPONSE_BYTES: Final = 2 * 1024 * 1024


class SmokeFailure(RuntimeError):
    """Describe an expected smoke failure without exposing sensitive values."""


def _resolve_executable(name: str, failure_message: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise SmokeFailure(failure_message)
    return executable


def _run_command(command: Sequence[str], failure_message: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            command,
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as error:
        raise SmokeFailure(f"{failure_message} Executável não encontrado.") from error

    if result.returncode != 0:
        raise SmokeFailure(f"{failure_message} Código de saída: {result.returncode}.")

    return result.stdout.strip()


def _parse_semantic_version(value: str, runtime: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", value.strip())
    if match is None:
        raise SmokeFailure(f"{runtime} retornou uma versão inválida.")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _check_runtimes() -> None:
    if sys.version_info[:2] != (3, 13):
        raise SmokeFailure(
            "Python 3.13 é obrigatório; "
            f"versão detectada: {sys.version_info.major}.{sys.version_info.minor}."
        )

    node_version = _parse_semantic_version(
        _run_command(
            (_resolve_executable("node", "Node.js não encontrado."), "--version"),
            "Falha ao verificar o Node.js.",
        ),
        "Node.js",
    )
    if node_version[0] != 22:
        raise SmokeFailure(
            f"Node.js 22 é obrigatório; versão detectada: {node_version[0]}."
        )

    pnpm_version = _parse_semantic_version(
        _run_command(
            (
                _resolve_executable("corepack", "Corepack não encontrado."),
                "pnpm",
                "--version",
            ),
            "Falha ao verificar o pnpm via Corepack.",
        ),
        "pnpm",
    )
    if pnpm_version != (10, 15, 1):
        detected_version = ".".join(str(part) for part in pnpm_version)
        raise SmokeFailure(
            f"pnpm 10.15.1 é obrigatório; versão detectada: {detected_version}."
        )

    print("Runtimes: Python 3.13, Node.js 22 e pnpm 10.15.1 verificados.")


def _load_application() -> FastAPI:
    try:
        package = importlib.import_module("prescriptive_maintenance")
        from prescriptive_maintenance.main import app
    except ImportError as error:
        raise SmokeFailure(
            "Não foi possível importar o pacote prescriptive_maintenance."
        ) from error

    if package.__name__ != "prescriptive_maintenance":
        raise SmokeFailure("O pacote importado não possui o namespace esperado.")

    print("Pacote: importação do backend verificada.")
    return app


@contextmanager
def _without_application_environment() -> Generator[None]:
    previous_values = {
        name: value
        for name, value in os.environ.items()
        if name.upper().startswith(APPLICATION_ENVIRONMENT_PREFIX)
    }
    for name in previous_values:
        os.environ.pop(name, None)

    try:
        yield
    finally:
        current_names = tuple(os.environ)
        for name in current_names:
            if name.upper().startswith(APPLICATION_ENVIRONMENT_PREFIX):
                os.environ.pop(name, None)
        for name, value in previous_values.items():
            os.environ[name] = value


def _check_example_configuration() -> None:
    if not ENV_EXAMPLE.is_file():
        raise SmokeFailure("O arquivo .env.example não foi encontrado na raiz.")

    try:
        from prescriptive_maintenance.settings import Settings

        with _without_application_environment():
            settings = Settings(
                _env_file=ENV_EXAMPLE  # pyright: ignore[reportCallIssue]
            )
    except (OSError, ValidationError) as error:
        raise SmokeFailure("Não foi possível carregar o .env.example.") from error

    database_url = settings.database_url
    if (
        settings.environment != "local"
        or settings.persistence_backend != "postgres"
        or settings.analysis_mode != "synthetic_demo"
        or database_url is None
        or database_url.scheme != "postgresql"
    ):
        raise SmokeFailure("O .env.example não contém a configuração local esperada.")

    print("Configuração: .env.example carregado explicitamente.")


def _check_compose_configuration() -> None:
    _run_command(
        (
            _resolve_executable("docker", "Docker não encontrado."),
            "compose",
            "--env-file",
            str(ENV_EXAMPLE),
            "config",
            "--quiet",
        ),
        "Falha ao validar docker compose config.",
    )
    print("Compose: configuração estática validada.")


def _reserve_ephemeral_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved_socket:
        reserved_socket.bind((LOOPBACK_HOST, 0))
        socket_address = reserved_socket.getsockname()
        return cast("tuple[str, int]", socket_address)[1]


def _read_liveness(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    request = Request(
        f"http://{LOOPBACK_HOST}:{port}/health/live",
        method="GET",
    )

    while True:
        if process.poll() is not None:
            raise SmokeFailure("O Uvicorn encerrou antes de responder à liveness.")

        try:
            # The URL is built exclusively from a fixed loopback host and local port.
            with urlopen(  # noqa: S310
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                status_code = response.status
                content_type = response.headers.get_content_type()
                correlation_id = response.headers.get(CORRELATION_ID_HEADER)
                analysis_mode = response.headers.get(ANALYSIS_MODE_HEADER)
                response_body = response.read(len(EXPECTED_LIVENESS_BODY) + 1)
        except HTTPError as error:
            raise SmokeFailure(
                f"A liveness respondeu com status HTTP {error.code}."
            ) from error
        except URLError as error:
            if time.monotonic() >= deadline:
                raise SmokeFailure(
                    "A liveness não respondeu dentro do tempo limite."
                ) from error
            time.sleep(0.05)
            continue

        if status_code != 200:
            raise SmokeFailure(f"A liveness respondeu com status HTTP {status_code}.")
        if content_type != "application/json":
            raise SmokeFailure("A liveness não respondeu com conteúdo JSON.")
        if response_body != EXPECTED_LIVENESS_BODY:
            raise SmokeFailure("A liveness respondeu com corpo inesperado.")
        if (
            correlation_id is None
            or CORRELATION_ID_PATTERN.fullmatch(correlation_id) is None
        ):
            raise SmokeFailure("A liveness não retornou um correlation ID seguro.")
        if analysis_mode != "synthetic_demo":
            raise SmokeFailure("A liveness não informou o modo de análise esperado.")
        return


def _read_readiness(port: int) -> None:
    request = Request(
        f"http://{LOOPBACK_HOST}:{port}/health/ready",
        method="GET",
    )
    try:
        with urlopen(  # noqa: S310
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            status_code = response.status
            content_type = response.headers.get_content_type()
            correlation_id = response.headers.get(CORRELATION_ID_HEADER)
            analysis_mode = response.headers.get(ANALYSIS_MODE_HEADER)
            response_body = response.read(len(EXPECTED_READINESS_BODY) + 1)
    except (HTTPError, URLError) as error:
        raise SmokeFailure("A readiness offline não respondeu corretamente.") from error

    if status_code != 200 or content_type != "application/json":
        raise SmokeFailure("A readiness offline respondeu incorretamente.")
    if response_body != EXPECTED_READINESS_BODY:
        raise SmokeFailure("A readiness offline respondeu com corpo inesperado.")
    if (
        correlation_id is None
        or CORRELATION_ID_PATTERN.fullmatch(correlation_id) is None
    ):
        raise SmokeFailure("A readiness não retornou um correlation ID seguro.")
    if analysis_mode != "synthetic_demo":
        raise SmokeFailure("A readiness não informou o modo de análise esperado.")


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()

    try:
        process.communicate(timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.communicate(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise SmokeFailure("Não foi possível encerrar o Uvicorn.") from error


def _check_liveness() -> None:
    port = _reserve_ephemeral_port()
    command = (
        sys.executable,
        "-m",
        "uvicorn",
        "prescriptive_maintenance.main:app",
        "--host",
        LOOPBACK_HOST,
        "--port",
        str(port),
        "--log-level",
        "error",
        "--no-access-log",
    )

    process_environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith(("AWS_", APPLICATION_ENVIRONMENT_PREFIX))
    }
    process_environment["PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT"] = "offline"
    process_environment["PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND"] = "memory"
    process_environment["PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE"] = "synthetic_demo"

    with TemporaryDirectory(prefix="prescriptive-maintenance-smoke-") as workdir:
        try:
            process = subprocess.Popen(  # noqa: S603
                command,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=process_environment,
            )
        except OSError as error:
            raise SmokeFailure("Não foi possível iniciar o Uvicorn.") from error

        try:
            _read_liveness(port, process)
            _read_readiness(port)
        finally:
            _stop_process(process)

    print("Health: liveness e readiness offline validadas por HTTP em loopback.")


def _parse_compose_records(output: str) -> list[Mapping[str, object]]:
    try:
        parsed: object = json.loads(output)
    except json.JSONDecodeError:
        records: list[Mapping[str, object]] = []
        try:
            for line in output.splitlines():
                item: object = json.loads(line)
                if not isinstance(item, dict):
                    raise SmokeFailure("docker compose ps retornou JSON inesperado.")
                records.append(cast("Mapping[str, object]", item))
        except json.JSONDecodeError as error:
            raise SmokeFailure("docker compose ps retornou JSON inválido.") from error
        return records

    if isinstance(parsed, dict):
        return [cast("Mapping[str, object]", parsed)]
    if isinstance(parsed, list):
        parsed_items = cast("list[object]", parsed)
        if all(isinstance(item, dict) for item in parsed_items):
            return [cast("Mapping[str, object]", item) for item in parsed_items]
    raise SmokeFailure("docker compose ps retornou JSON inesperado.")


def _run_psql(query: str, failure_message: str) -> str:
    return _run_command(
        (
            _resolve_executable("docker", "Docker não encontrado."),
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "--username",
            "prescriptive_maintenance",
            "--dbname",
            "prescriptive_maintenance",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            query,
        ),
        failure_message,
    )


def _check_services() -> None:
    records = _parse_compose_records(
        _run_command(
            (
                _resolve_executable("docker", "Docker não encontrado."),
                "compose",
                "ps",
                "--format",
                "json",
                "postgres",
            ),
            "Falha ao consultar o serviço postgres.",
        )
    )
    if len(records) != 1:
        raise SmokeFailure("O serviço postgres já iniciado não foi encontrado.")

    record = records[0]
    if record.get("State") != "running" or record.get("Health") != "healthy":
        raise SmokeFailure("O serviço postgres não está saudável.")

    extension_version = _run_psql(
        "SELECT extversion FROM pg_extension WHERE extname = 'vector';",
        "Falha ao consultar a extensão vector.",
    )
    if extension_version != "0.8.6":
        raise SmokeFailure("A extensão vector 0.8.6 não está instalada.")

    vector_result = _run_psql(
        "SELECT ('[1,2,3]'::vector <-> '[3,2,1]'::vector) > 0;",
        "Falha ao executar a operação vetorial mínima.",
    )
    if vector_result != "t":
        raise SmokeFailure("A operação vetorial mínima retornou resultado inesperado.")

    print("Serviços: PostgreSQL healthy e pgvector 0.8.6 verificados.")


def _application_host_port(variable_name: str, default: int) -> int:
    raw_value = os.environ.get(variable_name, str(default))
    if re.fullmatch(r"[1-9]\d{0,4}", raw_value) is None:
        raise SmokeFailure(f"{variable_name} deve ser uma porta TCP válida.")

    port = int(raw_value)
    if port > 65535:
        raise SmokeFailure(f"{variable_name} deve ser uma porta TCP válida.")
    return port


def _read_http_response(
    url: str,
    failure_subject: str,
    *,
    max_body_bytes: int,
) -> tuple[int, str, bytes, str | None]:
    # Callers build URLs only from fixed loopback hosts and validated numeric ports.
    request = Request(url, method="GET")  # noqa: S310
    try:
        with urlopen(  # noqa: S310
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            status_code = response.status
            content_type = response.headers.get_content_type()
            analysis_mode = response.headers.get(ANALYSIS_MODE_HEADER)
            body = response.read(max_body_bytes + 1)
    except HTTPError as error:
        raise SmokeFailure(
            f"{failure_subject} respondeu com status HTTP {error.code}."
        ) from error
    except URLError as error:
        raise SmokeFailure(f"{failure_subject} não respondeu em loopback.") from error

    if len(body) > max_body_bytes:
        raise SmokeFailure(f"{failure_subject} excedeu o limite de resposta.")
    return status_code, content_type, body, analysis_mode


def _check_container_liveness(url: str, service_name: str) -> None:
    status_code, content_type, body, analysis_mode = _read_http_response(
        url,
        f"A liveness do contêiner {service_name}",
        max_body_bytes=len(EXPECTED_LIVENESS_BODY),
    )
    if status_code != 200:
        raise SmokeFailure(
            f"A liveness do contêiner {service_name} respondeu com status "
            f"HTTP {status_code}."
        )
    if content_type != "application/json":
        raise SmokeFailure(
            f"A liveness do contêiner {service_name} não respondeu com JSON."
        )
    if body != EXPECTED_LIVENESS_BODY:
        raise SmokeFailure(
            f"A liveness do contêiner {service_name} respondeu com corpo inesperado."
        )
    if service_name == "api" and analysis_mode != "synthetic_demo":
        raise SmokeFailure(
            "A liveness do contêiner api não informou o modo de análise esperado."
        )


def _check_container_readiness(url: str) -> None:
    status_code, content_type, body, analysis_mode = _read_http_response(
        url,
        "A readiness do contêiner api",
        max_body_bytes=len(EXPECTED_READINESS_BODY),
    )
    if (
        status_code != 200
        or content_type != "application/json"
        or body != EXPECTED_READINESS_BODY
    ):
        raise SmokeFailure("A readiness do contêiner api respondeu incorretamente.")
    if analysis_mode != "synthetic_demo":
        raise SmokeFailure(
            "A readiness do contêiner api não informou o modo de análise esperado."
        )


def _check_containerized_applications() -> None:
    records = _parse_compose_records(
        _run_command(
            (
                _resolve_executable("docker", "Docker não encontrado."),
                "compose",
                "ps",
                "--format",
                "json",
                "api",
                "web",
            ),
            "Falha ao consultar os serviços de aplicação.",
        )
    )
    expected_services = {"api", "web"}
    records_by_service = {
        service: record
        for record in records
        if isinstance(service := record.get("Service"), str)
    }
    if set(records_by_service) != expected_services:
        raise SmokeFailure(
            "Os contêineres api e web já iniciados não foram encontrados."
        )
    if any(
        record.get("State") != "running" or record.get("Health") != "healthy"
        for record in records_by_service.values()
    ):
        raise SmokeFailure("Os contêineres api e web não estão saudáveis.")

    api_port = _application_host_port(
        "PRESCRIPTIVE_MAINTENANCE_API_HOST_PORT",
        8000,
    )
    web_port = _application_host_port(
        "PRESCRIPTIVE_MAINTENANCE_WEB_HOST_PORT",
        3000,
    )
    _check_container_liveness(
        f"http://{LOOPBACK_HOST}:{api_port}/health/live",
        "api",
    )
    _check_container_liveness(
        f"http://{LOOPBACK_HOST}:{web_port}/health/live",
        "web",
    )
    _check_container_readiness(
        f"http://{LOOPBACK_HOST}:{api_port}/health/ready",
    )

    status_code, content_type, body, _analysis_mode = _read_http_response(
        f"http://{LOOPBACK_HOST}:{api_port}/openapi.json",
        "O contrato OpenAPI do contêiner api",
        max_body_bytes=MAX_OPENAPI_RESPONSE_BYTES,
    )
    if status_code != 200 or content_type != "application/json":
        raise SmokeFailure(
            "O contrato OpenAPI do contêiner api respondeu incorretamente."
        )
    try:
        expected_openapi: object = json.loads(OPENAPI_SNAPSHOT.read_bytes())
        actual_openapi: object = json.loads(body)
    except (OSError, json.JSONDecodeError) as error:
        raise SmokeFailure(
            "Não foi possível comparar o contrato OpenAPI do contêiner api."
        ) from error
    if actual_openapi != expected_openapi:
        raise SmokeFailure(
            "O contrato OpenAPI do contêiner api diverge do snapshot v1."
        )

    print("Aplicações: api, web e contrato OpenAPI v1 verificados em contêineres.")


def _artifacts_mode_requested() -> bool:
    configured = os.environ.get("PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE")
    if configured is not None:
        if configured not in {"synthetic_demo", "artifacts"}:
            raise SmokeFailure("O modo de análise configurado é inválido.")
        return configured == "artifacts"
    dotenv = REPOSITORY_ROOT / ".env"
    if not dotenv.is_file():
        return False
    try:
        if dotenv.stat().st_size > 65_536:
            raise SmokeFailure("A configuração local de análise é inválida.")
        content = dotenv.read_text(encoding="utf-8", errors="strict")
    except SmokeFailure:
        raise
    except (OSError, UnicodeError):
        raise SmokeFailure("A configuração local de análise é inválida.") from None
    assignments = re.findall(
        r"(?m)^\s*PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE\s*=.*$",
        content,
    )
    configured_values = re.findall(
        r"(?m)^\s*PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE\s*=\s*"
        r"['\"]?([^'\"\s#]+)['\"]?\s*(?:#.*)?$",
        content,
    )
    if not assignments:
        return False
    if (
        len(assignments) != 1
        or len(configured_values) != 1
        or configured_values[0]
        not in {
            "synthetic_demo",
            "artifacts",
        }
    ):
        raise SmokeFailure("O modo de análise configurado é inválido.")
    return configured_values[0] == "artifacts"


def _check_artifacts() -> bool:
    if not _artifacts_mode_requested():
        print("Artefatos: indisponíveis; verificação opcional ignorada.")
        return False
    try:
        from prescriptive_maintenance.analysis_runtime import (
            compose_analysis_runtime,
        )
        from prescriptive_maintenance.settings import Settings

        dotenv = REPOSITORY_ROOT / ".env"
        settings = Settings(
            _env_file=(  # pyright: ignore[reportCallIssue]
                dotenv if dotenv.is_file() else None
            )
        )
        if settings.analysis_mode != "artifacts":
            raise SmokeFailure("O modo artifacts não está configurado.")
        summary = compose_analysis_runtime(settings).summary
    except SmokeFailure:
        raise
    except Exception:
        raise SmokeFailure(
            "Os artefatos configurados estão indisponíveis ou incompatíveis."
        ) from None

    print(
        "Artefatos: composição aprovada "
        f"(amostras={summary.model_sample_count}, "
        f"registros={summary.index_record_count}, "
        f"documentos={summary.approved_document_count}, "
        f"chunks={summary.indexed_chunk_count}, "
        f"classes_mapeadas={summary.mapped_fault_count})."
    )
    return True


def _run_smoke(
    with_services: bool,
    with_applications: bool,
    with_artifacts: bool,
) -> bool:
    _check_runtimes()
    _load_application()
    _check_example_configuration()
    _check_compose_configuration()
    _check_liveness()
    if with_services:
        _check_services()
    if with_applications:
        _check_containerized_applications()
    if with_artifacts:
        return _check_artifacts()
    return False


def main(
    with_services: bool = False,
    with_applications: bool = False,
    with_artifacts: bool = False,
    extra_args: str | None = None,
) -> None:
    """Run smoke checks and translate failures into a concise non-zero result."""
    try:
        if extra_args:
            raise SmokeFailure(
                "Argumentos adicionais não são aceitos; use somente as opções "
                "de smoke documentadas."
            )
        artifacts_approved = _run_smoke(
            with_services,
            with_applications,
            with_artifacts,
        )
    except SmokeFailure as error:
        print(f"Smoke falhou: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        print(
            f"Smoke falhou: erro inesperado ({type(error).__name__}).",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    enabled_modes = [
        name
        for enabled, name in (
            (with_services, "serviços"),
            (with_applications, "aplicações em contêineres"),
            (with_artifacts and artifacts_approved, "artefatos aprovados"),
        )
        if enabled
    ]
    selected_mode = ", ".join(enabled_modes) if enabled_modes else "base local"
    print(f"Smoke concluído com sucesso ({selected_mode}).")
