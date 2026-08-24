"""Local, network-free regression for frontend staging, publication, and smoke."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import patch

import published_smoke
from aws_delivery import AwsDeliveryError, validated_frontend_bucket_output
from frontend_delivery import (
    ASSET_SOURCE_PATHS,
    INDEX_KEY,
    PUBLISHED_SOURCE_PATHS,
    RUNTIME_CONFIG_KEY,
    FrontendDeliveryError,
    StagedFrontend,
    add_runtime_config,
    publication_order,
    publish_frontend,
    runtime_config,
    stage_frontend,
)
from orphan_inventory import CommandResult
from published_smoke import (
    SYNTHETIC_CODE_CHALLENGE,
    SYNTHETIC_OAUTH_STATE,
    HttpRequest,
    HttpResponse,
    PublishedSmokeError,
    http_transport,
    run_published_smoke,
)
from remote_smoke import SCENARIOS, scenario_request

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
WEB_SOURCE = REPOSITORY_ROOT / "apps/web/src"
SOURCE_SHA = "a" * 40
OLD_SHA = "b" * 40
API_ORIGIN = "https://abc123def4.execute-api.us-east-1.amazonaws.com"
COGNITO_ORIGIN = "https://spm-a1b2c3d4e5f6g7h8.auth.us-east-1.amazoncognito.com"
CLIENT_ID = "abc123client"
FRONTEND_ORIGIN = "https://senai.maib.com.br"
BUCKET = "senai-pm-frontend-000000000000-us-east-1"
DISTRIBUTION_ID = "E123456789AB"
SYNTHETIC_BEARER = "-".join(("synthetic", "bearer"))


class FrontendDeliveryRegressionError(RuntimeError):
    """Raised when the local frontend contract is not proven."""


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise FrontendDeliveryRegressionError(message)


class FakeAws:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: tuple[str, ...]) -> CommandResult:
        self.commands.append(command)
        if command[:2] == ("s3api", "list-objects-v2"):
            content = {
                "Contents": [
                    {"Key": INDEX_KEY},
                    {"Key": RUNTIME_CONFIG_KEY},
                    {"Key": f"assets/{OLD_SHA}/main.js"},
                ],
                "IsTruncated": False,
            }
        elif command[:2] == ("cloudfront", "create-invalidation"):
            expect(
                command
                == (
                    "cloudfront",
                    "create-invalidation",
                    "--distribution-id",
                    DISTRIBUTION_ID,
                    "--invalidation-batch",
                    json.dumps(
                        {
                            "CallerReference": f"sen75-{SOURCE_SHA}",
                            "Paths": {"Items": ["/*"], "Quantity": 1},
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
                "comando de invalidação divergiu da gramática canônica",
            )
            content = {"Invalidation": {"Id": "INV12345678", "Status": "InProgress"}}
        elif command[:2] == ("cloudfront", "get-invalidation"):
            content = {"Invalidation": {"Id": "INV12345678", "Status": "Completed"}}
        else:
            content = {}
        return CommandResult(0, json.dumps(content), "")


def security_headers() -> dict[str, str]:
    csp = "; ".join(
        (
            "default-src 'none'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self'",
            f"connect-src 'self' {API_ORIGIN} {COGNITO_ORIGIN}",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
            "object-src 'none'",
        )
    )
    return {
        "content-security-policy": csp,
        "permissions-policy": "camera=(), geolocation=(), microphone=()",
        "referrer-policy": "no-referrer",
        "strict-transport-security": "max-age=31536000; includeSubDomains",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "x-xss-protection": "1; mode=block",
    }


class FakeHttp:
    def __init__(
        self,
        staged: StagedFrontend,
        config: dict[str, object],
        *,
        post_origin: str | None = FRONTEND_ORIGIN,
        authorize_status: int = 302,
        authorize_location: str | None = f"{COGNITO_ORIGIN}/login",
        authorize_content_type: str | None = None,
        asset_mutation: Callable[[str, bytes], bytes] | None = None,
        security_mutation: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        self.staged = staged
        self.config = config
        self.post_origin = post_origin
        self.authorize_status = authorize_status
        self.authorize_location = authorize_location
        self.authorize_content_type = authorize_content_type
        self.asset_mutation = asset_mutation
        self.security_mutation = security_mutation
        self.requests: list[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if request.url.startswith(FRONTEND_ORIGIN):
            key = request.url.removeprefix(f"{FRONTEND_ORIGIN}/") or INDEX_KEY
            item = self.staged.by_key()[key]
            headers = {
                **security_headers(),
                "cache-control": item.cache_control,
                "content-type": item.content_type,
            }
            if self.security_mutation is not None:
                self.security_mutation(headers)
            body = item.path.read_bytes()
            if self.asset_mutation is not None:
                body = self.asset_mutation(key, body)
            return HttpResponse(200, headers, body)
        if request.method == "GET" and request.url.startswith(
            f"{COGNITO_ORIGIN}/oauth2/authorize?"
        ):
            parsed = published_smoke.urllib.parse.urlsplit(request.url)
            parameters = published_smoke.urllib.parse.parse_qs(
                parsed.query, keep_blank_values=True
            )
            expect(
                parameters
                == {
                    "client_id": [CLIENT_ID],
                    "code_challenge": [SYNTHETIC_CODE_CHALLENGE],
                    "code_challenge_method": ["S256"],
                    "redirect_uri": [f"{FRONTEND_ORIGIN}/"],
                    "response_type": ["code"],
                    "scope": ["openid"],
                    "state": [SYNTHETIC_OAUTH_STATE],
                },
                "authorize sintético divergiu do contrato OAuth exato",
            )
            expect(
                request.headers == {"accept": "text/html"},
                "authorize sintético enviou headers além da allowlist",
            )
            headers = (
                {"location": self.authorize_location}
                if self.authorize_location is not None
                else {}
            )
            if self.authorize_content_type is not None:
                headers["content-type"] = self.authorize_content_type
            return HttpResponse(self.authorize_status, headers, b"")
        if request.method == "OPTIONS":
            headers = {
                "access-control-allow-headers": "authorization,content-type",
                "access-control-allow-methods": "GET,POST,OPTIONS",
            }
            if request.headers.get("origin") == FRONTEND_ORIGIN:
                headers["access-control-allow-origin"] = FRONTEND_ORIGIN
            return HttpResponse(204, headers, b"")
        if request.method == "POST" and "authorization" not in request.headers:
            expect(
                json.loads((request.body or b"").decode("utf-8"))
                == scenario_request(SCENARIOS["normal"]),
                "POST anônimo divergiu do cenário canônico",
            )
            headers = {"content-type": "application/json"}
            if self.post_origin is not None:
                headers["access-control-allow-origin"] = self.post_origin
            return HttpResponse(401, headers, b"{}")
        if request.method == "POST":
            expect(
                json.loads((request.body or b"").decode("utf-8"))
                == scenario_request(SCENARIOS["normal"]),
                "POST autenticado divergiu do cenário canônico",
            )
            return HttpResponse(
                200,
                {
                    "access-control-allow-origin": self.post_origin or "",
                    "content-type": "application/json",
                },
                b'{"outcome":"normal"}',
            )
        raise FrontendDeliveryRegressionError("fake recebeu operação fora do contrato")


def require_failure(action: Callable[[], object]) -> None:
    try:
        action()
    except FrontendDeliveryError:
        return
    raise FrontendDeliveryRegressionError("regressão hostil deveria falhar fechada")


def prove_source_guardrails(temporary: Path) -> None:
    hostile = temporary / "hostile-source"
    shutil.copytree(WEB_SOURCE, hostile)
    (hostile / "main.js.map").write_text("{}", encoding="utf-8")
    require_failure(
        lambda: stage_frontend(hostile, temporary / "hostile-stage", SOURCE_SHA)
    )

    with patch.object(
        Path,
        "is_symlink",
        autospec=True,
        side_effect=lambda candidate: candidate.name == "styles.css",
    ):
        require_failure(
            lambda: stage_frontend(WEB_SOURCE, temporary / "symlink-stage", SOURCE_SHA)
        )


def prove_publication(staged: StagedFrontend) -> FakeAws:
    fake = FakeAws()
    invalidation_id = publish_frontend(
        staged,
        bucket=BUCKET,
        distribution_id=DISTRIBUTION_ID,
        runner=fake,
        sleep=lambda _: None,
        max_waits=2,
    )
    expect(invalidation_id == "INV12345678", "invalidação não retornou o ID esperado")
    keys = publication_order(fake.commands)
    put_keys = [
        command[command.index("--key") + 1]
        for command in fake.commands
        if command[:2] == ("s3api", "put-object")
    ]
    expect(
        put_keys[:-2]
        == [f"assets/{SOURCE_SHA}/{path}" for path in sorted(ASSET_SOURCE_PATHS)],
        "assets imutáveis não foram publicados primeiro",
    )
    expect(
        put_keys[-2:] == [RUNTIME_CONFIG_KEY, INDEX_KEY],
        "runtime config e index não foram publicados por último",
    )
    expect(
        keys[-1] == f"assets/{OLD_SHA}/main.js",
        "deleção residual não ocorreu após os uploads",
    )
    deleted = [
        command[command.index("--key") + 1]
        for command in fake.commands
        if command[:2] == ("s3api", "delete-object")
    ]
    expect(
        deleted == [f"assets/{OLD_SHA}/main.js"],
        "deleção residual excedeu a chave allowlisted",
    )
    expect(
        all(
            key.startswith(f"assets/{OLD_SHA}/")
            and key.removeprefix(f"assets/{OLD_SHA}/") in ASSET_SOURCE_PATHS
            for key in deleted
        ),
        "deleção residual saiu da allowlist do SHA antigo",
    )
    for command in fake.commands:
        if command[:2] != ("s3api", "put-object"):
            continue
        expect(
            command[command.index("--server-side-encryption") + 1] == "AES256",
            "upload não exigiu SSE AES256",
        )
        key = command[command.index("--key") + 1]
        cache = command[command.index("--cache-control") + 1]
        if key.startswith("assets/"):
            expect(
                cache == "public, max-age=31536000, immutable",
                "asset não recebeu cache imutável",
            )
        elif key == RUNTIME_CONFIG_KEY:
            expect(cache == "no-store", "runtime config não recebeu no-store")
        else:
            expect(
                cache == "no-cache, no-store, must-revalidate",
                "index não recebeu cache defensivo",
            )
    return fake


def prove_hostile_bucket_listings(staged: StagedFrontend) -> None:
    class HostileListingAws:
        def __init__(self, document: object) -> None:
            self.document = document
            self.commands: list[tuple[str, ...]] = []

        def __call__(self, command: tuple[str, ...]) -> CommandResult:
            self.commands.append(command)
            if command[:2] != ("s3api", "list-objects-v2"):
                raise FrontendDeliveryRegressionError(
                    "efeito AWS ocorreu após listagem hostil"
                )
            return CommandResult(0, json.dumps(self.document), "")

    hostile_documents = (
        {"Contents": [], "IsTruncated": True},
        {"Contents": [{"Key": "private/source.pdf"}], "IsTruncated": False},
        {
            "Contents": [{"Key": INDEX_KEY}, {"Key": INDEX_KEY}],
            "IsTruncated": False,
        },
        {"Contents": {}, "IsTruncated": False},
    )
    for document in hostile_documents:
        fake = HostileListingAws(document)
        require_failure(
            lambda fake=fake: publish_frontend(
                staged,
                bucket=BUCKET,
                distribution_id=DISTRIBUTION_ID,
                runner=fake,
                sleep=lambda _: None,
                max_waits=1,
            )
        )
        expect(
            fake.commands == [("s3api", "list-objects-v2", "--bucket", BUCKET)],
            "listagem hostil causou efeito AWS posterior",
        )


def prove_aws_delivery_bucket_gate() -> None:
    configuration = {
        "account_id": "000000000000",
        "name_prefix": "senai-pm",
        "region": "us-east-1",
    }
    expect(
        validated_frontend_bucket_output(configuration, BUCKET) == BUCKET,
        "bucket canônico foi recusado pelo gate",
    )
    try:
        validated_frontend_bucket_output(
            configuration,
            "senai-pm-frontend-111111111111-us-east-1",
        )
    except AwsDeliveryError:
        return
    raise FrontendDeliveryRegressionError(
        "bucket bem-formado de outra conta deveria ser recusado"
    )


def prove_smoke(staged: StagedFrontend, config: dict[str, object]) -> None:
    openapi = json.loads(
        (REPOSITORY_ROOT / "apps/api/openapi/v1.json").read_text(encoding="utf-8")
    )
    contract_features = set(
        openapi["components"]["schemas"]["AnalysisFeatures"]["properties"]
    )
    normal = scenario_request(SCENARIOS["normal"])
    expect(
        set(cast(dict[str, object], normal["features"])) == contract_features,
        "cenário sintético divergiu das features OpenAPI canônicas",
    )
    fake = FakeHttp(staged, config)
    run_published_smoke(
        frontend_origin=FRONTEND_ORIGIN,
        api_origin=API_ORIGIN,
        cognito_origin=COGNITO_ORIGIN,
        runtime_config=config,
        staged=staged,
        bearer_token=SYNTHETIC_BEARER,
        transport=fake,
    )
    posts = [request for request in fake.requests if request.method == "POST"]
    expect(len(posts) == 2, "smoke não executou os dois POSTs previstos")
    expect(
        "authorization" not in posts[0].headers,
        "POST anônimo enviou bearer",
    )
    expect(
        posts[1].headers.get("authorization") == f"Bearer {SYNTHETIC_BEARER}",
        "POST autenticado não enviou o bearer sintético",
    )
    expect(
        all(request.url == f"{API_ORIGIN}/analysis" for request in posts),
        "POST saiu da rota analysis exata",
    )
    preflights = [request for request in fake.requests if request.method == "OPTIONS"]
    expect(
        [request.headers["origin"] for request in preflights]
        == [FRONTEND_ORIGIN, "https://other.example"],
        "smoke não provou preflight permitido e hostil",
    )
    authorize = [
        request
        for request in fake.requests
        if request.url.startswith(f"{COGNITO_ORIGIN}/oauth2/authorize?")
    ]
    expect(len(authorize) == 1, "smoke não tocou authorize exatamente uma vez")
    expect(
        "authorization" not in authorize[0].headers,
        "authorize sintético enviou bearer",
    )
    expect("cookie" not in authorize[0].headers, "authorize sintético enviou cookie")
    run_published_smoke(
        frontend_origin=FRONTEND_ORIGIN,
        api_origin=API_ORIGIN,
        cognito_origin=COGNITO_ORIGIN,
        runtime_config=config,
        staged=staged,
        bearer_token=SYNTHETIC_BEARER,
        transport=FakeHttp(
            staged,
            config,
            authorize_status=200,
            authorize_location=None,
            authorize_content_type="text/html; charset=utf-8",
        ),
    )

    def corrupt_main(key: str, body: bytes) -> bytes:
        if key == f"assets/{SOURCE_SHA}/main.js":
            return body + b"\n"
        return body

    try:
        run_published_smoke(
            frontend_origin=FRONTEND_ORIGIN,
            api_origin=API_ORIGIN,
            cognito_origin=COGNITO_ORIGIN,
            runtime_config=config,
            staged=staged,
            bearer_token=SYNTHETIC_BEARER,
            transport=FakeHttp(staged, config, asset_mutation=corrupt_main),
        )
    except PublishedSmokeError:
        pass
    else:
        raise FrontendDeliveryRegressionError(
            "smoke deveria recusar bytes publicados divergentes do stage"
        )

    for hostile_origin in (None, "https://other.example"):
        try:
            run_published_smoke(
                frontend_origin=FRONTEND_ORIGIN,
                api_origin=API_ORIGIN,
                cognito_origin=COGNITO_ORIGIN,
                runtime_config=config,
                staged=staged,
                bearer_token=SYNTHETIC_BEARER,
                transport=FakeHttp(staged, config, post_origin=hostile_origin),
            )
        except PublishedSmokeError:
            continue
        raise FrontendDeliveryRegressionError(
            "smoke deveria recusar CORS ausente ou estrangeiro no POST"
        )

    for hostile in (
        FakeHttp(staged, config, authorize_status=500, authorize_location=None),
        FakeHttp(staged, config, authorize_status=200, authorize_location=None),
        FakeHttp(
            staged,
            config,
            authorize_status=200,
            authorize_location=None,
            authorize_content_type="application/json",
        ),
        FakeHttp(
            staged,
            config,
            authorize_status=302,
            authorize_location="https://other.example/login",
        ),
        FakeHttp(
            staged,
            config,
            authorize_status=302,
            authorize_location=f"{COGNITO_ORIGIN}/login?error=invalid_request",
        ),
        FakeHttp(
            staged,
            config,
            authorize_status=302,
            authorize_location=f"{COGNITO_ORIGIN}/oauth2/error",
        ),
    ):
        try:
            run_published_smoke(
                frontend_origin=FRONTEND_ORIGIN,
                api_origin=API_ORIGIN,
                cognito_origin=COGNITO_ORIGIN,
                runtime_config=config,
                staged=staged,
                bearer_token=SYNTHETIC_BEARER,
                transport=hostile,
            )
        except PublishedSmokeError:
            continue
        raise FrontendDeliveryRegressionError(
            "smoke deveria recusar erro ou redirect off-origin no authorize"
        )

    def remove_header(name: str) -> Callable[[dict[str, str]], None]:
        return lambda headers: headers.pop(name)

    def relaxed_hsts(headers: dict[str, str]) -> None:
        headers["strict-transport-security"] = "max-age=60; includeSubDomains; preload"

    def relaxed_xss(headers: dict[str, str]) -> None:
        headers["x-xss-protection"] = "0"

    def equivalent_security_spelling(headers: dict[str, str]) -> None:
        headers["strict-transport-security"] = (
            "  MAX-AGE = 31536000 ; IncludeSubDomains  "
        )
        headers["x-xss-protection"] = " 1 ; MODE = block "

    run_published_smoke(
        frontend_origin=FRONTEND_ORIGIN,
        api_origin=API_ORIGIN,
        cognito_origin=COGNITO_ORIGIN,
        runtime_config=config,
        staged=staged,
        bearer_token=SYNTHETIC_BEARER,
        transport=FakeHttp(
            staged,
            config,
            security_mutation=equivalent_security_spelling,
        ),
    )

    for mutation in (
        remove_header("strict-transport-security"),
        relaxed_hsts,
        remove_header("x-xss-protection"),
        relaxed_xss,
    ):
        try:
            run_published_smoke(
                frontend_origin=FRONTEND_ORIGIN,
                api_origin=API_ORIGIN,
                cognito_origin=COGNITO_ORIGIN,
                runtime_config=config,
                staged=staged,
                bearer_token=SYNTHETIC_BEARER,
                transport=FakeHttp(staged, config, security_mutation=mutation),
            )
        except PublishedSmokeError:
            continue
        raise FrontendDeliveryRegressionError(
            "smoke deveria recusar header publicado ausente ou relaxado"
        )


def prove_http_transport_boundary() -> None:
    request = HttpRequest(
        "POST",
        f"{API_ORIGIN}/analysis",
        {"authorization": f"Bearer {SYNTHETIC_BEARER}"},
        b"{}",
    )
    with (
        patch.dict(os.environ, {"SSL_CERT_FILE": "hostile.pem"}, clear=False),
        patch.object(published_smoke.urllib.request, "build_opener") as build_opener,
    ):
        try:
            http_transport(request)
        except PublishedSmokeError:
            pass
        else:
            raise FrontendDeliveryRegressionError(
                "override TLS hostil deveria falhar antes da rede"
            )
        build_opener.assert_not_called()

    calls: list[object] = []

    class FakeResponse:
        def __init__(self) -> None:
            self.status = 302
            self.headers: dict[str, str] = {"location": "https://other.example"}

        def read(self, _limit: int) -> bytes:
            return b""

        def close(self) -> None:
            return None

    class FakeOpener:
        def open(self, raw: object, *, timeout: int) -> FakeResponse:
            expect(timeout == 20, "transporte não aplicou o deadline exato")
            calls.append(raw)
            return FakeResponse()

    tls_context = object()

    def fake_build_opener(*handlers: object) -> FakeOpener:
        proxies = [
            handler
            for handler in handlers
            if isinstance(handler, published_smoke.urllib.request.ProxyHandler)
        ]
        https = [
            handler
            for handler in handlers
            if isinstance(handler, published_smoke.urllib.request.HTTPSHandler)
        ]
        redirects = [
            handler for handler in handlers if type(handler).__name__ == "_NoRedirect"
        ]
        expect(
            len(proxies) == 1 and proxies[0].proxies == {},
            "transporte não desabilitou proxies do ambiente",
        )
        expect(
            len(https) == 1 and vars(https[0]).get("_context") is tls_context,
            "transporte não instalou o contexto TLS verificado",
        )
        expect(len(redirects) == 1, "transporte não recusou redirects")
        return FakeOpener()

    with (
        patch.object(published_smoke, "verified_tls_context", return_value=tls_context),
        patch.object(
            published_smoke.urllib.request,
            "build_opener",
            side_effect=fake_build_opener,
        ),
    ):
        response = http_transport(request)
    expect(response.status == 302, "transporte alterou a resposta de redirect")
    expect(len(calls) == 1, "transporte reenviou a requisição")
    raw_request = cast(urllib.request.Request, calls[0])
    expect(
        raw_request.get_header("Authorization") == f"Bearer {SYNTHETIC_BEARER}",
        "transporte removeu ou alterou o bearer da chamada original",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="sen75-frontend-") as temporary_name:
        temporary = Path(temporary_name)
        staged = stage_frontend(WEB_SOURCE, temporary / "stage", SOURCE_SHA)
        config = runtime_config(
            api_origin=API_ORIGIN,
            client_id=CLIENT_ID,
            cognito_origin=COGNITO_ORIGIN,
        )
        staged = add_runtime_config(
            staged,
            temporary / "stage",
            api_origin=API_ORIGIN,
            client_id=CLIENT_ID,
            cognito_origin=COGNITO_ORIGIN,
        )
        expect(
            set(staged.by_key())
            == {
                INDEX_KEY,
                RUNTIME_CONFIG_KEY,
                *(
                    f"assets/{SOURCE_SHA}/{path}"
                    for path in PUBLISHED_SOURCE_PATHS - {INDEX_KEY}
                ),
            },
            "stage divergiu da allowlist publicada exata",
        )
        expect(
            not any(key.endswith((".d.ts", ".map")) for key in staged.by_key()),
            "stage incluiu declaração ou source map proibido",
        )
        prove_source_guardrails(temporary)
        prove_publication(staged)
        prove_hostile_bucket_listings(staged)
        prove_aws_delivery_bucket_gate()
        prove_smoke(staged, config)
        prove_http_transport_boundary()
    print(
        "Regressão local do frontend aprovada: allowlist, ordem, MIME, runtime config, "
        "CORS e POST autenticado foram provados sem rede AWS."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
