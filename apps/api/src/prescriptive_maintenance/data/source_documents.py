"""Read-only inventory and page-traceable extraction of authorized PDFs."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from importlib.metadata import version as package_version
from math import isfinite
from pathlib import Path
from shutil import which
from typing import Any, BinaryIO, Final, Protocol, cast
from unicodedata import category as unicode_category

import pypdfium2 as pdfium  # pyright: ignore[reportMissingTypeStubs]

SOURCE_DOCUMENT_NAMES: Final = tuple(f"Doc{number}.pdf" for number in range(1, 7))
SOURCE_DOCUMENT_INVENTORY_SCHEMA_VERSION: Final = 1
SOURCE_DOCUMENT_EXTRACTION_SCHEMA_VERSION: Final = 1
SOURCE_DOCUMENT_EXTRACTOR_VERSION: Final = 2

_SUPPORTED_MANIFEST_SCHEMA_VERSION: Final = 1
_SUPPORTED_HASH_ALGORITHM: Final = "sha256"
_FINGERPRINT_CHUNK_SIZE: Final = 1024 * 1024
_EXTRACTION_FILENAME: Final = "extraction.v1.json"
_INVENTORY_FILENAME: Final = "inventory.v1.json"
_OCR_RENDER_SCALE: Final = 300 / 72
_MINIMUM_ALPHANUMERIC_CHARACTERS: Final = 12
_MINIMUM_ALPHANUMERIC_RATIO: Final = 0.5
_MINIMUM_MEAN_OCR_CONFIDENCE: Final = 0.8
_MINIMUM_POINT_OCR_CONFIDENCE: Final = 0.6
_SAFE_ADAPTER_VALUE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
_NATIVE_REVIEW_ONLY_SIGNALS: Final = frozenset(
    {"text.too_short", "text.low_alphanumeric_ratio"}
)
_GIT_COMMAND_TIMEOUT_SECONDS: Final = 10


class SourceDocumentError(Exception):
    """Base class for sanitized document extraction failures."""


class SourceDocumentManifestError(SourceDocumentError):
    """Raised when the public source manifest is unavailable or invalid."""


class SourceDocumentOutputError(SourceDocumentError):
    """Raised when local derived artifacts cannot be persisted safely."""


class PageExtractionMethod(StrEnum):
    """Method that produced a page result."""

    NATIVE = "native"
    OCR = "ocr"
    NONE = "none"


class PageExtractionStatus(StrEnum):
    """Quality-aware state of one page extraction."""

    EXTRACTED = "extracted"
    SUSPECT = "suspect"
    OCR_REQUIRED = "ocr_required"
    FAILED = "failed"


class DocumentExtractionStatus(StrEnum):
    """Aggregate extraction state of one authorized document."""

    COMPLETED = "completed"
    ATTENTION_REQUIRED = "attention_required"
    OCR_REQUIRED = "ocr_required"
    PARTIAL = "partial"
    FAILED = "failed"


class SourceDocumentInventoryStatus(StrEnum):
    """Aggregate state of the six-document inventory."""

    COMPLETED = "completed"
    ATTENTION_REQUIRED = "attention_required"
    OCR_REQUIRED = "ocr_required"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class SourceDocumentFingerprint:
    """Size and SHA-256 observed on a read-only source descriptor."""

    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    """Text and confidence values returned by an injected local OCR adapter."""

    text: str
    confidences: tuple[float, ...]


class PageOcrAdapter(Protocol):
    """Optional local OCR boundary; the extractor never invokes a remote service."""

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def extract(self, image: object) -> OcrPageResult: ...


@dataclass(frozen=True, slots=True)
class SourceDocumentSummary:
    """Sanitized summary for one document in a completed inventory run."""

    name: str
    status: DocumentExtractionStatus
    page_count: int | None
    native_page_count: int
    ocr_page_count: int
    ocr_required_page_count: int
    suspect_page_count: int
    failed_page_count: int
    failure_code: str | None
    artifact_path: Path | None


@dataclass(frozen=True, slots=True)
class SourceDocumentExtractionRun:
    """Sanitized result of inventorying all six authorized documents."""

    status: SourceDocumentInventoryStatus
    inventory_path: Path
    documents: tuple[SourceDocumentSummary, ...]


@dataclass(frozen=True, slots=True)
class _ManifestEntry:
    name: str
    fingerprint: SourceDocumentFingerprint


@dataclass(frozen=True, slots=True)
class _TextQuality:
    character_count: int
    non_whitespace_character_count: int
    alphanumeric_character_count: int
    word_count: int
    alphanumeric_ratio: float | None
    replacement_character_count: int
    control_character_count: int
    mean_ocr_confidence: float | None
    minimum_ocr_confidence: float | None
    signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PageExtraction:
    page_number: int
    method: PageExtractionMethod
    status: PageExtractionStatus
    text: str | None
    native_quality: _TextQuality
    quality: _TextQuality
    ocr_trigger_codes: tuple[str, ...]
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class _DocumentExtraction:
    name: str
    status: DocumentExtractionStatus
    fingerprint: SourceDocumentFingerprint | None
    pdf_version: str | None
    page_count: int | None
    pages: tuple[_PageExtraction, ...]
    failure_code: str | None
    artifact_path: Path | None = None


def extract_source_documents(
    *,
    source_directory: Path,
    manifest_path: Path,
    output_directory: Path,
    ocr_adapter: PageOcrAdapter | None = None,
) -> SourceDocumentExtractionRun:
    """Inventory and extract exactly ``Doc1.pdf`` through ``Doc6.pdf``.

    Source and destination paths are explicit. Sources are opened through read-only
    descriptors, checked against the public manifest before extraction, and hashed
    again on the same descriptors afterwards. Real text is written only below the
    caller-provided local output directory and is never logged by this module.
    """

    manifest = _load_manifest(manifest_path)
    adapter_identity = _validate_adapter(ocr_adapter)
    safe_output_directory = _prepare_output_directory(output_directory)

    extracted_documents: list[_DocumentExtraction] = []
    for name in SOURCE_DOCUMENT_NAMES:
        extracted = _extract_document(
            source_path=source_directory / name,
            manifest_entry=manifest[name],
            ocr_adapter=ocr_adapter,
        )
        if extracted.fingerprint is not None:
            artifact_path = _document_artifact_path(
                output_directory=safe_output_directory,
                document_name=name,
                source_sha256=extracted.fingerprint.sha256,
            )
            payload = _document_payload(extracted, adapter_identity)
            _write_json_if_changed(artifact_path, payload)
            extracted = _replace_artifact_path(extracted, artifact_path)
        extracted_documents.append(extracted)

    documents = tuple(extracted_documents)
    inventory_status = _inventory_status(documents)
    inventory_path = safe_output_directory / _INVENTORY_FILENAME
    inventory_payload = _inventory_payload(
        status=inventory_status,
        documents=documents,
        output_directory=safe_output_directory,
        adapter_identity=adapter_identity,
    )
    _write_json_if_changed(inventory_path, inventory_payload)

    return SourceDocumentExtractionRun(
        status=inventory_status,
        inventory_path=inventory_path,
        documents=tuple(_summary(document) for document in documents),
    )


def _load_manifest(manifest_path: Path) -> dict[str, _ManifestEntry]:
    try:
        with manifest_path.open("rb") as manifest_file:
            raw_payload: object = json.load(manifest_file)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SourceDocumentManifestError(
            "Public source manifest is unavailable or invalid."
        ) from None

    if not isinstance(raw_payload, dict):
        raise SourceDocumentManifestError("Public source manifest is invalid.")
    payload = cast(dict[str, object], raw_payload)
    if (
        payload.get("schema_version") != _SUPPORTED_MANIFEST_SCHEMA_VERSION
        or payload.get("hash_algorithm") != _SUPPORTED_HASH_ALGORITHM
    ):
        raise SourceDocumentManifestError("Public source manifest is invalid.")

    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise SourceDocumentManifestError("Public source manifest is invalid.")

    entries: dict[str, _ManifestEntry] = {}
    for raw_entry in cast(list[object], raw_files):
        if not isinstance(raw_entry, dict):
            continue
        entry = cast(dict[str, object], raw_entry)
        name = entry.get("name")
        if name not in SOURCE_DOCUMENT_NAMES:
            continue
        if not isinstance(name, str) or name in entries:
            raise SourceDocumentManifestError("Public source manifest is invalid.")
        entries[name] = _parse_manifest_entry(name, entry)

    if tuple(name for name in SOURCE_DOCUMENT_NAMES if name in entries) != (
        SOURCE_DOCUMENT_NAMES
    ):
        raise SourceDocumentManifestError("Public source manifest is invalid.")
    return entries


def _parse_manifest_entry(name: str, entry: Mapping[str, object]) -> _ManifestEntry:
    size_bytes = entry.get("size_bytes")
    expected_hash = entry.get("sha256")
    if (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes < 0
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
    ):
        raise SourceDocumentManifestError("Public source manifest is invalid.")
    return _ManifestEntry(
        name=name,
        fingerprint=SourceDocumentFingerprint(
            size_bytes=size_bytes,
            sha256=expected_hash,
        ),
    )


def _validate_adapter(
    ocr_adapter: PageOcrAdapter | None,
) -> tuple[str, str] | None:
    if ocr_adapter is None:
        return None
    try:
        name = cast(object, ocr_adapter.name)
        version = cast(object, ocr_adapter.version)
    except Exception:
        raise SourceDocumentError("OCR adapter identity is invalid.") from None
    if (
        not isinstance(name, str)
        or not isinstance(version, str)
        or _SAFE_ADAPTER_VALUE.fullmatch(name) is None
        or _SAFE_ADAPTER_VALUE.fullmatch(version) is None
    ):
        raise SourceDocumentError("OCR adapter identity is invalid.")
    return name, version


def _prepare_output_directory(output_directory: Path) -> Path:
    safe_output_directory = _safe_output_path(output_directory)
    _require_git_ignored(safe_output_directory / _INVENTORY_FILENAME)
    try:
        safe_output_directory.mkdir(parents=True, exist_ok=True)
        if not safe_output_directory.is_dir():
            raise SourceDocumentOutputError("Local output directory is unavailable.")
    except SourceDocumentOutputError:
        raise
    except OSError:
        raise SourceDocumentOutputError(
            "Local output directory is unavailable."
        ) from None
    return safe_output_directory


def _safe_output_path(path: Path) -> Path:
    if ".." in path.parts:
        raise SourceDocumentOutputError("Local output path is unsafe.")
    try:
        absolute_path = path if path.is_absolute() else Path.cwd() / path
        current = Path(absolute_path.anchor)
        for part in absolute_path.parts[1:]:
            current /= part
            if current.is_symlink():
                raise SourceDocumentOutputError("Local output path is unsafe.")
        return absolute_path.resolve(strict=False)
    except SourceDocumentOutputError:
        raise
    except (OSError, RuntimeError):
        raise SourceDocumentOutputError("Local output path is unsafe.") from None


def _require_git_ignored(artifact_path: Path) -> None:
    safe_artifact_path = _safe_output_path(artifact_path)
    worktree_root = _git_worktree_root(safe_artifact_path.parent)
    if worktree_root is None:
        return
    try:
        relative_path = safe_artifact_path.relative_to(worktree_root)
    except ValueError:
        raise SourceDocumentOutputError(
            "Local output Git protection could not be verified."
        ) from None
    if relative_path.parts and relative_path.parts[0] == ".git":
        raise SourceDocumentOutputError("Local output path is unsafe.")

    result = _run_git(
        worktree_root,
        "check-ignore",
        "--quiet",
        "--",
        relative_path.as_posix(),
    )
    if result.returncode == 0:
        return
    if result.returncode == 1:
        raise SourceDocumentOutputError("Local output artifact is not ignored by Git.")
    raise SourceDocumentOutputError(
        "Local output Git protection could not be verified."
    )


def _git_worktree_root(path: Path) -> Path | None:
    probe = path
    try:
        while not probe.exists():
            if probe.parent == probe:
                return None
            probe = probe.parent
        if not probe.is_dir():
            probe = probe.parent
    except OSError:
        raise SourceDocumentOutputError(
            "Local output Git protection could not be verified."
        ) from None

    marker_directory: Path | None = None
    for candidate in (probe, *probe.parents):
        try:
            os.lstat(candidate / ".git")
        except FileNotFoundError:
            continue
        except OSError:
            raise SourceDocumentOutputError(
                "Local output Git protection could not be verified."
            ) from None
        marker_directory = candidate
        break
    if marker_directory is None:
        return None

    result = _run_git(marker_directory, "rev-parse", "--show-toplevel")
    if result.returncode != 0 or not result.stdout.strip():
        raise SourceDocumentOutputError(
            "Local output Git protection could not be verified."
        )
    try:
        worktree_root = Path(result.stdout.strip()).resolve(strict=True)
        path.relative_to(worktree_root)
    except (OSError, RuntimeError, ValueError):
        raise SourceDocumentOutputError(
            "Local output Git protection could not be verified."
        ) from None
    return worktree_root


def _run_git(worktree_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    git_executable = which("git")
    if git_executable is None:
        raise SourceDocumentOutputError(
            "Local output Git protection could not be verified."
        )
    try:
        return subprocess.run(  # noqa: S603
            [git_executable, *arguments],
            cwd=worktree_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SourceDocumentOutputError(
            "Local output Git protection could not be verified."
        ) from None


def _extract_document(
    *,
    source_path: Path,
    manifest_entry: _ManifestEntry,
    ocr_adapter: PageOcrAdapter | None,
) -> _DocumentExtraction:
    try:
        with _open_source_read_only(source_path) as source:
            before = _fingerprint(source)
            extracted = _extract_open_document(
                source=source,
                manifest_entry=manifest_entry,
                fingerprint=before,
                ocr_adapter=ocr_adapter,
            )
            after = _fingerprint(source)
            if after != before:
                return _failed_document(
                    manifest_entry.name,
                    "document.source_changed",
                )
    except _SourceAccessFailure as error:
        return _failed_document(manifest_entry.name, error.code)
    return extracted


def _extract_open_document(
    *,
    source: BinaryIO,
    manifest_entry: _ManifestEntry,
    fingerprint: SourceDocumentFingerprint,
    ocr_adapter: PageOcrAdapter | None,
) -> _DocumentExtraction:
    integrity_failure = _initial_integrity_failure(
        actual=fingerprint,
        expected=manifest_entry.fingerprint,
    )
    if integrity_failure is not None:
        return _failed_document(manifest_entry.name, integrity_failure)

    try:
        source.seek(0)
        document = pdfium.PdfDocument(source, autoclose=False)
    except Exception:
        return _failed_document(
            manifest_entry.name,
            "document.pdf_unreadable",
            fingerprint=fingerprint,
        )

    try:
        with document:
            pdf_version = _pdf_version(document.get_version())
            page_count = len(document)
            pages = tuple(
                _extract_page(
                    document=document,
                    page_index=page_index,
                    ocr_adapter=ocr_adapter,
                )
                for page_index in range(page_count)
            )
    except Exception:
        return _failed_document(
            manifest_entry.name,
            "document.pdf_processing_failed",
            fingerprint=fingerprint,
        )

    return _DocumentExtraction(
        name=manifest_entry.name,
        status=_document_status(pages),
        fingerprint=fingerprint,
        pdf_version=pdf_version,
        page_count=page_count,
        pages=pages,
        failure_code=None,
    )


def _extract_page(
    *,
    document: Any,
    page_index: int,
    ocr_adapter: PageOcrAdapter | None,
) -> _PageExtraction:
    page_number = page_index + 1
    try:
        page = document[page_index]
    except Exception:
        empty_quality = _measure_text("")
        return _PageExtraction(
            page_number=page_number,
            method=PageExtractionMethod.NONE,
            status=PageExtractionStatus.FAILED,
            text=None,
            native_quality=empty_quality,
            quality=empty_quality,
            ocr_trigger_codes=("native.page_unavailable",),
            failure_code="page.unavailable",
        )

    try:
        try:
            text_page = page.get_textpage()
            try:
                native_text = _normalize_text(
                    text_page.get_text_range(errors="replace")
                )
            finally:
                text_page.close()
            native_failure = None
        except Exception:
            native_text = ""
            native_failure = "native.extraction_failed"

        native_quality = _measure_text(native_text)
        trigger_codes = (
            (native_failure,) if native_failure is not None else native_quality.signals
        )
        if not trigger_codes:
            return _PageExtraction(
                page_number=page_number,
                method=PageExtractionMethod.NATIVE,
                status=PageExtractionStatus.EXTRACTED,
                text=native_text,
                native_quality=native_quality,
                quality=native_quality,
                ocr_trigger_codes=(),
                failure_code=None,
            )

        has_native_fallback = native_quality.non_whitespace_character_count > 0
        if has_native_fallback and set(trigger_codes) <= _NATIVE_REVIEW_ONLY_SIGNALS:
            return _PageExtraction(
                page_number=page_number,
                method=PageExtractionMethod.NATIVE,
                status=PageExtractionStatus.SUSPECT,
                text=native_text,
                native_quality=native_quality,
                quality=native_quality,
                ocr_trigger_codes=trigger_codes,
                failure_code=None,
            )

        if ocr_adapter is None:
            if has_native_fallback:
                return _PageExtraction(
                    page_number=page_number,
                    method=PageExtractionMethod.NATIVE,
                    status=PageExtractionStatus.SUSPECT,
                    text=native_text,
                    native_quality=native_quality,
                    quality=native_quality,
                    ocr_trigger_codes=trigger_codes,
                    failure_code=None,
                )
            return _PageExtraction(
                page_number=page_number,
                method=PageExtractionMethod.NONE,
                status=PageExtractionStatus.OCR_REQUIRED,
                text=None,
                native_quality=native_quality,
                quality=native_quality,
                ocr_trigger_codes=trigger_codes,
                failure_code=None,
            )

        return _extract_page_with_ocr(
            page=page,
            page_number=page_number,
            native_text=native_text,
            native_quality=native_quality,
            trigger_codes=trigger_codes,
            ocr_adapter=ocr_adapter,
        )
    finally:
        page.close()


def _extract_page_with_ocr(
    *,
    page: Any,
    page_number: int,
    native_text: str,
    native_quality: _TextQuality,
    trigger_codes: tuple[str, ...],
    ocr_adapter: PageOcrAdapter,
) -> _PageExtraction:
    try:
        bitmap = page.render(scale=_OCR_RENDER_SCALE)
        try:
            raw_result = cast(object, ocr_adapter.extract(bitmap.to_numpy()))
        finally:
            bitmap.close()
    except Exception:
        return _ocr_failure_page(
            page_number=page_number,
            native_text=native_text,
            native_quality=native_quality,
            trigger_codes=trigger_codes,
            failure_code="page.ocr_failed",
        )

    if (
        not isinstance(raw_result, OcrPageResult)
        or not isinstance(cast(object, raw_result.text), str)
        or not _valid_confidences(raw_result.confidences)
    ):
        return _ocr_failure_page(
            page_number=page_number,
            native_text=native_text,
            native_quality=native_quality,
            trigger_codes=trigger_codes,
            failure_code="page.ocr_invalid_result",
        )

    ocr_text = _normalize_text(raw_result.text)
    quality = _measure_text(ocr_text, raw_result.confidences, is_ocr=True)
    if quality.non_whitespace_character_count == 0:
        return _ocr_failure_page(
            page_number=page_number,
            native_text=native_text,
            native_quality=native_quality,
            trigger_codes=trigger_codes,
            failure_code="page.ocr_empty",
        )

    status = (
        PageExtractionStatus.SUSPECT
        if quality.signals
        else PageExtractionStatus.EXTRACTED
    )
    return _PageExtraction(
        page_number=page_number,
        method=PageExtractionMethod.OCR,
        status=status,
        text=ocr_text,
        native_quality=native_quality,
        quality=quality,
        ocr_trigger_codes=trigger_codes,
        failure_code=None,
    )


def _ocr_failure_page(
    *,
    page_number: int,
    native_text: str,
    native_quality: _TextQuality,
    trigger_codes: tuple[str, ...],
    failure_code: str,
) -> _PageExtraction:
    if native_quality.non_whitespace_character_count > 0:
        return _PageExtraction(
            page_number=page_number,
            method=PageExtractionMethod.NATIVE,
            status=PageExtractionStatus.SUSPECT,
            text=native_text,
            native_quality=native_quality,
            quality=native_quality,
            ocr_trigger_codes=trigger_codes,
            failure_code=failure_code,
        )
    return _PageExtraction(
        page_number=page_number,
        method=PageExtractionMethod.OCR,
        status=PageExtractionStatus.FAILED,
        text=None,
        native_quality=native_quality,
        quality=_measure_text(""),
        ocr_trigger_codes=trigger_codes,
        failure_code=failure_code,
    )


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "".join(
        "\ufffd" if unicode_category(character) == "Cs" else character
        for character in normalized
    )
    return normalized.strip()


def _measure_text(
    text: str,
    confidences: Sequence[float] = (),
    *,
    is_ocr: bool = False,
) -> _TextQuality:
    non_whitespace = sum(not character.isspace() for character in text)
    alphanumeric = sum(character.isalnum() for character in text)
    replacement_characters = text.count("\ufffd")
    control_characters = sum(
        unicode_category(character) in {"Cc", "Cf"} and character not in {"\n", "\t"}
        for character in text
    )
    ratio = round(alphanumeric / non_whitespace, 6) if non_whitespace > 0 else None
    confidence_values = tuple(float(value) for value in confidences)
    mean_confidence = (
        round(sum(confidence_values) / len(confidence_values), 6)
        if confidence_values
        else None
    )
    minimum_confidence = round(min(confidence_values), 6) if confidence_values else None

    signals: list[str] = []
    if non_whitespace == 0:
        signals.append("text.empty")
    else:
        if alphanumeric < _MINIMUM_ALPHANUMERIC_CHARACTERS:
            signals.append("text.too_short")
        if ratio is not None and ratio < _MINIMUM_ALPHANUMERIC_RATIO:
            signals.append("text.low_alphanumeric_ratio")
    if replacement_characters:
        signals.append("text.replacement_characters")
    if control_characters:
        signals.append("text.control_characters")
    if is_ocr:
        if not confidences:
            signals.append("ocr.confidence_unavailable")
        else:
            if (
                mean_confidence is None
                or mean_confidence < _MINIMUM_MEAN_OCR_CONFIDENCE
            ):
                signals.append("ocr.low_mean_confidence")
            if (
                minimum_confidence is None
                or minimum_confidence < _MINIMUM_POINT_OCR_CONFIDENCE
            ):
                signals.append("ocr.low_minimum_confidence")

    return _TextQuality(
        character_count=len(text),
        non_whitespace_character_count=non_whitespace,
        alphanumeric_character_count=alphanumeric,
        word_count=len(re.findall(r"\w+", text, flags=re.UNICODE)),
        alphanumeric_ratio=ratio,
        replacement_character_count=replacement_characters,
        control_character_count=control_characters,
        mean_ocr_confidence=mean_confidence,
        minimum_ocr_confidence=minimum_confidence,
        signals=tuple(signals),
    )


def _valid_confidences(confidences: object) -> bool:
    if not isinstance(confidences, tuple):
        return False
    values = cast(tuple[object, ...], confidences)
    return all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(float(value))
        and 0 <= float(value) <= 1
        for value in values
    )


class _SourceAccessFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@contextmanager
def _open_source_read_only(source_path: Path) -> Generator[BinaryIO]:
    try:
        if source_path.is_symlink():
            raise _SourceAccessFailure("document.source_unsafe")
    except OSError:
        raise _SourceAccessFailure("document.source_access_failed") from None

    binary_flag = cast(int, getattr(os, "O_BINARY", 0))
    close_on_exec_flag = cast(int, getattr(os, "O_CLOEXEC", 0))
    flags = os.O_RDONLY | binary_flag | close_on_exec_flag
    try:
        descriptor = os.open(source_path, flags)
    except FileNotFoundError:
        raise _SourceAccessFailure("document.source_not_found") from None
    except PermissionError:
        raise _SourceAccessFailure("document.source_permission_denied") from None
    except OSError:
        raise _SourceAccessFailure("document.source_access_failed") from None

    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _SourceAccessFailure("document.source_unsafe")
        source = cast(BinaryIO, os.fdopen(descriptor, "rb", closefd=True))
    except _SourceAccessFailure:
        os.close(descriptor)
        raise
    except OSError:
        os.close(descriptor)
        raise _SourceAccessFailure("document.source_access_failed") from None

    try:
        yield source
    finally:
        source.close()


def _fingerprint(source: BinaryIO) -> SourceDocumentFingerprint:
    try:
        source.seek(0)
        digest = sha256()
        size_bytes = 0
        while chunk := source.read(_FINGERPRINT_CHUNK_SIZE):
            digest.update(chunk)
            size_bytes += len(chunk)
        source.seek(0)
    except PermissionError:
        raise _SourceAccessFailure("document.source_permission_denied") from None
    except (OSError, ValueError):
        raise _SourceAccessFailure("document.source_access_failed") from None
    return SourceDocumentFingerprint(size_bytes=size_bytes, sha256=digest.hexdigest())


def _initial_integrity_failure(
    *, actual: SourceDocumentFingerprint, expected: SourceDocumentFingerprint
) -> str | None:
    if actual.size_bytes != expected.size_bytes:
        return "document.source_size_mismatch"
    if actual.sha256 != expected.sha256:
        return "document.source_hash_mismatch"
    return None


def _pdf_version(raw_version: object) -> str | None:
    if not isinstance(raw_version, int) or isinstance(raw_version, bool):
        return None
    major, minor = divmod(raw_version, 10)
    if major < 1 or minor > 9:
        return None
    return f"{major}.{minor}"


def _document_status(
    pages: Sequence[_PageExtraction],
) -> DocumentExtractionStatus:
    if not pages or all(page.status is PageExtractionStatus.FAILED for page in pages):
        return DocumentExtractionStatus.FAILED
    if any(page.status is PageExtractionStatus.FAILED for page in pages):
        return DocumentExtractionStatus.PARTIAL
    if any(page.status is PageExtractionStatus.OCR_REQUIRED for page in pages):
        return DocumentExtractionStatus.OCR_REQUIRED
    if any(page.status is PageExtractionStatus.SUSPECT for page in pages):
        return DocumentExtractionStatus.ATTENTION_REQUIRED
    return DocumentExtractionStatus.COMPLETED


def _inventory_status(
    documents: Sequence[_DocumentExtraction],
) -> SourceDocumentInventoryStatus:
    if any(
        document.status
        in {DocumentExtractionStatus.FAILED, DocumentExtractionStatus.PARTIAL}
        for document in documents
    ):
        return SourceDocumentInventoryStatus.PARTIAL
    if any(
        document.status is DocumentExtractionStatus.OCR_REQUIRED
        for document in documents
    ):
        return SourceDocumentInventoryStatus.OCR_REQUIRED
    if any(
        document.status is DocumentExtractionStatus.ATTENTION_REQUIRED
        for document in documents
    ):
        return SourceDocumentInventoryStatus.ATTENTION_REQUIRED
    return SourceDocumentInventoryStatus.COMPLETED


def _failed_document(
    name: str,
    failure_code: str,
    *,
    fingerprint: SourceDocumentFingerprint | None = None,
) -> _DocumentExtraction:
    return _DocumentExtraction(
        name=name,
        status=DocumentExtractionStatus.FAILED,
        fingerprint=fingerprint,
        pdf_version=None,
        page_count=None,
        pages=(),
        failure_code=failure_code,
    )


def _document_artifact_path(
    *, output_directory: Path, document_name: str, source_sha256: str
) -> Path:
    document_directory = document_name.removesuffix(".pdf").lower()
    return output_directory / document_directory / source_sha256 / _EXTRACTION_FILENAME


def _document_payload(
    document: _DocumentExtraction,
    adapter_identity: tuple[str, str] | None,
) -> dict[str, object]:
    fingerprint = document.fingerprint
    if fingerprint is None:
        raise SourceDocumentOutputError("Document artifact identity is unavailable.")
    return {
        "schema_version": SOURCE_DOCUMENT_EXTRACTION_SCHEMA_VERSION,
        "extractor_version": SOURCE_DOCUMENT_EXTRACTOR_VERSION,
        "tooling": {
            "pypdfium2": package_version("pypdfium2"),
            "ocr_adapter": _adapter_payload(adapter_identity),
        },
        "source": {
            "name": document.name,
            "source_version": f"sha256:{fingerprint.sha256}",
            "size_bytes": fingerprint.size_bytes,
            "sha256": fingerprint.sha256,
            "pdf_version": document.pdf_version,
        },
        "status": document.status.value,
        "failure_code": document.failure_code,
        "page_count": document.page_count,
        "pages": [_page_payload(page) for page in document.pages],
    }


def _page_payload(page: _PageExtraction) -> dict[str, object]:
    return {
        "page_number": page.page_number,
        "method": page.method.value,
        "status": page.status.value,
        "text": page.text,
        "native_quality": _quality_payload(page.native_quality),
        "quality": _quality_payload(page.quality),
        "ocr_trigger_codes": list(page.ocr_trigger_codes),
        "failure_code": page.failure_code,
    }


def _quality_payload(quality: _TextQuality) -> dict[str, object]:
    return {
        "character_count": quality.character_count,
        "non_whitespace_character_count": quality.non_whitespace_character_count,
        "alphanumeric_character_count": quality.alphanumeric_character_count,
        "word_count": quality.word_count,
        "alphanumeric_ratio": quality.alphanumeric_ratio,
        "replacement_character_count": quality.replacement_character_count,
        "control_character_count": quality.control_character_count,
        "mean_ocr_confidence": quality.mean_ocr_confidence,
        "minimum_ocr_confidence": quality.minimum_ocr_confidence,
        "signals": list(quality.signals),
    }


def _inventory_payload(
    *,
    status: SourceDocumentInventoryStatus,
    documents: Sequence[_DocumentExtraction],
    output_directory: Path,
    adapter_identity: tuple[str, str] | None,
) -> dict[str, object]:
    summaries = [
        _inventory_document_payload(document, output_directory)
        for document in documents
    ]
    doc1_matches = [summary for summary in summaries if summary["name"] == "Doc1.pdf"]
    if len(doc1_matches) != 1:
        raise SourceDocumentOutputError("Doc1 assessment is unavailable.")
    return {
        "schema_version": SOURCE_DOCUMENT_INVENTORY_SCHEMA_VERSION,
        "extractor_version": SOURCE_DOCUMENT_EXTRACTOR_VERSION,
        "expected_documents": list(SOURCE_DOCUMENT_NAMES),
        "document_count": len(documents),
        "status": status.value,
        "ocr_adapter": _adapter_payload(adapter_identity),
        "documents": summaries,
        "doc1_assessment": dict(doc1_matches[0]),
    }


def _inventory_document_payload(
    document: _DocumentExtraction, output_directory: Path
) -> dict[str, object]:
    fingerprint = document.fingerprint
    counts = _page_counts(document.pages)
    artifact = (
        document.artifact_path.relative_to(output_directory).as_posix()
        if document.artifact_path is not None
        else None
    )
    return {
        "name": document.name,
        "source_version": (
            f"sha256:{fingerprint.sha256}" if fingerprint is not None else None
        ),
        "size_bytes": fingerprint.size_bytes if fingerprint is not None else None,
        "sha256": fingerprint.sha256 if fingerprint is not None else None,
        "pdf_version": document.pdf_version,
        "page_count": document.page_count,
        "status": document.status.value,
        **counts,
        "failure_code": document.failure_code,
        "artifact": artifact,
    }


def _page_counts(pages: Sequence[_PageExtraction]) -> dict[str, int]:
    return {
        "native_page_count": sum(
            page.method is PageExtractionMethod.NATIVE for page in pages
        ),
        "ocr_page_count": sum(
            page.method is PageExtractionMethod.OCR for page in pages
        ),
        "ocr_required_page_count": sum(
            page.status is PageExtractionStatus.OCR_REQUIRED for page in pages
        ),
        "suspect_page_count": sum(
            page.status is PageExtractionStatus.SUSPECT for page in pages
        ),
        "failed_page_count": sum(
            page.status is PageExtractionStatus.FAILED for page in pages
        ),
    }


def _adapter_payload(
    adapter_identity: tuple[str, str] | None,
) -> dict[str, object]:
    if adapter_identity is None:
        return {"configured": False, "name": None, "version": None}
    return {
        "configured": True,
        "name": adapter_identity[0],
        "version": adapter_identity[1],
    }


def _replace_artifact_path(
    document: _DocumentExtraction, artifact_path: Path
) -> _DocumentExtraction:
    return _DocumentExtraction(
        name=document.name,
        status=document.status,
        fingerprint=document.fingerprint,
        pdf_version=document.pdf_version,
        page_count=document.page_count,
        pages=document.pages,
        failure_code=document.failure_code,
        artifact_path=artifact_path,
    )


def _summary(document: _DocumentExtraction) -> SourceDocumentSummary:
    counts = _page_counts(document.pages)
    return SourceDocumentSummary(
        name=document.name,
        status=document.status,
        page_count=document.page_count,
        native_page_count=counts["native_page_count"],
        ocr_page_count=counts["ocr_page_count"],
        ocr_required_page_count=counts["ocr_required_page_count"],
        suspect_page_count=counts["suspect_page_count"],
        failed_page_count=counts["failed_page_count"],
        failure_code=document.failure_code,
        artifact_path=document.artifact_path,
    )


def _write_json_if_changed(path: Path, payload: Mapping[str, object]) -> None:
    _require_git_ignored(path)
    try:
        content = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise SourceDocumentOutputError("Local document artifact is invalid.") from None

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise SourceDocumentOutputError("Local document artifact path is unsafe.")
        if path.exists() and path.read_bytes() == content:
            return
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".source-documents-",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
    except SourceDocumentOutputError:
        raise
    except OSError:
        raise SourceDocumentOutputError(
            "Local document artifact could not be written safely."
        ) from None
