"""Regression tests for the sanitized public Git object audit."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

pytestmark = pytest.mark.failure_matrix

_IDENTITY_COUNT = 8
_AUDIT_SCRIPT = Path(__file__).parents[3] / "scripts" / "public_repository_audit.py"


def test_clean_audit_ignores_local_files_and_emits_only_aggregate_evidence(
    tmp_path: Path,
) -> None:
    repository, manifest, names, contents = _synthetic_repository(tmp_path)
    ignored = repository / names[0]
    ignored.write_bytes(contents[0])

    completed = _audit(repository, manifest)
    serialized = completed.stdout.decode("utf-8")
    payload = json.loads(serialized)

    assert completed.returncode == 0
    assert completed.stderr == b""
    assert payload["protected_identity_count"] == _IDENTITY_COUNT
    assert payload["revision_count"] == 1
    assert payload["tree_count"] == 2
    assert payload["blob_count"] > 0
    assert payload["index_entry_count"] == 4
    assert payload["status"] == "passed"
    for name, content in zip(names, contents, strict=True):
        assert name not in serialized
        assert sha256(content).hexdigest() not in serialized
    assert str(repository) not in serialized


def test_staged_protected_filename_is_rejected_without_disclosure(
    tmp_path: Path,
) -> None:
    repository, manifest, names, contents = _synthetic_repository(tmp_path)
    leaked = repository / names[0]
    leaked.write_bytes(b"different entirely synthetic bytes\n")
    _git(repository, "add", "--force", "--", leaked.name)

    completed = _audit(repository, manifest)
    output = completed.stdout + completed.stderr

    assert completed.returncode == 1
    assert json.loads(completed.stderr)["code"] == "protected_filename_detected"
    assert names[0].encode() not in output
    assert sha256(contents[0]).hexdigest().encode() not in output
    assert str(repository).encode() not in output


def test_staged_protected_content_is_rejected_after_rename(
    tmp_path: Path,
) -> None:
    repository, manifest, names, contents = _synthetic_repository(tmp_path)
    renamed = repository / "renamed-synthetic.bin"
    renamed.write_bytes(contents[0])
    _git(repository, "add", "--", renamed.name)

    completed = _audit(repository, manifest)
    output = completed.stdout + completed.stderr

    assert completed.returncode == 1
    assert json.loads(completed.stderr)["code"] == "protected_content_detected"
    assert names[0].encode() not in output
    assert sha256(contents[0]).hexdigest().encode() not in output


def test_removed_protected_content_remains_rejected_from_reachable_history(
    tmp_path: Path,
) -> None:
    repository, manifest, _, contents = _synthetic_repository(tmp_path)
    historical = repository / "historical-synthetic.bin"
    historical.write_bytes(contents[0])
    _commit(repository, "add synthetic historical probe")
    historical.unlink()
    _commit(repository, "remove synthetic historical probe")

    completed = _audit(repository, manifest)

    assert completed.returncode == 1
    assert json.loads(completed.stderr)["code"] == "protected_content_detected"
    assert sha256(contents[0]).hexdigest().encode() not in completed.stderr


def test_shallow_clone_is_rejected_before_history_is_approved(tmp_path: Path) -> None:
    repository, _, _, _ = _synthetic_repository(tmp_path / "source")
    clone = tmp_path / "shallow-clone"
    _git(
        tmp_path,
        "clone",
        "--quiet",
        "--depth",
        "1",
        repository.as_uri(),
        str(clone),
    )

    completed = _audit(clone, Path("data/source-manifest.json"))

    assert completed.returncode == 1
    assert json.loads(completed.stderr)["code"] == "history_incomplete"
    assert str(clone).encode() not in completed.stderr


def _synthetic_repository(
    temporary_root: Path,
) -> tuple[Path, Path, tuple[str, ...], tuple[bytes, ...]]:
    repository = temporary_root / "synthetic-public-repository"
    repository.mkdir(parents=True)
    _git(repository, "init", "--quiet", "--initial-branch=main")

    names = tuple(f"restricted-source-{index}.bin" for index in range(8))
    contents = tuple(
        f"entirely synthetic protected payload {index}\n".encode() for index in range(8)
    )
    manifest = repository / "data" / "source-manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "hash_algorithm": "sha256",
                "files": [
                    {
                        "name": name,
                        "size_bytes": len(content),
                        "sha256": sha256(content).hexdigest(),
                    }
                    for name, content in zip(names, contents, strict=True)
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (repository / ".gitignore").write_text(
        f"/{names[0]}\n",
        encoding="utf-8",
        newline="\n",
    )
    (repository / "safe.txt").write_text(
        "entirely synthetic public fixture\n",
        encoding="utf-8",
        newline="\n",
    )
    _commit(repository, "add synthetic audit fixture")
    (repository / "second.txt").write_text(
        "second entirely synthetic public fixture\n",
        encoding="utf-8",
        newline="\n",
    )
    _commit(repository, "extend synthetic audit fixture")
    return repository, Path("data/source-manifest.json"), names, contents


def _commit(repository: Path, message: str) -> None:
    _git(repository, "add", "--all")
    _git(
        repository,
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "--quiet",
        "-m",
        message,
    )


def _audit(repository: Path, manifest: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        (
            sys.executable,
            str(_AUDIT_SCRIPT),
            "--repository",
            str(repository),
            "--manifest",
            str(manifest),
        ),
        cwd=repository,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=30.0,
    )


def _git(repository: Path, *arguments: str) -> bytes:
    executable = shutil.which("git")
    assert executable is not None
    completed = subprocess.run(  # noqa: S603
        (str(Path(executable).resolve()), *arguments),
        cwd=repository,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=20.0,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    return completed.stdout
