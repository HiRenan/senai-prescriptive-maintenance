"""Deterministic, page-traceable chunking and offline embedding boundaries."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256, shake_256
from math import isfinite, sqrt
from typing import Final, Protocol, cast
from unicodedata import category as unicode_category

from prescriptive_maintenance.data.source_documents import (
    SOURCE_DOCUMENT_EXTRACTION_SCHEMA_VERSION,
    DocumentExtractionStatus,
    PageExtractionMethod,
    PageExtractionStatus,
)

DOCUMENT_CHUNK_SCHEMA_VERSION: Final = 1
DOCUMENT_CHUNKING_CONFIGURATION_VERSION: Final = "document-chunking.v1"
DOCUMENT_REPRESENTATION_VERSION: Final = "fake-local-hash-embedding.v1"
DEFAULT_CHUNK_MAX_CHARACTERS: Final = 1_600
DEFAULT_CHUNK_OVERLAP_CHARACTERS: Final = 200
DEFAULT_EMBEDDING_DIMENSION: Final = 32

_DOCUMENT_ID_PREFIX: Final = "doc_"
_DOCUMENT_VERSION_PREFIX: Final = "docver_"
_CHUNK_ID_PREFIX: Final = "chunk_"
_SECTION_ID_PREFIX: Final = "section_"
_HASH_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_CHUNK_ID_PATTERN: Final = re.compile(r"chunk_[a-z0-9_]{3,64}")
_IDENTITY_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_FAILURE_CODE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_.]{0,79}")
_MARKDOWN_HEADING: Final = re.compile(r"^#{1,6}[ \t]+(?P<title>\S.*)$")
_REMOVED_TECHNICAL_NOISE: Final = frozenset({"\x00", "\x08", "\x7f"})
_LINE_BREAK_TECHNICAL_NOISE: Final = frozenset({"\x0b", "\x0c"})
_ZERO_WIDTH_JOINER: Final = "\u200d"


class DocumentChunkingError(Exception):
    """Sanitized failure raised for an invalid structured extraction payload."""


class ChunkRepositoryError(Exception):
    """Sanitized failure raised by a chunk repository boundary."""


class ChunkIdentityCollisionError(ChunkRepositoryError):
    """Raised when one chunk identity is associated with different records."""


class EmbeddingStatus(StrEnum):
    """Outcome of representing one chunk."""

    EMBEDDED = "embedded"
    FAILED = "failed"


class DocumentIndexingStatus(StrEnum):
    """Aggregate indexing state that deliberately carries no approval semantics."""

    COMPLETED = "completed"
    ATTENTION_REQUIRED = "attention_required"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ChunkingConfiguration:
    """Versioned character limits used by the deterministic chunker."""

    version: str = DOCUMENT_CHUNKING_CONFIGURATION_VERSION
    max_characters: int = DEFAULT_CHUNK_MAX_CHARACTERS
    overlap_characters: int = DEFAULT_CHUNK_OVERLAP_CHARACTERS
    cleanup_version: str = "technical-noise-cleanup.v1"
    section_detection_version: str = "conservative-headings.v1"

    def __post_init__(self) -> None:
        version = cast(object, self.version)
        cleanup_version = cast(object, self.cleanup_version)
        section_detection_version = cast(object, self.section_detection_version)
        if (
            not isinstance(version, str)
            or _IDENTITY_PATTERN.fullmatch(version) is None
            or not isinstance(cleanup_version, str)
            or _IDENTITY_PATTERN.fullmatch(cleanup_version) is None
            or not isinstance(section_detection_version, str)
            or _IDENTITY_PATTERN.fullmatch(section_detection_version) is None
        ):
            raise ValueError("Chunking configuration versions are invalid.")
        if type(self.max_characters) is not int or self.max_characters < 32:
            raise ValueError("Chunk size must be at least 32 characters.")
        if type(self.overlap_characters) is not int or not (
            0 <= self.overlap_characters < self.max_characters
        ):
            raise ValueError("Chunk overlap must be smaller than the chunk size.")

    @property
    def identity(self) -> str:
        payload = {
            "cleanup_version": self.cleanup_version,
            "max_characters": self.max_characters,
            "overlap_characters": self.overlap_characters,
            "section_detection_version": self.section_detection_version,
            "version": self.version,
        }
        return f"chunkcfg_{_digest_payload(payload)}"


DEFAULT_CHUNKING_CONFIGURATION: Final = ChunkingConfiguration()


@dataclass(frozen=True, slots=True)
class ExtractionProvenance:
    """Trusted SEN-43 extraction metadata retained on every chunk."""

    source_name: str
    source_sha256: str
    source_version: str
    source_size_bytes: int
    pdf_version: str | None
    extraction_schema_version: int
    extractor_version: int
    document_extraction_status: str
    document_failure_code: str | None
    page_number: int
    page_extraction_method: str
    page_extraction_status: str
    page_failure_code: str | None
    ocr_trigger_codes: tuple[str, ...]
    quality_signals: tuple[str, ...]
    pdfium_version: str
    ocr_adapter_name: str | None
    ocr_adapter_version: str | None


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """One local excerpt with deterministic identity and exact provenance."""

    schema_version: int
    chunk_id: str
    document_id: str
    document_version: str
    content: str
    content_sha256: str
    page_number: int
    section_id: str
    section_index: int
    section_title: str | None
    ordinal: int
    section_chunk_index: int
    character_start: int
    character_end: int
    chunking_configuration_id: str
    provenance: ExtractionProvenance


@dataclass(frozen=True, slots=True)
class ChunkEmbedding:
    """Provider-neutral vector or an explicit sanitized failure for one chunk."""

    chunk_id: str
    provider_id: str
    representation_version: str
    dimension: int
    status: EmbeddingStatus
    vector: tuple[float, ...] | None
    failure_code: str | None

    def __post_init__(self) -> None:
        if _CHUNK_ID_PATTERN.fullmatch(self.chunk_id) is None:
            raise ValueError("Embedding chunk identity is invalid.")
        if (
            _IDENTITY_PATTERN.fullmatch(self.provider_id) is None
            or _IDENTITY_PATTERN.fullmatch(self.representation_version) is None
        ):
            raise ValueError("Embedding provider identity is invalid.")
        if type(self.status) is not EmbeddingStatus:
            raise ValueError("Embedding status is invalid.")
        if type(self.dimension) is not int or self.dimension < 0:
            raise ValueError("Embedding dimension cannot be negative.")
        if self.status is EmbeddingStatus.EMBEDDED:
            if (
                self.failure_code is not None
                or self.vector is None
                or len(self.vector) != self.dimension
                or self.dimension == 0
                or any(not isfinite(value) for value in self.vector)
            ):
                raise ValueError("Embedded results require a finite vector.")
        elif (
            self.vector is not None
            or self.failure_code is None
            or _FAILURE_CODE_PATTERN.fullmatch(self.failure_code) is None
        ):
            raise ValueError("Failed embeddings require a code and no vector.")


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    """A chunk and its representation outcome, including failures."""

    chunk: DocumentChunk
    embedding: ChunkEmbedding

    def __post_init__(self) -> None:
        if self.chunk.chunk_id != self.embedding.chunk_id:
            raise ValueError("Chunk and embedding identities must match.")


@dataclass(frozen=True, slots=True)
class IndexingFailure:
    """Sanitized page- or chunk-level failure retained in an indexing result."""

    code: str
    page_number: int | None
    chunk_id: str | None
    provenance: ExtractionProvenance | None = None

    def __post_init__(self) -> None:
        if _FAILURE_CODE_PATTERN.fullmatch(self.code) is None:
            raise ValueError("Indexing failure code is invalid.")
        if self.page_number is not None and (
            type(self.page_number) is not int or self.page_number < 1
        ):
            raise ValueError("Indexing failure page number is invalid.")
        if (
            self.chunk_id is not None
            and _CHUNK_ID_PATTERN.fullmatch(self.chunk_id) is None
        ):
            raise ValueError("Indexing failure chunk identity is invalid.")
        if self.provenance is not None and (
            self.page_number is None or self.provenance.page_number != self.page_number
        ):
            raise ValueError("Indexing failure provenance does not match its page.")


@dataclass(frozen=True, slots=True)
class DocumentChunkingResult:
    """Deterministic chunking result before representation."""

    document_id: str
    document_version: str
    document_extraction_status: str
    document_extraction_failure_code: str | None
    status: DocumentIndexingStatus
    chunks: tuple[DocumentChunk, ...]
    failures: tuple[IndexingFailure, ...]


@dataclass(frozen=True, slots=True)
class DocumentIndexingResult:
    """Complete result persisted by the indexing boundary."""

    document_id: str
    document_version: str
    document_extraction_status: str
    document_extraction_failure_code: str | None
    status: DocumentIndexingStatus
    records: tuple[IndexedChunk, ...]
    failures: tuple[IndexingFailure, ...]


class EmbeddingProvider(Protocol):
    """Versioned representation boundary with per-chunk outcomes."""

    @property
    def provider_id(self) -> str: ...

    @property
    def representation_version(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, chunks: Sequence[DocumentChunk]) -> tuple[ChunkEmbedding, ...]: ...


class ChunkRepository(Protocol):
    """Persistence boundary for chunks, including failed representations."""

    def save(self, records: Sequence[IndexedChunk]) -> None: ...


class PgVectorWriter(Protocol):
    """Injectable write boundary; implementations own connections and SQL."""

    def upsert(self, rows: Sequence[PgVectorRow]) -> None: ...


@dataclass(frozen=True, slots=True)
class PgVectorRow:
    """Synthetic pgvector-compatible row without a database dependency."""

    chunk_schema_version: int
    chunk_id: str
    document_id: str
    document_version: str
    page_number: int
    section_id: str
    section_index: int
    section_title: str | None
    ordinal: int
    section_chunk_index: int
    character_start: int
    character_end: int
    content: str
    content_sha256: str
    chunking_configuration_id: str
    source_name: str
    source_sha256: str
    source_version: str
    source_size_bytes: int
    pdf_version: str | None
    extraction_schema_version: int
    extractor_version: int
    document_extraction_status: str
    document_failure_code: str | None
    page_extraction_method: str
    page_extraction_status: str
    page_failure_code: str | None
    ocr_trigger_codes: tuple[str, ...]
    quality_signals: tuple[str, ...]
    pdfium_version: str
    ocr_adapter_name: str | None
    ocr_adapter_version: str | None
    embedding_provider_id: str
    representation_version: str
    embedding_dimension: int
    embedding_status: str
    embedding: tuple[float, ...] | None
    embedding_failure_code: str | None


class LocalHashEmbeddingProvider:
    """Deterministic offline representation for CI, not a semantic model."""

    def __init__(self, *, dimension: int = DEFAULT_EMBEDDING_DIMENSION) -> None:
        if type(dimension) is not int or not 1 <= dimension <= 4_096:
            raise ValueError("Embedding dimension must be between 1 and 4096.")
        self._dimension = dimension

    @property
    def provider_id(self) -> str:
        return "fake-local-hash"

    @property
    def representation_version(self) -> str:
        return f"{DOCUMENT_REPRESENTATION_VERSION}.d{self.dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, chunks: Sequence[DocumentChunk]) -> tuple[ChunkEmbedding, ...]:
        return tuple(
            ChunkEmbedding(
                chunk_id=chunk.chunk_id,
                provider_id=self.provider_id,
                representation_version=self.representation_version,
                dimension=self.dimension,
                status=EmbeddingStatus.EMBEDDED,
                vector=_hash_vector(
                    chunk.content,
                    dimension=self.dimension,
                    representation_version=self.representation_version,
                ),
                failure_code=None,
            )
            for chunk in chunks
        )


class InMemoryChunkRepository:
    """Ordered, idempotent repository that rejects identity collisions."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str, str, str], IndexedChunk] = {}

    def save(self, records: Sequence[IndexedChunk]) -> None:
        staged = dict(self._records)
        for record in records:
            key = _repository_key(record)
            existing = staged.get(key)
            if existing is not None and existing != record:
                raise ChunkIdentityCollisionError(
                    "Chunk repository identity collision detected."
                )
            staged[key] = record
        self._records = staged

    def list_by_document(
        self,
        document_id: str,
        *,
        document_version: str | None = None,
    ) -> tuple[IndexedChunk, ...]:
        records = (
            record
            for record in self._records.values()
            if record.chunk.document_id == document_id
            and (
                document_version is None
                or record.chunk.document_version == document_version
            )
        )
        return tuple(sorted(records, key=_record_order_key))

    def __len__(self) -> int:
        return len(self._records)


class PgVectorChunkRepository:
    """Map indexing records to an injected pgvector writer without opening services."""

    def __init__(self, *, writer: PgVectorWriter) -> None:
        self._writer = writer

    def save(self, records: Sequence[IndexedChunk]) -> None:
        rows = tuple(_pgvector_row(record) for record in records)
        try:
            self._writer.upsert(rows)
        except Exception:
            raise ChunkRepositoryError("Pgvector chunk persistence failed.") from None


@dataclass(frozen=True, slots=True)
class _ExtractionPage:
    page_number: int
    method: PageExtractionMethod
    status: PageExtractionStatus
    text: str | None
    failure_code: str | None
    ocr_trigger_codes: tuple[str, ...]
    quality_signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _StructuredExtraction:
    source_name: str
    source_sha256: str
    source_version: str
    source_size_bytes: int
    pdf_version: str | None
    extraction_schema_version: int
    extractor_version: int
    document_status: DocumentExtractionStatus
    document_failure_code: str | None
    pages: tuple[_ExtractionPage, ...]
    pdfium_version: str
    ocr_adapter_name: str | None
    ocr_adapter_version: str | None


@dataclass(frozen=True, slots=True)
class _Section:
    index: int
    title: str | None
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class _CleanedPageText:
    text: str
    source_spans: tuple[tuple[int, int], ...]

    def source_bounds(self, start: int, end: int) -> tuple[int, int]:
        if not 0 <= start < end <= len(self.source_spans):
            raise ValueError("Cleaned text bounds are invalid.")
        return self.source_spans[start][0], self.source_spans[end - 1][1]


def chunk_extracted_document(
    extraction: Mapping[str, object],
    *,
    configuration: ChunkingConfiguration = DEFAULT_CHUNKING_CONFIGURATION,
) -> DocumentChunkingResult:
    """Consume only a structured SEN-43 extraction and create local chunks."""

    structured = _parse_extraction(extraction)
    document_id = _document_id(structured.source_name)
    document_version = f"{_DOCUMENT_VERSION_PREFIX}{structured.source_sha256}"
    chunks: list[DocumentChunk] = []
    failures: list[IndexingFailure] = []
    identities: dict[str, DocumentChunk] = {}
    ordinal = 0

    for page in structured.pages:
        if page.text is None:
            failures.append(
                _page_failure(
                    structured,
                    page,
                    fallback_code="chunking.page_text_unavailable",
                )
            )
            continue
        try:
            page.text.encode("utf-8")
            cleaned_page = _clean_technical_noise_with_mapping(page.text)
        except UnicodeError:
            failures.append(
                _page_failure(
                    structured,
                    page,
                    fallback_code="chunking.page_text_invalid_unicode",
                )
            )
            continue
        if not cleaned_page.text:
            failures.append(
                _page_failure(
                    structured,
                    page,
                    fallback_code="chunking.page_empty_after_cleanup",
                )
            )
            continue

        for section in _split_sections(cleaned_page.text):
            section_id = _section_id(
                document_id=document_id,
                document_version=document_version,
                page_number=page.page_number,
                section=section,
            )
            for section_chunk_index, (local_start, local_end) in enumerate(
                _chunk_bounds(section.text, configuration),
                start=1,
            ):
                content = section.text[local_start:local_end]
                ordinal += 1
                cleaned_start = section.start + local_start
                cleaned_end = section.start + local_end
                character_start, character_end = cleaned_page.source_bounds(
                    cleaned_start,
                    cleaned_end,
                )
                content_sha256 = _digest_text(content)
                chunk_id = _build_chunk_id(
                    content_sha256=content_sha256,
                    document_id=document_id,
                    document_version=document_version,
                    page_number=page.page_number,
                    section_id=section_id,
                    section_index=section.index,
                    ordinal=ordinal,
                    section_chunk_index=section_chunk_index,
                    character_start=character_start,
                    character_end=character_end,
                    configuration_id=configuration.identity,
                )
                chunk = DocumentChunk(
                    schema_version=DOCUMENT_CHUNK_SCHEMA_VERSION,
                    chunk_id=chunk_id,
                    document_id=document_id,
                    document_version=document_version,
                    content=content,
                    content_sha256=content_sha256,
                    page_number=page.page_number,
                    section_id=section_id,
                    section_index=section.index,
                    section_title=section.title,
                    ordinal=ordinal,
                    section_chunk_index=section_chunk_index,
                    character_start=character_start,
                    character_end=character_end,
                    chunking_configuration_id=configuration.identity,
                    provenance=_provenance(structured, page),
                )
                existing = identities.get(chunk_id)
                if existing is not None and existing != chunk:
                    failures.append(
                        IndexingFailure(
                            code="chunking.chunk_id_collision",
                            page_number=page.page_number,
                            chunk_id=chunk_id,
                            provenance=_provenance(structured, page),
                        )
                    )
                    continue
                identities[chunk_id] = chunk
                chunks.append(chunk)

    if not chunks and not failures:
        failures.append(
            IndexingFailure(
                code="chunking.document_text_unavailable",
                page_number=None,
                chunk_id=None,
            )
        )
    status = _chunking_status(structured, chunks, failures)
    return DocumentChunkingResult(
        document_id=document_id,
        document_version=document_version,
        document_extraction_status=structured.document_status.value,
        document_extraction_failure_code=structured.document_failure_code,
        status=status,
        chunks=tuple(chunks),
        failures=tuple(failures),
    )


def index_extracted_document(
    extraction: Mapping[str, object],
    *,
    embedding_provider: EmbeddingProvider,
    repository: ChunkRepository,
    configuration: ChunkingConfiguration = DEFAULT_CHUNKING_CONFIGURATION,
) -> DocumentIndexingResult:
    """Chunk, represent, and persist every outcome without approving a document."""

    chunking = chunk_extracted_document(extraction, configuration=configuration)
    embeddings = _embed_chunks(embedding_provider, chunking.chunks)
    records = tuple(
        IndexedChunk(chunk=chunk, embedding=embedding)
        for chunk, embedding in zip(chunking.chunks, embeddings, strict=True)
    )
    repository.save(records)
    embedding_failures = tuple(
        IndexingFailure(
            code=cast(str, record.embedding.failure_code),
            page_number=record.chunk.page_number,
            chunk_id=record.chunk.chunk_id,
            provenance=record.chunk.provenance,
        )
        for record in records
        if record.embedding.status is EmbeddingStatus.FAILED
    )
    failures = (*chunking.failures, *embedding_failures)
    status = _indexing_status(chunking.status, records)
    return DocumentIndexingResult(
        document_id=chunking.document_id,
        document_version=chunking.document_version,
        document_extraction_status=chunking.document_extraction_status,
        document_extraction_failure_code=chunking.document_extraction_failure_code,
        status=status,
        records=records,
        failures=failures,
    )


def _parse_extraction(extraction: Mapping[str, object]) -> _StructuredExtraction:
    try:
        schema_version = _strict_int(extraction["schema_version"], minimum=1)
        if schema_version != SOURCE_DOCUMENT_EXTRACTION_SCHEMA_VERSION:
            raise ValueError
        extractor_version = _strict_int(extraction["extractor_version"], minimum=1)
        source = _mapping(extraction["source"])
        source_name = _non_empty_string(source["name"])
        source_sha256 = _hash_string(source["sha256"])
        source_version = _non_empty_string(source["source_version"])
        if source_version != f"sha256:{source_sha256}":
            raise ValueError
        source_size_bytes = _strict_int(source["size_bytes"], minimum=0)
        pdf_version = _optional_string(source["pdf_version"])
        document_status = DocumentExtractionStatus(
            _non_empty_string(extraction["status"])
        )
        document_failure_code = _optional_failure_code(extraction["failure_code"])
        raw_pages = _object_sequence(extraction["pages"])
        raw_page_count = extraction["page_count"]
        page_count = (
            None if raw_page_count is None else _strict_int(raw_page_count, minimum=0)
        )
        if page_count != len(raw_pages) and not (page_count is None and not raw_pages):
            raise ValueError
        pages = tuple(
            _parse_page(raw_page, expected_page_number=index)
            for index, raw_page in enumerate(raw_pages, start=1)
        )
        tooling = _mapping(extraction["tooling"])
        pdfium_version = _non_empty_string(tooling["pypdfium2"])
        adapter = _mapping(tooling["ocr_adapter"])
        configured = adapter["configured"]
        if type(configured) is not bool:
            raise ValueError
        ocr_adapter_name = _optional_string(adapter["name"])
        ocr_adapter_version = _optional_string(adapter["version"])
        if configured != (
            ocr_adapter_name is not None and ocr_adapter_version is not None
        ):
            raise ValueError
    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        raise DocumentChunkingError(
            "Structured document extraction is invalid."
        ) from None
    return _StructuredExtraction(
        source_name=source_name,
        source_sha256=source_sha256,
        source_version=source_version,
        source_size_bytes=source_size_bytes,
        pdf_version=pdf_version,
        extraction_schema_version=schema_version,
        extractor_version=extractor_version,
        document_status=document_status,
        document_failure_code=document_failure_code,
        pages=pages,
        pdfium_version=pdfium_version,
        ocr_adapter_name=ocr_adapter_name,
        ocr_adapter_version=ocr_adapter_version,
    )


def _parse_page(raw_page: object, *, expected_page_number: int) -> _ExtractionPage:
    page = _mapping(raw_page)
    page_number = _strict_int(page["page_number"], minimum=1)
    if page_number != expected_page_number:
        raise ValueError
    text = page["text"]
    if text is not None and not isinstance(text, str):
        raise ValueError
    quality = _mapping(page["quality"])
    return _ExtractionPage(
        page_number=page_number,
        method=PageExtractionMethod(_non_empty_string(page["method"])),
        status=PageExtractionStatus(_non_empty_string(page["status"])),
        text=text,
        failure_code=_optional_failure_code(page["failure_code"]),
        ocr_trigger_codes=_code_tuple(page["ocr_trigger_codes"]),
        quality_signals=_code_tuple(quality["signals"]),
    )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError
    return cast(Mapping[str, object], value)


def _object_sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise ValueError
    return tuple(cast(list[object], value))


def _strict_int(value: object, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError
    return value


def _non_empty_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _non_empty_string(value)


def _optional_failure_code(value: object) -> str | None:
    text = _optional_string(value)
    if text is not None and _FAILURE_CODE_PATTERN.fullmatch(text) is None:
        raise ValueError
    return text


def _hash_string(value: object) -> str:
    text = _non_empty_string(value)
    if _HASH_PATTERN.fullmatch(text) is None:
        raise ValueError
    return text


def _code_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or _FAILURE_CODE_PATTERN.fullmatch(item) is None
        for item in cast(list[object], value)
    ):
        raise ValueError
    return tuple(cast(list[str], value))


def _clean_technical_noise_with_mapping(text: str) -> _CleanedPageText:
    mapped: list[tuple[str, int, int]] = []
    cursor = 0
    while cursor < len(text):
        character = text[cursor]
        if character == "\r":
            source_end = (
                cursor + 2 if text[cursor : cursor + 2] == "\r\n" else cursor + 1
            )
            mapped.append(("\n", cursor, source_end))
            cursor = source_end
            continue
        if character in _REMOVED_TECHNICAL_NOISE:
            cursor += 1
            continue
        if character in _LINE_BREAK_TECHNICAL_NOISE:
            mapped.append(("\n", cursor, cursor + 1))
        else:
            mapped.append((character, cursor, cursor + 1))
        cursor += 1

    trimmed: list[tuple[str, int, int]] = []
    line: list[tuple[str, int, int]] = []
    for item in mapped:
        if item[0] != "\n":
            line.append(item)
            continue
        while line and line[-1][0] in {" ", "\t"}:
            line.pop()
        trimmed.extend(line)
        line.clear()
        trimmed.append(item)
    while line and line[-1][0] in {" ", "\t"}:
        line.pop()
    trimmed.extend(line)

    collapsed: list[tuple[str, int, int]] = []
    consecutive_newlines = 0
    for item in trimmed:
        if item[0] == "\n":
            consecutive_newlines += 1
            if consecutive_newlines > 3:
                continue
        else:
            consecutive_newlines = 0
        collapsed.append(item)

    start = 0
    end = len(collapsed)
    while start < end and collapsed[start][0] == "\n":
        start += 1
    while end > start and collapsed[end - 1][0] == "\n":
        end -= 1
    cleaned = collapsed[start:end]
    return _CleanedPageText(
        text="".join(character for character, _, _ in cleaned),
        source_spans=tuple(
            (source_start, source_end) for _, source_start, source_end in cleaned
        ),
    )


def _split_sections(text: str) -> tuple[_Section, ...]:
    headings: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        visible_line = line.rstrip("\n")
        title = _heading_title(visible_line)
        if title is not None:
            headings.append((offset, title))
        offset += len(line)
    if not headings:
        return (_Section(index=1, title=None, start=0, end=len(text), text=text),)

    boundaries: list[tuple[int, str | None]] = []
    if headings[0][0] > 0:
        boundaries.append((0, None))
    boundaries.extend(headings)
    sections: list[_Section] = []
    for index, (start, title) in enumerate(boundaries, start=1):
        hard_end = boundaries[index][0] if index < len(boundaries) else len(text)
        trimmed_start, trimmed_end = _trim_bounds(text, start, hard_end)
        if trimmed_start == trimmed_end:
            continue
        sections.append(
            _Section(
                index=len(sections) + 1,
                title=title,
                start=trimmed_start,
                end=trimmed_end,
                text=text[trimmed_start:trimmed_end],
            )
        )
    return tuple(sections)


def _heading_title(line: str) -> str | None:
    markdown = _MARKDOWN_HEADING.fullmatch(line.strip())
    if markdown is not None:
        return markdown.group("title").strip()
    candidate = line.strip()
    if (
        not candidate
        or len(candidate) > 120
        or len(candidate.split()) > 12
        or candidate[-1:] in {".", "?", "!", ";"}
    ):
        return None
    cased_characters = tuple(
        character for character in candidate if character.isalpha()
    )
    if cased_characters and all(
        not character.islower() for character in cased_characters
    ):
        return candidate
    return None


def _trim_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start = _next_grapheme_boundary(text, start + 1, maximum=end)
    while (
        end > start and text[end - 1].isspace() and _is_grapheme_boundary(text, end - 1)
    ):
        end -= 1
    return start, end


def _chunk_bounds(
    text: str,
    configuration: ChunkingConfiguration,
) -> tuple[tuple[int, int], ...]:
    bounds: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(text):
        hard_end = min(cursor + configuration.max_characters, len(text))
        end = (
            hard_end
            if hard_end == len(text)
            else _preferred_break(text, cursor=cursor, hard_end=hard_end)
        )
        end = _previous_grapheme_boundary(text, end, minimum=cursor)
        if end == cursor:
            end = _next_grapheme_boundary(text, hard_end, maximum=len(text))
        start, trimmed_end = _trim_bounds(text, cursor, end)
        if start < trimmed_end:
            bounds.append((start, trimmed_end))
        if end == len(text):
            break
        next_cursor = max(cursor + 1, end - configuration.overlap_characters)
        next_cursor = _next_grapheme_boundary(text, next_cursor, maximum=end)
        while next_cursor < end and text[next_cursor].isspace():
            next_cursor = _next_grapheme_boundary(
                text,
                next_cursor + 1,
                maximum=end,
            )
        cursor = next_cursor
    return tuple(bounds)


def _preferred_break(text: str, *, cursor: int, hard_end: int) -> int:
    minimum = cursor + max((hard_end - cursor) // 2, 1)
    for delimiter in ("\n\n", "\n", ". ", "; ", " "):
        position = text.rfind(delimiter, minimum, hard_end)
        if position >= minimum:
            return position + len(delimiter)
    return hard_end


def _is_grapheme_boundary(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return True
    previous = text[index - 1]
    following = text[index]
    return not (
        _is_grapheme_extension(following)
        or previous == _ZERO_WIDTH_JOINER
        or following == _ZERO_WIDTH_JOINER
    )


def _is_grapheme_extension(character: str) -> bool:
    code_point = ord(character)
    return (
        unicode_category(character).startswith("M")
        or 0xFE00 <= code_point <= 0xFE0F
        or 0xE0100 <= code_point <= 0xE01EF
        or 0x1F3FB <= code_point <= 0x1F3FF
    )


def _previous_grapheme_boundary(text: str, index: int, *, minimum: int) -> int:
    while index > minimum and not _is_grapheme_boundary(text, index):
        index -= 1
    return index


def _next_grapheme_boundary(text: str, index: int, *, maximum: int) -> int:
    while index < maximum and not _is_grapheme_boundary(text, index):
        index += 1
    return index


def _document_id(source_name: str) -> str:
    return f"{_DOCUMENT_ID_PREFIX}{_digest_payload({'source_name': source_name})}"


def _section_id(
    *,
    document_id: str,
    document_version: str,
    page_number: int,
    section: _Section,
) -> str:
    return document_section_id(
        document_id=document_id,
        document_version=document_version,
        page_number=page_number,
        section_index=section.index,
        section_title=section.title,
    )


def document_section_id(
    *,
    document_id: str,
    document_version: str,
    page_number: int,
    section_index: int,
    section_title: str | None,
) -> str:
    """Derive the canonical identity for one indexed document section."""

    digest = _digest_payload(
        {
            "document_id": document_id,
            "document_version": document_version,
            "page_number": page_number,
            "section_index": section_index,
            "section_title": section_title,
        }
    )
    return f"{_SECTION_ID_PREFIX}{digest}"


def _build_chunk_id(
    *,
    content_sha256: str,
    document_id: str,
    document_version: str,
    page_number: int,
    section_id: str,
    section_index: int,
    ordinal: int,
    section_chunk_index: int,
    character_start: int,
    character_end: int,
    configuration_id: str,
) -> str:
    return document_chunk_id(
        content_sha256=content_sha256,
        document_id=document_id,
        document_version=document_version,
        page_number=page_number,
        section_id=section_id,
        section_index=section_index,
        ordinal=ordinal,
        section_chunk_index=section_chunk_index,
        character_start=character_start,
        character_end=character_end,
        chunking_configuration_id=configuration_id,
    )


def document_chunk_id(
    *,
    content_sha256: str,
    document_id: str,
    document_version: str,
    page_number: int,
    section_id: str,
    section_index: int,
    ordinal: int,
    section_chunk_index: int,
    character_start: int,
    character_end: int,
    chunking_configuration_id: str,
) -> str:
    """Derive the canonical identity for one indexed document chunk."""

    digest = _digest_payload(
        {
            "character_end": character_end,
            "character_start": character_start,
            "chunking_configuration_id": chunking_configuration_id,
            "content_sha256": content_sha256,
            "document_id": document_id,
            "document_version": document_version,
            "ordinal": ordinal,
            "page_number": page_number,
            "section_chunk_index": section_chunk_index,
            "section_id": section_id,
            "section_index": section_index,
        }
    )
    return f"{_CHUNK_ID_PREFIX}{digest}"


def _provenance(
    structured: _StructuredExtraction,
    page: _ExtractionPage,
) -> ExtractionProvenance:
    return ExtractionProvenance(
        source_name=structured.source_name,
        source_sha256=structured.source_sha256,
        source_version=structured.source_version,
        source_size_bytes=structured.source_size_bytes,
        pdf_version=structured.pdf_version,
        extraction_schema_version=structured.extraction_schema_version,
        extractor_version=structured.extractor_version,
        document_extraction_status=structured.document_status.value,
        document_failure_code=structured.document_failure_code,
        page_number=page.page_number,
        page_extraction_method=page.method.value,
        page_extraction_status=page.status.value,
        page_failure_code=page.failure_code,
        ocr_trigger_codes=page.ocr_trigger_codes,
        quality_signals=page.quality_signals,
        pdfium_version=structured.pdfium_version,
        ocr_adapter_name=structured.ocr_adapter_name,
        ocr_adapter_version=structured.ocr_adapter_version,
    )


def _page_failure(
    structured: _StructuredExtraction,
    page: _ExtractionPage,
    *,
    fallback_code: str,
) -> IndexingFailure:
    return IndexingFailure(
        code=page.failure_code or fallback_code,
        page_number=page.page_number,
        chunk_id=None,
        provenance=_provenance(structured, page),
    )


def _chunking_status(
    structured: _StructuredExtraction,
    chunks: Sequence[DocumentChunk],
    failures: Sequence[IndexingFailure],
) -> DocumentIndexingStatus:
    if not chunks:
        return DocumentIndexingStatus.FAILED
    if failures:
        return DocumentIndexingStatus.PARTIAL
    if structured.document_status is not DocumentExtractionStatus.COMPLETED or any(
        page.status is not PageExtractionStatus.EXTRACTED for page in structured.pages
    ):
        return DocumentIndexingStatus.ATTENTION_REQUIRED
    return DocumentIndexingStatus.COMPLETED


def _provider_descriptor(
    provider: EmbeddingProvider,
) -> tuple[str, str, int] | None:
    try:
        provider_id = cast(object, provider.provider_id)
        representation_version = cast(object, provider.representation_version)
        dimension = cast(object, provider.dimension)
    except Exception:
        return None
    if (
        not isinstance(provider_id, str)
        or _IDENTITY_PATTERN.fullmatch(provider_id) is None
        or not isinstance(representation_version, str)
        or _IDENTITY_PATTERN.fullmatch(representation_version) is None
        or type(dimension) is not int
        or dimension <= 0
    ):
        return None
    return provider_id, representation_version, dimension


def _embed_chunks(
    provider: EmbeddingProvider,
    chunks: Sequence[DocumentChunk],
) -> tuple[ChunkEmbedding, ...]:
    descriptor = _provider_descriptor(provider)
    if descriptor is None:
        return tuple(
            _failed_embedding(chunk, code="embedding.provider_invalid")
            for chunk in chunks
        )
    provider_id, representation_version, dimension = descriptor
    try:
        raw_outcomes = cast(object, provider.embed(chunks))
    except Exception:
        return tuple(
            _failed_embedding(
                chunk,
                code="embedding.provider_failed",
                provider_id=provider_id,
                representation_version=representation_version,
                dimension=dimension,
            )
            for chunk in chunks
        )
    outcomes = (
        cast(tuple[object, ...], raw_outcomes)
        if isinstance(raw_outcomes, tuple)
        else ()
    )
    if len(outcomes) != len(chunks):
        return tuple(
            _failed_embedding(
                chunk,
                code="embedding.provider_contract_invalid",
                provider_id=provider_id,
                representation_version=representation_version,
                dimension=dimension,
            )
            for chunk in chunks
        )

    validated: list[ChunkEmbedding] = []
    for index, chunk in enumerate(chunks):
        outcome = outcomes[index] if index < len(outcomes) else None
        if (
            type(outcome) is not ChunkEmbedding
            or outcome.chunk_id != chunk.chunk_id
            or outcome.provider_id != provider_id
            or outcome.representation_version != representation_version
            or outcome.dimension != dimension
        ):
            validated.append(
                _failed_embedding(
                    chunk,
                    code="embedding.provider_contract_invalid",
                    provider_id=provider_id,
                    representation_version=representation_version,
                    dimension=dimension,
                )
            )
            continue
        validated.append(outcome)
    return tuple(validated)


def _failed_embedding(
    chunk: DocumentChunk,
    *,
    code: str,
    provider_id: str = "invalid-provider",
    representation_version: str = "invalid-representation",
    dimension: int = 0,
) -> ChunkEmbedding:
    return ChunkEmbedding(
        chunk_id=chunk.chunk_id,
        provider_id=provider_id,
        representation_version=representation_version,
        dimension=dimension,
        status=EmbeddingStatus.FAILED,
        vector=None,
        failure_code=code,
    )


def _indexing_status(
    chunking_status: DocumentIndexingStatus,
    records: Sequence[IndexedChunk],
) -> DocumentIndexingStatus:
    if not records or all(
        record.embedding.status is EmbeddingStatus.FAILED for record in records
    ):
        return DocumentIndexingStatus.FAILED
    if chunking_status in {
        DocumentIndexingStatus.PARTIAL,
        DocumentIndexingStatus.FAILED,
    } or any(record.embedding.status is EmbeddingStatus.FAILED for record in records):
        return DocumentIndexingStatus.PARTIAL
    return chunking_status


def _hash_vector(
    text: str,
    *,
    dimension: int,
    representation_version: str,
) -> tuple[float, ...]:
    payload = f"{representation_version}\0{text}".encode()
    raw = shake_256(payload).digest(dimension * 2)
    values = tuple(
        int.from_bytes(raw[index : index + 2], "big") / 32_767.5 - 1.0
        for index in range(0, len(raw), 2)
    )
    norm = sqrt(sum(value * value for value in values))
    if norm == 0:
        return (1.0, *(0.0 for _ in range(dimension - 1)))
    return tuple(value / norm for value in values)


def _repository_key(record: IndexedChunk) -> tuple[str, str, str, str, str]:
    return (
        record.chunk.document_id,
        record.chunk.document_version,
        record.chunk.chunk_id,
        record.embedding.provider_id,
        record.embedding.representation_version,
    )


def _record_order_key(record: IndexedChunk) -> tuple[int, int, int, str, str]:
    return (
        record.chunk.ordinal,
        record.chunk.page_number,
        record.chunk.section_index,
        record.embedding.provider_id,
        record.embedding.representation_version,
    )


def _pgvector_row(record: IndexedChunk) -> PgVectorRow:
    chunk = record.chunk
    provenance = chunk.provenance
    embedding = record.embedding
    return PgVectorRow(
        chunk_schema_version=chunk.schema_version,
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_version=chunk.document_version,
        page_number=chunk.page_number,
        section_id=chunk.section_id,
        section_index=chunk.section_index,
        section_title=chunk.section_title,
        ordinal=chunk.ordinal,
        section_chunk_index=chunk.section_chunk_index,
        character_start=chunk.character_start,
        character_end=chunk.character_end,
        content=chunk.content,
        content_sha256=chunk.content_sha256,
        chunking_configuration_id=chunk.chunking_configuration_id,
        source_name=provenance.source_name,
        source_sha256=provenance.source_sha256,
        source_version=provenance.source_version,
        source_size_bytes=provenance.source_size_bytes,
        pdf_version=provenance.pdf_version,
        extraction_schema_version=provenance.extraction_schema_version,
        extractor_version=provenance.extractor_version,
        document_extraction_status=provenance.document_extraction_status,
        document_failure_code=provenance.document_failure_code,
        page_extraction_method=provenance.page_extraction_method,
        page_extraction_status=provenance.page_extraction_status,
        page_failure_code=provenance.page_failure_code,
        ocr_trigger_codes=provenance.ocr_trigger_codes,
        quality_signals=provenance.quality_signals,
        pdfium_version=provenance.pdfium_version,
        ocr_adapter_name=provenance.ocr_adapter_name,
        ocr_adapter_version=provenance.ocr_adapter_version,
        embedding_provider_id=embedding.provider_id,
        representation_version=embedding.representation_version,
        embedding_dimension=embedding.dimension,
        embedding_status=embedding.status.value,
        embedding=embedding.vector,
        embedding_failure_code=embedding.failure_code,
    )


def _digest_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _digest_payload(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()
