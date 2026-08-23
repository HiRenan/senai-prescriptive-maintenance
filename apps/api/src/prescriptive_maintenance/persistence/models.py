"""Minimal immutable metadata persisted for traceable analyses."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from prescriptive_maintenance.domain import AnalysisOutcome

_ANALYSIS_ID: Final = re.compile(r"^ana_[a-z0-9_]{3,64}$")
_DOCUMENT_ID: Final = re.compile(r"^doc_[a-z0-9_]{3,64}$")
_DOCUMENT_VERSION_ID: Final = re.compile(r"^docver_[a-z0-9_]{3,64}$")
_CHUNK_REF: Final = re.compile(r"^chunk_[a-z0-9_]{3,64}$")
_EVIDENCE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DATASET_ID: Final = re.compile(r"^[0-9a-f]{64}$")
_MODEL_ID: Final = re.compile(r"^model_[a-z0-9_.-]{3,64}$")
_PROMPT_ID: Final = re.compile(r"^prompt_[a-z0-9_.-]{3,64}$")
_CONFIGURATION_ID: Final = re.compile(r"^config_[a-z0-9_.-]{3,64}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


def _validate_identifier(value: str, pattern: re.Pattern[str], label: str) -> None:
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{label} does not match its traceable identifier format.")


def _validate_aware_datetime(value: datetime, label: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class ChunkReference:
    """Opaque document location compatible with the SEN-45 citation contract."""

    chunk_ref: str
    document_id: str
    document_version_id: str
    page_number: int

    def __post_init__(self) -> None:
        _validate_identifier(self.chunk_ref, _CHUNK_REF, "chunk_ref")
        _validate_identifier(self.document_id, _DOCUMENT_ID, "document_id")
        _validate_identifier(
            self.document_version_id,
            _DOCUMENT_VERSION_ID,
            "document_version_id",
        )
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("page_number must be a positive integer.")


@dataclass(frozen=True, slots=True)
class DocumentVersionMetadata:
    """One content identity and its opaque chunk locations, never its bytes."""

    document_version_id: str
    document_id: str
    source_sha256: str
    created_at: datetime
    chunks: tuple[ChunkReference, ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(
            self.document_version_id,
            _DOCUMENT_VERSION_ID,
            "document_version_id",
        )
        _validate_identifier(self.document_id, _DOCUMENT_ID, "document_id")
        if _SHA256.fullmatch(self.source_sha256) is None:
            raise ValueError("source_sha256 must be a lowercase SHA-256 digest.")
        _validate_aware_datetime(self.created_at, "created_at")

        chunks = tuple(sorted(self.chunks, key=lambda chunk: chunk.chunk_ref))
        if len({chunk.chunk_ref for chunk in chunks}) != len(chunks):
            raise ValueError("Chunk references must be unique within a version.")
        if any(
            chunk.document_id != self.document_id
            or chunk.document_version_id != self.document_version_id
            for chunk in chunks
        ):
            raise ValueError(
                "Every chunk must belong to its declared document version."
            )
        object.__setattr__(self, "chunks", chunks)


@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    """Stable document identity plus versions safe for persistence."""

    document_id: str
    created_at: datetime
    versions: tuple[DocumentVersionMetadata, ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(self.document_id, _DOCUMENT_ID, "document_id")
        _validate_aware_datetime(self.created_at, "created_at")

        versions = tuple(
            sorted(self.versions, key=lambda version: version.document_version_id)
        )
        if len({version.document_version_id for version in versions}) != len(versions):
            raise ValueError("Document version identifiers must be unique.")
        if any(version.document_id != self.document_id for version in versions):
            raise ValueError("Every version must belong to its declared document.")
        if len({version.source_sha256 for version in versions}) != len(versions):
            raise ValueError("Document version hashes must be unique.")
        chunk_refs = [
            chunk.chunk_ref for version in versions for chunk in version.chunks
        ]
        if len(set(chunk_refs)) != len(chunk_refs):
            raise ValueError(
                "Chunk references must be unique across document versions."
            )
        object.__setattr__(self, "versions", versions)


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """Ordered link from an analysis to a governed document chunk."""

    evidence_id: str
    document_id: str
    document_version_id: str
    chunk_ref: str
    ordinal: int

    def __post_init__(self) -> None:
        _validate_identifier(self.evidence_id, _EVIDENCE_ID, "evidence_id")
        _validate_identifier(self.document_id, _DOCUMENT_ID, "document_id")
        _validate_identifier(
            self.document_version_id,
            _DOCUMENT_VERSION_ID,
            "document_version_id",
        )
        _validate_identifier(self.chunk_ref, _CHUNK_REF, "chunk_ref")
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise ValueError("ordinal must be a positive integer.")


@dataclass(frozen=True, slots=True)
class AnalysisMetadata:
    """Trace identifiers and governed evidence for one API v1 analysis."""

    analysis_id: str
    outcome: AnalysisOutcome
    dataset_id: str
    model_id: str
    prompt_id: str
    configuration_id: str
    created_at: datetime
    evidence_references: tuple[EvidenceReference, ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(self.analysis_id, _ANALYSIS_ID, "analysis_id")
        if type(self.outcome) is not AnalysisOutcome:
            raise ValueError("outcome must be one of the five API v1 outcomes.")
        _validate_identifier(self.dataset_id, _DATASET_ID, "dataset_id")
        _validate_identifier(self.model_id, _MODEL_ID, "model_id")
        _validate_identifier(self.prompt_id, _PROMPT_ID, "prompt_id")
        _validate_identifier(
            self.configuration_id,
            _CONFIGURATION_ID,
            "configuration_id",
        )
        _validate_aware_datetime(self.created_at, "created_at")

        references = tuple(
            sorted(self.evidence_references, key=lambda reference: reference.ordinal)
        )
        if len({reference.evidence_id for reference in references}) != len(references):
            raise ValueError("Evidence identifiers must be unique within an analysis.")
        if tuple(reference.ordinal for reference in references) != tuple(
            range(1, len(references) + 1)
        ):
            raise ValueError("Evidence ordinals must be contiguous and start at one.")
        object.__setattr__(self, "evidence_references", references)

    @property
    def document_version_ids(self) -> tuple[str, ...]:
        """Return every used version once, preserving first evidence order."""

        identifiers: list[str] = []
        for reference in self.evidence_references:
            if reference.document_version_id not in identifiers:
                identifiers.append(reference.document_version_id)
        return tuple(identifiers)


def _canonical_chunk_reference(reference: ChunkReference) -> ChunkReference:
    return ChunkReference(
        chunk_ref=reference.chunk_ref,
        document_id=reference.document_id,
        document_version_id=reference.document_version_id,
        page_number=reference.page_number,
    )


def canonical_document_version(
    version: DocumentVersionMetadata,
) -> DocumentVersionMetadata:
    """Rebuild one version using only its governed metadata fields."""

    return DocumentVersionMetadata(
        document_version_id=version.document_version_id,
        document_id=version.document_id,
        source_sha256=version.source_sha256,
        created_at=version.created_at,
        chunks=tuple(_canonical_chunk_reference(chunk) for chunk in version.chunks),
    )


def canonical_document(document: DocumentMetadata) -> DocumentMetadata:
    """Rebuild one document without retaining caller-owned subclasses."""

    return DocumentMetadata(
        document_id=document.document_id,
        created_at=document.created_at,
        versions=tuple(
            canonical_document_version(version) for version in document.versions
        ),
    )


def _canonical_evidence_reference(reference: EvidenceReference) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=reference.evidence_id,
        document_id=reference.document_id,
        document_version_id=reference.document_version_id,
        chunk_ref=reference.chunk_ref,
        ordinal=reference.ordinal,
    )


def canonical_analysis(analysis: AnalysisMetadata) -> AnalysisMetadata:
    """Rebuild one analysis using the closed, minimal persistence shape."""

    return AnalysisMetadata(
        analysis_id=analysis.analysis_id,
        outcome=analysis.outcome,
        dataset_id=analysis.dataset_id,
        model_id=analysis.model_id,
        prompt_id=analysis.prompt_id,
        configuration_id=analysis.configuration_id,
        created_at=analysis.created_at,
        evidence_references=tuple(
            _canonical_evidence_reference(reference)
            for reference in analysis.evidence_references
        ),
    )
