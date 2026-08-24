"""Smoke the published frontend and authenticated browser-facing API contract."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import NoReturn, cast

from frontend_delivery import (
    INDEX_KEY,
    PUBLISHED_DOMAIN,
    RUNTIME_CONFIG_KEY,
    StagedFrontend,
)
from remote_smoke import (
    SCENARIOS,
    RemoteSmokeError,
    scenario_request,
    verified_tls_context,
)

MAX_RESPONSE_BYTES = 1_000_000
ALLOWED_OUTCOMES = {
    "normal",
    "documented_fault",
    "undocumented_fault",
    "out_of_distribution",
    "degraded",
}
SYNTHETIC_OAUTH_STATE = "s" * 43
SYNTHETIC_CODE_CHALLENGE = "c" * 43


class PublishedSmokeError(RuntimeError):
    """Raised without response bodies, bearer values, or remote identifiers."""


def fail(message: str) -> NoReturn:
    raise PublishedSmokeError(message)


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)


Transport = Callable[[HttpRequest], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[no-untyped-def]
        self, request, file_pointer, code, message, headers, new_url
    ) -> None:
        del request, file_pointer, code, message, headers, new_url
        return None


def http_transport(request: HttpRequest) -> HttpResponse:
    parsed = urllib.parse.urlsplit(request.url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        fail("Smoke publicado recusou URL fora de HTTPS estrito.")
    try:
        tls_context = verified_tls_context()
    except RemoteSmokeError:
        fail("Ambiente TLS do smoke publicado é inseguro.")
    raw = urllib.request.Request(  # noqa: S310 - URL is validated above.
        request.url,
        data=request.body,
        headers=dict(request.headers),
        method=request.method,
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=tls_context),
        _NoRedirect(),
    )
    try:
        response = opener.open(raw, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    except (OSError, urllib.error.URLError):
        fail("Smoke publicado não alcançou um endpoint obrigatório.")
    try:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            fail("Resposta do smoke publicado excedeu o limite.")
        return HttpResponse(
            status=response.status,
            headers={key.lower(): value for key, value in response.headers.items()},
            body=body,
        )
    finally:
        response.close()


def _request(
    transport: Transport,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
) -> HttpResponse:
    response = transport(HttpRequest(method, url, headers or {}, body))
    if type(response) is not HttpResponse or not 100 <= response.status <= 599:
        fail("Transporte do smoke publicou resposta inválida.")
    if len(response.body) > MAX_RESPONSE_BYTES:
        fail("Resposta do smoke publicado excedeu o limite.")
    return response


def _media_type(response: HttpResponse) -> str:
    return response.headers.get("content-type", "").split(";", maxsplit=1)[0].strip()


def _json(response: HttpResponse, *, context: str) -> object:
    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        fail(f"{context} do smoke publicado não é JSON UTF-8 válido.")


def _assert_security_headers(response: HttpResponse, *, api: str, cognito: str) -> None:
    csp = response.headers.get("content-security-policy", "")
    expected = {
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self'",
        f"connect-src 'self' {api} {cognito}",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "object-src 'none'",
    }
    if {
        directive.strip() for directive in csp.split(";") if directive.strip()
    } != expected:
        fail("CSP publicada diverge das origens e diretivas exatas.")
    exact = {
        "permissions-policy": "camera=(), geolocation=(), microphone=()",
        "referrer-policy": "no-referrer",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
    }
    if any(response.headers.get(key) != value for key, value in exact.items()):
        fail("Cabeçalhos seguros do frontend publicado divergiram.")
    hsts = [
        part.strip()
        for part in response.headers.get("strict-transport-security", "").split(";")
    ]
    if (
        len(hsts) != 2
        or sum(
            re.fullmatch(r"max-age\s*=\s*31536000", part, flags=re.IGNORECASE)
            is not None
            for part in hsts
        )
        != 1
        or sum(part.lower() == "includesubdomains" for part in hsts) != 1
    ):
        fail("HSTS publicado diverge de max-age e includeSubDomains exatos.")
    xss = [
        part.strip() for part in response.headers.get("x-xss-protection", "").split(";")
    ]
    if (
        len(xss) != 2
        or sum(part == "1" for part in xss) != 1
        or sum(
            re.fullmatch(r"mode\s*=\s*block", part, flags=re.IGNORECASE) is not None
            for part in xss
        )
        != 1
    ):
        fail("X-XSS-Protection publicado diverge de 1; mode=block.")


def _assert_authorize_endpoint(
    transport: Transport,
    *,
    frontend_origin: str,
    cognito_origin: str,
    client_id: str,
) -> None:
    authorize = urllib.parse.urlsplit(f"{cognito_origin}/oauth2/authorize")
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "code_challenge": SYNTHETIC_CODE_CHALLENGE,
            "code_challenge_method": "S256",
            "redirect_uri": f"{frontend_origin}/",
            "response_type": "code",
            "scope": "openid",
            "state": SYNTHETIC_OAUTH_STATE,
        }
    )
    response = _request(
        transport,
        "GET",
        urllib.parse.urlunsplit((*authorize[:3], query, "")),
        headers={"accept": "text/html"},
    )
    if response.status == 200:
        if _media_type(response) != "text/html":
            fail("Authorize Cognito respondeu sem HTML canônico.")
        return
    if not 300 <= response.status < 400:
        fail("Endpoint authorize do Cognito recusou a configuração PKCE pública.")
    location = response.headers.get("location")
    if type(location) is not str:
        fail("Authorize Cognito redirecionou sem destino explícito.")
    destination = urllib.parse.urlsplit(urllib.parse.urljoin(cognito_origin, location))
    expected = urllib.parse.urlsplit(cognito_origin)
    query_names = {
        name.lower()
        for name, _value in urllib.parse.parse_qsl(
            destination.query, keep_blank_values=True
        )
    }
    if (
        destination.scheme != "https"
        or destination.netloc != expected.netloc
        or destination.username is not None
        or destination.password is not None
        or destination.fragment
        or destination.path.rstrip("/").endswith("/error")
        or {"error", "error_description"} & query_names
    ):
        fail("Authorize Cognito retornou erro ou redirecionamento inseguro.")


def run_published_smoke(
    *,
    frontend_origin: str,
    api_origin: str,
    cognito_origin: str,
    runtime_config: Mapping[str, object],
    staged: StagedFrontend,
    bearer_token: str,
    transport: Transport = http_transport,
) -> None:
    if (
        frontend_origin != f"https://{PUBLISHED_DOMAIN}"
        or re.fullmatch(
            r"https://[a-z0-9]{10}\.execute-api\.us-east-1\.amazonaws\.com", api_origin
        )
        is None
        or re.fullmatch(
            r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
            r"\.auth\.us-east-1\.amazoncognito\.com",
            cognito_origin,
        )
        is None
        or not bearer_token
        or any(character.isspace() for character in bearer_token)
    ):
        fail("Configuração do smoke publicado está ausente ou inválida.")
    raw_cognito = runtime_config.get("cognito")
    if type(raw_cognito) is not dict:
        fail("Runtime config não contém configuração Cognito canônica.")
    cognito_config = cast(dict[str, object], raw_cognito)
    client_id = cognito_config.get("client_id")
    if (
        type(client_id) is not str
        or re.fullmatch(r"[a-z0-9]{1,128}", client_id) is None
        or cognito_config.get("hosted_ui_origin") != cognito_origin
        or cognito_config.get("redirect_uri") != f"{frontend_origin}/"
        or cognito_config.get("logout_uri") != f"{frontend_origin}/"
        or cognito_config.get("scopes") != ["openid"]
    ):
        fail("Runtime config não contém OAuth público exato.")

    index = _request(transport, "GET", f"{frontend_origin}/")
    if index.status != 200 or _media_type(index) != "text/html":
        fail("Index publicado não está disponível com MIME correto.")
    _assert_security_headers(index, api=api_origin, cognito=cognito_origin)
    if "no-store" not in index.headers.get("cache-control", ""):
        fail("Index publicado não possui cache defensivo.")
    try:
        index_text = index.body.decode("utf-8")
    except UnicodeError:
        fail("Index publicado não é UTF-8.")
    if (
        f"./assets/{staged.source_sha}/main.js" not in index_text
        or f"./assets/{staged.source_sha}/styles.css" not in index_text
    ):
        fail("Index publicado não aponta para os assets imutáveis atuais.")

    files = staged.by_key()
    for key, expected in sorted(files.items()):
        if key in {INDEX_KEY, RUNTIME_CONFIG_KEY}:
            continue
        response = _request(transport, "GET", f"{frontend_origin}/{key}")
        if (
            response.status != 200
            or _media_type(response) != expected.content_type.split(";", 1)[0]
        ):
            fail("Asset publicado não está disponível com MIME correto.")
        if response.headers.get("cache-control") != expected.cache_control:
            fail("Asset publicado não possui cache imutável.")
        _assert_security_headers(response, api=api_origin, cognito=cognito_origin)
        try:
            expected_body = expected.path.read_bytes()
        except OSError:
            fail("Asset stageado não pôde ser lido para comparação.")
        if response.body != expected_body:
            fail("Asset publicado diverge dos bytes stageados.")

    config_response = _request(
        transport, "GET", f"{frontend_origin}/{RUNTIME_CONFIG_KEY}"
    )
    if (
        config_response.status != 200
        or _media_type(config_response) != "application/json"
        or config_response.headers.get("cache-control") != "no-store"
        or _json(config_response, context="Runtime config") != runtime_config
    ):
        fail("Runtime config publicado diverge do documento canônico sem cache.")
    _assert_security_headers(config_response, api=api_origin, cognito=cognito_origin)

    _assert_authorize_endpoint(
        transport,
        frontend_origin=frontend_origin,
        cognito_origin=cognito_origin,
        client_id=client_id,
    )

    preflight_headers = {
        "access-control-request-headers": "authorization,content-type",
        "access-control-request-method": "POST",
        "origin": frontend_origin,
    }
    preflight = _request(
        transport,
        "OPTIONS",
        f"{api_origin}/analysis",
        headers=preflight_headers,
    )
    if preflight.status not in {200, 204}:
        fail("Preflight CORS publicado não respondeu sem JWT.")
    if preflight.headers.get("access-control-allow-origin") != frontend_origin:
        fail("Preflight CORS não autorizou a origem final exata.")
    allowed_methods = {
        value.strip().upper()
        for value in preflight.headers.get("access-control-allow-methods", "").split(
            ","
        )
    }
    allowed_headers = {
        value.strip().lower()
        for value in preflight.headers.get("access-control-allow-headers", "").split(
            ","
        )
    }
    if (
        "POST" not in allowed_methods
        or not {"authorization", "content-type"} <= allowed_headers
    ):
        fail("Preflight CORS omitiu método ou headers mínimos.")

    foreign = _request(
        transport,
        "OPTIONS",
        f"{api_origin}/analysis",
        headers={**preflight_headers, "origin": "https://other.example"},
    )
    if foreign.headers.get("access-control-allow-origin") is not None:
        fail("Preflight CORS expôs a API para outra origem.")

    analysis_body = json.dumps(
        scenario_request(SCENARIOS["normal"]),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    anonymous = _request(
        transport,
        "POST",
        f"{api_origin}/analysis",
        headers={"content-type": "application/json", "origin": frontend_origin},
        body=analysis_body,
    )
    if (
        anonymous.status not in {401, 403}
        or anonymous.headers.get("access-control-allow-origin") != frontend_origin
    ):
        fail("POST anônimo do smoke publicado não foi rejeitado.")

    authenticated = _request(
        transport,
        "POST",
        f"{api_origin}/analysis",
        headers={
            "authorization": f"Bearer {bearer_token}",
            "content-type": "application/json",
            "origin": frontend_origin,
        },
        body=analysis_body,
    )
    outcome = _json(authenticated, context="POST autenticado")
    if (
        authenticated.status != 200
        or authenticated.headers.get("access-control-allow-origin") != frontend_origin
        or type(outcome) is not dict
        or outcome.get("outcome") not in ALLOWED_OUTCOMES
    ):
        fail("POST autenticado do smoke publicado não produziu outcome público.")
