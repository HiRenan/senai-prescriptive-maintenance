"""Entirely synthetic tests for authorized source-document extraction."""

from __future__ import annotations

import inspect
import json
import os
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from shutil import which
from types import SimpleNamespace
from typing import BinaryIO, NoReturn, cast

import pytest
from prescriptive_maintenance.data import (
    SOURCE_DOCUMENT_NAMES,
    DocumentExtractionStatus,
    OcrPageResult,
    SourceDocumentError,
    SourceDocumentFingerprint,
    SourceDocumentInventoryStatus,
    SourceDocumentManifestError,
    SourceDocumentOutputError,
    extract_source_documents,
)

_SYNTHETIC_NATIVE_TEXT = (
    "Entirely synthetic maintenance guidance for a fictional training asset."
)


@dataclass(slots=True)
class _SyntheticOcrAdapter:
    fail_on_calls: frozenset[int] = frozenset()
    confidences: tuple[float, ...] = (0.95, 0.98)
    calls: int = 0
    name: str = "synthetic-local-ocr"
    version: str = "1.0"

    def extract(self, image: object) -> OcrPageResult:
        self.calls += 1
        assert len(cast(Sequence[object], image)) > 0
        if self.calls in self.fail_on_calls:
            raise RuntimeError("synthetic OCR failure with private detail")
        return OcrPageResult(
            text="Entirely synthetic OCR text for a fictional scanned page.",
            confidences=self.confidences,
        )


@dataclass(slots=True)
class _MalformedOcrAdapter:
    name: str = "synthetic-malformed-ocr"
    version: str = "1.0"

    def extract(self, image: object) -> OcrPageResult:
        assert len(cast(Sequence[object], image)) > 0
        return OcrPageResult(text=cast(str, 42), confidences=(0.99,))


class _UnreadableFileAttributes:
    def __init__(self, st_mode: int) -> None:
        self.st_mode = st_mode

    @property
    def st_file_attributes(self) -> int:
        raise OSError


def _escape_pdf_text(text: str) -> bytes:
    return (text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")).encode(
        "ascii"
    )


def _synthetic_pdf_bytes(page_texts: Sequence[str | None]) -> bytes:
    page_count = len(page_texts)
    font_object_number = 3 + page_count * 2
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(page_count))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode(),
    ]
    for index, text in enumerate(page_texts):
        page_object_number = 3 + index * 2
        content_object_number = page_object_number + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_object_number} 0 R >> >> "
                f"/Contents {content_object_number} 0 R >>"
            ).encode()
        )
        content = (
            b""
            if text is None
            else b"BT /F1 12 Tf 72 720 Td (" + _escape_pdf_text(text) + b") Tj ET"
        )
        objects.append(
            f"<< /Length {len(content)} >>\nstream\n".encode()
            + content
            + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_number, content in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{object_number} 0 obj\n".encode())
        payload.extend(content)
        payload.extend(b"\nendobj\n")

    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(payload)


def _prepare_synthetic_sources(
    tmp_path: Path,
    *,
    doc1_pages: Sequence[str | None] = (_SYNTHETIC_NATIVE_TEXT,),
) -> tuple[Path, Path, Path]:
    source_directory = tmp_path / "synthetic-sources"
    output_directory = tmp_path / "local-derived"
    source_directory.mkdir()
    manifest_files: list[dict[str, object]] = []
    for name in SOURCE_DOCUMENT_NAMES:
        page_texts = doc1_pages if name == "Doc1.pdf" else (_SYNTHETIC_NATIVE_TEXT,)
        content = _synthetic_pdf_bytes(page_texts)
        (source_directory / name).write_bytes(content)
        manifest_files.append(
            {
                "name": name,
                "size_bytes": len(content),
                "sha256": sha256(content).hexdigest(),
            }
        )
    manifest_path = tmp_path / "source-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "hash_algorithm": "sha256",
                "files": manifest_files,
            }
        ),
        encoding="utf-8",
    )
    return source_directory, manifest_path, output_directory


def _load_json(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def _initialize_synthetic_git_worktree(tmp_path: Path) -> Path:
    worktree = tmp_path / "synthetic-git-worktree"
    worktree.mkdir()
    git_executable = which("git")
    assert git_executable is not None
    subprocess.run(  # noqa: S603
        [git_executable, "init", "--quiet", str(worktree)],
        check=True,
        capture_output=True,
    )
    (worktree / ".gitignore").write_text("/data/processed/\n", encoding="utf-8")
    return worktree


def _replace_manifest_fingerprint(
    manifest_path: Path, *, name: str, source_path: Path
) -> None:
    payload = _load_json(manifest_path)
    entries = cast(list[dict[str, object]], payload["files"])
    entry = next(candidate for candidate in entries if candidate["name"] == name)
    content = source_path.read_bytes()
    entry["size_bytes"] = len(content)
    entry["sha256"] = sha256(content).hexdigest()
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")


def _stream_fingerprint(source: BinaryIO) -> SourceDocumentFingerprint:
    source.seek(0)
    content = source.read()
    source.seek(0)
    return SourceDocumentFingerprint(
        size_bytes=len(content),
        sha256=sha256(content).hexdigest(),
    )


def _changed_fingerprint(
    fingerprint: SourceDocumentFingerprint,
) -> SourceDocumentFingerprint:
    replacement = "0" if fingerprint.sha256[0] != "0" else "1"
    return SourceDocumentFingerprint(
        size_bytes=fingerprint.size_bytes,
        sha256=replacement + fingerprint.sha256[1:],
    )


def _change_on_second_fingerprint() -> tuple[
    Callable[[BinaryIO], SourceDocumentFingerprint], list[BinaryIO]
]:
    observed_target_calls: list[BinaryIO] = []
    target: BinaryIO | None = None

    def fingerprint(source: BinaryIO) -> SourceDocumentFingerprint:
        nonlocal target
        measured = _stream_fingerprint(source)
        if target is None:
            target = source
        if source is target:
            observed_target_calls.append(source)
            if len(observed_target_calls) == 2:
                return _changed_fingerprint(measured)
        return measured

    return fingerprint, observed_target_calls


def _document_entry(inventory: Mapping[str, object], name: str) -> dict[str, object]:
    documents = cast(list[dict[str, object]], inventory["documents"])
    return next(document for document in documents if document["name"] == name)


def test_paths_are_explicit_required_keywords() -> None:
    parameters = inspect.signature(extract_source_documents).parameters

    for name in ("source_directory", "manifest_path", "output_directory"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty


def test_allows_ignored_data_processed_output_inside_git_worktree(
    tmp_path: Path,
) -> None:
    source_directory, manifest_path, _ = _prepare_synthetic_sources(tmp_path)
    worktree = _initialize_synthetic_git_worktree(tmp_path)
    output_directory = worktree / "data" / "processed" / "documents"

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
    )

    assert result.status is SourceDocumentInventoryStatus.COMPLETED
    assert result.inventory_path == output_directory.resolve() / "inventory.v1.json"


def test_rejects_unignored_apps_api_docs_output_inside_git_worktree(
    tmp_path: Path,
) -> None:
    source_directory, manifest_path, _ = _prepare_synthetic_sources(tmp_path)
    worktree = _initialize_synthetic_git_worktree(tmp_path)
    output_directory = worktree / "apps" / "api" / "docs"

    with pytest.raises(SourceDocumentOutputError) as raised:
        extract_source_documents(
            source_directory=source_directory,
            manifest_path=manifest_path,
            output_directory=output_directory,
        )

    assert str(raised.value) == "Local output artifact is not ignored by Git."
    assert not output_directory.exists()


def test_fails_closed_when_git_ignore_verification_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_directory, manifest_path, _ = _prepare_synthetic_sources(tmp_path)
    worktree = _initialize_synthetic_git_worktree(tmp_path)
    output_directory = worktree / "data" / "processed" / "documents"

    def fail_git(*args: object, **kwargs: object) -> NoReturn:
        raise OSError

    monkeypatch.setattr(
        "prescriptive_maintenance.data.source_documents.subprocess.run",
        fail_git,
    )

    with pytest.raises(SourceDocumentOutputError) as raised:
        extract_source_documents(
            source_directory=source_directory,
            manifest_path=manifest_path,
            output_directory=output_directory,
        )

    assert str(raised.value) == ("Local output Git protection could not be verified.")
    assert not output_directory.exists()


def test_rejects_symlinked_output_before_writing(tmp_path: Path) -> None:
    source_directory, manifest_path, _ = _prepare_synthetic_sources(tmp_path)
    worktree = _initialize_synthetic_git_worktree(tmp_path)
    escaped_directory = tmp_path / "synthetic-escaped-output"
    escaped_directory.mkdir()
    (worktree / "data").mkdir()
    try:
        (worktree / "data" / "processed").symlink_to(
            escaped_directory,
            target_is_directory=True,
        )
    except OSError:
        pytest.skip("Directory symlinks are unavailable in this environment.")

    with pytest.raises(SourceDocumentOutputError) as raised:
        extract_source_documents(
            source_directory=source_directory,
            manifest_path=manifest_path,
            output_directory=worktree / "data" / "processed" / "documents",
        )

    assert str(raised.value) == "Local output path is unsafe."
    assert list(escaped_directory.iterdir()) == []


def test_rejects_synthetic_reparse_component_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_directory, manifest_path, _ = _prepare_synthetic_sources(tmp_path)
    worktree = _initialize_synthetic_git_worktree(tmp_path)
    reparse_component = worktree / "data" / "processed"
    reparse_component.mkdir(parents=True)
    output_directory = reparse_component / "documents"
    original_lstat = Path.lstat

    def lstat_with_reparse_attribute(path: Path) -> os.stat_result:
        metadata = original_lstat(path)
        if path != reparse_component:
            return metadata
        return cast(
            os.stat_result,
            SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
            ),
        )

    monkeypatch.setattr(Path, "lstat", lstat_with_reparse_attribute)

    with pytest.raises(SourceDocumentOutputError) as raised:
        extract_source_documents(
            source_directory=source_directory,
            manifest_path=manifest_path,
            output_directory=output_directory,
        )

    assert str(raised.value) == "Local output path is unsafe."
    assert not output_directory.exists()


@pytest.mark.parametrize(
    "metadata_case",
    ("missing", "bool", "invalid_type", "read_error"),
)
def test_windows_rejects_invalid_file_attributes_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata_case: str,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path
    )
    inspected_component = output_directory.parent
    original_lstat = Path.lstat

    def lstat_with_invalid_file_attributes(path: Path) -> os.stat_result:
        metadata = original_lstat(path)
        if path != inspected_component:
            return metadata
        if metadata_case == "missing":
            synthetic_metadata: object = SimpleNamespace(st_mode=metadata.st_mode)
        elif metadata_case == "bool":
            synthetic_metadata = SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=False,
            )
        elif metadata_case == "invalid_type":
            synthetic_metadata = SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes="1024",
            )
        else:
            synthetic_metadata = _UnreadableFileAttributes(metadata.st_mode)
        return cast(os.stat_result, synthetic_metadata)

    monkeypatch.setattr(Path, "lstat", lstat_with_invalid_file_attributes)
    monkeypatch.setattr(
        "prescriptive_maintenance.data.source_documents._is_windows_platform",
        lambda: True,
    )

    with pytest.raises(SourceDocumentOutputError) as raised:
        extract_source_documents(
            source_directory=source_directory,
            manifest_path=manifest_path,
            output_directory=output_directory,
        )

    assert str(raised.value) == "Local output path is unsafe."
    assert not output_directory.exists()


def test_non_windows_allows_missing_file_attributes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path
    )
    inspected_component = output_directory.parent
    original_lstat = Path.lstat

    def lstat_without_file_attributes(path: Path) -> os.stat_result:
        metadata = original_lstat(path)
        if path != inspected_component:
            return metadata
        return cast(os.stat_result, SimpleNamespace(st_mode=metadata.st_mode))

    monkeypatch.setattr(Path, "lstat", lstat_without_file_attributes)
    monkeypatch.setattr(
        "prescriptive_maintenance.data.source_documents._is_windows_platform",
        lambda: False,
    )

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
    )

    assert result.status is SourceDocumentInventoryStatus.COMPLETED
    assert result.inventory_path.exists()


def test_fails_closed_when_output_component_inspection_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_directory, manifest_path, _ = _prepare_synthetic_sources(tmp_path)
    worktree = _initialize_synthetic_git_worktree(tmp_path)
    inspected_component = worktree / "data" / "processed"
    inspected_component.mkdir(parents=True)
    output_directory = inspected_component / "documents"
    original_lstat = Path.lstat

    def denied_lstat(path: Path) -> os.stat_result:
        if path == inspected_component:
            raise PermissionError
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", denied_lstat)

    with pytest.raises(SourceDocumentOutputError) as raised:
        extract_source_documents(
            source_directory=source_directory,
            manifest_path=manifest_path,
            output_directory=output_directory,
        )

    assert str(raised.value) == "Local output path is unsafe."
    assert not output_directory.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions require Windows.")
def test_rejects_real_windows_junction_without_writing_target(tmp_path: Path) -> None:
    source_directory, manifest_path, _ = _prepare_synthetic_sources(tmp_path)
    worktree = _initialize_synthetic_git_worktree(tmp_path)
    escaped_directory = tmp_path / "synthetic-junction-target"
    escaped_directory.mkdir()
    (worktree / "data").mkdir()
    junction = worktree / "data" / "processed"
    cmd_executable = which("cmd.exe")
    assert cmd_executable is not None
    creation = subprocess.run(  # noqa: S603
        [
            cmd_executable,
            "/d",
            "/c",
            "mklink",
            "/J",
            str(junction),
            str(escaped_directory),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if creation.returncode != 0 or not junction.is_junction():
        if junction.exists() or junction.is_junction():
            junction.rmdir()
        pytest.skip("Directory junctions are unavailable in this environment.")

    try:
        git_executable = which("git")
        assert git_executable is not None
        ignored = subprocess.run(  # noqa: S603
            [
                git_executable,
                "-C",
                str(worktree),
                "check-ignore",
                "--quiet",
                "--",
                "data/processed/documents/inventory.v1.json",
            ],
            check=False,
            capture_output=True,
            timeout=10,
        )
        assert ignored.returncode == 0

        with pytest.raises(SourceDocumentOutputError) as raised:
            extract_source_documents(
                source_directory=source_directory,
                manifest_path=manifest_path,
                output_directory=junction / "documents",
            )

        assert str(raised.value) == "Local output path is unsafe."
        assert list(escaped_directory.iterdir()) == []
    finally:
        junction.rmdir()


def test_extracts_six_native_documents_without_modifying_sources(
    tmp_path: Path,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path
    )
    before = {
        path.name: (path.stat().st_mtime_ns, sha256(path.read_bytes()).hexdigest())
        for path in source_directory.iterdir()
    }

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
    )

    assert result.status is SourceDocumentInventoryStatus.COMPLETED
    assert len(result.documents) == 6
    assert all(
        document.status is DocumentExtractionStatus.COMPLETED
        and document.native_page_count == 1
        and document.ocr_page_count == 0
        for document in result.documents
    )
    after = {
        path.name: (path.stat().st_mtime_ns, sha256(path.read_bytes()).hexdigest())
        for path in source_directory.iterdir()
    }
    assert after == before

    inventory = _load_json(result.inventory_path)
    assert inventory["expected_documents"] == list(SOURCE_DOCUMENT_NAMES)
    assert inventory["document_count"] == 6
    assert len(cast(list[object], inventory["documents"])) == 6
    first_artifact = cast(Path, result.documents[0].artifact_path)
    first_payload = _load_json(first_artifact)
    assert first_payload["extractor_version"] == 2
    page = cast(list[dict[str, object]], first_payload["pages"])[0]
    assert page["method"] == "native"
    assert page["status"] == "extracted"
    assert page["text"] == _SYNTHETIC_NATIVE_TEXT
    assert cast(dict[str, object], page["quality"])["signals"] == []


def test_marks_pages_for_ocr_and_assesses_doc1_without_an_adapter(
    tmp_path: Path,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path,
        doc1_pages=(None, None),
    )

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
    )

    assert result.status is SourceDocumentInventoryStatus.OCR_REQUIRED
    doc1 = result.documents[0]
    assert doc1.status is DocumentExtractionStatus.OCR_REQUIRED
    assert doc1.page_count == 2
    assert doc1.ocr_required_page_count == 2
    artifact = _load_json(cast(Path, doc1.artifact_path))
    pages = cast(list[dict[str, object]], artifact["pages"])
    assert [page["status"] for page in pages] == ["ocr_required", "ocr_required"]
    assert all(page["text"] is None for page in pages)
    assert all(page["ocr_trigger_codes"] == ["text.empty"] for page in pages)

    inventory = _load_json(result.inventory_path)
    assert inventory["doc1_assessment"] == _document_entry(inventory, "Doc1.pdf")


def test_preserves_short_and_low_ratio_native_text_as_suspect(
    tmp_path: Path,
) -> None:
    short_text = "A"
    low_ratio_text = "::::::::::::A::::::::::::"
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path,
        doc1_pages=(short_text, low_ratio_text),
    )
    adapter = _SyntheticOcrAdapter()

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
        ocr_adapter=adapter,
    )

    assert result.status is SourceDocumentInventoryStatus.ATTENTION_REQUIRED
    assert result.documents[0].ocr_required_page_count == 0
    assert result.documents[0].suspect_page_count == 2
    assert adapter.calls == 0
    artifact = _load_json(cast(Path, result.documents[0].artifact_path))
    pages = cast(list[dict[str, object]], artifact["pages"])
    assert [page["method"] for page in pages] == ["native", "native"]
    assert [page["status"] for page in pages] == ["suspect", "suspect"]
    assert [page["text"] for page in pages] == [short_text, low_ratio_text]


def test_uses_injected_ocr_only_for_pages_with_insufficient_native_text(
    tmp_path: Path,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path,
        doc1_pages=(None,),
    )
    adapter = _SyntheticOcrAdapter()

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
        ocr_adapter=adapter,
    )

    assert result.status is SourceDocumentInventoryStatus.COMPLETED
    assert adapter.calls == 1
    assert result.documents[0].ocr_page_count == 1
    assert all(document.native_page_count == 1 for document in result.documents[1:])
    artifact = _load_json(cast(Path, result.documents[0].artifact_path))
    page = cast(list[dict[str, object]], artifact["pages"])[0]
    assert page["method"] == "ocr"
    assert page["status"] == "extracted"
    assert page["ocr_trigger_codes"] == ["text.empty"]
    assert cast(dict[str, object], page["quality"])["mean_ocr_confidence"] == 0.965


def test_partial_ocr_failure_is_visible_and_reprocessable_idempotently(
    tmp_path: Path,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path,
        doc1_pages=(None, None),
    )

    failed_run = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
        ocr_adapter=_SyntheticOcrAdapter(fail_on_calls=frozenset({1})),
    )

    assert failed_run.status is SourceDocumentInventoryStatus.PARTIAL
    assert failed_run.documents[0].status is DocumentExtractionStatus.PARTIAL
    assert failed_run.documents[0].failed_page_count == 1
    assert failed_run.documents[0].ocr_page_count == 2
    stable_artifact = cast(Path, failed_run.documents[1].artifact_path)
    stable_bytes = stable_artifact.read_bytes()
    stable_mtime = stable_artifact.stat().st_mtime_ns

    successful_run = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
        ocr_adapter=_SyntheticOcrAdapter(),
    )

    assert successful_run.status is SourceDocumentInventoryStatus.COMPLETED
    assert successful_run.documents[0].status is DocumentExtractionStatus.COMPLETED
    assert stable_artifact.read_bytes() == stable_bytes
    assert stable_artifact.stat().st_mtime_ns == stable_mtime

    inventory_bytes = successful_run.inventory_path.read_bytes()
    inventory_mtime = successful_run.inventory_path.stat().st_mtime_ns
    repeated_run = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
        ocr_adapter=_SyntheticOcrAdapter(),
    )
    assert repeated_run.inventory_path.read_bytes() == inventory_bytes
    assert repeated_run.inventory_path.stat().st_mtime_ns == inventory_mtime


def test_failed_ocr_preserves_available_suspect_native_fallback(
    tmp_path: Path,
) -> None:
    native_fallback = "Entirely synthetic native fallback\x01"
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path,
        doc1_pages=(native_fallback,),
    )
    adapter = _SyntheticOcrAdapter(fail_on_calls=frozenset({1}))

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
        ocr_adapter=adapter,
    )

    assert result.status is SourceDocumentInventoryStatus.ATTENTION_REQUIRED
    assert adapter.calls == 1
    assert result.documents[0].native_page_count == 1
    assert result.documents[0].suspect_page_count == 1
    assert result.documents[0].failed_page_count == 0
    artifact = _load_json(cast(Path, result.documents[0].artifact_path))
    page = cast(list[dict[str, object]], artifact["pages"])[0]
    assert page["method"] == "native"
    assert page["status"] == "suspect"
    assert page["text"] == native_fallback
    assert page["failure_code"] == "page.ocr_failed"


def test_low_ocr_confidence_marks_page_and_inventory_as_suspect(
    tmp_path: Path,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path,
        doc1_pages=(None,),
    )

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
        ocr_adapter=_SyntheticOcrAdapter(confidences=(0.4, 0.5)),
    )

    assert result.status is SourceDocumentInventoryStatus.ATTENTION_REQUIRED
    assert result.documents[0].status is DocumentExtractionStatus.ATTENTION_REQUIRED
    assert result.documents[0].suspect_page_count == 1
    artifact = _load_json(cast(Path, result.documents[0].artifact_path))
    page = cast(list[dict[str, object]], artifact["pages"])[0]
    assert page["status"] == "suspect"
    assert cast(dict[str, object], page["quality"])["signals"] == [
        "ocr.low_mean_confidence",
        "ocr.low_minimum_confidence",
    ]


def test_low_minimum_ocr_confidence_is_suspect_even_when_mean_passes(
    tmp_path: Path,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path,
        doc1_pages=(None,),
    )

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
        ocr_adapter=_SyntheticOcrAdapter(confidences=(1, 1, 1, 1, 0)),
    )

    assert result.status is SourceDocumentInventoryStatus.ATTENTION_REQUIRED
    assert result.documents[0].status is DocumentExtractionStatus.ATTENTION_REQUIRED
    artifact = _load_json(cast(Path, result.documents[0].artifact_path))
    page = cast(list[dict[str, object]], artifact["pages"])[0]
    quality = cast(dict[str, object], page["quality"])
    assert page["status"] == "suspect"
    assert quality["mean_ocr_confidence"] == 0.8
    assert quality["minimum_ocr_confidence"] == 0.0
    assert quality["signals"] == ["ocr.low_minimum_confidence"]


def test_malformed_ocr_text_is_a_sanitized_page_failure(tmp_path: Path) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path,
        doc1_pages=(None,),
    )

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
        ocr_adapter=_MalformedOcrAdapter(),
    )

    assert result.status is SourceDocumentInventoryStatus.PARTIAL
    assert result.documents[0].status is DocumentExtractionStatus.FAILED
    assert result.documents[0].failed_page_count == 1
    assert all(
        document.status is DocumentExtractionStatus.COMPLETED
        for document in result.documents[1:]
    )
    artifact = _load_json(cast(Path, result.documents[0].artifact_path))
    page = cast(list[dict[str, object]], artifact["pages"])[0]
    assert page["method"] == "ocr"
    assert page["status"] == "failed"
    assert page["text"] is None
    assert page["failure_code"] == "page.ocr_invalid_result"


def test_non_textual_adapter_identity_is_a_sanitized_source_error(
    tmp_path: Path,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path
    )
    adapter = _SyntheticOcrAdapter()
    adapter.name = cast(str, 42)

    with pytest.raises(SourceDocumentError) as raised:
        extract_source_documents(
            source_directory=source_directory,
            manifest_path=manifest_path,
            output_directory=output_directory,
            ocr_adapter=adapter,
        )

    assert str(raised.value) == "OCR adapter identity is invalid."
    assert not output_directory.exists()


def test_missing_document_does_not_hide_other_document_results(tmp_path: Path) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path
    )
    (source_directory / "Doc6.pdf").unlink()

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
    )

    assert result.status is SourceDocumentInventoryStatus.PARTIAL
    assert [document.status for document in result.documents[:5]] == [
        DocumentExtractionStatus.COMPLETED
    ] * 5
    missing = result.documents[5]
    assert missing.status is DocumentExtractionStatus.FAILED
    assert missing.failure_code == "document.source_not_found"
    assert missing.artifact_path is None
    inventory = _load_json(result.inventory_path)
    assert _document_entry(inventory, "Doc6.pdf")["failure_code"] == (
        "document.source_not_found"
    )


def test_hash_mismatch_is_sanitized_and_does_not_extract_the_source(
    tmp_path: Path,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path
    )
    changed_path = source_directory / "Doc6.pdf"
    changed_content = bytearray(changed_path.read_bytes())
    changed_content[20] = ord("X")
    changed_path.write_bytes(changed_content)

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
    )

    changed = result.documents[5]
    assert changed.status is DocumentExtractionStatus.FAILED
    assert changed.failure_code == "document.source_hash_mismatch"
    assert changed.artifact_path is None
    assert str(source_directory) not in cast(str, changed.failure_code)


def test_source_changed_overrides_initial_integrity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path
    )
    changed_path = source_directory / "Doc1.pdf"
    changed_content = bytearray(changed_path.read_bytes())
    changed_content[20] = ord("X")
    changed_path.write_bytes(changed_content)
    fingerprint, observed_calls = _change_on_second_fingerprint()
    monkeypatch.setattr(
        "prescriptive_maintenance.data.source_documents._fingerprint",
        fingerprint,
    )

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
    )

    assert result.documents[0].failure_code == "document.source_changed"
    assert len(observed_calls) == 2
    assert observed_calls[0] is observed_calls[1]


def test_source_changed_overrides_unreadable_pdf_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path
    )
    unreadable_path = source_directory / "Doc1.pdf"
    unreadable_path.write_bytes(b"entirely synthetic invalid PDF")
    _replace_manifest_fingerprint(
        manifest_path,
        name="Doc1.pdf",
        source_path=unreadable_path,
    )
    fingerprint, observed_calls = _change_on_second_fingerprint()
    monkeypatch.setattr(
        "prescriptive_maintenance.data.source_documents._fingerprint",
        fingerprint,
    )

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
    )

    assert result.documents[0].failure_code == "document.source_changed"
    assert len(observed_calls) == 2
    assert observed_calls[0] is observed_calls[1]


def test_source_changed_overrides_pdf_processing_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_directory, manifest_path, output_directory = _prepare_synthetic_sources(
        tmp_path
    )
    fingerprint, observed_calls = _change_on_second_fingerprint()

    def fail_page_processing(**kwargs: object) -> NoReturn:
        raise RuntimeError("synthetic processing failure")

    monkeypatch.setattr(
        "prescriptive_maintenance.data.source_documents._fingerprint",
        fingerprint,
    )
    monkeypatch.setattr(
        "prescriptive_maintenance.data.source_documents._extract_page",
        fail_page_processing,
    )

    result = extract_source_documents(
        source_directory=source_directory,
        manifest_path=manifest_path,
        output_directory=output_directory,
    )

    assert result.documents[0].failure_code == "document.source_changed"
    assert len(observed_calls) == 2
    assert observed_calls[0] is observed_calls[1]


def test_rejects_manifest_missing_an_authorized_document(tmp_path: Path) -> None:
    _, manifest_path, output_directory = _prepare_synthetic_sources(tmp_path)
    payload = _load_json(manifest_path)
    payload["files"] = cast(list[object], payload["files"])[:-1]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceDocumentManifestError) as raised:
        extract_source_documents(
            source_directory=tmp_path / "private-source-location",
            manifest_path=manifest_path,
            output_directory=output_directory,
        )

    assert str(tmp_path) not in str(raised.value)
