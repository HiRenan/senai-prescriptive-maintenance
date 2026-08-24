"""Stage and publish the closed AWS demo frontend inventory."""

from __future__ import annotations

import json
import re
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

from orphan_inventory import CommandResult

PUBLISHED_DOMAIN = "senai.maib.com.br"
PUBLISHED_REGION = "us-east-1"
RUNTIME_CONFIG_KEY = "runtime-config.v1.json"
INDEX_KEY = "index.html"
SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
API_ORIGIN_PATTERN = re.compile(
    r"^https://[a-z0-9]{10}\.execute-api\.us-east-1\.amazonaws\.com$"
)
COGNITO_ORIGIN_PATTERN = re.compile(
    r"^https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"\.auth\.us-east-1\.amazoncognito\.com$"
)
CLIENT_ID_PATTERN = re.compile(r"^[a-z0-9]{1,128}$")
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
DISTRIBUTION_ID_PATTERN = re.compile(r"^[A-Z0-9]{10,32}$")
INVALIDATION_ID_PATTERN = re.compile(r"^[A-Z0-9]{8,32}$")
MAX_SOURCE_FILE_BYTES = 1_000_000
MAX_SOURCE_TOTAL_BYTES = 4_000_000

PUBLISHED_SOURCE_PATHS = frozenset(
    {
        "api/analysis-client.js",
        "api/authenticated-fetch.js",
        "api/document-client.js",
        "api/offline-analysis-client.js",
        "auth/cognito.js",
        "auth/pkce.js",
        "auth/session.js",
        "config/runtime-config.js",
        "core/comparison.js",
        "core/contract-decode.js",
        "core/document-presentation.js",
        "core/document-registration.js",
        "core/features.js",
        "core/format.js",
        "core/latest-request.js",
        "core/presentation.js",
        "core/request-import.js",
        "generated/analysis-contract.js",
        "generated/document-contract.js",
        "index.html",
        "main.js",
        "styles.css",
        "ui/console-view.js",
        "ui/document-marks.js",
        "ui/documents-view.js",
        "ui/dom.js",
        "ui/marks.js",
        "ui/report-view.js",
        "ui/workspace-navigation.js",
    }
)
EXCLUDED_DEVELOPMENT_PATHS = frozenset(
    {
        "generated/analysis-contract.d.ts",
        "generated/document-contract.d.ts",
    }
)
ASSET_SOURCE_PATHS = PUBLISHED_SOURCE_PATHS - {INDEX_KEY}


class FrontendDeliveryError(RuntimeError):
    """Raised without raw AWS output, token material, or source content."""


def fail(message: str) -> NoReturn:
    raise FrontendDeliveryError(message)


@dataclass(frozen=True, slots=True)
class PublishedFile:
    key: str
    path: Path
    content_type: str
    cache_control: str


@dataclass(frozen=True, slots=True)
class StagedFrontend:
    source_sha: str
    files: tuple[PublishedFile, ...]

    def by_key(self) -> dict[str, PublishedFile]:
        return {item.key: item for item in self.files}


Runner = Callable[[tuple[str, ...]], CommandResult]
Sleeper = Callable[[float], None]


def _relative_files(source_root: Path) -> dict[str, Path]:
    if not source_root.is_dir() or source_root.is_symlink():
        fail("Raiz pública do frontend está ausente ou não é um diretório regular.")
    discovered: dict[str, Path] = {}
    total = 0
    for candidate in source_root.rglob("*"):
        if candidate.is_symlink():
            fail("Fonte pública do frontend contém symlink proibido.")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(source_root).as_posix()
        if relative in discovered:
            fail("Inventário fonte do frontend possui caminho duplicado.")
        size = candidate.stat().st_size
        if size > MAX_SOURCE_FILE_BYTES:
            fail("Arquivo público do frontend excede o limite individual.")
        total += size
        discovered[relative] = candidate
    if total > MAX_SOURCE_TOTAL_BYTES:
        fail("Inventário público do frontend excede o limite total.")
    expected = PUBLISHED_SOURCE_PATHS | EXCLUDED_DEVELOPMENT_PATHS
    if set(discovered) != expected:
        fail("Inventário fonte do frontend diverge da allowlist exata.")
    for relative in PUBLISHED_SOURCE_PATHS:
        suffix = Path(relative).suffix
        if suffix not in {".css", ".html", ".js"}:
            fail("Allowlist pública contém extensão inesperada.")
        try:
            discovered[relative].read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            fail("Arquivo público do frontend não é UTF-8 legível.")
    return discovered


def _content_type(relative: str) -> str:
    suffix = Path(relative).suffix
    content_types = {
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }
    if suffix not in content_types:
        fail("Extensão de publicação não pertence à allowlist.")
    return content_types[suffix]


def stage_frontend(
    source_root: Path, stage_root: Path, source_sha: str
) -> StagedFrontend:
    if SOURCE_SHA_PATTERN.fullmatch(source_sha) is None:
        fail("SHA da publicação não é canônico.")
    discovered = _relative_files(source_root)
    if stage_root.exists() and any(stage_root.iterdir()):
        fail("Diretório de staging precisa começar vazio.")
    stage_root.mkdir(parents=True, exist_ok=True)
    asset_root = stage_root / "assets" / source_sha
    files: list[PublishedFile] = []
    for relative in sorted(ASSET_SOURCE_PATHS):
        destination = asset_root / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(discovered[relative], destination)
        files.append(
            PublishedFile(
                key=f"assets/{source_sha}/{relative}",
                path=destination,
                content_type=_content_type(relative),
                cache_control="public, max-age=31536000, immutable",
            )
        )

    html = discovered[INDEX_KEY].read_text(encoding="utf-8")
    stylesheet = 'href="./styles.css"'
    entrypoint = 'src="./main.js"'
    if html.count(stylesheet) != 1 or html.count(entrypoint) != 1:
        fail("Entrypoints do HTML não correspondem ao contrato de staging.")
    html = html.replace(
        stylesheet, f'href="./assets/{source_sha}/styles.css"', 1
    ).replace(entrypoint, f'src="./assets/{source_sha}/main.js"', 1)
    index_path = stage_root / INDEX_KEY
    index_path.write_text(html, encoding="utf-8", newline="\n")
    files.append(
        PublishedFile(
            key=INDEX_KEY,
            path=index_path,
            content_type=_content_type(INDEX_KEY),
            cache_control="no-cache, no-store, must-revalidate",
        )
    )
    return StagedFrontend(source_sha=source_sha, files=tuple(files))


def runtime_config(
    *, api_origin: str, client_id: str, cognito_origin: str
) -> dict[str, object]:
    if (
        API_ORIGIN_PATTERN.fullmatch(api_origin) is None
        or CLIENT_ID_PATTERN.fullmatch(client_id) is None
        or COGNITO_ORIGIN_PATTERN.fullmatch(cognito_origin) is None
    ):
        fail("Outputs públicos do runtime não correspondem ao perfil AWS final.")
    root = f"https://{PUBLISHED_DOMAIN}/"
    return {
        "api_base_url": api_origin,
        "cognito": {
            "client_id": client_id,
            "hosted_ui_origin": cognito_origin,
            "logout_uri": root,
            "redirect_uri": root,
            "scopes": ["openid"],
        },
        "schema_version": "runtime-config.v1",
    }


def add_runtime_config(
    staged: StagedFrontend,
    stage_root: Path,
    *,
    api_origin: str,
    client_id: str,
    cognito_origin: str,
) -> StagedFrontend:
    by_key = staged.by_key()
    if RUNTIME_CONFIG_KEY in by_key:
        fail("Runtime config já existe no staging.")
    document = runtime_config(
        api_origin=api_origin,
        client_id=client_id,
        cognito_origin=cognito_origin,
    )
    destination = stage_root / RUNTIME_CONFIG_KEY
    destination.write_text(
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    runtime_file = PublishedFile(
        key=RUNTIME_CONFIG_KEY,
        path=destination,
        content_type=_content_type(RUNTIME_CONFIG_KEY),
        cache_control="no-store",
    )
    return StagedFrontend(
        source_sha=staged.source_sha, files=(*staged.files, runtime_file)
    )


def _json_output(result: CommandResult, *, context: str) -> Mapping[str, Any]:
    if type(result) is not CommandResult or result.returncode != 0:
        fail(f"AWS recusou {context} do frontend.")
    try:
        value = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeError):
        fail(f"AWS devolveu JSON inválido em {context} do frontend.")
    if type(value) is not dict:
        fail(f"AWS devolveu tipo inválido em {context} do frontend.")
    return cast(dict[str, Any], value)


def _validated_existing_keys(document: Mapping[str, Any]) -> set[str]:
    if document.get("IsTruncated") is not False:
        fail("Listagem do bucket frontend está truncada ou ambígua.")
    raw_contents = document.get("Contents", [])
    if type(raw_contents) is not list:
        fail("Listagem do bucket frontend omitiu Contents válido.")
    keys: set[str] = set()
    asset_suffixes = {str(path) for path in ASSET_SOURCE_PATHS}
    for raw_item in raw_contents:
        if type(raw_item) is not dict or type(raw_item.get("Key")) is not str:
            fail("Listagem do bucket frontend contém item inválido.")
        key = cast(str, raw_item["Key"])
        if key in keys:
            fail("Listagem do bucket frontend contém chave duplicada.")
        if key not in {INDEX_KEY, RUNTIME_CONFIG_KEY}:
            match = re.fullmatch(r"assets/([0-9a-f]{40})/(.+)", key)
            if match is None or match.group(2) not in asset_suffixes:
                fail("Bucket frontend contém chave fora da allowlist removível.")
        keys.add(key)
    return keys


def _put_file(bucket: str, item: PublishedFile, runner: Runner) -> None:
    result = runner(
        (
            "s3api",
            "put-object",
            "--bucket",
            bucket,
            "--key",
            item.key,
            "--body",
            str(item.path),
            "--content-type",
            item.content_type,
            "--cache-control",
            item.cache_control,
            "--server-side-encryption",
            "AES256",
        )
    )
    _json_output(result, context="upload")


def publish_frontend(
    staged: StagedFrontend,
    *,
    bucket: str,
    distribution_id: str,
    runner: Runner,
    sleep: Sleeper = time.sleep,
    max_waits: int = 240,
) -> str:
    if (
        BUCKET_PATTERN.fullmatch(bucket) is None
        or DISTRIBUTION_ID_PATTERN.fullmatch(distribution_id) is None
        or type(max_waits) is not int
        or max_waits < 1
    ):
        fail("Destino da publicação do frontend é inválido.")
    files = staged.by_key()
    expected_keys = {
        INDEX_KEY,
        RUNTIME_CONFIG_KEY,
        *(f"assets/{staged.source_sha}/{path}" for path in ASSET_SOURCE_PATHS),
    }
    if set(files) != expected_keys or any(
        item.path.is_symlink() or not item.path.is_file() for item in files.values()
    ):
        fail("Staging do frontend diverge da allowlist publicável.")

    listed = _json_output(
        runner(("s3api", "list-objects-v2", "--bucket", bucket)),
        context="listagem",
    )
    existing = _validated_existing_keys(listed)

    immutable = [files[key] for key in sorted(files) if key.startswith("assets/")]
    for item in immutable:
        _put_file(bucket, item, runner)
    _put_file(bucket, files[RUNTIME_CONFIG_KEY], runner)
    _put_file(bucket, files[INDEX_KEY], runner)

    for key in sorted(existing - expected_keys):
        _json_output(
            runner(("s3api", "delete-object", "--bucket", bucket, "--key", key)),
            context="limpeza",
        )

    invalidation = _json_output(
        runner(
            (
                "cloudfront",
                "create-invalidation",
                "--distribution-id",
                distribution_id,
                "--invalidation-batch",
                json.dumps(
                    {
                        "CallerReference": f"sen75-{staged.source_sha}",
                        "Paths": {"Items": ["/*"], "Quantity": 1},
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        ),
        context="invalidação",
    )
    raw_invalidation = invalidation.get("Invalidation")
    if type(raw_invalidation) is not dict:
        fail("CloudFront omitiu a invalidação criada.")
    invalidation_id = cast(dict[str, object], raw_invalidation).get("Id")
    if (
        type(invalidation_id) is not str
        or INVALIDATION_ID_PATTERN.fullmatch(invalidation_id) is None
    ):
        fail("CloudFront devolveu ID de invalidação inválido.")

    for attempt in range(max_waits):
        answer = _json_output(
            runner(
                (
                    "cloudfront",
                    "get-invalidation",
                    "--distribution-id",
                    distribution_id,
                    "--id",
                    invalidation_id,
                )
            ),
            context="espera da invalidação",
        )
        raw_status = answer.get("Invalidation")
        status = (
            cast(dict[str, object], raw_status).get("Status")
            if type(raw_status) is dict
            else None
        )
        if status == "Completed":
            return invalidation_id
        if status != "InProgress":
            fail("CloudFront devolveu estado inesperado para a invalidação.")
        if attempt + 1 < max_waits:
            sleep(5)
    fail("Invalidação CloudFront excedeu a janela de espera.")


def assert_final_profile(*, region: str, domain: str) -> None:
    if region != PUBLISHED_REGION or domain != PUBLISHED_DOMAIN:
        fail("Publicação exige us-east-1 e o domínio final aprovado.")


def publication_order(arguments: Sequence[tuple[str, ...]]) -> tuple[str, ...]:
    """Expose only uploaded/deleted keys for deterministic local regressions."""
    keys: list[str] = []
    for command in arguments:
        if command[:2] not in {("s3api", "put-object"), ("s3api", "delete-object")}:
            continue
        try:
            keys.append(command[command.index("--key") + 1])
        except (ValueError, IndexError):
            fail("Comando S3 local omitiu chave explícita.")
    return tuple(keys)
