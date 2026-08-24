"""Local, network-free regression for frontend staging, publication, and smoke."""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import patch

import published_smoke
from aws_delivery import AwsDeliveryError, validated_frontend_bucket_output
from frontend_delivery import (
    ASSET_PREFIX,
    INDEX_KEY,
    ROOT_KEYS,
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
WEB_DIST = REPOSITORY_ROOT / "apps/web/dist"
SOURCE_SHA = "a" * 40
RESIDUAL_ASSET_KEY = f"{ASSET_PREFIX}index-Legacy00.js"
SYNTHETIC_SCRIPT_KEY = f"{ASSET_PREFIX}index-Synth001.js"
SYNTHETIC_STYLE_KEY = f"{ASSET_PREFIX}index-Synth002.css"
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
                    {"Key": RESIDUAL_ASSET_KEY},
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


def require_failure(action: Callable[[], object], context: str) -> None:
    try:
        action()
    except FrontendDeliveryError:
        return
    raise FrontendDeliveryRegressionError(
        f"gramática publicável deveria recusar {context}"
    )


def synthetic_index(
    *,
    script_key: str | None = SYNTHETIC_SCRIPT_KEY,
    style_key: str | None = SYNTHETIC_STYLE_KEY,
    head_extra: str = "",
    body_extra: str = "",
) -> str:
    script = f'    <script type="module" src="./{script_key}"></script>\n'
    style = f'    <link rel="stylesheet" href="./{style_key}" />\n'
    return (
        "<!doctype html>\n"
        '<html lang="pt-BR">\n'
        "  <head>\n"
        '    <meta charset="utf-8" />\n'
        "    <title>Manutenção prescritiva</title>\n"
        '    <link rel="icon" href="./favicon.svg" />\n'
        '    <script src="./theme-init.js"></script>\n'
        f"{script if script_key is not None else ''}"
        f"{style if style_key is not None else ''}"
        f"{head_extra}"
        "  </head>\n"
        "  <body>\n"
        '    <div id="root"></div>\n'
        f"{body_extra}"
        "  </body>\n"
        "</html>\n"
    )


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def write_synthetic_dist(root: Path) -> Path:
    """Smallest tree the publishable grammar accepts, used as the hostile base."""
    write_text(root / INDEX_KEY, synthetic_index())
    write_text(
        root / "favicon.svg",
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"></svg>\n',
    )
    write_text(
        root / "theme-init.js", 'document.documentElement.dataset.theme="light";\n'
    )
    write_text(root / SYNTHETIC_SCRIPT_KEY, "export const boot = () => undefined;\n")
    write_text(root / SYNTHETIC_STYLE_KEY, ":root { color-scheme: light dark; }\n")
    return root


HostileMutation = Callable[[Path], object]


def oversized_asset(root: Path) -> object:
    return (root / ASSET_PREFIX / "big-Synth004.js").write_text(
        "x" * 1_000_001, encoding="utf-8", newline="\n"
    )


def oversized_inventory(root: Path) -> object:
    for ordinal in range(1, 6):
        (root / ASSET_PREFIX / f"bulk{ordinal}-Synth00{ordinal}.js").write_text(
            "x" * 900_000, encoding="utf-8", newline="\n"
        )
    return None


def prove_dist_guardrails(temporary: Path) -> None:
    healthy = write_synthetic_dist(temporary / "healthy-dist")
    stage_frontend(healthy, temporary / "healthy-stage", SOURCE_SHA)

    mutations: tuple[tuple[str, HostileMutation], ...] = (
        (
            "source map publicado",
            lambda root: write_text(root / f"{SYNTHETIC_SCRIPT_KEY}.map", "{}\n"),
        ),
        (
            "script inline recusado pela CSP",
            lambda root: write_text(
                root / INDEX_KEY,
                synthetic_index(head_extra="    <script>window.pm = 1;</script>\n"),
            ),
        ),
        (
            "bloco de estilo inline",
            lambda root: write_text(
                root / INDEX_KEY,
                synthetic_index(head_extra="    <style>body { color: red; }</style>\n"),
            ),
        ),
        (
            "atributo style inline",
            lambda root: write_text(
                root / INDEX_KEY,
                synthetic_index(body_extra='    <p style="color: red">a</p>\n'),
            ),
        ),
        (
            "asset em subdiretório",
            lambda root: write_text(
                root / ASSET_PREFIX / "chunk" / "deep-Synth003.js", "export {};\n"
            ),
        ),
        (
            "asset sem hash de conteúdo",
            lambda root: write_text(root / ASSET_PREFIX / "vendor.js", "export {};\n"),
        ),
        (
            "arquivo estranho na raiz",
            lambda root: write_text(root / "robots.txt", "User-agent: *\n"),
        ),
        (
            "entrada obrigatória ausente na raiz",
            lambda root: (root / "favicon.svg").unlink(),
        ),
        (
            "referência pendente no índice",
            lambda root: write_text(
                root / INDEX_KEY,
                synthetic_index(script_key=f"{ASSET_PREFIX}index-Missing1.js"),
            ),
        ),
        ("asset acima do limite individual", oversized_asset),
        ("inventário acima do limite total", oversized_inventory),
        (
            "asset que não é UTF-8",
            lambda root: (root / SYNTHETIC_SCRIPT_KEY).write_bytes(
                b"export const boot = '\xff\xfe';\n"
            ),
        ),
        (
            "build sem bundle JavaScript",
            lambda root: (
                write_text(root / INDEX_KEY, synthetic_index(script_key=None)),
                (root / SYNTHETIC_SCRIPT_KEY).unlink(),
            ),
        ),
    )
    for ordinal, (context, mutate) in enumerate(mutations):
        hostile = write_synthetic_dist(temporary / f"hostile-{ordinal}")
        mutate(hostile)
        require_failure(
            lambda hostile=hostile, ordinal=ordinal: stage_frontend(
                hostile, temporary / f"hostile-stage-{ordinal}", SOURCE_SHA
            ),
            context,
        )

    with patch.object(
        Path,
        "is_symlink",
        autospec=True,
        side_effect=lambda candidate: candidate.name == "favicon.svg",
    ):
        require_failure(
            lambda: stage_frontend(healthy, temporary / "symlink-stage", SOURCE_SHA),
            "symlink dentro do build",
        )

    require_failure(
        lambda: stage_frontend(
            temporary / "absent-dist", temporary / "absent-stage", SOURCE_SHA
        ),
        "raiz publicável ausente",
    )
    require_failure(
        lambda: stage_frontend(healthy, temporary / "sha-stage", "z" * 40),
        "SHA de publicação fora do formato canônico",
    )
    occupied = temporary / "occupied-stage"
    write_text(occupied / "leftover.txt", "resíduo\n")
    require_failure(
        lambda: stage_frontend(healthy, occupied, SOURCE_SHA),
        "staging que já contém arquivos",
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
    asset_keys = sorted(key for key in staged.by_key() if key.startswith(ASSET_PREFIX))
    expect(
        put_keys
        == [
            *asset_keys,
            *sorted(ROOT_KEYS - {INDEX_KEY}),
            RUNTIME_CONFIG_KEY,
            INDEX_KEY,
        ],
        "ordem de upload divergiu de assets, raiz, runtime config e índice",
    )
    expect(
        keys[-1] == RESIDUAL_ASSET_KEY,
        "deleção residual não ocorreu após os uploads",
    )
    deleted = [
        command[command.index("--key") + 1]
        for command in fake.commands
        if command[:2] == ("s3api", "delete-object")
    ]
    expect(
        deleted == [RESIDUAL_ASSET_KEY],
        "deleção residual excedeu a chave allowlisted",
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
        if key.startswith(ASSET_PREFIX):
            expect(
                cache == "public, max-age=31536000, immutable",
                "asset hasheado não recebeu cache imutável",
            )
        elif key == RUNTIME_CONFIG_KEY:
            expect(cache == "no-store", "runtime config não recebeu no-store")
        else:
            expect(
                cache == "no-cache, no-store, must-revalidate",
                "chave de nome estável não recebeu cache defensivo",
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
            "Contents": [{"Key": f"{ASSET_PREFIX}chunk/deep-Synth003.js"}],
            "IsTruncated": False,
        },
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
            ),
            "listagem hostil do bucket",
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

    script_key = next(
        key
        for key in sorted(staged.by_key())
        if key.startswith(ASSET_PREFIX) and key.endswith(".js")
    )

    def corrupt_bundle(key: str, body: bytes) -> bytes:
        if key == script_key:
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
            transport=FakeHttp(staged, config, asset_mutation=corrupt_bundle),
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


def prove_real_build(staged: StagedFrontend) -> None:
    """The grammar is only worth anything if the build Vite actually emits
    satisfies it, so the published inventory is checked against the real dist."""
    keys = set(staged.by_key())
    assets = {key for key in keys if key.startswith(ASSET_PREFIX)}
    expect(
        keys == ROOT_KEYS | {RUNTIME_CONFIG_KEY} | assets,
        "stage do build real declarou chave fora da gramática publicável",
    )
    expect(
        any(key.endswith(".js") for key in assets)
        and any(key.endswith(".css") for key in assets),
        "build real não produziu os bundles esperados",
    )
    expect(
        not any(key.endswith((".d.ts", ".map")) for key in keys),
        "stage incluiu declaração ou source map proibido",
    )
    index_text = staged.by_key()[INDEX_KEY].path.read_text(encoding="utf-8")
    expect(
        all(f"./{key}" in index_text for key in assets),
        "índice do build real não referencia todos os bundles publicados",
    )


def main() -> int:
    expect(
        WEB_DIST.is_dir(),
        "build do frontend ausente: execute 'poe web-build' antes da regressão.",
    )
    with tempfile.TemporaryDirectory(prefix="sen75-frontend-") as temporary_name:
        temporary = Path(temporary_name)
        prove_dist_guardrails(temporary)
        staged = stage_frontend(WEB_DIST, temporary / "stage", SOURCE_SHA)
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
        prove_real_build(staged)
        prove_publication(staged)
        prove_hostile_bucket_listings(staged)
        prove_aws_delivery_bucket_gate()
        prove_smoke(staged, config)
        prove_http_transport_boundary()
    print(
        "Regressão local do frontend aprovada: gramática do build, ordem, MIME, "
        "runtime config, CORS e POST autenticado foram provados sem rede AWS."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
