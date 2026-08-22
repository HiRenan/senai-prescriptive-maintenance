"""Synthetic tests for the authorized banner source boundary."""

from __future__ import annotations

import inspect
import json
from hashlib import sha256
from io import UnsupportedOperation
from pathlib import Path
from typing import BinaryIO, NoReturn

import prescriptive_maintenance.data.source as source_module
import pytest
from prescriptive_maintenance.data import (
    BannerSourceFingerprint,
    SourceChangedError,
    SourceHashMismatchError,
    SourceManifestError,
    SourceNotFoundError,
    SourcePermissionError,
    SourceSizeMismatchError,
    UnexpectedSourceNameError,
    consume_banner_source,
    consume_banner_source_audited,
)

_SYNTHETIC_CONTENT = b"entirely-synthetic-source\n"


def _write_manifest(directory: Path, expected_content: bytes) -> Path:
    manifest_path = directory / "source-manifest.json"
    manifest: dict[str, object] = {
        "schema_version": 1,
        "hash_algorithm": "sha256",
        "files": [
            {
                "name": "banner.csv",
                "size_bytes": len(expected_content),
                "sha256": sha256(expected_content).hexdigest(),
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _unexpected_consumer(_source: BinaryIO) -> NoReturn:
    pytest.fail("consumer must not run before initial validation succeeds")


def _assert_sanitized(error: Exception, *forbidden_values: str) -> None:
    message = str(error)
    for forbidden_value in forbidden_values:
        assert forbidden_value not in message


def test_input_path_is_an_explicit_required_keyword() -> None:
    parameter = inspect.signature(consume_banner_source).parameters["input_path"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_consumes_valid_source_without_copying_or_modifying_it(tmp_path: Path) -> None:
    source_path = tmp_path / "banner.csv"
    source_path.write_bytes(_SYNTHETIC_CONTENT)
    manifest_path = _write_manifest(tmp_path, _SYNTHETIC_CONTENT)
    before_stat = source_path.stat()
    before_hash = sha256(source_path.read_bytes()).hexdigest()
    before_files = set(tmp_path.iterdir())

    def consumer(source: BinaryIO) -> int:
        assert source.readable()
        assert not source.writable()
        assert source.read() == _SYNTHETIC_CONTENT
        with pytest.raises(UnsupportedOperation):
            source.write(b"write-must-fail")
        return source.tell()

    consumed_bytes = consume_banner_source(
        input_path=source_path,
        manifest_path=manifest_path,
        consumer=consumer,
    )

    after_stat = source_path.stat()
    assert consumed_bytes == len(_SYNTHETIC_CONTENT)
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert after_stat.st_size == before_stat.st_size
    assert sha256(source_path.read_bytes()).hexdigest() == before_hash
    assert set(tmp_path.iterdir()) == before_files


def test_audited_consumption_returns_exact_immutable_pre_post_receipt(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "banner.csv"
    source_path.write_bytes(_SYNTHETIC_CONTENT)
    manifest_path = _write_manifest(tmp_path, _SYNTHETIC_CONTENT)
    expected = BannerSourceFingerprint(
        size_bytes=len(_SYNTHETIC_CONTENT),
        sha256=sha256(_SYNTHETIC_CONTENT).hexdigest(),
    )

    receipt = consume_banner_source_audited(
        input_path=source_path,
        manifest_path=manifest_path,
        consumer=lambda source: source.read(),
    )

    assert receipt.result == _SYNTHETIC_CONTENT
    assert receipt.pre_fingerprint == expected
    assert receipt.post_fingerprint == expected


def test_rejects_missing_source_before_consumer(tmp_path: Path) -> None:
    source_path = tmp_path / "private-directory" / "banner.csv"
    manifest_path = _write_manifest(tmp_path, _SYNTHETIC_CONTENT)

    with pytest.raises(SourceNotFoundError) as raised:
        consume_banner_source(
            input_path=source_path,
            manifest_path=manifest_path,
            consumer=_unexpected_consumer,
        )

    _assert_sanitized(raised.value, str(source_path), "private-directory")


def test_rejects_unexpected_name_before_consumer(tmp_path: Path) -> None:
    source_path = tmp_path / "private-name.csv"
    source_path.write_bytes(_SYNTHETIC_CONTENT)
    manifest_path = _write_manifest(tmp_path, _SYNTHETIC_CONTENT)

    with pytest.raises(UnexpectedSourceNameError) as raised:
        consume_banner_source(
            input_path=source_path,
            manifest_path=manifest_path,
            consumer=_unexpected_consumer,
        )

    _assert_sanitized(raised.value, str(source_path), "private-name.csv")


def test_rejects_truncated_source_before_consumer(tmp_path: Path) -> None:
    source_path = tmp_path / "banner.csv"
    source_path.write_bytes(_SYNTHETIC_CONTENT[:-1])
    manifest_path = _write_manifest(tmp_path, _SYNTHETIC_CONTENT)

    with pytest.raises(SourceSizeMismatchError) as raised:
        consume_banner_source(
            input_path=source_path,
            manifest_path=manifest_path,
            consumer=_unexpected_consumer,
        )

    _assert_sanitized(raised.value, str(source_path), "entirely-synthetic-source")


def test_rejects_invalid_hash_before_consumer(tmp_path: Path) -> None:
    source_path = tmp_path / "banner.csv"
    source_path.write_bytes(b"x" * len(_SYNTHETIC_CONTENT))
    manifest_path = _write_manifest(tmp_path, _SYNTHETIC_CONTENT)

    with pytest.raises(SourceHashMismatchError) as raised:
        consume_banner_source(
            input_path=source_path,
            manifest_path=manifest_path,
            consumer=_unexpected_consumer,
        )

    assert sha256(_SYNTHETIC_CONTENT).hexdigest() in str(raised.value)
    assert sha256(source_path.read_bytes()).hexdigest() in str(raised.value)
    _assert_sanitized(raised.value, str(source_path), "entirely-synthetic-source")


def test_reports_permission_failure_without_exposing_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "banner.csv"
    source_path.write_bytes(_SYNTHETIC_CONTENT)
    manifest_path = _write_manifest(tmp_path, _SYNTHETIC_CONTENT)

    def deny_open(*_args: object, **_kwargs: object) -> NoReturn:
        raise PermissionError

    monkeypatch.setattr(source_module.os, "open", deny_open)

    with pytest.raises(SourcePermissionError) as raised:
        consume_banner_source(
            input_path=source_path,
            manifest_path=manifest_path,
            consumer=_unexpected_consumer,
        )

    _assert_sanitized(
        raised.value,
        str(source_path),
        str(tmp_path),
        "entirely-synthetic-source",
    )


def test_detects_mutation_during_consumption(tmp_path: Path) -> None:
    source_path = tmp_path / "banner.csv"
    source_path.write_bytes(_SYNTHETIC_CONTENT)
    manifest_path = _write_manifest(tmp_path, _SYNTHETIC_CONTENT)
    mutated_content = b"x" * len(_SYNTHETIC_CONTENT)

    def mutating_consumer(source: BinaryIO) -> None:
        assert source.read() == _SYNTHETIC_CONTENT
        source_path.write_bytes(mutated_content)

    with pytest.raises(SourceChangedError) as raised:
        consume_banner_source(
            input_path=source_path,
            manifest_path=manifest_path,
            consumer=mutating_consumer,
        )

    assert source_path.read_bytes() == mutated_content
    _assert_sanitized(
        raised.value,
        str(source_path),
        "entirely-synthetic-source",
        mutated_content.decode(),
    )


def test_rejects_invalid_manifest_before_consumer(tmp_path: Path) -> None:
    source_path = tmp_path / "banner.csv"
    source_path.write_bytes(_SYNTHETIC_CONTENT)
    manifest_path = tmp_path / "private-manifest.json"
    manifest_path.write_text("not-json-private-value", encoding="utf-8")

    with pytest.raises(SourceManifestError) as raised:
        consume_banner_source(
            input_path=source_path,
            manifest_path=manifest_path,
            consumer=_unexpected_consumer,
        )

    _assert_sanitized(
        raised.value,
        str(manifest_path),
        str(tmp_path),
        "not-json-private-value",
    )
