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

# The published inventory is a build output, so it is validated as a closed
# grammar instead of a file allowlist: exactly these unhashed entries at the
# top level, plus a flat assets/ directory of Vite-hashed bundles.
ROOT_KEYS = frozenset({INDEX_KEY, "favicon.svg", "theme-init.js"})
ASSET_PREFIX = "assets/"
ASSET_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}-[A-Za-z0-9_-]{8}\.(?:css|js)$"
)
ALLOWED_SUFFIXES = frozenset({".css", ".html", ".js", ".svg"})
INDEX_REFERENCE_PATTERN = re.compile(r'(?:src|href)="(\./[^"]+)"')
SCRIPT_TAG_PATTERN = re.compile(r"<script\b[^>]*>", re.IGNORECASE)


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
        fail("Raiz publicável do frontend está ausente ou não é um diretório regular.")
    discovered: dict[str, Path] = {}
    total = 0
    for candidate in source_root.rglob("*"):
        if candidate.is_symlink():
            fail("Build publicável do frontend contém symlink proibido.")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(source_root).as_posix()
        if relative in discovered:
            fail("Inventário do build possui caminho duplicado.")
        if relative in ROOT_KEYS:
            pass
        elif relative.startswith(ASSET_PREFIX):
            name = relative[len(ASSET_PREFIX) :]
            if "/" in name or ASSET_NAME_PATTERN.fullmatch(name) is None:
                fail("Build do frontend contém asset fora da gramática publicável.")
        else:
            fail("Build do frontend contém arquivo fora da gramática publicável.")
        if Path(relative).suffix not in ALLOWED_SUFFIXES:
            fail("Build do frontend contém extensão inesperada.")
        size = candidate.stat().st_size
        if size > MAX_SOURCE_FILE_BYTES:
            fail("Arquivo publicável do frontend excede o limite individual.")
        total += size
        discovered[relative] = candidate
    if total > MAX_SOURCE_TOTAL_BYTES:
        fail("Inventário publicável do frontend excede o limite total.")
    if not set(discovered) >= ROOT_KEYS:
        fail("Build do frontend não declara as entradas obrigatórias da raiz.")
    assets = {key for key in discovered if key.startswith(ASSET_PREFIX)}
    if not any(key.endswith(".js") for key in assets) or not any(
        key.endswith(".css") for key in assets
    ):
        fail("Build do frontend não produziu os bundles esperados.")
    for relative in sorted(set(discovered) - {"favicon.svg"}):
        try:
            discovered[relative].read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            fail("Arquivo publicável do frontend não é UTF-8 legível.")
    _assert_inline_free_index(discovered)
    return discovered


def _assert_inline_free_index(discovered: Mapping[str, Path]) -> None:
    """Refuse an index the published CSP would block or that references a
    file this publication does not carry."""
    html = discovered[INDEX_KEY].read_text(encoding="utf-8")
    lowered = html.lower()
    if "<style" in lowered or " style=" in lowered:
        fail("Índice publicável declara estilo inline recusado pela CSP.")
    for tag in SCRIPT_TAG_PATTERN.findall(html):
        if "src=" not in tag.lower():
            fail("Índice publicável declara script inline recusado pela CSP.")
    for reference in INDEX_REFERENCE_PATTERN.findall(html):
        target = reference[2:]
        if target not in discovered:
            fail("Índice publicável referencia arquivo ausente do build.")


def _content_type(relative: str) -> str:
    suffix = Path(relative).suffix
    content_types = {
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml; charset=utf-8",
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
    files: list[PublishedFile] = []
    for relative in sorted(discovered):
        destination = stage_root / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(discovered[relative], destination)
        # Only the hashed bundles may be cached forever: every other key keeps
        # its name across publications, so a stale copy would outlive the build.
        cache_control = (
            "public, max-age=31536000, immutable"
            if relative.startswith(ASSET_PREFIX)
            else "no-cache, no-store, must-revalidate"
        )
        files.append(
            PublishedFile(
                key=relative,
                path=destination,
                content_type=_content_type(relative),
                cache_control=cache_control,
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
    for raw_item in raw_contents:
        if type(raw_item) is not dict or type(raw_item.get("Key")) is not str:
            fail("Listagem do bucket frontend contém item inválido.")
        key = cast(str, raw_item["Key"])
        if key in keys:
            fail("Listagem do bucket frontend contém chave duplicada.")
        if key not in ROOT_KEYS | {RUNTIME_CONFIG_KEY}:
            name = key[len(ASSET_PREFIX) :] if key.startswith(ASSET_PREFIX) else ""
            if "/" in name or ASSET_NAME_PATTERN.fullmatch(name) is None:
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
    expected_keys = set(files)
    if not ROOT_KEYS | {RUNTIME_CONFIG_KEY} <= expected_keys or any(
        item.path.is_symlink() or not item.path.is_file() for item in files.values()
    ):
        fail("Staging do frontend diverge da gramática publicável.")

    listed = _json_output(
        runner(("s3api", "list-objects-v2", "--bucket", bucket)),
        context="listagem",
    )
    existing = _validated_existing_keys(listed)

    # Publish what the index depends on before the index itself, so no visitor
    # can load a document whose bundles are not served yet.
    immutable = [files[key] for key in sorted(files) if key.startswith(ASSET_PREFIX)]
    for item in immutable:
        _put_file(bucket, item, runner)
    for key in sorted(ROOT_KEYS - {INDEX_KEY}):
        _put_file(bucket, files[key], runner)
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
