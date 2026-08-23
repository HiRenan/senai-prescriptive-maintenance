"""Fail-closed retrieval over approved, current documentary knowledge."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Final, Never, Protocol, cast

from prescriptive_maintenance.contracts import MAX_TOP_K, DocumentStatus
from prescriptive_maintenance.data.document_indexing import (
    DOCUMENT_CHUNK_SCHEMA_VERSION,
    ChunkEmbedding,
    DocumentChunk,
    EmbeddingStatus,
    ExtractionProvenance,
    IndexedChunk,
)
from prescriptive_maintenance.data.source_documents import (
    SOURCE_DOCUMENT_EXTRACTION_SCHEMA_VERSION,
    DocumentExtractionStatus,
    PageExtractionMethod,
    PageExtractionStatus,
)
from prescriptive_maintenance.document_lifecycle import (
    DocumentRepository,
    DocumentSnapshot,
    ProcessingStepStatus,
)

FAULT_KNOWLEDGE_MAPPING_SCHEMA_VERSION: Final = 1
FAULT_KNOWLEDGE_MAPPING_FORMAT_VERSION: Final = "fault-knowledge-mapping.v1"

_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_VERSION_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_DOCUMENT_ID_PATTERN: Final = re.compile(r"doc_[a-z0-9_]{3,64}")
_DOCUMENT_VERSION_PATTERN: Final = re.compile(r"docver_[a-z0-9_]{3,64}")
_CHUNK_ID_PATTERN: Final = re.compile(r"chunk_[a-z0-9_]{3,64}")
_SECTION_ID_PATTERN: Final = re.compile(r"section_[a-z0-9_]{3,64}")
_CHUNK_CONFIGURATION_ID_PATTERN: Final = re.compile(r"chunkcfg_[0-9a-f]{64}")
_FAULT_CLASS_PATTERN: Final = re.compile(
    r"(?:[a-z0-9]|%[0-9A-F]{2})+"
    r"(?:-(?:[a-z0-9]|%[0-9A-F]{2})+)*"
)


class FaultKnowledgeMappingError(Exception):
    """Sanitized failure raised for an invalid mapping configuration."""


class FaultKnowledgeReferenceError(FaultKnowledgeMappingError):
    """Raised when a configured opaque document reference is unknown."""


class KnowledgeRetrievalInputError(Exception):
    """Raised when a retrieval request violates the internal contract."""


class KnowledgeRetrievalReason(StrEnum):
    """Typed fail-closed reasons for an empty retrieval result."""

    FAULT_CLASS_UNMAPPED = "fault_class_unmapped"
    NO_APPROVED_COVERAGE = "no_approved_coverage"
    EMPTY_RANKING = "empty_ranking"
    INDEX_UNAVAILABLE = "index_unavailable"
    INDEX_INTEGRITY_FAILED = "index_integrity_failed"
    RANKING_FAILED = "ranking_failed"


@dataclass(frozen=True, slots=True)
class FaultKnowledgeMappingEntry:
    """One canonical fault class and its ordered opaque document references."""

    fault_class: str
    document_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_fault_class(self.fault_class, mapping_error=True)
        if type(self.document_ids) is not tuple:
            raise FaultKnowledgeMappingError(
                "Fault knowledge document references must be an ordered sequence."
            )
        if len(self.document_ids) != len(set(self.document_ids)):
            raise FaultKnowledgeMappingError(
                "Fault knowledge document references must be unique per class."
            )
        if any(
            not _matches_text_pattern(item, _DOCUMENT_ID_PATTERN)
            for item in self.document_ids
        ):
            raise FaultKnowledgeMappingError(
                "Fault knowledge document reference is invalid."
            )


@dataclass(frozen=True, slots=True)
class FaultKnowledgeMapping:
    """Versioned mapping whose SHA-256 identifies its normalized semantics."""

    schema_version: int
    mapping_version: str
    mapping_sha256: str
    mappings: tuple[FaultKnowledgeMappingEntry, ...]

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != FAULT_KNOWLEDGE_MAPPING_SCHEMA_VERSION
        ):
            raise FaultKnowledgeMappingError(
                "Fault knowledge mapping schema version is unsupported."
            )
        if not _matches_text_pattern(self.mapping_version, _VERSION_PATTERN):
            raise FaultKnowledgeMappingError(
                "Fault knowledge mapping version is invalid."
            )
        if not _matches_text_pattern(self.mapping_sha256, _SHA256_PATTERN):
            raise FaultKnowledgeMappingError("Fault knowledge mapping hash is invalid.")
        if type(self.mappings) is not tuple or any(
            type(entry) is not FaultKnowledgeMappingEntry for entry in self.mappings
        ):
            raise FaultKnowledgeMappingError("Fault knowledge mappings are invalid.")
        fault_classes = tuple(entry.fault_class for entry in self.mappings)
        if len(fault_classes) != len(set(fault_classes)):
            raise FaultKnowledgeMappingError("Fault knowledge classes must be unique.")

    def document_ids_for(self, fault_class: str) -> tuple[str, ...] | None:
        """Return exact configured coverage without normalization or fallback."""

        for entry in self.mappings:
            if entry.fault_class == fault_class:
                return entry.document_ids
        return None


@dataclass(frozen=True, slots=True)
class RankedKnowledgeEvidence:
    """Content-free evidence navigable to one exact indexed section."""

    document_id: str
    document_version: str
    chunk_id: str
    page_number: int
    section_id: str
    score: float

    def __post_init__(self) -> None:
        if not all(
            _matches_text_pattern(value, pattern)
            for value, pattern in (
                (self.document_id, _DOCUMENT_ID_PATTERN),
                (self.document_version, _DOCUMENT_VERSION_PATTERN),
                (self.chunk_id, _CHUNK_ID_PATTERN),
                (self.section_id, _SECTION_ID_PATTERN),
            )
        ):
            raise ValueError("Knowledge evidence identifiers are invalid.")
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("Knowledge evidence page number must be positive.")
        if type(self.score) is not float or not isfinite(self.score):
            raise ValueError("Knowledge evidence score must be finite.")


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalResult:
    """Auditable bounded result, including a typed reason whenever empty."""

    fault_class: str
    mapping_version: str
    mapping_sha256: str
    evidence: tuple[RankedKnowledgeEvidence, ...]
    reason: KnowledgeRetrievalReason | None

    def __post_init__(self) -> None:
        _validate_fault_class(self.fault_class, mapping_error=False)
        if not _matches_text_pattern(
            self.mapping_version, _VERSION_PATTERN
        ) or not _matches_text_pattern(self.mapping_sha256, _SHA256_PATTERN):
            raise ValueError("Knowledge retrieval mapping identity is invalid.")
        if type(self.evidence) is not tuple or len(self.evidence) > MAX_TOP_K:
            raise ValueError("Knowledge retrieval evidence is invalid.")
        if self.evidence and self.reason is not None:
            raise ValueError("Successful retrieval cannot contain an empty reason.")
        if not self.evidence and type(self.reason) is not KnowledgeRetrievalReason:
            raise ValueError("Empty retrieval requires a typed reason.")


class IndexedChunkReader(Protocol):
    """Read only the exact document version selected by lifecycle governance."""

    def list_by_document(
        self,
        document_id: str,
        *,
        document_version: str | None = None,
    ) -> tuple[IndexedChunk, ...]: ...


class KnowledgeChunkScorer(Protocol):
    """Score one already-filtered chunk; ``None`` represents no ranked hit."""

    def score(self, *, fault_class: str, chunk: IndexedChunk) -> float | None: ...


@dataclass(frozen=True, slots=True)
class _ScoredChunk:
    record: IndexedChunk
    score: float


def build_fault_knowledge_mapping(
    *,
    mapping_version: str,
    mappings: Mapping[str, Sequence[str]],
) -> FaultKnowledgeMapping:
    """Build normalized semantics and calculate their deterministic SHA-256."""

    raw_mappings = cast(object, mappings)
    if not isinstance(raw_mappings, Mapping):
        raise FaultKnowledgeMappingError("Fault knowledge mappings are invalid.")
    typed_mappings = cast(Mapping[object, object], raw_mappings)
    entries = _normalize_entries(
        tuple(
            FaultKnowledgeMappingEntry(
                fault_class=_mapping_text(fault_class),
                document_ids=_document_id_sequence(document_ids),
            )
            for fault_class, document_ids in typed_mappings.items()
        )
    )
    provisional = FaultKnowledgeMapping(
        schema_version=FAULT_KNOWLEDGE_MAPPING_SCHEMA_VERSION,
        mapping_version=mapping_version,
        mapping_sha256="0" * 64,
        mappings=entries,
    )
    return FaultKnowledgeMapping(
        schema_version=provisional.schema_version,
        mapping_version=provisional.mapping_version,
        mapping_sha256=identify_fault_knowledge_mapping(provisional),
        mappings=provisional.mappings,
    )


def identify_fault_knowledge_mapping(payload: object) -> str:
    """Hash normalized mapping semantics without trusting their stored hash."""

    mapping = _normalize_mapping(_coerce_mapping(payload))
    return sha256(_canonical_json_bytes(_mapping_semantics(mapping))).hexdigest()


def validate_fault_knowledge_mapping(payload: object) -> FaultKnowledgeMapping:
    """Strictly validate bytes, a mapping, or an immutable configuration."""

    mapping = _normalize_mapping(_coerce_mapping(payload))
    if mapping.mapping_sha256 != identify_fault_knowledge_mapping(mapping):
        raise FaultKnowledgeMappingError(
            "Fault knowledge mapping hash does not match its semantics."
        )
    return mapping


def load_fault_knowledge_mapping(path: Path) -> FaultKnowledgeMapping:
    """Load one explicit external mapping path without discovery or defaults."""

    try:
        raw = path.read_bytes()
    except OSError:
        raise FaultKnowledgeMappingError(
            "Fault knowledge mapping resource is unavailable."
        ) from None
    return validate_fault_knowledge_mapping(raw)


def fault_knowledge_mapping_json_bytes(payload: object) -> bytes:
    """Serialize a valid mapping deterministically for external audit storage."""

    mapping = validate_fault_knowledge_mapping(payload)
    serialized = {
        "schema_version": mapping.schema_version,
        "mapping_version": mapping.mapping_version,
        "mapping_sha256": mapping.mapping_sha256,
        "mappings": [
            {
                "fault_class": entry.fault_class,
                "document_ids": list(entry.document_ids),
            }
            for entry in mapping.mappings
        ],
    }
    return (
        json.dumps(
            serialized,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")


def validate_fault_knowledge_mapping_references(
    mapping: object,
    repository: DocumentRepository,
) -> FaultKnowledgeMapping:
    """Require every configured opaque document to exist in lifecycle storage."""

    validated = validate_fault_knowledge_mapping(mapping)
    references = sorted(
        {
            document_id
            for entry in validated.mappings
            for document_id in entry.document_ids
        }
    )
    for document_id in references:
        try:
            snapshot = repository.get(document_id)
        except Exception:
            raise FaultKnowledgeReferenceError(
                "Fault knowledge document reference could not be validated."
            ) from None
        if (
            type(snapshot) is not DocumentSnapshot
            or snapshot.document.identity != document_id
        ):
            raise FaultKnowledgeReferenceError(
                "Fault knowledge document reference is unknown."
            )
    return validated


class ApprovedKnowledgeRetrievalService:
    """Filter lifecycle and indexing integrity before any scoring call."""

    def __init__(
        self,
        *,
        mapping: FaultKnowledgeMapping,
        documents: DocumentRepository,
        chunks: IndexedChunkReader,
        scorer: KnowledgeChunkScorer,
    ) -> None:
        self._mapping = validate_fault_knowledge_mapping_references(
            mapping,
            documents,
        )
        self._documents = documents
        self._chunks = chunks
        self._scorer = scorer

    def retrieve(self, fault_class: str, *, top_k: int) -> KnowledgeRetrievalResult:
        """Return only approved current evidence from the exact mapped class."""

        clean_fault_class = _validate_fault_class(fault_class, mapping_error=False)
        if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
            raise KnowledgeRetrievalInputError(
                f"Top-k must be an integer between 1 and {MAX_TOP_K}."
            )
        document_ids = self._mapping.document_ids_for(clean_fault_class)
        if document_ids is None:
            return self._empty(
                clean_fault_class,
                KnowledgeRetrievalReason.FAULT_CLASS_UNMAPPED,
            )
        if not document_ids:
            return self._empty(
                clean_fault_class,
                KnowledgeRetrievalReason.NO_APPROVED_COVERAGE,
            )

        candidates, failure = self._eligible_candidates(document_ids)
        if failure is not None:
            return self._empty(clean_fault_class, failure)
        if not candidates:
            return self._empty(
                clean_fault_class,
                KnowledgeRetrievalReason.NO_APPROVED_COVERAGE,
            )

        scored: list[_ScoredChunk] = []
        for record in candidates:
            try:
                raw_score = cast(
                    object,
                    self._scorer.score(
                        fault_class=clean_fault_class,
                        chunk=record,
                    ),
                )
            except Exception:
                return self._empty(
                    clean_fault_class,
                    KnowledgeRetrievalReason.RANKING_FAILED,
                )
            if raw_score is None:
                continue
            if isinstance(raw_score, bool) or not isinstance(raw_score, int | float):
                return self._empty(
                    clean_fault_class,
                    KnowledgeRetrievalReason.RANKING_FAILED,
                )
            score = float(raw_score)
            if not isfinite(score):
                return self._empty(
                    clean_fault_class,
                    KnowledgeRetrievalReason.RANKING_FAILED,
                )
            scored.append(
                _ScoredChunk(
                    record=record,
                    score=0.0 if score == 0.0 else score,
                )
            )
        if not scored:
            return self._empty(
                clean_fault_class,
                KnowledgeRetrievalReason.EMPTY_RANKING,
            )

        ranked = sorted(scored, key=_scored_order_key)[:top_k]
        evidence = tuple(
            RankedKnowledgeEvidence(
                document_id=item.record.chunk.document_id,
                document_version=item.record.chunk.document_version,
                chunk_id=item.record.chunk.chunk_id,
                page_number=item.record.chunk.page_number,
                section_id=item.record.chunk.section_id,
                score=item.score,
            )
            for item in ranked
        )
        return KnowledgeRetrievalResult(
            fault_class=clean_fault_class,
            mapping_version=self._mapping.mapping_version,
            mapping_sha256=self._mapping.mapping_sha256,
            evidence=evidence,
            reason=None,
        )

    def _eligible_candidates(
        self,
        document_ids: Sequence[str],
    ) -> tuple[tuple[IndexedChunk, ...], KnowledgeRetrievalReason | None]:
        candidates: dict[tuple[str, str, str], IndexedChunk] = {}
        for document_id in document_ids:
            try:
                snapshot = self._documents.get(document_id)
            except Exception:
                return (), KnowledgeRetrievalReason.INDEX_UNAVAILABLE
            if type(snapshot) is not DocumentSnapshot:
                return (), KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
            document = snapshot.document
            eligible = document.eligible_version
            if (
                document.identity != document_id
                or eligible is None
                or eligible.status is not DocumentStatus.APPROVED
                or document.current_version != eligible.number
                or eligible.integrity.extraction is not ProcessingStepStatus.SUCCEEDED
                or eligible.integrity.indexing is not ProcessingStepStatus.SUCCEEDED
            ):
                continue
            document_version = f"docver_{eligible.sha256}"
            try:
                records = cast(
                    object,
                    self._chunks.list_by_document(
                        document_id,
                        document_version=document_version,
                    ),
                )
            except Exception:
                return (), KnowledgeRetrievalReason.INDEX_UNAVAILABLE
            if not isinstance(records, tuple):
                return (), KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
            for record in cast(tuple[object, ...], records):
                if not _is_intact_record(
                    record,
                    document_id=document_id,
                    document_version=document_version,
                    source_sha256=eligible.sha256,
                ):
                    continue
                typed_record = cast(IndexedChunk, record)
                key = _evidence_key(typed_record)
                if key in candidates:
                    return (), KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
                candidates[key] = typed_record
        return tuple(candidates[key] for key in sorted(candidates)), None

    def _empty(
        self,
        fault_class: str,
        reason: KnowledgeRetrievalReason,
    ) -> KnowledgeRetrievalResult:
        return KnowledgeRetrievalResult(
            fault_class=fault_class,
            mapping_version=self._mapping.mapping_version,
            mapping_sha256=self._mapping.mapping_sha256,
            evidence=(),
            reason=reason,
        )


def _coerce_mapping(payload: object) -> FaultKnowledgeMapping:
    if isinstance(payload, FaultKnowledgeMapping):
        return payload
    if isinstance(payload, bytes):
        return _parse_mapping(_decode_json(payload))
    if isinstance(payload, Mapping):
        return _parse_mapping(cast(Mapping[object, object], payload))
    raise FaultKnowledgeMappingError("Fault knowledge mapping is invalid.")


def _decode_json(raw: bytes) -> Mapping[object, object]:
    try:
        payload: object = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, FaultKnowledgeMappingError, ValueError):
        raise FaultKnowledgeMappingError(
            "Fault knowledge mapping JSON is invalid."
        ) from None
    if not isinstance(payload, Mapping):
        raise FaultKnowledgeMappingError("Fault knowledge mapping JSON is invalid.")
    return cast(Mapping[object, object], payload)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FaultKnowledgeMappingError(
                "Fault knowledge mapping JSON contains duplicate keys."
            )
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Never:
    raise ValueError("non-finite JSON number")


def _parse_mapping(payload: Mapping[object, object]) -> FaultKnowledgeMapping:
    _exact_keys(
        payload,
        ("schema_version", "mapping_version", "mapping_sha256", "mappings"),
    )
    raw_entries = payload["mappings"]
    if not isinstance(raw_entries, list):
        raise FaultKnowledgeMappingError("Fault knowledge mappings are invalid.")
    entries: list[FaultKnowledgeMappingEntry] = []
    for raw_entry in cast(list[object], raw_entries):
        if not isinstance(raw_entry, Mapping):
            raise FaultKnowledgeMappingError(
                "Fault knowledge mapping entry is invalid."
            )
        entry = cast(Mapping[object, object], raw_entry)
        _exact_keys(entry, ("fault_class", "document_ids"))
        entries.append(
            FaultKnowledgeMappingEntry(
                fault_class=_mapping_text(entry["fault_class"]),
                document_ids=_document_id_sequence(entry["document_ids"]),
            )
        )
    schema_version = payload["schema_version"]
    if type(schema_version) is not int:
        raise FaultKnowledgeMappingError(
            "Fault knowledge mapping schema version is invalid."
        )
    return FaultKnowledgeMapping(
        schema_version=schema_version,
        mapping_version=_mapping_text(payload["mapping_version"]),
        mapping_sha256=_mapping_text(payload["mapping_sha256"]),
        mappings=tuple(entries),
    )


def _exact_keys(
    payload: Mapping[object, object],
    expected: tuple[str, ...],
) -> None:
    if set(payload) != set(expected):
        raise FaultKnowledgeMappingError("Fault knowledge mapping fields are invalid.")


def _mapping_text(value: object) -> str:
    if not isinstance(value, str):
        raise FaultKnowledgeMappingError("Fault knowledge mapping text is invalid.")
    return value


def _document_id_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise FaultKnowledgeMappingError(
            "Fault knowledge document references must be an ordered sequence."
        )
    items = tuple(cast(Sequence[object], value))
    if any(not isinstance(item, str) for item in items):
        raise FaultKnowledgeMappingError(
            "Fault knowledge document reference is invalid."
        )
    return tuple(cast(tuple[str, ...], items))


def _normalize_entries(
    entries: Sequence[FaultKnowledgeMappingEntry],
) -> tuple[FaultKnowledgeMappingEntry, ...]:
    normalized = tuple(
        sorted(
            (
                FaultKnowledgeMappingEntry(
                    fault_class=entry.fault_class,
                    document_ids=tuple(sorted(entry.document_ids)),
                )
                for entry in entries
            ),
            key=lambda entry: entry.fault_class,
        )
    )
    fault_classes = tuple(entry.fault_class for entry in normalized)
    if len(fault_classes) != len(set(fault_classes)):
        raise FaultKnowledgeMappingError("Fault knowledge classes must be unique.")
    return normalized


def _normalize_mapping(mapping: FaultKnowledgeMapping) -> FaultKnowledgeMapping:
    return FaultKnowledgeMapping(
        schema_version=mapping.schema_version,
        mapping_version=mapping.mapping_version,
        mapping_sha256=mapping.mapping_sha256,
        mappings=_normalize_entries(mapping.mappings),
    )


def _mapping_semantics(mapping: FaultKnowledgeMapping) -> dict[str, object]:
    return {
        "schema_version": mapping.schema_version,
        "mapping_version": mapping.mapping_version,
        "mappings": [
            {
                "fault_class": entry.fault_class,
                "document_ids": list(entry.document_ids),
            }
            for entry in mapping.mappings
        ],
    }


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_fault_class(value: object, *, mapping_error: bool) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 200
        or _FAULT_CLASS_PATTERN.fullmatch(value) is None
    ):
        if mapping_error:
            raise FaultKnowledgeMappingError(
                "Fault knowledge class must be a canonical slug."
            )
        raise KnowledgeRetrievalInputError("Fault class must be a canonical slug.")
    return value


def _matches_text_pattern(value: object, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _is_intact_record(
    value: object,
    *,
    document_id: str,
    document_version: str,
    source_sha256: str,
) -> bool:
    if type(value) is not IndexedChunk:
        return False
    record = value
    chunk = record.chunk
    embedding = record.embedding
    if type(chunk) is not DocumentChunk or type(embedding) is not ChunkEmbedding:
        return False
    provenance = chunk.provenance
    if type(provenance) is not ExtractionProvenance:
        return False
    try:
        content_sha256 = sha256(
            chunk.content.encode("utf-8", errors="strict")
        ).hexdigest()
    except (AttributeError, UnicodeError):
        return False
    vector = embedding.vector
    return (
        chunk.schema_version == DOCUMENT_CHUNK_SCHEMA_VERSION
        and chunk.document_id == document_id
        and chunk.document_version == document_version
        and chunk.content != ""
        and chunk.content_sha256 == content_sha256
        and type(chunk.page_number) is int
        and chunk.page_number >= 1
        and provenance.page_number == chunk.page_number
        and _CHUNK_ID_PATTERN.fullmatch(chunk.chunk_id) is not None
        and _SECTION_ID_PATTERN.fullmatch(chunk.section_id) is not None
        and _CHUNK_CONFIGURATION_ID_PATTERN.fullmatch(chunk.chunking_configuration_id)
        is not None
        and type(chunk.section_index) is int
        and chunk.section_index >= 1
        and type(chunk.ordinal) is int
        and chunk.ordinal >= 1
        and type(chunk.section_chunk_index) is int
        and chunk.section_chunk_index >= 1
        and type(chunk.character_start) is int
        and type(chunk.character_end) is int
        and 0 <= chunk.character_start < chunk.character_end
        and provenance.source_sha256 == source_sha256
        and provenance.source_version == f"sha256:{source_sha256}"
        and provenance.extraction_schema_version
        == SOURCE_DOCUMENT_EXTRACTION_SCHEMA_VERSION
        and provenance.document_extraction_status
        == DocumentExtractionStatus.COMPLETED.value
        and provenance.document_failure_code is None
        and provenance.page_extraction_method
        in {PageExtractionMethod.NATIVE.value, PageExtractionMethod.OCR.value}
        and provenance.page_extraction_status == PageExtractionStatus.EXTRACTED.value
        and provenance.page_failure_code is None
        and embedding.chunk_id == chunk.chunk_id
        and embedding.status is EmbeddingStatus.EMBEDDED
        and embedding.failure_code is None
        and type(embedding.dimension) is int
        and embedding.dimension > 0
        and type(vector) is tuple
        and len(vector) == embedding.dimension
        and all(type(item) is float and isfinite(item) for item in vector)
    )


def _evidence_key(record: IndexedChunk) -> tuple[str, str, str]:
    return (
        record.chunk.document_id,
        record.chunk.document_version,
        record.chunk.chunk_id,
    )


def _scored_order_key(
    item: _ScoredChunk,
) -> tuple[float, str, str, int, str, str]:
    chunk = item.record.chunk
    return (
        -item.score,
        chunk.document_id,
        chunk.document_version,
        chunk.page_number,
        chunk.section_id,
        chunk.chunk_id,
    )
