"""Audit reachable public Git objects without reading ignored local files."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Final, cast

_EXPECTED_PROTECTED_IDENTITIES: Final = 8
_GIT_TIMEOUT_SECONDS: Final = 60.0
_MAX_MANIFEST_BYTES: Final = 1_000_000
_OBJECT_ID_PATTERN: Final = re.compile(rb"[0-9a-f]{40,64}")
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")

_ERROR_MESSAGES: Final = {
    "git_unavailable": "Git is unavailable for the public repository audit.",
    "history_incomplete": "The public repository audit requires complete history.",
    "manifest_invalid": "The protected material manifest is invalid.",
    "manifest_untrusted": (
        "The protected material manifest is not a clean tracked file."
    ),
    "protected_content_detected": (
        "Protected material content was detected in Git objects."
    ),
    "protected_filename_detected": (
        "A protected material filename was detected in Git trees."
    ),
    "repository_invalid": "The public repository could not be audited safely.",
}


class PublicRepositoryAuditError(RuntimeError):
    """Describe a sanitized, expected public repository audit failure."""

    def __init__(self, code: str) -> None:
        super().__init__(_ERROR_MESSAGES[code])
        self.code = code


@dataclass(frozen=True, slots=True)
class ProtectedMaterialPolicy:
    """Keep protected identities out of representations and reports."""

    names: frozenset[str] = field(repr=False)
    digests: frozenset[str] = field(repr=False)


@dataclass(frozen=True, slots=True)
class PublicRepositoryAuditReport:
    """Expose only aggregate, non-sensitive audit evidence."""

    protected_identity_count: int
    revision_count: int
    tree_count: int
    blob_count: int
    index_entry_count: int

    def to_json(self) -> str:
        """Render a deterministic and sanitized JSON report."""
        return (
            json.dumps(
                {
                    "blob_count": self.blob_count,
                    "history_complete": True,
                    "index_entry_count": self.index_entry_count,
                    "protected_identity_count": self.protected_identity_count,
                    "revision_count": self.revision_count,
                    "scope": ["HEAD", "origin", "tags", "index"],
                    "status": "passed",
                    "tree_count": self.tree_count,
                },
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )


@dataclass(frozen=True, slots=True)
class _GitRepository:
    root: Path = field(repr=False)
    executable: str = field(repr=False)


def audit_public_repository(
    repository_root: Path,
    manifest_path: Path,
) -> PublicRepositoryAuditReport:
    """Audit the index and public reachable history using protected metadata."""
    repository = _resolve_repository(repository_root)
    manifest = _resolve_manifest(repository, manifest_path)
    policy = _load_policy(manifest)
    _require_complete_history(repository)

    revisions = _public_revisions(repository)
    tree_ids = _tree_ids(repository, revisions)
    for tree_id in tree_ids:
        paths = _run_git(
            repository,
            ("ls-tree", "-r", "-z", "--name-only", tree_id),
        ).split(b"\0")
        _reject_protected_filenames(paths, policy)

    index_paths, index_blob_ids = _index_entries(repository)
    _reject_protected_filenames(index_paths, policy)

    reachable_ids = _reachable_object_ids(repository, revisions)
    reachable_blob_ids = _blob_ids(repository, reachable_ids)
    blob_ids = tuple(sorted(set(reachable_blob_ids).union(index_blob_ids)))
    digests = _hash_blobs(repository, blob_ids)
    if not policy.digests.isdisjoint(digests):
        raise PublicRepositoryAuditError("protected_content_detected")

    return PublicRepositoryAuditReport(
        protected_identity_count=len(policy.names),
        revision_count=len(revisions),
        tree_count=len(tree_ids),
        blob_count=len(blob_ids),
        index_entry_count=len(index_paths),
    )


def _resolve_repository(repository_root: Path) -> _GitRepository:
    git = shutil.which("git")
    if git is None:
        raise PublicRepositoryAuditError("git_unavailable")
    try:
        root = repository_root.resolve(strict=True)
    except OSError:
        raise PublicRepositoryAuditError("repository_invalid") from None
    if not root.is_dir():
        raise PublicRepositoryAuditError("repository_invalid")

    repository = _GitRepository(root=root, executable=str(Path(git).resolve()))
    top_level = _run_git(repository, ("rev-parse", "--show-toplevel"))
    try:
        discovered = Path(top_level.decode("utf-8").strip()).resolve(strict=True)
    except (OSError, UnicodeError):
        raise PublicRepositoryAuditError("repository_invalid") from None
    if discovered != root:
        raise PublicRepositoryAuditError("repository_invalid")
    return repository


def _resolve_manifest(repository: _GitRepository, manifest_path: Path) -> Path:
    candidate = (
        manifest_path
        if manifest_path.is_absolute()
        else repository.root / manifest_path
    )
    if candidate.is_symlink():
        raise PublicRepositoryAuditError("manifest_untrusted")
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(repository.root)
    except (OSError, ValueError):
        raise PublicRepositoryAuditError("manifest_untrusted") from None
    if not resolved.is_file():
        raise PublicRepositoryAuditError("manifest_untrusted")

    relative_name = relative.as_posix()
    _run_git(
        repository,
        ("ls-files", "--error-unmatch", "--", relative_name),
    )
    if (
        _run_git_returncode(
            repository,
            ("diff", "--quiet", "HEAD", "--", relative_name),
        )
        != 0
    ):
        raise PublicRepositoryAuditError("manifest_untrusted")
    return resolved


def _load_policy(manifest_path: Path) -> ProtectedMaterialPolicy:
    try:
        raw = manifest_path.read_bytes()
        if len(raw) > _MAX_MANIFEST_BYTES:
            raise ValueError
        payload = json.loads(raw)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise PublicRepositoryAuditError("manifest_invalid") from None
    if type(payload) is not dict:
        raise PublicRepositoryAuditError("manifest_invalid")
    root = cast(dict[str, object], payload)
    files = root.get("files")
    if (
        root.get("schema_version") != 1
        or root.get("hash_algorithm") != "sha256"
        or type(files) is not list
    ):
        raise PublicRepositoryAuditError("manifest_invalid")

    names: set[str] = set()
    digests: set[str] = set()
    for raw_entry in cast(list[object], files):
        if type(raw_entry) is not dict:
            raise PublicRepositoryAuditError("manifest_invalid")
        entry = cast(dict[str, object], raw_entry)
        name = entry.get("name")
        size_bytes = entry.get("size_bytes")
        digest = entry.get("sha256")
        if (
            type(name) is not str
            or not name
            or "/" in name
            or "\\" in name
            or type(size_bytes) is not int
            or size_bytes < 0
            or type(digest) is not str
            or _SHA256_PATTERN.fullmatch(digest) is None
        ):
            raise PublicRepositoryAuditError("manifest_invalid")
        names.add(name.casefold())
        digests.add(digest)

    if (
        len(names) != _EXPECTED_PROTECTED_IDENTITIES
        or len(digests) != _EXPECTED_PROTECTED_IDENTITIES
    ):
        raise PublicRepositoryAuditError("manifest_invalid")
    return ProtectedMaterialPolicy(
        names=frozenset(names),
        digests=frozenset(digests),
    )


def _require_complete_history(repository: _GitRepository) -> None:
    shallow = _run_git(
        repository,
        ("rev-parse", "--is-shallow-repository"),
    ).strip()
    if shallow != b"false":
        raise PublicRepositoryAuditError("history_incomplete")


def _public_revisions(repository: _GitRepository) -> tuple[str, ...]:
    raw_refs = _run_git(
        repository,
        (
            "for-each-ref",
            "--format=%(refname)",
            "refs/remotes/origin",
            "refs/tags",
        ),
    )
    try:
        refs = tuple(
            line
            for line in raw_refs.decode("utf-8").splitlines()
            if line and not line.endswith("/HEAD")
        )
    except UnicodeError:
        raise PublicRepositoryAuditError("repository_invalid") from None
    return ("HEAD", *refs)


def _tree_ids(
    repository: _GitRepository,
    revisions: tuple[str, ...],
) -> tuple[str, ...]:
    raw = _run_git(repository, ("log", "--format=%T", *revisions))
    ids = _parse_object_ids(raw.splitlines())
    if not ids:
        raise PublicRepositoryAuditError("repository_invalid")
    return tuple(sorted(set(ids)))


def _reachable_object_ids(
    repository: _GitRepository,
    revisions: tuple[str, ...],
) -> tuple[str, ...]:
    raw = _run_git(
        repository,
        ("rev-list", "--objects", "--no-object-names", *revisions),
    )
    ids = _parse_object_ids(raw.splitlines())
    if not ids:
        raise PublicRepositoryAuditError("repository_invalid")
    return tuple(sorted(set(ids)))


def _blob_ids(
    repository: _GitRepository,
    object_ids: tuple[str, ...],
) -> tuple[str, ...]:
    query = ("\n".join(object_ids) + "\n").encode("ascii")
    raw = _run_git(
        repository,
        ("cat-file", "--batch-check=%(objectname) %(objecttype)"),
        input_data=query,
    )
    blobs: list[str] = []
    lines = raw.splitlines()
    if len(lines) != len(object_ids):
        raise PublicRepositoryAuditError("repository_invalid")
    for expected, line in zip(object_ids, lines, strict=True):
        fields = line.split(b" ")
        if (
            len(fields) != 2
            or fields[0] != expected.encode("ascii")
            or fields[1] not in {b"blob", b"commit", b"tag", b"tree"}
        ):
            raise PublicRepositoryAuditError("repository_invalid")
        if fields[1] == b"blob":
            blobs.append(expected)
    return tuple(blobs)


def _index_entries(
    repository: _GitRepository,
) -> tuple[tuple[bytes, ...], tuple[str, ...]]:
    raw = _run_git(repository, ("ls-files", "--stage", "-z"))
    paths: list[bytes] = []
    blob_ids: list[str] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode, object_id, stage = metadata.split(b" ")
        except ValueError:
            raise PublicRepositoryAuditError("repository_invalid") from None
        if (
            mode not in {b"100644", b"100755", b"120000", b"160000"}
            or _OBJECT_ID_PATTERN.fullmatch(object_id) is None
            or stage not in {b"0", b"1", b"2", b"3"}
        ):
            raise PublicRepositoryAuditError("repository_invalid")
        if stage == b"0":
            paths.append(path)
            if mode != b"160000":
                blob_ids.append(object_id.decode("ascii"))
    return tuple(paths), tuple(blob_ids)


def _reject_protected_filenames(
    raw_paths: tuple[bytes, ...] | list[bytes],
    policy: ProtectedMaterialPolicy,
) -> None:
    for raw_path in raw_paths:
        if not raw_path:
            continue
        try:
            basename = raw_path.rsplit(b"/", 1)[-1].decode("utf-8")
        except UnicodeError:
            raise PublicRepositoryAuditError("repository_invalid") from None
        if basename.casefold() in policy.names:
            raise PublicRepositoryAuditError("protected_filename_detected")


def _hash_blobs(
    repository: _GitRepository,
    blob_ids: tuple[str, ...],
) -> frozenset[str]:
    if not blob_ids:
        return frozenset()
    query = ("\n".join(blob_ids) + "\n").encode("ascii")
    raw = _run_git(
        repository,
        ("cat-file", "--batch"),
        input_data=query,
    )
    cursor = 0
    digests: set[str] = set()
    view = memoryview(raw)
    try:
        for expected in blob_ids:
            header_end = raw.find(b"\n", cursor)
            if header_end < 0:
                raise PublicRepositoryAuditError("repository_invalid")
            fields = raw[cursor:header_end].split(b" ")
            if (
                len(fields) != 3
                or fields[0] != expected.encode("ascii")
                or fields[1] != b"blob"
            ):
                raise PublicRepositoryAuditError("repository_invalid")
            try:
                size = int(fields[2])
            except ValueError:
                raise PublicRepositoryAuditError("repository_invalid") from None
            content_start = header_end + 1
            content_end = content_start + size
            if content_end >= len(raw) or raw[content_end : content_end + 1] != b"\n":
                raise PublicRepositoryAuditError("repository_invalid")
            digests.add(sha256(view[content_start:content_end]).hexdigest())
            cursor = content_end + 1
    finally:
        view.release()
    if cursor != len(raw):
        raise PublicRepositoryAuditError("repository_invalid")
    return frozenset(digests)


def _parse_object_ids(lines: list[bytes]) -> tuple[str, ...]:
    values: list[str] = []
    for line in lines:
        if _OBJECT_ID_PATTERN.fullmatch(line) is None:
            raise PublicRepositoryAuditError("repository_invalid")
        values.append(line.decode("ascii"))
    return tuple(values)


def _run_git(
    repository: _GitRepository,
    arguments: tuple[str, ...],
    *,
    input_data: bytes | None = None,
) -> bytes:
    try:
        completed = subprocess.run(  # noqa: S603
            (repository.executable, *arguments),
            cwd=repository.root,
            input=input_data,
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PublicRepositoryAuditError("repository_invalid") from None
    if completed.returncode != 0:
        raise PublicRepositoryAuditError("repository_invalid")
    return completed.stdout


def _run_git_returncode(
    repository: _GitRepository,
    arguments: tuple[str, ...],
) -> int:
    try:
        completed = subprocess.run(  # noqa: S603
            (repository.executable, *arguments),
            cwd=repository.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PublicRepositoryAuditError("repository_invalid") from None
    if completed.returncode not in {0, 1}:
        raise PublicRepositoryAuditError("repository_invalid")
    return completed.returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit public Git objects against protected material identities."
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/source-manifest.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the sanitized public repository audit CLI."""
    arguments = _parser().parse_args(argv)
    try:
        report = audit_public_repository(arguments.repository, arguments.manifest)
    except PublicRepositoryAuditError as error:
        print(
            json.dumps(
                {"code": error.code, "status": "failed"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(report.to_json(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
