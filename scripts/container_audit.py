"""Audit the Docker-filtered application contexts and builder filesystems."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
FORBIDDEN_SOURCE_PARTS: Final = frozenset(
    {
        ".cache",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "openapi",
        "test",
        "tests",
    }
)


@dataclass(frozen=True)
class ContextSpec:
    """Describe one Dockerfile context and its exact allowed inputs."""

    name: str
    dockerfile: Path
    static_files: frozenset[str]
    source_root: Path | None = None
    source_suffixes: frozenset[str] = frozenset()
    source_excluded_endings: frozenset[str] = frozenset()


CONTEXT_SPECS: Final = (
    ContextSpec(
        name="api",
        dockerfile=Path("apps/api/Dockerfile"),
        static_files=frozenset(
            {
                "apps/api/README.md",
                "apps/api/pyproject.toml",
                "pyproject.toml",
                "uv.lock",
            }
        ),
        source_root=Path("apps/api/src"),
        source_suffixes=frozenset({".json", ".py", ".txt"}),
    ),
    ContextSpec(
        name="web",
        dockerfile=Path("apps/web/Dockerfile"),
        static_files=frozenset(
            {
                "apps/web/index.html",
                "apps/web/package.json",
                "apps/web/public/favicon.svg",
                "apps/web/public/theme-init.js",
                "apps/web/server.mjs",
                "apps/web/vite.config.ts",
                "package.json",
                "pnpm-lock.yaml",
                "pnpm-workspace.yaml",
            }
        ),
        source_root=Path("apps/web/src"),
        source_suffixes=frozenset({".css", ".js", ".svg", ".ts", ".tsx"}),
        # The generated contract ships its declarations beside the module the
        # bundle imports; only the module is an input to the build.
        source_excluded_endings=frozenset({".d.ts"}),
    ),
)


class ContainerAuditFailure(RuntimeError):
    """Describe an expected context or builder audit failure."""


def _resolve_docker() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise ContainerAuditFailure("Docker não foi encontrado.")
    return docker


def _run_command(command: tuple[str, ...], failure_message: str) -> None:
    try:
        result = subprocess.run(  # noqa: S603
            command,
            cwd=REPOSITORY_ROOT,
            check=False,
        )
    except OSError as error:
        raise ContainerAuditFailure(failure_message) from error

    if result.returncode != 0:
        raise ContainerAuditFailure(
            f"{failure_message} Código de saída: {result.returncode}."
        )


def _expected_files(spec: ContextSpec) -> frozenset[str]:
    expected = set(spec.static_files)
    if spec.source_root is None:
        return frozenset(expected)

    source_root = REPOSITORY_ROOT / spec.source_root
    for path in source_root.rglob("*"):
        if (
            path.is_file()
            and not path.is_symlink()
            and not FORBIDDEN_SOURCE_PARTS.intersection(
                part.lower() for part in path.parts
            )
            and not path.name.lower().startswith("readme")
            and not path.name.lower().startswith("test_")
            and not path.stem.lower().endswith("_test")
            and path.suffix.lower() in spec.source_suffixes
            and not any(
                path.name.lower().endswith(ending)
                for ending in spec.source_excluded_endings
            )
        ):
            expected.add(path.relative_to(REPOSITORY_ROOT).as_posix())
    return frozenset(expected)


def _export_context(
    docker: str,
    spec: ContextSpec,
    output_directory: Path,
) -> Path:
    _run_command(
        (
            docker,
            "buildx",
            "build",
            "--file",
            spec.dockerfile.as_posix(),
            "--target",
            "context-audit",
            "--no-cache",
            "--progress=plain",
            "--output",
            f"type=local,dest={output_directory}",
            ".",
        ),
        f"Falha ao exportar o contexto Docker da {spec.name}.",
    )
    return output_directory / "context"


def _audit_exported_context(spec: ContextSpec, context_root: Path) -> None:
    if not context_root.is_dir():
        raise ContainerAuditFailure(
            f"O contexto exportado da {spec.name} não foi encontrado."
        )

    actual = frozenset(
        path.relative_to(context_root).as_posix()
        for path in context_root.rglob("*")
        if path.is_file() or path.is_symlink()
    )
    expected = _expected_files(spec)
    unexpected = actual - expected
    missing = expected - actual
    if unexpected or missing:
        raise ContainerAuditFailure(
            f"O contexto da {spec.name} diverge da allowlist: "
            f"{len(unexpected)} inesperado(s), {len(missing)} ausente(s)."
        )

    manifest_digest = hashlib.sha256()
    total_bytes = 0
    for relative_path in sorted(actual):
        content = (context_root / relative_path).read_bytes()
        total_bytes += len(content)
        manifest_digest.update(relative_path.encode("utf-8"))
        manifest_digest.update(b"\0")
        manifest_digest.update(hashlib.sha256(content).digest())

    print(
        f"Contexto {spec.name}: files={len(actual)} bytes={total_bytes} "
        f"manifest_sha256={manifest_digest.hexdigest()}"
    )
    for relative_path in sorted(actual):
        print(f"  {relative_path}")


def _audit_builder(docker: str, spec: ContextSpec) -> None:
    _run_command(
        (
            docker,
            "buildx",
            "build",
            "--file",
            spec.dockerfile.as_posix(),
            "--target",
            "builder-audit",
            "--no-cache",
            "--progress=plain",
            "--output",
            "type=cacheonly",
            ".",
        ),
        f"Falha na auditoria do builder da {spec.name}.",
    )


def main() -> None:
    """Export each real context, enforce its allowlist, and audit its builder."""
    try:
        docker = _resolve_docker()
        with tempfile.TemporaryDirectory(prefix="sen49-container-context-") as temp:
            temporary_root = Path(temp)
            for spec in CONTEXT_SPECS:
                context_root = _export_context(
                    docker,
                    spec,
                    temporary_root / spec.name,
                )
                _audit_exported_context(spec, context_root)
                _audit_builder(docker, spec)
    except ContainerAuditFailure as error:
        print(f"Auditoria de contêiner falhou: {error}", file=sys.stderr)
        raise SystemExit(1) from None

    print("Contextos Docker e filesystems dos builders verificados.")


if __name__ == "__main__":
    main()
