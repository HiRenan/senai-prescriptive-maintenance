"""Entirely synthetic persistence aggregates shared by adapter tests."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Final

from prescriptive_maintenance.domain import AnalysisOutcome
from prescriptive_maintenance.persistence import (
    AnalysisMetadata,
    ChunkReference,
    DocumentMetadata,
    DocumentVersionMetadata,
    EvidenceReference,
)

SYNTHETIC_TIME: Final = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
SYNTHETIC_DATASET_ID: Final = "a" * 64
SYNTHETIC_FORBIDDEN_PAYLOAD: Final = (
    "synthetic raw payload that must never be persisted"
)


class SyntheticTaintedStr(str):
    """Accepted string subtype carrying forbidden caller-owned state."""

    raw_content: str


class SyntheticTaintedDateTime(datetime):
    """Accepted datetime subtype carrying forbidden caller-owned state."""

    raw_content: str


SYNTHETIC_DOCUMENT_VERSION_V1: Final = DocumentVersionMetadata(
    document_version_id="docver_synthetic_guide_v1",
    document_id="doc_synthetic_guide",
    source_sha256="1" * 64,
    created_at=SYNTHETIC_TIME,
    chunks=(
        ChunkReference(
            chunk_ref="chunk_synthetic_guide_v1_02",
            document_id="doc_synthetic_guide",
            document_version_id="docver_synthetic_guide_v1",
            page_number=2,
        ),
        ChunkReference(
            chunk_ref="chunk_synthetic_guide_v1_01",
            document_id="doc_synthetic_guide",
            document_version_id="docver_synthetic_guide_v1",
            page_number=1,
        ),
    ),
)
SYNTHETIC_DOCUMENT_VERSION_V2: Final = DocumentVersionMetadata(
    document_version_id="docver_synthetic_guide_v2",
    document_id="doc_synthetic_guide",
    source_sha256="2" * 64,
    created_at=SYNTHETIC_TIME,
    chunks=(
        ChunkReference(
            chunk_ref="chunk_synthetic_guide_v2_01",
            document_id="doc_synthetic_guide",
            document_version_id="docver_synthetic_guide_v2",
            page_number=3,
        ),
    ),
)
SYNTHETIC_INITIAL_DOCUMENT: Final = DocumentMetadata(
    document_id="doc_synthetic_guide",
    created_at=SYNTHETIC_TIME,
    versions=(SYNTHETIC_DOCUMENT_VERSION_V1,),
)
SYNTHETIC_DOCUMENT: Final = DocumentMetadata(
    document_id="doc_synthetic_guide",
    created_at=SYNTHETIC_TIME,
    versions=(
        SYNTHETIC_DOCUMENT_VERSION_V2,
        SYNTHETIC_DOCUMENT_VERSION_V1,
    ),
)

SYNTHETIC_ANALYSIS: Final = AnalysisMetadata(
    analysis_id="ana_synthetic_trace",
    outcome=AnalysisOutcome.DOCUMENTED_FAULT,
    dataset_id=SYNTHETIC_DATASET_ID,
    model_id="model_synthetic_v1",
    prompt_id="prompt_synthetic_v1",
    configuration_id="config_synthetic_v1",
    created_at=SYNTHETIC_TIME,
    evidence_references=(
        EvidenceReference(
            evidence_id="synthetic-evidence-v1-chunk-02",
            document_id="doc_synthetic_guide",
            document_version_id="docver_synthetic_guide_v1",
            chunk_ref="chunk_synthetic_guide_v1_02",
            ordinal=2,
        ),
        EvidenceReference(
            evidence_id="synthetic-evidence-v2-chunk-01",
            document_id="doc_synthetic_guide",
            document_version_id="docver_synthetic_guide_v2",
            chunk_ref="chunk_synthetic_guide_v2_01",
            ordinal=1,
        ),
    ),
)


def _tainted_text(value: str) -> SyntheticTaintedStr:
    tainted = SyntheticTaintedStr(value)
    tainted.raw_content = SYNTHETIC_FORBIDDEN_PAYLOAD
    return tainted


def _tainted_datetime(value: datetime) -> SyntheticTaintedDateTime:
    local_value = value.astimezone(timezone(timedelta(hours=-3)))
    tainted = SyntheticTaintedDateTime(
        local_value.year,
        local_value.month,
        local_value.day,
        local_value.hour,
        local_value.minute,
        local_value.second,
        local_value.microsecond,
        tzinfo=local_value.tzinfo,
        fold=local_value.fold,
    )
    tainted.raw_content = SYNTHETIC_FORBIDDEN_PAYLOAD
    return tainted


def synthetic_tainted_scalar_aggregates() -> tuple[
    DocumentMetadata,
    AnalysisMetadata,
]:
    """Build every persisted scalar field with accepted synthetic subtypes."""

    versions = tuple(
        DocumentVersionMetadata(
            document_version_id=_tainted_text(version.document_version_id),
            document_id=_tainted_text(version.document_id),
            source_sha256=_tainted_text(version.source_sha256),
            created_at=_tainted_datetime(version.created_at),
            chunks=tuple(
                ChunkReference(
                    chunk_ref=_tainted_text(chunk.chunk_ref),
                    document_id=_tainted_text(chunk.document_id),
                    document_version_id=_tainted_text(chunk.document_version_id),
                    page_number=chunk.page_number,
                )
                for chunk in version.chunks
            ),
        )
        for version in SYNTHETIC_DOCUMENT.versions
    )
    document = DocumentMetadata(
        document_id=_tainted_text(SYNTHETIC_DOCUMENT.document_id),
        created_at=_tainted_datetime(SYNTHETIC_DOCUMENT.created_at),
        versions=versions,
    )
    analysis = AnalysisMetadata(
        analysis_id=_tainted_text(SYNTHETIC_ANALYSIS.analysis_id),
        outcome=SYNTHETIC_ANALYSIS.outcome,
        dataset_id=_tainted_text(SYNTHETIC_ANALYSIS.dataset_id),
        model_id=_tainted_text(SYNTHETIC_ANALYSIS.model_id),
        prompt_id=_tainted_text(SYNTHETIC_ANALYSIS.prompt_id),
        configuration_id=_tainted_text(SYNTHETIC_ANALYSIS.configuration_id),
        created_at=_tainted_datetime(SYNTHETIC_ANALYSIS.created_at),
        evidence_references=tuple(
            EvidenceReference(
                evidence_id=_tainted_text(reference.evidence_id),
                document_id=_tainted_text(reference.document_id),
                document_version_id=_tainted_text(reference.document_version_id),
                chunk_ref=_tainted_text(reference.chunk_ref),
                ordinal=reference.ordinal,
            )
            for reference in SYNTHETIC_ANALYSIS.evidence_references
        ),
    )
    return document, analysis


def assert_persisted_scalars_are_canonical(
    document: DocumentMetadata,
    analysis: AnalysisMetadata,
) -> None:
    """Assert base scalar types recursively across the persistence shape."""

    versions = document.versions
    chunks = tuple(chunk for version in versions for chunk in version.chunks)
    references = analysis.evidence_references
    text_values = (
        document.document_id,
        *(version.document_version_id for version in versions),
        *(version.document_id for version in versions),
        *(version.source_sha256 for version in versions),
        *(chunk.chunk_ref for chunk in chunks),
        *(chunk.document_id for chunk in chunks),
        *(chunk.document_version_id for chunk in chunks),
        analysis.analysis_id,
        analysis.dataset_id,
        analysis.model_id,
        analysis.prompt_id,
        analysis.configuration_id,
        *(reference.evidence_id for reference in references),
        *(reference.document_id for reference in references),
        *(reference.document_version_id for reference in references),
        *(reference.chunk_ref for reference in references),
    )
    datetime_values = (
        document.created_at,
        *(version.created_at for version in versions),
        analysis.created_at,
    )
    integer_values = (
        *(chunk.page_number for chunk in chunks),
        *(reference.ordinal for reference in references),
    )
    scalar_values: tuple[object, ...] = (
        *text_values,
        *datetime_values,
        *integer_values,
        analysis.outcome,
    )

    assert all(type(value) is str for value in text_values)
    assert all(type(value) is datetime for value in datetime_values)
    assert all(type(value) is int for value in integer_values)
    assert type(analysis.outcome) is AnalysisOutcome
    assert all(not hasattr(value, "raw_content") for value in scalar_values)
