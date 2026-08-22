"""Read-only, integrity-checked access to the authorized banner source."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO, Final, cast

_BANNER_MANIFEST_NAME: Final = "banner.csv"
_FINGERPRINT_CHUNK_SIZE: Final = 1024 * 1024
_SUPPORTED_MANIFEST_SCHEMA: Final = 1
_SUPPORTED_HASH_ALGORITHM: Final = "sha256"


class BannerSourceError(Exception):
    """Base class for sanitized banner source failures."""


class SourceAccessError(BannerSourceError):
    """Raised when the source cannot be read safely."""


class SourceNotFoundError(SourceAccessError):
    """Raised when the required source file does not exist."""


class SourcePermissionError(SourceAccessError):
    """Raised when the source cannot be opened with read-only access."""


class UnexpectedSourceNameError(BannerSourceError):
    """Raised when the source basename is not approved by the manifest."""


class SourceIntegrityError(BannerSourceError):
    """Base class for source fingerprint mismatches."""


class SourceSizeMismatchError(SourceIntegrityError):
    """Raised when the source byte size differs from the manifest."""


class SourceHashMismatchError(SourceIntegrityError):
    """Raised when the source SHA-256 differs from the manifest."""


class SourceChangedError(SourceIntegrityError):
    """Raised when the source fingerprint changes during consumption."""


class SourceManifestError(BannerSourceError):
    """Raised when the public source manifest cannot be trusted."""


@dataclass(frozen=True, slots=True)
class _Fingerprint:
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _ManifestEntry:
    name: str
    fingerprint: _Fingerprint


def consume_banner_source[ConsumerResult](
    *,
    input_path: Path,
    manifest_path: Path,
    consumer: Callable[[BinaryIO], ConsumerResult],
) -> ConsumerResult:
    """Validate, expose, and revalidate ``banner.csv`` through a read-only handle.

    Both paths are mandatory and no source discovery or default is performed. The
    consumer runs only after the source matches the public manifest. Its result is
    returned only after the same open file has an identical post-consumption
    fingerprint.
    """

    expected = _load_banner_manifest_entry(manifest_path)
    if input_path.name != expected.name:
        raise UnexpectedSourceNameError(
            f"Banner source name is invalid; expected {expected.name}."
        )

    with _open_source_read_only(input_path) as source:
        before = _fingerprint(source)
        _validate_initial_fingerprint(actual=before, expected=expected.fingerprint)
        source.seek(0)

        try:
            return consumer(source)
        finally:
            after = _fingerprint(source)
            if after != before:
                raise SourceChangedError(
                    "Banner source changed during consumption."
                ) from None


def _load_banner_manifest_entry(manifest_path: Path) -> _ManifestEntry:
    try:
        with manifest_path.open("rb") as manifest_file:
            payload: object = json.load(manifest_file)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SourceManifestError(
            "Public source manifest is unavailable or invalid."
        ) from None

    if not isinstance(payload, dict):
        raise SourceManifestError("Public source manifest is invalid.")

    manifest = cast(dict[str, object], payload)
    if (
        manifest.get("schema_version") != _SUPPORTED_MANIFEST_SCHEMA
        or manifest.get("hash_algorithm") != _SUPPORTED_HASH_ALGORITHM
    ):
        raise SourceManifestError("Public source manifest is invalid.")

    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise SourceManifestError("Public source manifest is invalid.")

    matches: list[_ManifestEntry] = []
    for raw_entry in cast(list[object], raw_files):
        if not isinstance(raw_entry, dict):
            continue
        entry = cast(dict[str, object], raw_entry)
        if entry.get("name") != _BANNER_MANIFEST_NAME:
            continue
        matches.append(_parse_banner_entry(entry))

    if len(matches) != 1:
        raise SourceManifestError("Public source manifest is invalid.")
    return matches[0]


def _parse_banner_entry(entry: dict[str, object]) -> _ManifestEntry:
    name = entry.get("name")
    size_bytes = entry.get("size_bytes")
    expected_hash = entry.get("sha256")
    if (
        not isinstance(name, str)
        or not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes < 0
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
    ):
        raise SourceManifestError("Public source manifest is invalid.")

    return _ManifestEntry(
        name=name,
        fingerprint=_Fingerprint(size_bytes=size_bytes, sha256=expected_hash),
    )


def _open_source_read_only(input_path: Path) -> BinaryIO:
    binary_flag = cast(int, getattr(os, "O_BINARY", 0))
    close_on_exec_flag = cast(int, getattr(os, "O_CLOEXEC", 0))
    flags = os.O_RDONLY | binary_flag | close_on_exec_flag

    try:
        descriptor = os.open(input_path, flags)
    except FileNotFoundError:
        raise SourceNotFoundError("Required banner source was not found.") from None
    except PermissionError:
        raise SourcePermissionError(
            "Permission denied while opening the banner source read-only."
        ) from None
    except OSError:
        raise SourceAccessError("Banner source could not be opened safely.") from None

    try:
        return cast(BinaryIO, os.fdopen(descriptor, "rb", closefd=True))
    except OSError:
        os.close(descriptor)
        raise SourceAccessError("Banner source could not be opened safely.") from None


def _fingerprint(source: BinaryIO) -> _Fingerprint:
    try:
        source.seek(0)
        digest = sha256()
        size_bytes = 0
        while chunk := source.read(_FINGERPRINT_CHUNK_SIZE):
            digest.update(chunk)
            size_bytes += len(chunk)
        return _Fingerprint(size_bytes=size_bytes, sha256=digest.hexdigest())
    except PermissionError:
        raise SourcePermissionError(
            "Permission denied while reading the banner source."
        ) from None
    except (OSError, ValueError):
        raise SourceAccessError("Banner source could not be read safely.") from None


def _validate_initial_fingerprint(
    *, actual: _Fingerprint, expected: _Fingerprint
) -> None:
    if actual.size_bytes != expected.size_bytes:
        raise SourceSizeMismatchError(
            "Banner source size does not match the manifest "
            f"(expected {expected.size_bytes} bytes, got {actual.size_bytes})."
        )
    if actual.sha256 != expected.sha256:
        raise SourceHashMismatchError(
            "Banner source SHA-256 does not match the manifest "
            f"(expected {expected.sha256}, got {actual.sha256})."
        )
