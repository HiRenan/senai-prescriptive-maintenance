"""Authenticated, content-free remote smoke for the synthetic API scenarios."""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import time
from base64 import urlsafe_b64decode
from collections.abc import Mapping
from http.client import HTTPSConnection
from typing import Any, NoReturn, cast
from urllib.parse import urlsplit

MAX_RESPONSE_BYTES = 1_000_000
MIN_TOKEN_REMAINING_SECONDS = 6_000
CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
JWT_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]{10,4096}\.[A-Za-z0-9_-]{10,4096}\.[A-Za-z0-9_-]{10,4096}$"
)
API_ID_PATTERN = re.compile(r"^[a-z0-9]{10}$")
RESULT_FIELDS = {
    "abstention",
    "analysis_id",
    "citations",
    "diagnosis",
    "model_id",
    "neighbors",
    "outcome",
    "prescription",
    "support",
    "warnings",
}
BASE_FEATURES = {
    "z_rms_velocity_mm_s": 1.2,
    "temperature_c": 42.0,
    "x_rms_velocity_mm_s": 1.1,
    "z_peak_acceleration_g": 0.3,
    "x_peak_acceleration_g": 0.25,
    "z_peak_vel_comp_freq_hz": 60.0,
    "x_peak_vel_comp_freq_hz": 58.0,
    "z_rms_acceleration_g": 0.08,
    "x_rms_acceleration_g": 0.07,
    "z_kurtosis": 3.1,
    "x_kurtosis": 3.0,
    "z_crest_factor": 1.8,
    "x_crest_factor": 1.7,
    "z_peak_velocity_mm_s": 2.4,
    "x_peak_velocity_mm_s": 2.2,
    "z_high_freq_rms_accel_g": 0.04,
    "x_high_freq_rms_accel_g": 0.03,
}
SCENARIOS = {
    "normal": 1000.0,
    "documented_fault": 1100.0,
    "undocumented_fault": 1200.0,
}
FORBIDDEN_TLS_ENVIRONMENT = (
    "CURL_CA_BUNDLE",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SSLKEYLOGFILE",
)


class RemoteSmokeError(RuntimeError):
    """Raised without endpoint, token, request, response, or provider details."""


def fail(message: str) -> NoReturn:
    raise RemoteSmokeError(message)


def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            fail("Resposta remota possui chave JSON duplicada.")
        result[key] = value
    return result


def reject_nonfinite(value: str) -> NoReturn:
    del value
    fail("Resposta remota possui número não finito.")


def mapping(value: object, *, context: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        fail(f"{context} não é um objeto JSON base.")
    return cast(dict[str, Any], value)


def sequence(value: object, *, context: str) -> list[object]:
    if type(value) is not list:
        fail(f"{context} não é uma lista JSON base.")
    return cast(list[object], value)


def parse_json(content: bytes) -> Mapping[str, Any]:
    try:
        text = content.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=reject_nonfinite,
        )
    except (UnicodeError, json.JSONDecodeError):
        raise RemoteSmokeError("Resposta remota não é JSON UTF-8 válido.") from None
    return mapping(parsed, context="resposta remota")


def validate_endpoint(raw_endpoint: object, raw_region: object) -> str:
    if type(raw_endpoint) is not str or type(raw_region) is not str:
        fail("Endpoint e região devem usar o tipo canônico.")
    endpoint = raw_endpoint
    region = raw_region
    if re.fullmatch(r"^[a-z]{2}-[a-z]+-\d$", region) is None:
        fail("Região AWS possui formato inválido.")
    parsed = urlsplit(endpoint)
    expected_suffix = f".execute-api.{region}.amazonaws.com"
    hostname = parsed.hostname or ""
    api_id = hostname.removesuffix(expected_suffix)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not hostname.endswith(expected_suffix)
        or API_ID_PATTERN.fullmatch(api_id) is None
    ):
        fail("Endpoint remoto não pertence ao API Gateway regional esperado.")
    return f"https://{hostname}"


def validate_token(
    raw_token: object,
    *,
    now_epoch: int | None = None,
    minimum_remaining_seconds: int = MIN_TOKEN_REMAINING_SECONDS,
) -> str:
    if type(raw_token) is not str or JWT_PATTERN.fullmatch(raw_token) is None:
        fail("Bearer token temporário está ausente ou possui formato inválido.")
    if (
        type(minimum_remaining_seconds) is not int
        or minimum_remaining_seconds < 0
        or minimum_remaining_seconds > 7_200
    ):
        fail("Janela mínima do token está fora do contrato.")
    encoded_payload = raw_token.split(".", maxsplit=2)[1]
    try:
        padding = "=" * (-len(encoded_payload) % 4)
        payload_bytes = urlsafe_b64decode(encoded_payload + padding)
        payload = parse_json(payload_bytes)
    except (ValueError, UnicodeError):
        raise RemoteSmokeError(
            "Bearer token temporário possui payload inválido."
        ) from None
    current_epoch = int(time.time()) if now_epoch is None else now_epoch
    expiration = payload.get("exp")
    issued_at = payload.get("iat")
    if (
        type(current_epoch) is not int
        or type(expiration) is not int
        or type(issued_at) is not int
        or payload.get("token_use") != "access"
        or issued_at > current_epoch + 60
        or expiration - issued_at > 7_200
        or expiration - current_epoch < minimum_remaining_seconds
    ):
        fail("Bearer token não cobre toda a janela protegida do runtime.")
    return raw_token


def verified_tls_context() -> ssl.SSLContext:
    if any(name in os.environ for name in FORBIDDEN_TLS_ENVIRONMENT):
        fail("Ambiente TLS contém override de confiança ou key log proibido.")
    return ssl.create_default_context()


def request_json(
    endpoint: str,
    token: str,
    *,
    path: str,
    payload: Mapping[str, object] | None,
    timeout_seconds: float = 15.0,
) -> Mapping[str, Any]:
    body = None
    method = "GET"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "senai-pm-remote-smoke/1",
    }
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        method = "POST"
        headers["Content-Type"] = "application/json"
    hostname = urlsplit(endpoint).hostname
    if hostname is None:
        fail("Endpoint remoto perdeu o hostname validado.")
    connection: HTTPSConnection | None = None
    close_failed = False
    try:
        connection = HTTPSConnection(
            hostname,
            port=443,
            timeout=timeout_seconds,
            context=verified_tls_context(),
        )
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        try:
            status = response.status
            content_type = response.headers.get_content_type()
            correlation_id = response.getheader("x-correlation-id")
            content = response.read(MAX_RESPONSE_BYTES + 1)
        finally:
            response.close()
    except Exception:
        raise RemoteSmokeError(
            "Chamada autenticada remota falhou com segurança."
        ) from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                close_failed = True
    if close_failed:
        fail("Chamada autenticada remota falhou com segurança.")
    if status != 200 or content_type != "application/json":
        fail("Chamada autenticada não devolveu o contrato HTTP esperado.")
    if len(content) > MAX_RESPONSE_BYTES:
        fail("Resposta remota excede o limite operacional.")
    if (
        type(correlation_id) is not str
        or CORRELATION_ID_PATTERN.fullmatch(correlation_id) is None
    ):
        fail("Resposta remota não devolveu correlation ID seguro.")
    return parse_json(content)


def require_anonymous_denial(
    endpoint: str,
    *,
    method: str,
    path: str,
    body: bytes | None,
    timeout_seconds: float = 15.0,
) -> None:
    hostname = urlsplit(endpoint).hostname
    if hostname is None:
        fail("Endpoint remoto perdeu o hostname validado.")
    connection: HTTPSConnection | None = None
    close_failed = False
    try:
        connection = HTTPSConnection(
            hostname,
            port=443,
            timeout=timeout_seconds,
            context=verified_tls_context(),
        )
        headers = {
            "Accept": "application/json",
            "User-Agent": "senai-pm-remote-smoke/1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        try:
            status = response.status
        finally:
            response.close()
    except Exception:
        raise RemoteSmokeError(
            "Preflight remoto sem autenticação falhou com segurança."
        ) from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                close_failed = True
    if close_failed:
        fail("Preflight remoto sem autenticação falhou com segurança.")
    if type(status) is not int or status not in {401, 403}:
        fail("Endpoint remoto não comprovou a proteção por autenticação.")


def require_authentication(
    endpoint: str,
    *,
    timeout_seconds: float = 15.0,
) -> None:
    require_anonymous_denial(
        endpoint,
        method="GET",
        path="/health/ready",
        body=None,
        timeout_seconds=timeout_seconds,
    )
    synthetic_body = json.dumps(
        scenario_request(SCENARIOS["normal"]),
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    require_anonymous_denial(
        endpoint,
        method="POST",
        path="/analysis",
        body=synthetic_body,
        timeout_seconds=timeout_seconds,
    )


def validate_readiness(payload: object) -> None:
    if payload != {"status": "ready"}:
        fail("Readiness remota diverge do contrato exato.")


def validate_scenario(name: str, payload: object) -> None:
    response = mapping(payload, context="resultado de análise")
    if set(response) != RESULT_FIELDS or response.get("outcome") != name:
        fail("Resultado remoto diverge do estado sintético esperado.")
    citations = sequence(response.get("citations"), context="citações")
    neighbors = sequence(response.get("neighbors"), context="vizinhos")
    if not neighbors:
        fail("Resultado remoto omitiu vizinhos content-free.")
    if name == "normal":
        if (
            response.get("abstention") is not None
            or response.get("prescription") is not None
            or citations
        ):
            fail("Cenário normal cruzou prescrição, citação ou abstenção.")
        return
    if name == "documented_fault":
        if (
            response.get("abstention") is not None
            or type(response.get("prescription")) is not dict
            or not citations
        ):
            fail("Falha documentada não preservou prescrição e citações.")
        return
    abstention = mapping(response.get("abstention"), context="abstenção")
    if (
        abstention.get("reason") != "undocumented_fault"
        or response.get("prescription") is not None
        or citations
    ):
        fail("Falha sem documentação não recusou prescrição com segurança.")


def scenario_request(rpm: float) -> dict[str, object]:
    features = dict(BASE_FEATURES)
    features["rpm"] = rpm
    return {"features": features, "top_k": 3}


def run_smoke(endpoint: str, token: str) -> None:
    require_authentication(endpoint)
    readiness_error: RemoteSmokeError | None = None
    for attempt in range(12):
        try:
            validate_readiness(
                request_json(endpoint, token, path="/health/ready", payload=None)
            )
            readiness_error = None
            break
        except RemoteSmokeError as error:
            readiness_error = error
            if attempt < 11:
                time.sleep(5)
    if readiness_error is not None:
        raise RemoteSmokeError("Readiness remota não estabilizou no prazo.") from None

    for name, rpm in SCENARIOS.items():
        response = request_json(
            endpoint,
            token,
            path="/analysis",
            payload=scenario_request(rpm),
        )
        validate_scenario(name, response)


def main() -> int:
    endpoint = validate_endpoint(
        os.environ.get("AWS_DEMO_API_BASE_URL"),
        os.environ.get("AWS_REGION"),
    )
    token = validate_token(os.environ.get("AWS_DEMO_SMOKE_BEARER_TOKEN"))
    run_smoke(endpoint, token)
    print(
        "Smoke remoto aprovado: acesso anônimo recusado, readiness autenticada e "
        "cenários normal, falha documentada e recusa sem conteúdo em logs."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RemoteSmokeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("Smoke remoto falhou com segurança.", file=sys.stderr)
        raise SystemExit(1) from None
