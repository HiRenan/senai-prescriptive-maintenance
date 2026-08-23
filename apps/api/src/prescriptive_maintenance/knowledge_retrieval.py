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
    DocumentVersion,
    GovernedDocument,
    LifecycleEvent,
    ProcessingIntegrity,
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
        if any(
            not _matches_text_pattern(item, _DOCUMENT_ID_PATTERN)
            for item in self.document_ids
        ):
            raise FaultKnowledgeMappingError(
                "Fault knowledge document reference is invalid."
            )
        if len(self.document_ids) != len(set(self.document_ids)):
            raise FaultKnowledgeMappingError(
                "Fault knowledge document references must be unique per class."
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
        fault_classes: list[str] = []
        for entry in self.mappings:
            clean_fault_class = _validate_fault_class(
                entry.fault_class,
                mapping_error=True,
            )
            if type(entry.document_ids) is not tuple or any(
                not _matches_text_pattern(document_id, _DOCUMENT_ID_PATTERN)
                for document_id in entry.document_ids
            ):
                raise FaultKnowledgeMappingError(
                    "Fault knowledge document reference is invalid."
                )
            fault_classes.append(clean_fault_class)
        if len(fault_classes) != len(set(fault_classes)):
            raise FaultKnowledgeMappingError("Fault knowledge classes must be unique.")

    def document_ids_for(self, fault_class: str) -> tuple[str, ...] | None:
        """Return exact configured coverage without normalization or fallback."""

        clean_fault_class = _validate_fault_class(fault_class, mapping_error=False)
        for entry in self.mappings:
            if entry.fault_class == clean_fault_class:
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
    evidence: _EvidenceSnapshot
    score: float


@dataclass(frozen=True, slots=True)
class _EvidenceSnapshot:
    document_id: str
    document_version: str
    chunk_id: str
    page_number: int
    section_id: str


@dataclass(frozen=True, slots=True)
class _IndexedRecordSnapshot:
    record: IndexedChunk
    evidence: _EvidenceSnapshot
    source_sha256: str
    fingerprint: bytes
    eligible: bool


@dataclass(frozen=True, slots=True)
class _EligibleDocumentSnapshot:
    document_id: str
    document_version: str
    source_sha256: str


def build_fault_knowledge_mapping(
    *,
    mapping_version: str,
    mappings: Mapping[str, Sequence[str]],
) -> FaultKnowledgeMapping:
    """Build normalized semantics and calculate their deterministic SHA-256."""

    try:
        clean_mapping_version = _mapping_text(mapping_version)
        raw_mappings = cast(object, mappings)
        if type(raw_mappings) is not dict:
            raise FaultKnowledgeMappingError("Fault knowledge mappings are invalid.")
        typed_mappings = cast(dict[object, object], raw_mappings)
        pairs: tuple[tuple[object, object], ...] = tuple(typed_mappings.items())
        entries = _normalize_entries(
            tuple(
                FaultKnowledgeMappingEntry(
                    fault_class=_mapping_text(fault_class),
                    document_ids=_document_id_sequence(document_ids),
                )
                for fault_class, document_ids in pairs
            )
        )
        provisional = FaultKnowledgeMapping(
            schema_version=FAULT_KNOWLEDGE_MAPPING_SCHEMA_VERSION,
            mapping_version=clean_mapping_version,
            mapping_sha256="0" * 64,
            mappings=entries,
        )
        return FaultKnowledgeMapping(
            schema_version=provisional.schema_version,
            mapping_version=provisional.mapping_version,
            mapping_sha256=_identify_normalized_mapping(provisional),
            mappings=provisional.mappings,
        )
    except FaultKnowledgeMappingError:
        raise
    except Exception:
        raise FaultKnowledgeMappingError(
            "Fault knowledge mappings are invalid."
        ) from None


def identify_fault_knowledge_mapping(payload: object) -> str:
    """Hash normalized mapping semantics without trusting their stored hash."""

    try:
        mapping = _normalize_mapping(_coerce_mapping(payload))
        return _identify_normalized_mapping(mapping)
    except FaultKnowledgeMappingError:
        raise
    except Exception:
        raise FaultKnowledgeMappingError(
            "Fault knowledge mapping is invalid."
        ) from None


def validate_fault_knowledge_mapping(payload: object) -> FaultKnowledgeMapping:
    """Strictly validate bytes, a mapping, or an immutable configuration."""

    try:
        mapping = _normalize_mapping(_coerce_mapping(payload))
        if mapping.mapping_sha256 != _identify_normalized_mapping(mapping):
            raise FaultKnowledgeMappingError(
                "Fault knowledge mapping hash does not match its semantics."
            )
        return mapping
    except FaultKnowledgeMappingError:
        raise
    except Exception:
        raise FaultKnowledgeMappingError(
            "Fault knowledge mapping is invalid."
        ) from None


def load_fault_knowledge_mapping(path: Path) -> FaultKnowledgeMapping:
    """Load one explicit external mapping path without discovery or defaults."""

    try:
        raw = path.read_bytes()
    except Exception:
        raise FaultKnowledgeMappingError(
            "Fault knowledge mapping resource is unavailable."
        ) from None
    return validate_fault_knowledge_mapping(raw)


def fault_knowledge_mapping_json_bytes(payload: object) -> bytes:
    """Serialize a valid mapping deterministically for external audit storage."""

    try:
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
    except FaultKnowledgeMappingError:
        raise
    except Exception:
        raise FaultKnowledgeMappingError(
            "Fault knowledge mapping could not be serialized."
        ) from None


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
        if not _is_expected_document_reference(snapshot, document_id):
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
        mapping_version = self._mapping.mapping_version
        mapping_sha256 = self._mapping.mapping_sha256
        document_ids = self._mapping.document_ids_for(clean_fault_class)
        if document_ids is None:
            return self._empty(
                clean_fault_class,
                KnowledgeRetrievalReason.FAULT_CLASS_UNMAPPED,
                mapping_version=mapping_version,
                mapping_sha256=mapping_sha256,
            )
        if not document_ids:
            return self._empty(
                clean_fault_class,
                KnowledgeRetrievalReason.NO_APPROVED_COVERAGE,
                mapping_version=mapping_version,
                mapping_sha256=mapping_sha256,
            )

        candidates, failure = self._eligible_candidates(document_ids)
        if failure is not None:
            return self._empty(
                clean_fault_class,
                failure,
                mapping_version=mapping_version,
                mapping_sha256=mapping_sha256,
            )
        if not candidates:
            return self._empty(
                clean_fault_class,
                KnowledgeRetrievalReason.NO_APPROVED_COVERAGE,
                mapping_version=mapping_version,
                mapping_sha256=mapping_sha256,
            )

        scored: list[_ScoredChunk] = []
        for candidate in candidates:
            try:
                scorer_record = _clone_indexed_record(candidate.record)
                raw_score = cast(
                    object,
                    self._scorer.score(
                        fault_class=clean_fault_class,
                        chunk=scorer_record,
                    ),
                )
            except Exception:
                return self._empty(
                    clean_fault_class,
                    KnowledgeRetrievalReason.RANKING_FAILED,
                    mapping_version=mapping_version,
                    mapping_sha256=mapping_sha256,
                )
            rescored, corrupted = _snapshot_indexed_record(
                scorer_record,
                document_id=candidate.evidence.document_id,
                document_version=candidate.evidence.document_version,
                source_sha256=candidate.source_sha256,
            )
            if (
                corrupted
                or rescored is None
                or not rescored.eligible
                or rescored.fingerprint != candidate.fingerprint
            ):
                return self._empty(
                    clean_fault_class,
                    KnowledgeRetrievalReason.RANKING_FAILED,
                    mapping_version=mapping_version,
                    mapping_sha256=mapping_sha256,
                )
            if raw_score is None:
                continue
            if type(raw_score) is float:
                score = raw_score
            elif type(raw_score) is int:
                try:
                    score = float(raw_score)
                except Exception:
                    return self._empty(
                        clean_fault_class,
                        KnowledgeRetrievalReason.RANKING_FAILED,
                        mapping_version=mapping_version,
                        mapping_sha256=mapping_sha256,
                    )
            else:
                return self._empty(
                    clean_fault_class,
                    KnowledgeRetrievalReason.RANKING_FAILED,
                    mapping_version=mapping_version,
                    mapping_sha256=mapping_sha256,
                )
            if not isfinite(score):
                return self._empty(
                    clean_fault_class,
                    KnowledgeRetrievalReason.RANKING_FAILED,
                    mapping_version=mapping_version,
                    mapping_sha256=mapping_sha256,
                )
            scored.append(
                _ScoredChunk(
                    evidence=candidate.evidence,
                    score=0.0 if score == 0.0 else score,
                )
            )
        if not scored:
            return self._empty(
                clean_fault_class,
                KnowledgeRetrievalReason.EMPTY_RANKING,
                mapping_version=mapping_version,
                mapping_sha256=mapping_sha256,
            )

        try:
            ranked = sorted(scored, key=_scored_order_key)[:top_k]
            evidence = tuple(
                RankedKnowledgeEvidence(
                    document_id=item.evidence.document_id,
                    document_version=item.evidence.document_version,
                    chunk_id=item.evidence.chunk_id,
                    page_number=item.evidence.page_number,
                    section_id=item.evidence.section_id,
                    score=item.score,
                )
                for item in ranked
            )
            return KnowledgeRetrievalResult(
                fault_class=clean_fault_class,
                mapping_version=mapping_version,
                mapping_sha256=mapping_sha256,
                evidence=evidence,
                reason=None,
            )
        except Exception:
            return self._empty(
                clean_fault_class,
                KnowledgeRetrievalReason.RANKING_FAILED,
                mapping_version=mapping_version,
                mapping_sha256=mapping_sha256,
            )

    def _eligible_candidates(
        self,
        document_ids: Sequence[str],
    ) -> tuple[
        tuple[_IndexedRecordSnapshot, ...],
        KnowledgeRetrievalReason | None,
    ]:
        candidates: dict[tuple[str, str, str], _IndexedRecordSnapshot] = {}
        seen_evidence: set[tuple[str, str, str]] = set()
        for document_id in document_ids:
            try:
                snapshot = self._documents.get(document_id)
            except Exception:
                return (), KnowledgeRetrievalReason.INDEX_UNAVAILABLE
            eligible, snapshot_failure = _eligible_document_snapshot(
                snapshot,
                document_id=document_id,
            )
            if snapshot_failure is not None:
                return (), snapshot_failure
            if eligible is None:
                continue
            try:
                records = cast(
                    object,
                    self._chunks.list_by_document(
                        document_id,
                        document_version=eligible.document_version,
                    ),
                )
            except Exception:
                return (), KnowledgeRetrievalReason.INDEX_UNAVAILABLE
            if type(records) is not tuple:
                return (), KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
            for record in cast(tuple[object, ...], records):
                assessed, corrupted = _snapshot_indexed_record(
                    record,
                    document_id=document_id,
                    document_version=eligible.document_version,
                    source_sha256=eligible.source_sha256,
                )
                if corrupted or assessed is None:
                    return (), KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
                key = _evidence_key(assessed.evidence)
                if key in seen_evidence:
                    return (), KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
                seen_evidence.add(key)
                if assessed.eligible:
                    candidates[key] = assessed
        return tuple(candidates[key] for key in sorted(candidates)), None

    def _empty(
        self,
        fault_class: str,
        reason: KnowledgeRetrievalReason,
        *,
        mapping_version: str,
        mapping_sha256: str,
    ) -> KnowledgeRetrievalResult:
        return KnowledgeRetrievalResult(
            fault_class=fault_class,
            mapping_version=mapping_version,
            mapping_sha256=mapping_sha256,
            evidence=(),
            reason=reason,
        )


def _coerce_mapping(payload: object) -> FaultKnowledgeMapping:
    if type(payload) is FaultKnowledgeMapping:
        return payload
    if type(payload) is bytes:
        return _parse_mapping(_decode_json(payload))
    if type(payload) is dict:
        return _parse_mapping(cast(dict[object, object], payload))
    raise FaultKnowledgeMappingError("Fault knowledge mapping is invalid.")


def _decode_json(raw: bytes) -> Mapping[object, object]:
    try:
        payload: object = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except Exception:
        raise FaultKnowledgeMappingError(
            "Fault knowledge mapping JSON is invalid."
        ) from None
    if type(payload) is not dict:
        raise FaultKnowledgeMappingError("Fault knowledge mapping JSON is invalid.")
    return cast(dict[object, object], payload)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str:
            raise FaultKnowledgeMappingError(
                "Fault knowledge mapping JSON contains an invalid key."
            )
        if key in result:
            raise FaultKnowledgeMappingError(
                "Fault knowledge mapping JSON contains duplicate keys."
            )
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Never:
    raise ValueError("non-finite JSON number")


def _parse_mapping(payload: Mapping[object, object]) -> FaultKnowledgeMapping:
    fields = _exact_keys(
        payload,
        ("schema_version", "mapping_version", "mapping_sha256", "mappings"),
    )
    raw_entries = fields["mappings"]
    if type(raw_entries) is not list:
        raise FaultKnowledgeMappingError("Fault knowledge mappings are invalid.")
    entries: list[FaultKnowledgeMappingEntry] = []
    for raw_entry in cast(list[object], raw_entries):
        if type(raw_entry) is not dict:
            raise FaultKnowledgeMappingError(
                "Fault knowledge mapping entry is invalid."
            )
        entry = _exact_keys(
            cast(dict[object, object], raw_entry),
            ("fault_class", "document_ids"),
        )
        entries.append(
            FaultKnowledgeMappingEntry(
                fault_class=_mapping_text(entry["fault_class"]),
                document_ids=_document_id_sequence(entry["document_ids"]),
            )
        )
    schema_version = fields["schema_version"]
    if type(schema_version) is not int:
        raise FaultKnowledgeMappingError(
            "Fault knowledge mapping schema version is invalid."
        )
    return FaultKnowledgeMapping(
        schema_version=schema_version,
        mapping_version=_mapping_text(fields["mapping_version"]),
        mapping_sha256=_mapping_text(fields["mapping_sha256"]),
        mappings=tuple(entries),
    )


def _exact_keys(
    payload: Mapping[object, object],
    expected: tuple[str, ...],
) -> dict[str, object]:
    if type(payload) is not dict:
        raise FaultKnowledgeMappingError("Fault knowledge mapping fields are invalid.")
    try:
        typed_payload = cast(dict[object, object], payload)
        pairs: tuple[tuple[object, object], ...] = tuple(typed_payload.items())
    except Exception:
        raise FaultKnowledgeMappingError(
            "Fault knowledge mapping fields are invalid."
        ) from None
    fields: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str:
            raise FaultKnowledgeMappingError(
                "Fault knowledge mapping fields are invalid."
            )
        fields[key] = value
    if set(fields) != set(expected):
        raise FaultKnowledgeMappingError("Fault knowledge mapping fields are invalid.")
    return fields


def _mapping_text(value: object) -> str:
    if type(value) is not str:
        raise FaultKnowledgeMappingError("Fault knowledge mapping text is invalid.")
    return value


def _document_id_sequence(value: object) -> tuple[str, ...]:
    if type(value) not in {list, tuple}:
        raise FaultKnowledgeMappingError(
            "Fault knowledge document references must be an ordered sequence."
        )
    items = tuple(cast(list[object] | tuple[object, ...], value))
    if any(type(item) is not str for item in items):
        raise FaultKnowledgeMappingError(
            "Fault knowledge document reference is invalid."
        )
    return cast(tuple[str, ...], items)


def _normalize_entries(
    entries: Sequence[FaultKnowledgeMappingEntry],
) -> tuple[FaultKnowledgeMappingEntry, ...]:
    if type(entries) is not tuple:
        raise FaultKnowledgeMappingError("Fault knowledge mappings are invalid.")
    typed_entries = cast(tuple[object, ...], entries)
    snapshot: list[FaultKnowledgeMappingEntry] = []
    for raw_entry in typed_entries:
        if type(raw_entry) is not FaultKnowledgeMappingEntry:
            raise FaultKnowledgeMappingError("Fault knowledge mappings are invalid.")
        entry = raw_entry
        snapshot.append(
            FaultKnowledgeMappingEntry(
                fault_class=_mapping_text(entry.fault_class),
                document_ids=tuple(sorted(_document_id_sequence(entry.document_ids))),
            )
        )
    normalized = tuple(
        sorted(
            snapshot,
            key=lambda entry: entry.fault_class,
        )
    )
    fault_classes = tuple(entry.fault_class for entry in normalized)
    if len(fault_classes) != len(set(fault_classes)):
        raise FaultKnowledgeMappingError("Fault knowledge classes must be unique.")
    return normalized


def _normalize_mapping(mapping: FaultKnowledgeMapping) -> FaultKnowledgeMapping:
    if type(mapping) is not FaultKnowledgeMapping:
        raise FaultKnowledgeMappingError("Fault knowledge mapping is invalid.")
    return FaultKnowledgeMapping(
        schema_version=mapping.schema_version,
        mapping_version=_mapping_text(mapping.mapping_version),
        mapping_sha256=_mapping_text(mapping.mapping_sha256),
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


def _identify_normalized_mapping(mapping: FaultKnowledgeMapping) -> str:
    return sha256(_canonical_json_bytes(_mapping_semantics(mapping))).hexdigest()


def _validate_fault_class(value: object, *, mapping_error: bool) -> str:
    if (
        type(value) is not str
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
    return type(value) is str and pattern.fullmatch(value) is not None


def _is_expected_document_reference(value: object, document_id: str) -> bool:
    try:
        if (
            type(value) is not DocumentSnapshot
            or type(value.revision) is not int
            or value.revision < 1
            or type(value.document) is not GovernedDocument
            or not _matches_text_pattern(value.document.identity, _DOCUMENT_ID_PATTERN)
            or value.document.identity != document_id
            or type(value.document.versions) is not tuple
            or not value.document.versions
            or type(value.document.history) is not tuple
            or not value.document.history
            or any(
                type(version) is not DocumentVersion
                for version in value.document.versions
            )
            or any(
                type(event) is not LifecycleEvent for event in value.document.history
            )
        ):
            return False
    except Exception:
        return False
    return True


def _eligible_document_snapshot(
    value: object,
    *,
    document_id: str,
) -> tuple[_EligibleDocumentSnapshot | None, KnowledgeRetrievalReason | None]:
    try:
        if not _is_expected_document_reference(value, document_id):
            return None, KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
        snapshot = cast(DocumentSnapshot, value)
        document = snapshot.document
        versions = document.versions
        approved_numbers: list[int] = []
        for expected_number, version in enumerate(versions, start=1):
            if (
                type(version.number) is not int
                or version.number != expected_number
                or not _matches_text_pattern(version.sha256, _SHA256_PATTERN)
                or type(version.status) is not DocumentStatus
                or type(version.integrity) is not ProcessingIntegrity
                or type(version.integrity.extraction) is not ProcessingStepStatus
                or type(version.integrity.indexing) is not ProcessingStepStatus
            ):
                return None, KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
            if version.status is DocumentStatus.APPROVED:
                approved_numbers.append(version.number)

        current_version = document.current_version
        if current_version is None:
            if approved_numbers:
                return None, KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
            return None, None
        if (
            type(current_version) is not int
            or current_version < 1
            or current_version > len(versions)
            or approved_numbers != [current_version]
        ):
            return None, KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
        eligible = versions[current_version - 1]
        if (
            eligible.status is not DocumentStatus.APPROVED
            or eligible.integrity.extraction is not ProcessingStepStatus.SUCCEEDED
            or eligible.integrity.indexing is not ProcessingStepStatus.SUCCEEDED
        ):
            return None, KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
        return (
            _EligibleDocumentSnapshot(
                document_id=document_id,
                document_version=f"docver_{eligible.sha256}",
                source_sha256=eligible.sha256,
            ),
            None,
        )
    except Exception:
        return None, KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED


def _snapshot_indexed_record(
    value: object,
    *,
    document_id: str,
    document_version: str,
    source_sha256: str,
) -> tuple[_IndexedRecordSnapshot | None, bool]:
    try:
        if (
            type(value) is not IndexedChunk
            or type(value.chunk) is not DocumentChunk
            or type(value.embedding) is not ChunkEmbedding
            or type(value.chunk.provenance) is not ExtractionProvenance
        ):
            return None, True
        record = value
        chunk = record.chunk
        embedding = record.embedding
        provenance = chunk.provenance

        if (
            type(chunk.schema_version) is not int
            or chunk.schema_version != DOCUMENT_CHUNK_SCHEMA_VERSION
            or not _matches_text_pattern(chunk.chunk_id, _CHUNK_ID_PATTERN)
            or not _matches_text_pattern(chunk.document_id, _DOCUMENT_ID_PATTERN)
            or chunk.document_id != document_id
            or not _matches_text_pattern(
                chunk.document_version,
                _DOCUMENT_VERSION_PATTERN,
            )
            or chunk.document_version != document_version
            or type(chunk.content) is not str
            or chunk.content == ""
            or not _matches_text_pattern(chunk.content_sha256, _SHA256_PATTERN)
            or type(chunk.page_number) is not int
            or chunk.page_number < 1
            or not _matches_text_pattern(chunk.section_id, _SECTION_ID_PATTERN)
            or type(chunk.section_index) is not int
            or chunk.section_index < 1
            or not _is_exact_optional_text(chunk.section_title)
            or type(chunk.ordinal) is not int
            or chunk.ordinal < 1
            or type(chunk.section_chunk_index) is not int
            or chunk.section_chunk_index < 1
            or type(chunk.character_start) is not int
            or type(chunk.character_end) is not int
            or not 0 <= chunk.character_start < chunk.character_end
            or not _matches_text_pattern(
                chunk.chunking_configuration_id,
                _CHUNK_CONFIGURATION_ID_PATTERN,
            )
        ):
            return None, True
        content_sha256 = sha256(
            chunk.content.encode("utf-8", errors="strict")
        ).hexdigest()
        if chunk.content_sha256 != content_sha256:
            return None, True

        if (
            not _is_nonempty_exact_text(provenance.source_name)
            or not _matches_text_pattern(provenance.source_sha256, _SHA256_PATTERN)
            or provenance.source_sha256 != source_sha256
            or type(provenance.source_version) is not str
            or provenance.source_version != f"sha256:{source_sha256}"
            or type(provenance.source_size_bytes) is not int
            or provenance.source_size_bytes < 0
            or not _is_exact_optional_text(provenance.pdf_version)
            or type(provenance.extraction_schema_version) is not int
            or provenance.extraction_schema_version
            != SOURCE_DOCUMENT_EXTRACTION_SCHEMA_VERSION
            or type(provenance.extractor_version) is not int
            or provenance.extractor_version < 1
            or type(provenance.document_extraction_status) is not str
            or provenance.document_extraction_status
            not in tuple(status.value for status in DocumentExtractionStatus)
            or not _is_exact_optional_text(provenance.document_failure_code)
            or type(provenance.page_number) is not int
            or provenance.page_number != chunk.page_number
            or type(provenance.page_extraction_method) is not str
            or provenance.page_extraction_method
            not in tuple(method.value for method in PageExtractionMethod)
            or type(provenance.page_extraction_status) is not str
            or provenance.page_extraction_status
            not in tuple(status.value for status in PageExtractionStatus)
            or not _is_exact_optional_text(provenance.page_failure_code)
            or not _is_exact_text_tuple(provenance.ocr_trigger_codes)
            or not _is_exact_text_tuple(provenance.quality_signals)
            or not _is_nonempty_exact_text(provenance.pdfium_version)
            or not _is_exact_optional_text(provenance.ocr_adapter_name)
            or not _is_exact_optional_text(provenance.ocr_adapter_version)
        ):
            return None, True
        document_extracted = (
            provenance.document_extraction_status
            == DocumentExtractionStatus.COMPLETED.value
        )
        if document_extracted and provenance.document_failure_code is not None:
            return None, True
        page_extracted = (
            provenance.page_extraction_status == PageExtractionStatus.EXTRACTED.value
        )
        if page_extracted and (
            provenance.page_extraction_method
            not in (PageExtractionMethod.NATIVE.value, PageExtractionMethod.OCR.value)
            or provenance.page_failure_code is not None
        ):
            return None, True
        if (
            provenance.page_extraction_status == PageExtractionStatus.FAILED.value
            and provenance.page_failure_code is None
        ):
            return None, True

        if (
            not _matches_text_pattern(embedding.chunk_id, _CHUNK_ID_PATTERN)
            or embedding.chunk_id != chunk.chunk_id
            or not _is_nonempty_exact_text(embedding.provider_id)
            or not _is_nonempty_exact_text(embedding.representation_version)
            or type(embedding.dimension) is not int
            or embedding.dimension < 0
            or type(embedding.status) is not EmbeddingStatus
            or not _is_exact_optional_text(embedding.failure_code)
        ):
            return None, True
        vector = embedding.vector
        embedding_available = embedding.status is EmbeddingStatus.EMBEDDED
        if embedding_available:
            if (
                embedding.failure_code is not None
                or embedding.dimension < 1
                or type(vector) is not tuple
                or len(vector) != embedding.dimension
                or any(type(item) is not float or not isfinite(item) for item in vector)
            ):
                return None, True
        elif vector is not None or not _is_nonempty_exact_text(embedding.failure_code):
            return None, True

        safe_provenance = ExtractionProvenance(
            source_name=provenance.source_name,
            source_sha256=provenance.source_sha256,
            source_version=provenance.source_version,
            source_size_bytes=provenance.source_size_bytes,
            pdf_version=provenance.pdf_version,
            extraction_schema_version=provenance.extraction_schema_version,
            extractor_version=provenance.extractor_version,
            document_extraction_status=provenance.document_extraction_status,
            document_failure_code=provenance.document_failure_code,
            page_number=provenance.page_number,
            page_extraction_method=provenance.page_extraction_method,
            page_extraction_status=provenance.page_extraction_status,
            page_failure_code=provenance.page_failure_code,
            ocr_trigger_codes=tuple(provenance.ocr_trigger_codes),
            quality_signals=tuple(provenance.quality_signals),
            pdfium_version=provenance.pdfium_version,
            ocr_adapter_name=provenance.ocr_adapter_name,
            ocr_adapter_version=provenance.ocr_adapter_version,
        )
        safe_chunk = DocumentChunk(
            schema_version=chunk.schema_version,
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            document_version=chunk.document_version,
            content=chunk.content,
            content_sha256=chunk.content_sha256,
            page_number=chunk.page_number,
            section_id=chunk.section_id,
            section_index=chunk.section_index,
            section_title=chunk.section_title,
            ordinal=chunk.ordinal,
            section_chunk_index=chunk.section_chunk_index,
            character_start=chunk.character_start,
            character_end=chunk.character_end,
            chunking_configuration_id=chunk.chunking_configuration_id,
            provenance=safe_provenance,
        )
        safe_embedding = ChunkEmbedding(
            chunk_id=embedding.chunk_id,
            provider_id=embedding.provider_id,
            representation_version=embedding.representation_version,
            dimension=embedding.dimension,
            status=embedding.status,
            vector=None if vector is None else tuple(vector),
            failure_code=embedding.failure_code,
        )
        safe_record = IndexedChunk(chunk=safe_chunk, embedding=safe_embedding)
        fingerprint = _indexed_record_fingerprint(safe_record)
        eligible = document_extracted and page_extracted and embedding_available
        return (
            _IndexedRecordSnapshot(
                record=safe_record,
                evidence=_EvidenceSnapshot(
                    document_id=safe_chunk.document_id,
                    document_version=safe_chunk.document_version,
                    chunk_id=safe_chunk.chunk_id,
                    page_number=safe_chunk.page_number,
                    section_id=safe_chunk.section_id,
                ),
                source_sha256=source_sha256,
                fingerprint=fingerprint,
                eligible=eligible,
            ),
            False,
        )
    except Exception:
        return None, True


def _is_exact_optional_text(value: object) -> bool:
    return value is None or type(value) is str


def _is_nonempty_exact_text(value: object) -> bool:
    return type(value) is str and 0 < len(value) <= 500


def _is_exact_text_tuple(value: object) -> bool:
    return type(value) is tuple and all(
        type(item) is str for item in cast(tuple[object, ...], value)
    )


def _clone_indexed_record(record: IndexedChunk) -> IndexedChunk:
    chunk = record.chunk
    provenance = chunk.provenance
    embedding = record.embedding
    return IndexedChunk(
        chunk=DocumentChunk(
            schema_version=chunk.schema_version,
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            document_version=chunk.document_version,
            content=chunk.content,
            content_sha256=chunk.content_sha256,
            page_number=chunk.page_number,
            section_id=chunk.section_id,
            section_index=chunk.section_index,
            section_title=chunk.section_title,
            ordinal=chunk.ordinal,
            section_chunk_index=chunk.section_chunk_index,
            character_start=chunk.character_start,
            character_end=chunk.character_end,
            chunking_configuration_id=chunk.chunking_configuration_id,
            provenance=ExtractionProvenance(
                source_name=provenance.source_name,
                source_sha256=provenance.source_sha256,
                source_version=provenance.source_version,
                source_size_bytes=provenance.source_size_bytes,
                pdf_version=provenance.pdf_version,
                extraction_schema_version=provenance.extraction_schema_version,
                extractor_version=provenance.extractor_version,
                document_extraction_status=provenance.document_extraction_status,
                document_failure_code=provenance.document_failure_code,
                page_number=provenance.page_number,
                page_extraction_method=provenance.page_extraction_method,
                page_extraction_status=provenance.page_extraction_status,
                page_failure_code=provenance.page_failure_code,
                ocr_trigger_codes=tuple(provenance.ocr_trigger_codes),
                quality_signals=tuple(provenance.quality_signals),
                pdfium_version=provenance.pdfium_version,
                ocr_adapter_name=provenance.ocr_adapter_name,
                ocr_adapter_version=provenance.ocr_adapter_version,
            ),
        ),
        embedding=ChunkEmbedding(
            chunk_id=embedding.chunk_id,
            provider_id=embedding.provider_id,
            representation_version=embedding.representation_version,
            dimension=embedding.dimension,
            status=embedding.status,
            vector=None if embedding.vector is None else tuple(embedding.vector),
            failure_code=embedding.failure_code,
        ),
    )


def _indexed_record_fingerprint(record: IndexedChunk) -> bytes:
    chunk = record.chunk
    provenance = chunk.provenance
    embedding = record.embedding
    return _canonical_json_bytes(
        {
            "chunk": {
                "character_end": chunk.character_end,
                "character_start": chunk.character_start,
                "chunk_id": chunk.chunk_id,
                "chunking_configuration_id": chunk.chunking_configuration_id,
                "content": chunk.content,
                "content_sha256": chunk.content_sha256,
                "document_id": chunk.document_id,
                "document_version": chunk.document_version,
                "ordinal": chunk.ordinal,
                "page_number": chunk.page_number,
                "provenance": {
                    "document_extraction_status": provenance.document_extraction_status,
                    "document_failure_code": provenance.document_failure_code,
                    "extraction_schema_version": provenance.extraction_schema_version,
                    "extractor_version": provenance.extractor_version,
                    "ocr_adapter_name": provenance.ocr_adapter_name,
                    "ocr_adapter_version": provenance.ocr_adapter_version,
                    "ocr_trigger_codes": list(provenance.ocr_trigger_codes),
                    "page_extraction_method": provenance.page_extraction_method,
                    "page_extraction_status": provenance.page_extraction_status,
                    "page_failure_code": provenance.page_failure_code,
                    "page_number": provenance.page_number,
                    "pdf_version": provenance.pdf_version,
                    "pdfium_version": provenance.pdfium_version,
                    "quality_signals": list(provenance.quality_signals),
                    "source_name": provenance.source_name,
                    "source_sha256": provenance.source_sha256,
                    "source_size_bytes": provenance.source_size_bytes,
                    "source_version": provenance.source_version,
                },
                "schema_version": chunk.schema_version,
                "section_chunk_index": chunk.section_chunk_index,
                "section_id": chunk.section_id,
                "section_index": chunk.section_index,
                "section_title": chunk.section_title,
            },
            "embedding": {
                "chunk_id": embedding.chunk_id,
                "dimension": embedding.dimension,
                "failure_code": embedding.failure_code,
                "provider_id": embedding.provider_id,
                "representation_version": embedding.representation_version,
                "status": embedding.status.value,
                "vector": None if embedding.vector is None else list(embedding.vector),
            },
        }
    )


def _evidence_key(record: _EvidenceSnapshot) -> tuple[str, str, str]:
    return (
        record.document_id,
        record.document_version,
        record.chunk_id,
    )


def _scored_order_key(
    item: _ScoredChunk,
) -> tuple[float, str, str, int, str, str]:
    return (
        -item.score,
        item.evidence.document_id,
        item.evidence.document_version,
        item.evidence.page_number,
        item.evidence.section_id,
        item.evidence.chunk_id,
    )
